from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from core.models import DefectRecord,TrialContext
from core.ivs_latest import select_latest
from core.ivs import load_records_from_payload,load_ivs_dummy_scrap_strips,load_ivs_records_by_strip
from core.its_validation import load_history
from workers.ivs_workers import IvsScanThread

class LatestTests(unittest.TestCase):
    def record(self,machine='A',trial='99',folder='1',code='SM03',xml='old.xml'):
        return DefectRecord('RX-01',machine,'M','RX-',trial,folder,'Unit','T','1','0',code,code,'','F',1,1,0,0,xml,[])
    def event(self,r,stamp):
        return {'strip_id':r.strip_id,'machine':r.machine,'trial':r.trial,'strip_folder':r.strip_folder,'source_xml':r.source_xml,'source_mtime':stamp}
    def test_latest_clock_wins_across_machines_not_trial_number_or_iteration(self):
        old=self.record();new=self.record('B','1',code='OT12',xml='new.xml')
        for rows,events in (([old,new],[self.event(old,100),self.event(new,200)]),([new,old],[self.event(new,200),self.event(old,100)])):
            result,audit=select_latest(rows,events);self.assertEqual(result,[new]);self.assertTrue(audit['complete']);self.assertEqual(audit['replaced_occurrences'],1)
    def test_newest_zero_defect_history_clears_old_ng(self):
        old=self.record();new=self.record('C','1',xml='zero.xml')
        result,audit=select_latest([old],[self.event(old,100),self.event(new,300)])
        self.assertEqual(result,[]);self.assertEqual(audit['selected']['RX-01']['source_xml'],'zero.xml')
    def test_keep_split_front_back_xml_in_one_selected_occurrence(self):
        first=self.record('B','2',xml='front.xml');second=replace(first,face='B',source_xml='back.xml',ivs_1='OT12')
        result,audit=select_latest([first,second,first],[self.event(first,200),self.event(second,210)])
        self.assertEqual(result,[first,second]);self.assertTrue(audit['complete'])
    def test_same_trial_repeated_barcode_different_folder_uses_latest_folder(self):
        old=self.record(folder='1');new=self.record(folder='2',xml='new.xml')
        result,audit=select_latest([old,new],[self.event(old,100),self.event(new,200)])
        self.assertEqual(result,[new]);self.assertEqual(audit['selected']['RX-01']['epoch_start'],200)
    def test_other_barcode_keeps_its_own_latest_occurrence(self):
        old=self.record();other=replace(old,strip_id='RX-02');new=self.record('B','1',xml='new.xml')
        result,_=select_latest([old,other,new],[self.event(old,100),self.event(other,110),self.event(new,200)])
        self.assertEqual(result,[other,new])
    def test_missing_or_equal_clocks_are_explicitly_unverified(self):
        old=self.record();new=self.record('B','1',xml='new.xml')
        for clock in (100,None):
            result,audit=select_latest([old,new],[self.event(old,100),self.event(new,clock)])
            self.assertFalse(audit['complete']);self.assertEqual(len(result),1);self.assertTrue(audit['issues'])
    def test_failed_latest_xml_does_not_resurrect_old_result(self):
        old=self.record();new=self.record('C','1',xml='failed.xml');event={**self.event(new,300),'parse_failed':True}
        result,audit=select_latest([old],[self.event(old,100),event])
        self.assertEqual(result,[]);self.assertFalse(audit['complete']);self.assertEqual(audit['issues'][0]['reason'],'LATEST_XML_UNREADABLE')
    def test_real_scan_and_json_keep_archive_but_remove_old_dummy_and_ng(self):
        from PySide6.QtWidgets import QApplication
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);trials=[]
            for machine,trial,stamp,xml in (
                ('A','99',100,'<Root barcode="RX-01"><Dummy><P0 ivs_1="9999" unit_idx="-1"/></Dummy></Root>'),
                ('B','2',200,'<Root barcode="RX-01"><Unit><P0 ivs_1="SM03" unit_idx="1"/></Unit></Root>'),
                ('C','1',300,'<Root barcode="RX-01"><Unit><P0 ivs_1="0" unit_idx="1"/></Unit></Root>'),
            ):
                remote=f'/Data/{machine}/M/RX-/{trial}';folder=root/remote.lstrip('/')/'1';folder.mkdir(parents=True)
                source=folder/'result_ivs.xml';source.write_text(xml);os.utime(source,(stamp,stamp))
                trials.insert(0,TrialContext(machine,'M','RX-',trial,remote))
            class Sftp:
                def open(self,path,mode='rb'):return (root/str(path).lstrip('/')).open(mode)
                def stat(self,path):return (root/str(path).lstrip('/')).stat()
                def listdir_attr(self,path):return [SimpleNamespace(filename=f.name,st_mode=f.stat().st_mode) for f in (root/str(path).lstrip('/')).iterdir()]
                def close(self):pass
            worker=IvsScanThread('M','RX-',units_per_strip=8);results=[];errors=[]
            worker.finished.connect(results.append);worker.failed.connect(errors.append)
            with patch('workers.ivs_workers.connect',return_value=(SimpleNamespace(close=lambda:None),Sftp())),patch('workers.ivs_workers.list_lot_trials',return_value=trials),patch('workers.ivs_workers.load_valid_bundle_indexes',return_value={1}):worker.run()
            self.assertFalse(errors,errors);payload=results[0]
            self.assertEqual(payload['records'],[]);self.assertEqual(len(payload['history_records']),2)
            self.assertEqual(payload['population']['strip_count'],1);self.assertEqual(payload['population']['total_units'],8)
            self.assertTrue(payload['strip_history']['complete']);self.assertEqual(len(payload['strip_history']['events']),3)
            path=root/'result.json';path.write_text(json.dumps(payload))
            self.assertEqual(load_records_from_payload(payload),[]);self.assertEqual(len(load_records_from_payload(payload,True)),2)
            self.assertEqual(load_ivs_dummy_scrap_strips([path]),set());self.assertEqual(load_ivs_records_by_strip([path]),{})
            self.assertEqual(len(load_ivs_records_by_strip([path],include_history=True)['RX-01']),2)
            self.assertEqual(load_history([path])['RX-01']['machine'],'C')
            from core.yield_calc import calculate_yield_summary_by_process
            txt=root/'old-ivs.txt';txt.write_text('1,RX-,RX-01\nRX-,RX-01,1,1,T,SM03\nRX-,RX-01,EOS\n');os.utime(txt,(150,150))
            ski=root/'final.ski';ski.write_text('');os.utime(ski,(400,400))
            ids=root/'strips.json';ids.write_text(json.dumps({'schema':'dmt-studio-strip-population-v1','scope':'lot','complete':True,'lot_id':'RX-','strip_ids':['RX-01'],'units_per_strip':8}))
            summary=calculate_yield_summary_by_process([],[],[txt],[path],ids,8,7,None,ski)
            self.assertEqual(summary['defective_units'],0);self.assertEqual(summary['report_code_distribution'],{})
            self.assertTrue(summary['its_validation']['auto_pass']);self.assertEqual(summary['ivs_dummy_9999_scrap_strip_count'],0)
