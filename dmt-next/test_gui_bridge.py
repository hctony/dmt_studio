import os
import sys
import json
import tempfile
import unittest
import time
from unittest.mock import patch
from pathlib import Path

os.environ['QT_QPA_PLATFORM']='offscreen'
_settings=tempfile.TemporaryDirectory()
os.environ['DMT_DATA_DIR']=_settings.name
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from PySide6 import QtWidgets as W, QtCore as C
C.QSettings.setDefaultFormat(C.QSettings.IniFormat)
C.QSettings.setPath(C.QSettings.IniFormat,C.QSettings.UserScope,_settings.name)
from gui_bridge import Controller


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=W.QApplication.instance() or W.QApplication([])
    def setUp(self):
        self.events=[];self.c=Controller(self.app,self.events.append)
        self.answers={};self.dialogs=[]
        def ask(kind,data):
            self.dialogs.append((kind,data))
            answer=self.answers.get(kind)
            if callable(answer):return answer(data)
            return answer if answer is not None else W.QMessageBox.Ok.value if kind=='message' else ''
        self.c.ask=ask
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self): self.c.timer.stop();self.c.window.close();self.c.window.deleteLater();self.app.processEvents();self.temp.cleanup()
    def tab(self,i):
        tabs=self.c.window.centralWidget();tabs.setCurrentIndex(i);self.app.processEvents();return tabs.widget(i)
    def action(self,w,kind,value=None,**extra):
        self.c.handle({'id':99,'method':'gui.action','args':dict(id=self.c.register(w),action=kind,value=value,**extra)})
        errors=[e for e in self.events if e.get('id')==99 and 'error' in e]
        self.assertFalse(errors,errors)
    def fixture(self):
        txt=self.root/'gather.txt';txt.write_text('1,RX1234-,RX1234-01\nRX1234-,RX1234-01,1,1,T,SM03\nRX1234-,RX1234-01,EOS\n',encoding='utf-8')
        ids=self.root/'strip.json';ids.write_text(json.dumps({'lot_id':'RX1234-','strip_ids':['RX1234-01']}))
        return txt,ids
    def test_all_ten_tabs_serialize_original_controls(self):
        def walk(w):
            if not w:return []
            return [w]+sum([walk(c) for c in w.get('children',[])],[])+walk(w.get('content'))
        total=0
        for i in range(10):
            self.tab(i);s=self.c.snapshot();nodes=walk(s['root']['content'])
            self.assertTrue(any(w['type']=='button' for w in nodes),i)
            total+=sum(w['type']=='button' for w in nodes)
            self.assertEqual(s['root']['index'],i)
        self.assertGreater(total,45)
    def test_yield_original_calculation_and_excel_export(self):
        txt,ids=self.fixture();tab=self.tab(6)
        for field in ('vrs_txt_in','fvi_txt_in','ivs_txt_in','ivs_json_in','skipdata_in','ski_in'):getattr(tab,field).clear()
        tab.txt_in.setText(str(txt));tab.strip_json_in.setText(str(ids));tab.units_per_strip_in.setText('4');tab.xout_limit_in.setText('3')
        self.action(tab.btn_calc,'click')
        self.assertEqual(tab.summary['total_units'],4)
        self.assertEqual(tab.summary['good_units'],3)
        target=self.root/'report.xlsx';self.answers['save']=str(target);self.answers['file']=''
        self.action(tab.btn_export_excel,'click')
        self.assertTrue(target.is_file(),self.dialogs)
        from openpyxl import load_workbook
        self.assertTrue(load_workbook(target).sheetnames)
    def test_ivs_json_loading_and_list_checkbox(self):
        from core.models import DefectRecord
        from core.ivs import records_to_json
        record=DefectRecord(strip_id='RX1234-01',machine='AFVI1',model='M',lot='RX1234-',trial='T',strip_folder='1',container='Unit',face='T',unit_idx='1',img_merge_idx='0',ivs_1='SM03',code_main='SM03',code_sub='',def_side='F',strip_pt_x=1,strip_pt_y=1,img_pt_x=0,img_pt_y=0,source_xml='',images=[])
        file=self.root/'ivs.json';file.write_text(json.dumps(records_to_json('M','RX1234-',[record])))
        self.answers['file']=str(file);tab=self.tab(0)
        self.action(tab.btn_load,'click');self.assertEqual(len(tab.records),1)
        self.action(tab.download_group_list,'list-check',False,row=0)
        self.assertEqual(tab.download_group_list.item(0).checkState(),C.Qt.Unchecked)
    def test_lm_preview_cancel_then_execute_with_backup(self):
        tab=self.tab(8);smk=self.root/'smk';pmk=self.root/'pmk';smk.mkdir();pmk.mkdir()
        (smk/'S1.smk').write_text('sample');(pmk/'x_Master.pmk').write_text('S1\nS2\n')
        data=self.root/'validation.json';data.write_text(json.dumps({'lot_id':'RX1234-','strip_ids':{'ski_only_strips':['S1']}}))
        tab.json_in.setText(str(data));tab.smk_folder_in.setText(str(smk));tab.pmk_folder_in.setText(str(pmk))
        self.action(tab.btn_preview,'click');self.assertEqual(tab.last_preview['smk_move_count'],1)
        self.answers['message']=W.QMessageBox.No.value;self.action(tab.btn_execute,'click');self.assertTrue((smk/'S1.smk').exists())
        self.answers['message']=W.QMessageBox.Yes.value;self.action(tab.btn_execute,'click')
        self.assertTrue((smk/'BK'/'S1.smk').exists());self.assertEqual((pmk/'x_Master.pmk').read_text(),'S2\n');self.assertTrue(list((pmk/'BK').glob('*.bak')))
    def test_date_and_duplicate_dialogs_have_electron_actions(self):
        from ui.tabs.gathering_tab import ReworkDateDialog,GatherDuplicateDialog
        date=ReworkDateDialog(['2026-01-01','2026-01-02'],self.c.window);date.show();self.app.processEvents()
        self.action(date.combo,'select',1);self.assertEqual(date.get_selected_date(),'2026-01-02')
        self.assertTrue(self.c.snapshot()['dialogs']);self.action(date,'reject');self.assertFalse(date.isVisible())
        duplicate=GatherDuplicateDialog({'strip_id':'S1'},1,1,self.c.window);duplicate.show();self.app.processEvents()
        self.action(duplicate.radio_merge,'check',True);duplicate.accept();self.assertEqual(duplicate.choice,'merge')
    def test_table_pagination_and_input_persistence(self):
        table=W.QTableWidget(450,1,self.c.window);table.show()
        for i in range(450):table.setItem(i,0,W.QTableWidgetItem(str(i)))
        self.action(table,'page',400);s=self.c.widget(table);self.assertEqual(s['rows'][0],['400']);self.assertEqual(s['total'],450)
        table.setSortingEnabled(True)
        self.action(table,'sort',True,column=0);self.action(table,'page',0)
        self.assertEqual(self.c.widget(table)['rows'][0],['99'])
        self.assertTrue(self.c.widget(table)['sortable'])
        tab=self.tab(1);self.action(tab.model_in,'input','TEST-MODEL')
        self.assertIn('TEST-MODEL',self.c.input_path.read_text(encoding='utf-8'))
    def test_original_test_registry(self):
        from ui.tabs.test_tab import TESTS,_FUNC_MAP
        for _,label,name in TESTS:
            with self.subTest(label=label):_FUNC_MAP[name]()
    def test_workflow_recommendation_apply_preserves_existing_and_does_not_execute(self):
        from workflow_assistant import WorkflowHistory
        self.c.workflow.history=WorkflowHistory(self.root)
        tab=self.tab(6);tab.model_in.setText('M1')
        key=next(k for k,w in self.c.input_widgets() if w is tab.vrs_txt_in)
        history=self.c.workflow.history
        for lot in ['AA01-','AA02-','AA03-']:
            history.start('M1',lot,'test');history.record('action',{key:f'D:\\data\\{lot}.txt'});history.finish('success')
        tab.vrs_txt_in.setText('manual.txt')
        result=self.c.workflow.handle('apply',dict(model='M1',lot='AA04-',keys=[key]))
        self.assertEqual(result['applied'],[]);self.assertEqual(tab.vrs_txt_in.text(),'manual.txt')
        with patch.object(tab,'calculate') as calculate:
            result=self.c.workflow.handle('apply',dict(model='M1',lot='AA04-',keys=[key],overwrite=True))
            self.assertEqual(tab.vrs_txt_in.text(),'D:\\data\\AA04-.txt');calculate.assert_not_called()
        tab.model_in.setText('OTHER');tab.vrs_txt_in.clear()
        self.assertEqual(self.c.workflow.handle('apply',dict(model='M1',lot='AA04-',keys=[key]))['applied'],[])
    def test_workflow_records_action_and_blocks_finish_during_work(self):
        from workflow_assistant import WorkflowHistory
        self.c.workflow.history=WorkflowHistory(self.root)
        tab=self.tab(8)
        self.c.workflow.handle('start',dict(model='M1',lot='AA01-',version='test'))
        self.action(tab.btn_preview,'click')
        self.assertTrue(any(e['event']=='action' for e in self.c.workflow.history.active['events']))
        self.c.action_depth=1
        with self.assertRaises(ValueError):self.c.workflow.handle('finish',dict(outcome='success'))
        self.c.action_depth=0
        self.c.workflow.handle('finish',dict(outcome='failed',note='validation expected'))
        self.assertEqual(self.c.workflow.history.recommend('M1','AA04-'),[])
    def test_nested_dialog_protocol(self):
        # Restore the real event-loop handshake, not the fixture's canned response.
        self.c.ask=Controller.ask.__get__(self.c)
        C.QTimer.singleShot(20,lambda:self.c.handle({'id':100,'method':'dialog.reply','args':{'id':1,'value':W.QMessageBox.Yes.value}}))
        value=self.c.ask('message',{'title':'Confirm','text':'Test','buttons':[]})
        self.assertEqual(value,W.QMessageBox.Yes.value)

    def test_gather_worker_and_duplicate_selection_flow(self):
        from workers.gather_workers import GatheringWorker
        from ui.tabs.gathering_tab import GatheringModePage,GatherDuplicateDialog
        txt,_=self.fixture();second=self.root/'second.txt';second.write_text(txt.read_text().replace(',1,1,',',2,1,'))
        stamp=1700000000;os.utime(txt,(stamp,stamp));os.utime(second,(stamp+1,stamp+1))
        worker=GatheringWorker('VRS','1','unused','M','RX1234-',2,2,10,'170',set(),set())
        worker.build_paths=lambda:[self.root]
        results=[];worker.finished.connect(lambda *args:results.append(args));worker.run()
        self.assertEqual(len(results),1);self.assertTrue(results[0][2]['duplicate_conflicts'])
        tab=self.tab(3);tab.lot_in.setText('RX1234-');tab.allowance_in.setText('10');tab.x_count_in.setText('2');tab.y_count_in.setText('2')
        page=next(p for p in tab.findChildren(GatheringModePage) if p.mode=='VRS')
        def choose_merge():
            dlg=next(w for w in self.app.topLevelWidgets() if isinstance(w,GatherDuplicateDialog) and w.isVisible())
            self.action(dlg.radio_merge,'check',True)
            buttons=dlg.findChild(W.QDialogButtonBox)
            self.action(buttons.button(W.QDialogButtonBox.Ok),'click')
        C.QTimer.singleShot(20,choose_merge)
        page.on_finished(*results[0])
        self.assertEqual(page.summary['manual_duplicate_selection_count'],1)
        self.assertIn(',1,1,',page.output_text);self.assertIn(',2,1,',page.output_text)

    def test_three_process_batch_export(self):
        from workers.gather_workers import GatheringWorker
        txt,_=self.fixture();inputs=self.root/'inputs';inputs.mkdir();(inputs/'data.txt').write_bytes(txt.read_bytes())
        output=self.root/'output';output.mkdir();tab=self.tab(3)
        tab.lot_in.setText('RX1234-');tab.exclude_json_in.clear();tab.skipdata_in.clear();tab.ski_in.clear();tab.x_count_in.setText('2');tab.y_count_in.setText('2');tab.allowance_in.setText('3')
        self.answers['directory']=str(output)
        with patch.object(GatheringWorker,'build_paths',return_value=[inputs]):
            self.action(tab.btn_batch_export,'click')
            deadline=time.monotonic()+8
            while not tab.btn_batch_export.isEnabled() and time.monotonic()<deadline:self.app.processEvents();time.sleep(.01)
            self.assertTrue(tab.btn_batch_export.isEnabled())
        for mode in ('VRS','FVI','IVS'):self.assertTrue((output/f'Gather_{mode}_RX1234-.txt').is_file())

    def test_ski_diagnosis_and_validation_json_export(self):
        txt,ids=self.fixture();folder=self.root/'bbt';folder.mkdir();ski=self.root/'final.ski';ski.write_text('RX1234-01,1,1\n')
        tab=self.tab(7);tab.strip_json_in.setText(str(ids));tab.txt_in.setText(str(txt));tab.skipdata_in.setText(str(folder));tab.ski_in.setText(str(ski))
        self.action(tab.btn_ski_validate,'click');self.assertEqual(tab.ski_validation_summary['total_strips'],1)
        output=self.root/'validation.json';self.answers['save']=str(output);self.action(tab.btn_save_ski_lists,'click');self.assertIn('strip_ids',json.loads(output.read_text(encoding='utf8')))

    def test_nas_xml_scans_and_image_download_using_local_transport(self):
        from workers.ivs_workers import IvsScanThread,DownloadThread
        from workers.strip_workers import StripIdThread,FullXmlBarcodeScanThread
        from core.ivs import load_records_from_payload
        from PySide6.QtGui import QImage,QColor
        from types import SimpleNamespace
        trial='/Pixel/ResultData/Data/AFVI1/M/RX1234-/trial1'
        local=self.root/trial.lstrip('/');strip=local/'0001';strip.mkdir(parents=True)
        (local/'Bundle1.ini').write_text('Index=1\nViaMCName=AFVI1\n')
        xml='<Root barcode="RX1234-01"><Unit><P0 ivs_1="SM03" unit_idx="1" def_side="F" strip_pt_x="1" strip_pt_y="1"/></Unit></Root>'
        (strip/'fc.xml').write_text('<Root barcode="RX1234-01"/>');(strip/'data_ivs.xml').write_text(xml)
        image_dir=strip/'Unit'/'U1'/'FC';image_dir.mkdir(parents=True)
        image=QImage(64,64,QImage.Format_RGB32);image.fill(QColor('red'))
        for suffix in ('_Sub2','','_Sub1'):image.save(str(image_dir/f'p0{suffix}.bmp'))
        root=self.root
        class Sftp:
            def resolve(self,p):return root/str(p).lstrip('/')
            def open(self,p,mode='rb'):return self.resolve(p).open(mode)
            def stat(self,p):return self.resolve(p).stat()
            def listdir(self,p):return [x.name for x in self.resolve(p).iterdir()]
            def listdir_attr(self,p):return [SimpleNamespace(filename=x.name,st_mode=x.stat().st_mode) for x in self.resolve(p).iterdir()]
            def close(self):pass
        transport=lambda:(SimpleNamespace(close=lambda:None),Sftp())
        payloads=[]
        with patch('workers.ivs_workers.connect',side_effect=transport),patch('workers.strip_workers.connect',side_effect=transport):
            for cls in (IvsScanThread,StripIdThread,FullXmlBarcodeScanThread):
                results=[];errors=[];worker=cls('M','RX1234-',trial);worker.finished.connect(results.append);worker.failed.connect(errors.append);worker.run();self.assertFalse(errors,errors);self.assertTrue(results);payloads.append(results[0])
            self.assertEqual(payloads[1]['strip_ids'],['RX1234-01']);self.assertEqual(payloads[2]['duplicate_strip_count'],1)
            records=load_records_from_payload(payloads[0]);self.assertEqual(len(records),1)
            dest=self.root/'images';result=[];worker=DownloadThread(records,str(dest),'SM03');worker.finished.connect(result.append);worker.run();self.assertEqual(result,[3]);self.assertEqual(len(list(dest.rglob('*.png'))),3)

if __name__=='__main__':unittest.main()
