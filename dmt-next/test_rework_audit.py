import csv
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from core import rework_scope
from core.ot14_audit import rows, export_csv
from core.report_trace import ReportTrace
from core.dummy_policy import evaluate, WHOLE_STRIP_CODES
from core.models import DefectRecord, GatherStrip
from core.ivs import records_to_json
from core.yield_calc import calculate_yield_summary_by_process


class ReworkAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.env=patch.dict(os.environ,{'DMT_CACHE_DIR':str(self.root/'cache'),'DMT_REMOTE_SESSION':'0'})
        self.env.start();self.addCleanup(self.env.stop)

    def record(self, code):
        return DefectRecord('RX-01','A','MODEL','RX-','TRIAL','1','Dummy','T','-1','0',code,'','','F',0,0,0,0,'',[])

    def test_dummy_codes_and_9999_alarm(self):
        for code in WHOLE_STRIP_CODES:
            codes,alarms=evaluate({'RX-01':[self.record(code),self.record(code)]})
            self.assertEqual(codes,{'RX-01':code});self.assertEqual(alarms,[])
        codes,alarms=evaluate({'RX-01':[self.record('9999')]})
        self.assertFalse(codes);self.assertEqual(alarms[0]['reason'],'DUMMY_9999_ALARM')
        codes,alarms=evaluate({'RX-01':[self.record('DR01'),self.record('SM03')]})
        self.assertFalse(codes);self.assertEqual(alarms[0]['reason'],'DUMMY_CODE_CONFLICT')

    def test_88_units_minus_four_bbt_are_84_dr01(self):
        txt=self.root/'vrs.txt';txt.write_text('88,RX-,RX-01\nRX-,RX-01,EOS\n')
        xml=self.root/'ivs.json';xml.write_text(json.dumps(records_to_json('MODEL','RX-',[self.record('DR01')])))
        ski=self.root/'final.ski';ski.write_text(''.join(f'RX-01,{x},{y}\n' for y in range(1,5) for x in range(1,23)))
        rpt=self.root/'bbt';rpt.mkdir();(rpt/'RX-01.rpt').write_text(''.join(f'X:{x:03d} Y:001 = 1 - OLD\n' for x in range(1,5)))
        summary=calculate_yield_summary_by_process([txt],[],[],[xml],None,88,13,rpt,ski)
        self.assertEqual(summary['report_code_distribution'],{'DR01':84,'OT14':4})
        self.assertEqual(summary['total_units'],84);self.assertEqual(summary['defective_units'],84)
        self.assertEqual(summary['good_units'],0);self.assertEqual(summary['ot14_excluded_units'],4)

    def test_scope_shared_and_filename_timestamp_priority(self):
        scope=rework_scope.save('model','rx-',True,'2026-11-18',True)
        self.assertEqual(rework_scope.load('MODEL','RX-'),scope)
        stamp,basis=rework_scope.txt_timestamp('MODEL_RX-_192_2026-11-18 132845.txt','RX-',1)
        self.assertGreater(stamp,rework_scope.cutoff(scope));self.assertEqual(basis,'filename_local_time')
        self.assertEqual(rework_scope.txt_timestamp('MODEL_RX-_192_010345.txt','RX-',123),(123,'source_file_mtime'))

    def test_ot14_csv_keeps_duplicate_rows_and_excluded_strip_evidence(self):
        rpt=self.root/'rpt';rpt.mkdir();source=rpt/'lot.rpt'
        source.write_text('[RX-01]\nX:001 Y:001 = 1 - SM03\nX:001 Y:001 = 1 - DR01\n[RX-02]\nX:002 Y:001 = 1 - OT14\nX:broken\n',encoding='utf-8')
        trace=ReportTrace({},[],rpt,None);trace.selected_strip_ids={'RX-01'}
        trace.strips['RX-01']={'ski_coords':[[1,1]]};trace.add('RX-01','OT14','BBT',(1,1),'bbt')
        snapshot=trace.snapshot();data=rows(snapshot)
        unique=[r for r in data if r['row_type']=='OT14_COORDINATE']
        self.assertEqual(len(unique),2);self.assertEqual(unique[0]['rpt_occurrences'],2)
        self.assertEqual(unique[0]['duplicate_rows'],1);self.assertEqual(unique[1]['final_ot14'],0)
        self.assertEqual(unique[1]['parse_status'],'OUTSIDE_SELECTED_STRIPS')
        self.assertEqual(len([r for r in data if r['row_type']=='RPT_RAW']),6)
        output=self.root/'audit.csv';export_csv(snapshot,output)
        with output.open(encoding='utf-8-sig') as stream:self.assertEqual(len(list(csv.DictReader(stream))),8)
        (rpt/'new.rpt').write_text('X:003 Y:001 = 1 - OT14')
        with self.assertRaises(ValueError):rows(snapshot)

    def test_missing_and_explicit_zero_are_distinct(self):
        from core.inspection_coverage import build
        block=GatherStrip('RX-','RX-01',[],[])
        coverage=build({'RX-01','RX-02'},{'IVS':{'RX-01':block},'VRS':{},'FVI':{}})
        self.assertEqual(coverage[0]['processes']['IVS'],'ZERO_DEFECT_BLOCK')
        self.assertFalse(coverage[0]['no_result_block']);self.assertTrue(coverage[1]['no_result_block'])

    def test_xml_cutoff_filters_before_parsing_and_reuse_skips_nas(self):
        from types import SimpleNamespace
        from PySide6.QtWidgets import QApplication
        from workers.ivs_workers import IvsScanThread
        from core.models import TrialContext
        app=QApplication.instance() or QApplication([])
        scope=rework_scope.save('MODEL','RX-',True,'2026-11-18',False)
        boundary=rework_scope.cutoff(scope)
        sftp=SimpleNamespace(stat=lambda name:SimpleNamespace(st_mtime=boundary+1 if name=='new.xml' else boundary-1),close=lambda:None)
        ctx=TrialContext('A','MODEL','RX-','TRIAL','/trial')
        calls=[]
        def parse(name, connection, context, folder, progress, stop, on_strip):
            calls.append(name);on_strip('RX-01',88)
            return [self.record('DR01')]
        results=[];errors=[];worker=IvsScanThread('MODEL','RX-',units_per_strip=88)
        worker.finished.connect(results.append);worker.failed.connect(errors.append)
        with patch('workers.ivs_workers.connect',return_value=(SimpleNamespace(close=lambda:None),sftp)), \
             patch('workers.ivs_workers.list_lot_trials',return_value=[ctx]), \
             patch('workers.ivs_workers.iter_dirs',return_value=['1']), \
             patch('workers.ivs_workers.load_valid_bundle_indexes',return_value={1}), \
             patch('workers.ivs_workers.find_ivs_files',return_value=['old.xml','new.xml']), \
             patch('workers.ivs_workers.parse_ivs_xml',side_effect=parse):worker.run()
        self.assertFalse(errors,errors);self.assertEqual(calls,['new.xml'])
        self.assertEqual(results[0]['rework_scope'],scope)
        baseline=self.root/'baseline.json';baseline.write_text(json.dumps(results[0]))
        scope=rework_scope.save('MODEL','RX-',True,'2026-11-18',True)
        reuse=IvsScanThread('MODEL','RX-',units_per_strip=88);saved=[]
        reuse.finished.connect(saved.append);reuse.failed.connect(errors.append)
        with patch('core.yield_handoff.context',return_value={'fields':{'ivs_json_in':{'value':str(baseline)}}}), \
             patch('workers.ivs_workers.connect',side_effect=AssertionError('NAS must not be contacted')):reuse.run()
        self.assertFalse(errors,errors);self.assertEqual(saved[0]['records'],results[0]['records'])
        self.assertEqual(saved[0]['rework_scope'],scope)

    def test_reentry_latest_zero_block_uses_bound_source_time_not_export_time(self):
        from core.collection import write_export
        from core.gather import load_latest_process_blocks
        scope={'enabled':True,'date':'2026-11-18','reuse_xml':True}
        boundary=rework_scope.cutoff(scope)
        old=self.root/'old-export.txt';new=self.root/'new-export.txt'
        write_export(old,'88,RX-,RX-01\nRX-,RX-01,1,1,T,SM03\nRX-,RX-01,EOS\n',
                     {'RX-01':{'source_mtime':boundary-1,'sources':['old']}})
        write_export(new,'88,RX-,RX-01\nRX-,RX-01,EOS\n',
                     {'RX-01':{'source_mtime':boundary+1,'sources':['new']}})
        blocks,_=load_latest_process_blocks([new,old],'RX-',ski_map={'RX-01':{(1,1)}},rework_scope=scope)
        self.assertEqual(blocks['RX-01'].defect_lines,[])
        self.assertEqual(blocks['RX-01'].source_mtime,boundary+1)


if __name__=='__main__':unittest.main()
