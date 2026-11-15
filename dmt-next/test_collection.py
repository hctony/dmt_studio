"""Real native collector + shared index integration; never contacts production PCs."""
import hashlib
import json
import os
import sys
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))


class CollectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        exe=Path(os.environ.get('DMT_COLLECTOR_BINARY',str(Path(__file__).parent/'collector-bin/dmt-collector.exe')))
        if not exe.is_file():raise unittest.SkipTest('Build collector first')
        cls.temp=tempfile.TemporaryDirectory();cls.cache=Path(cls.temp.name)/'cache';cls.token='t'*64
        cls.process=subprocess.Popen([str(exe.resolve())],env={**os.environ,'DMT_COLLECTOR_TOKEN':cls.token,'DMT_COLLECTOR_PORT':'0','DMT_COLLECTOR_STORE':str(cls.cache/'collector')},stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
        out=queue.Queue();threading.Thread(target=lambda:out.put(cls.process.stdout.readline()),daemon=True).start()
        cls.url=json.loads(out.get(timeout=15))['url']
    @classmethod
    def tearDownClass(cls):
        cls.process.terminate();cls.process.wait(timeout=10);cls.process.stdout.close();cls.process.stderr.close();cls.temp.cleanup()
    def setUp(self):
        self.tempfile=tempfile.TemporaryDirectory();self.addCleanup(self.tempfile.cleanup);self.root=Path(self.tempfile.name)
        self.pc=self.root/'pc1';self.pc.mkdir()
        self.env=patch.dict(os.environ,{'DMT_CACHE_DIR':str(self.cache),'DMT_COLLECTOR_URL':self.url,'DMT_COLLECTOR_TOKEN':self.token,'DMT_REMOTE_SESSION':'0'})
        self.env.start();self.addCleanup(self.env.stop)
    def txt(self,root=None,code='SM03',lot='RX-'):
        root=root or self.pc;p=root/'gather.txt';p.write_text(f'1,{lot},{lot}01\n{lot},{lot}01,1,1,T,{code}\n{lot},{lot}01,EOS\n',encoding='utf-8');return p
    def collect(self,paths=None,lot='RX-',force=False):
        from core.collection import collect
        return collect(paths or [self.pc],30,lot,'VRS',lambda:False,lambda message:None,force)
    def test_success_no_lot_and_missing_host_are_distinct(self):
        self.txt();other=self.root/'pc2';other.mkdir();self.txt(other,lot='OTHER-')
        parsed,report=self.collect([self.pc,other,self.root/'offline'])
        self.assertFalse(report['complete']);self.assertEqual(len(parsed[self.pc]),1)
        hosts={Path(h['path']).name:h for h in report['hosts']}
        self.assertEqual(hosts['pc2']['state'],'complete');self.assertEqual(hosts['pc2']['matched_strips'],0)
        self.assertEqual(hosts['offline']['state'],'incomplete');self.assertTrue(hosts['offline']['errors'])
        self.assertNotIn(self.token,json.dumps(report))
    def test_warm_cache_queries_other_lot_without_rereading_original(self):
        file=self.txt();file.write_text(file.read_text()+'1,OTHER-,OTHER-01\nOTHER-,OTHER-01,2,1,T,OT12\nOTHER-,OTHER-01,EOS\n')
        first,report=self.collect();second,warm=self.collect(lot='OTHER-')
        self.assertEqual(first[self.pc][0][1].strip_id,'RX-01');self.assertEqual(second[self.pc][0][1].strip_id,'OTHER-01')
        self.assertEqual(warm['hosts'][0]['reused'],1);self.assertTrue(warm['complete'])
    def test_snapshots_and_code_source_survive_original_change(self):
        file=self.txt();first,_=self.collect();old_snapshot=Path(first[self.pc][0][1].sources[1])
        self.txt(code='OT12');second,_=self.collect(force=True)
        self.assertIn('SM03',old_snapshot.read_text());self.assertIn('OT12',second[self.pc][0][1].defect_lines[0])
        self.assertEqual(first[self.pc][0][1].sources[0],str(file))
    def test_truncated_or_malformed_file_is_not_successful_zero_defects(self):
        file=self.txt();file.write_text('1,RX-,RX-01\nRX-,RX-01,1,1,T,SM03\n')
        _,report=self.collect();self.assertFalse(report['complete']);self.assertEqual(len(report['hosts'][0]['parse_errors']),1)
        self.assertIn('TXT_WRITING_OR_TRUNCATED',report['hosts'][0]['parse_errors'][0]['error'])
    def test_export_receipt_blocks_partial_changed_or_wrong_stage(self):
        from core.collection import write_export,reports_for_inputs
        self.txt();_,report=self.collect([self.pc,self.root/'offline'])
        export=self.root/'result.txt';write_export(export,'1,RX-,RX-01\nRX-,RX-01,EOS\n',{},report['receipt'])
        guard=reports_for_inputs([export]);self.assertTrue(guard['enabled']);self.assertFalse(guard['complete'])
        self.assertEqual(len(guard['incomplete_hosts']),1)
        wrong=reports_for_inputs([export],{'IVS':[export]});self.assertIn('COLLECTION_STAGE_MISMATCH',wrong['errors'])
        export.write_text('changed');changed=reports_for_inputs([export]);self.assertIn('COLLECTION_INPUT_CHANGED',changed['errors'])
    def test_source_completion_alone_does_not_mark_unknown_as_resolved(self):
        from core.collection import write_export
        from core.yield_calc import calculate_yield_summary_by_process
        self.txt();parsed,report=self.collect();source=parsed[self.pc][0][1]
        target=self.root/'gather.txt';write_export(target,'1,RX-,RX-01\nRX-,RX-01,1,1,T,SM03\nRX-,RX-01,EOS\n',{'RX-01':{'source_mtime':source.source_mtime,'sources':source.sources}},report['receipt'])
        ski=self.root/'final.ski';ski.write_text('RX-01,1,1\nRX-01,2,1\n')
        result=calculate_yield_summary_by_process([target],[],[],[],None,4,3,None,ski)
        self.assertTrue(result['collection_validation']['complete']);self.assertEqual(result['report_code_distribution']['UNKNOWN'],1)
        self.assertEqual(result['report_trace']['collection_validation']['reports'][0]['mode'],'VRS')
    def test_remote_archive_requires_receipt_and_approved_original_root(self):
        from core.collection import write_export,reports_for_inputs
        from core.report_evidence import live_text,local_allowed
        self.txt();parsed,report=self.collect();snapshot=Path(parsed[self.pc][0][1].sources[1])
        export=self.root/'output.txt';write_export(export,'1,RX-,RX-01\nRX-,RX-01,EOS\n',{},report['receipt'])
        with patch.dict(os.environ,{'DMT_REMOTE_SESSION':'1','DMT_DATA_DIR':str(self.root),'DMT_ALLOWED_ROOTS':json.dumps([str(self.pc)])}):
            guard=reports_for_inputs([export]);self.assertTrue(guard['complete'])
            with self.assertRaises(PermissionError):local_allowed(snapshot)
            text=live_text({'collection_validation':guard},snapshot,'RX-01',(1,1),'txt')
            self.assertIn('SM03',text)
        with patch.dict(os.environ,{'DMT_REMOTE_SESSION':'1','DMT_DATA_DIR':str(self.root),'DMT_ALLOWED_ROOTS':'[]'}):
            # self.pc is still inside this test's data dir; use a narrower session dir.
            with patch.dict(os.environ,{'DMT_DATA_DIR':str(self.root/'session')}):
                self.assertFalse(reports_for_inputs([export])['complete'])
    def test_collect_rejects_unapproved_remote_root(self):
        self.txt()
        with patch.dict(os.environ,{'DMT_REMOTE_SESSION':'1','DMT_DATA_DIR':str(self.root/'session'),'DMT_ALLOWED_ROOTS':'[]'}):
            parsed,report=self.collect();self.assertFalse(report['complete']);self.assertIn('A_PC_ROOT_NOT_ALLOWED',report['hosts'][0]['errors'][0])
    def test_cancelled_request_never_publishes_a_complete_receipt(self):
        from core.collection import collect
        from core.cancellation import OperationCancelled
        self.txt()
        with self.assertRaises(OperationCancelled):collect([self.pc],30,'RX-','VRS',lambda:True,lambda message:None)
        calls=0
        def stop_after_start():
            nonlocal calls
            calls+=1
            return calls>1
        with self.assertRaises(OperationCancelled):collect([self.pc],30,'RX-','VRS',stop_after_start,lambda message:None)
    def test_index_keeps_explicit_empty_latest_blocks(self):
        file=self.txt();file.write_text(file.read_text()+'1,RX-,RX-01\nRX-,RX-01,EOS\n')
        parsed,report=self.collect();self.assertTrue(report['complete']);self.assertEqual(len(parsed[self.pc]),2)
        self.assertEqual(parsed[self.pc][-1][1].defect_lines,[])

if __name__=='__main__':unittest.main()
