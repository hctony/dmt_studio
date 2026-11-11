import json
import os
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QApplication
from core.models import DefectRecord, TrialContext
from core.ivs import build_summary, records_to_json
from core.ivs_population import population_payload, xml_grid_units
from workers.ivs_workers import IvsScanThread


class IvsControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def test_stop_before_connect_never_reads_or_publishes_partial_data(self):
        worker=IvsScanThread('M','RX-',units_per_strip=64)
        cancelled=[];finished=[];failed=[]
        worker.cancelled.connect(lambda:cancelled.append(True));worker.finished.connect(finished.append);worker.failed.connect(failed.append)
        worker.stop()
        with patch('workers.ivs_workers.connect') as connect:
            worker.run()
        connect.assert_not_called();self.assertEqual(cancelled,[True]);self.assertEqual(finished,[]);self.assertEqual(failed,[])

    def test_stop_unblocks_a_real_worker_waiting_on_its_own_ssh_connection(self):
        entered=threading.Event();released=threading.Event();ssh=SimpleNamespace(close=released.set)
        worker=IvsScanThread('M','RX-',units_per_strip=64)
        def parse(*args):
            entered.set()
            if not released.wait(4):
                raise RuntimeError('Stop did not close transport')
            raise OSError('SSH connection closed')
        cancelled=[];results=[];errors=[]
        worker.cancelled.connect(lambda:cancelled.append(True),Qt.DirectConnection);worker.finished.connect(results.append,Qt.DirectConnection);worker.failed.connect(errors.append,Qt.DirectConnection)
        ctx=SimpleNamespace(path='/trial',machine='A',trial='T')
        with patch('workers.ivs_workers.connect',return_value=(ssh,MagicMock())),patch('workers.ivs_workers.list_lot_trials',return_value=[ctx]),patch('workers.ivs_workers.load_valid_bundle_indexes',return_value={1}),patch('workers.ivs_workers.iter_dirs',return_value=['1']),patch('workers.ivs_workers.find_ivs_files',return_value=['one.xml']),patch('workers.ivs_workers.parse_ivs_xml',side_effect=parse):
            worker.start();self.assertTrue(entered.wait(2));worker.stop();self.assertTrue(worker.wait(3000))
        self.assertEqual(cancelled,[True]);self.assertEqual(results,[]);self.assertEqual(errors,[])

    def test_four_parallel_scans_stop_only_the_requested_connection(self):
        from core.cancellation import check_cancelled
        names=['A','B','C','D'];entered={name:threading.Event() for name in names};released={name:threading.Event() for name in names}
        workers=[IvsScanThread(name,'RX-',units_per_strip=64) for name in names];cancelled=[];completed=[];errors=[]
        for worker in workers:
            worker.cancelled.connect(lambda name=worker.model:cancelled.append(name),Qt.DirectConnection)
            worker.finished.connect(lambda payload:completed.append(payload['model']),Qt.DirectConnection)
            worker.failed.connect(errors.append,Qt.DirectConnection)
        def connection():
            name=QThread.currentThread().model
            return SimpleNamespace(close=released[name].set),MagicMock()
        def parse(path,sftp,ctx,folder,progress,should_stop,on_strip):
            entered[ctx.model].set()
            while not released[ctx.model].wait(.01):check_cancelled(should_stop)
            check_cancelled(should_stop);on_strip('RX-'+ctx.model+'01',64);return []
        with patch('workers.ivs_workers.connect',side_effect=connection),patch('workers.ivs_workers.list_lot_trials',side_effect=lambda model,*args:[SimpleNamespace(model=model,path='/trial',machine='machine',trial='T')]),patch('workers.ivs_workers.load_valid_bundle_indexes',return_value={1}),patch('workers.ivs_workers.iter_dirs',return_value=['1']),patch('workers.ivs_workers.find_ivs_files',return_value=['one.xml']),patch('workers.ivs_workers.parse_ivs_xml',side_effect=parse):
            try:
                for worker in workers:worker.start()
                self.assertTrue(all(event.wait(2) for event in entered.values()))
                workers[1].stop();self.assertTrue(workers[1].wait(2000))
                self.assertTrue(all(workers[i].isRunning() for i in (0,2,3)))
            finally:
                for event in released.values():event.set()
                for worker in workers:self.assertTrue(worker.wait(3000))
        self.assertEqual(cancelled,['B']);self.assertEqual(set(completed),{'A','C','D'});self.assertEqual(errors,[])

    def test_native_thread_cleanup_finishes_before_scan_button_reenables(self):
        from ui.tabs.ivs_tab import IvsStudioTab
        from core import yield_handoff
        released=threading.Event()
        class Worker(QThread):
            progress=Signal(str);finished=Signal(dict);failed=Signal(str);cancelled=Signal()
            def __init__(self,*args):super().__init__()
            def run(self):
                self.finished.emit(records_to_json('M','RX-',[]))
                released.wait(3)
        with tempfile.TemporaryDirectory() as folder,patch.object(yield_handoff,'DATA_DIR',Path(folder)),patch('ui.tabs.ivs_tab.IvsScanThread',Worker):
            tab=IvsStudioTab();tab.model_in.setText('M');tab.lot_in.setText('RX-');tab.trial_path_in.clear();tab.units_per_strip_in.setText('64')
            try:
                tab.start_scan();original=tab.scan_thread
                end=time.monotonic()+1
                while not getattr(tab,'current_payload',None) and time.monotonic()<end:self.app.processEvents();time.sleep(.005)
                self.assertTrue(original.isRunning());self.assertFalse(tab.btn_scan.isEnabled());tab.start_scan();self.assertIs(tab.scan_thread,original)
                released.set();self.assertTrue(original.wait(1000));end=time.monotonic()+1
                while not tab.btn_scan.isEnabled() and time.monotonic()<end:self.app.processEvents();time.sleep(.01)
                self.assertTrue(tab.btn_scan.isEnabled())
            finally:
                released.set();tab.scan_thread.wait(1000);tab.deleteLater();self.app.processEvents()

    def test_single_rate_counts_unique_units_and_uses_good_strips_in_denominator(self):
        record=DefectRecord('RX-01','A','M','RX-','T','1','Unit','T','1','0','SM03','SM03','','F',1,1,0,0,'x',[])
        records=[record,replace(record,face='B'),replace(record,unit_idx='2'),replace(record,unit_idx='2',ivs_1='OT12')]
        summary=build_summary(records,192)
        by_code={r['ivs_1']:r for r in summary['by_defect_type']}
        self.assertEqual(by_code['SM03']['defect_units'],2)
        self.assertEqual(by_code['SM03']['rate_percent'],100)
        self.assertEqual(by_code['SM03']['single_defect_rate_percent'],1.041667)
        self.assertEqual(by_code['OT12']['single_defect_rate_percent'],0.520833)
        self.assertIsNone(build_summary(records)['by_defect_type'][0]['single_defect_rate_percent'])

    def test_missing_population_or_grid_conflict_never_guesses_denominator(self):
        self.assertIsNone(population_payload(['RX-01'],64,missing=1)['total_units'])
        self.assertIsNone(population_payload(['RX-01'],64,[88])['total_units'])
        self.assertEqual(population_payload(['RX-01','RX-01','RX-02'],0,[64])['total_units'],128)
        import xml.etree.ElementTree as E
        self.assertEqual(xml_grid_units(E.fromstring('<Root rows="4" columns="16"/>')),64)
        self.assertEqual(xml_grid_units(E.fromstring('<Root><P strip_pt_x="22" strip_pt_y="4"/></Root>')),0)

    def test_yield_uses_the_same_verified_full_lot_denominator_and_keeps_missing_data_checks(self):
        from core.yield_calc import calculate_yield_summary_by_process,calculate_yield_summary_many
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);txt=root/'gather.txt';ids=root/'strips.json'
            txt.write_text('1,RX-,RX-01\nRX-,RX-01,1,1,T,SM03\nRX-,RX-01,EOS\n')
            ids.write_text(json.dumps({'schema':'dmt-studio-strip-population-v1','scope':'lot','complete':True,'lot_id':'RX-','strip_ids':['RX-01','RX-02','RX-03'],'units_per_strip':64}))
            summary=calculate_yield_summary_by_process([txt],[],[],[],ids,64,13)
            self.assertEqual(summary['total_units'],192);self.assertEqual(summary['good_units'],191)
            self.assertEqual(summary['strip_json_validation']['json_only_strips'],['RX-02','RX-03'])
            self.assertEqual(calculate_yield_summary_many([txt],ids,64,13)['total_units'],192)
            with self.assertRaisesRegex(ValueError,'Unit'):
                calculate_yield_summary_by_process([txt],[],[],[],ids,88,13)

    def test_nas_scan_counts_zero_defect_and_fc_only_strips_without_recounting_retests(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);remote='/Pixel/ResultData/Data/A/M/RX-/T';local=root/remote.lstrip('/')
            for index in ('1','2','3'):(local/index).mkdir(parents=True)
            (local/'1/one_ivs.xml').write_text('<Root barcode="RX-01" rows="4" columns="16"><Unit><P1 ivs_1="SM03" unit_idx="1" strip_pt_x="1" strip_pt_y="1"/></Unit></Root>')
            (local/'2/two_ivs.xml').write_text('<Root barcode="RX-02" rows="4" columns="16"><Unit><P1 ivs_1="0" unit_idx="1"/></Unit></Root>')
            (local/'3/fc.xml').write_text('<Root barcode="RX-03"/>')
            class Sftp:
                def open(self,p,mode='rb'):return (root/str(p).lstrip('/')).open(mode)
                def listdir_attr(self,p):return [SimpleNamespace(filename=f.name,st_mode=f.stat().st_mode) for f in (root/str(p).lstrip('/')).iterdir()]
                def stat(self,p):return (root/str(p).lstrip('/')).stat()
                def close(self):pass
            ctx=TrialContext('A','M','RX-','T',remote);worker=IvsScanThread('M','RX-',units_per_strip=64);results=[];errors=[]
            worker.finished.connect(results.append);worker.failed.connect(errors.append)
            with patch('workers.ivs_workers.connect',return_value=(SimpleNamespace(close=lambda:None),Sftp())),patch('workers.ivs_workers.list_lot_trials',return_value=[ctx,ctx]),patch('workers.ivs_workers.load_valid_bundle_indexes',return_value={1,2,3}):
                worker.run()
            self.assertFalse(errors,errors);population=results[0]['population'];self.assertTrue(population['complete']);self.assertEqual(population['strip_count'],3);self.assertEqual(population['total_units'],192)
            self.assertEqual(results[0]['summary']['by_defect_type'][0]['single_defect_rate_percent'],0.520833)
