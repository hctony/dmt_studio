import unittest
from types import SimpleNamespace
from core.its_validation import validate_strip,overlapping_history_dates


class ItsValidationTests(unittest.TestCase):
    def check(self,processes,ski,**kwargs):
        return validate_strip(processes,{(19,2)},ski,history_verified=True,ski_scope_verified=True,**kwargs)

    def test_supplied_strip_matches_six_confirmed_defects_plus_bbt(self):
        coords={(6,1),(13,1),(14,3),(17,3),(22,3),(1,4)}
        result=self.check({'VRS':{'SM04':coords}},coords|{(19,2)})
        self.assertEqual(result['status'],'PASS');self.assertEqual(result['expected_count'],7)

    def test_same_count_different_coordinates_is_not_pass(self):
        result=self.check({'IVS':{'SM03':{(1,1)}}},{(2,1),(19,2)})
        self.assertEqual(result['status'],'COORDINATE_MISMATCH');self.assertFalse(result['auto_pass'])

    def test_ski_has_more_coordinates_means_source_missing(self):
        result=self.check({'IVS':{'SM03':{(1,1)}}},{(1,1),(2,1),(19,2)})
        self.assertEqual(result['status'],'SOURCE_MISSING');self.assertEqual(result['missing_source_coords'],[(2,1)])

    def test_fvi_new_defect_requires_ski_refresh_not_silent_drop(self):
        result=self.check({'IVS':{'SM03':{(1,1)}},'FVI':{'OT08':{(2,1)}}},{(1,1),(19,2)},inspection_times={'FVI':200},ski_time=100)
        self.assertEqual(result['status'],'SKI_REFRESH_REQUIRED');self.assertEqual(result['newer_than_ski_stages'],['FVI'])

    def test_9998_is_not_a_confirmed_defect_and_all_stages_deduplicate(self):
        result=self.check({'IVS':{'9998':{(2,1)},'SM03':{(1,1)}},'VRS':{'OT08':{(2,1),(1,1)}},'FVI':{'OT12':{(2,1)}}},{(1,1),(2,1),(19,2)})
        self.assertEqual(result['status'],'PASS');self.assertEqual(result['expected_count'],3)

    def test_exact_coordinates_without_verified_history_cannot_auto_pass(self):
        result=validate_strip({}, {(19,2)}, {(19,2)},ski_scope_verified=True)
        self.assertEqual(result['status'],'HISTORY_UNVERIFIED');self.assertFalse(result['auto_pass'])

    def test_zero_ski_without_verified_scope_is_not_a_false_positive_proof(self):
        self.assertFalse(validate_strip({},set(),set(),history_verified=True)['auto_pass'])

    def test_different_dates_without_repeated_strip_need_no_date_review(self):
        items=[SimpleNamespace(strip_id='A',source_mtime=1000000000),SimpleNamespace(strip_id='B',source_mtime=1000086400)]
        self.assertEqual(overlapping_history_dates(items),{})
        items.append(SimpleNamespace(strip_id='A',source_mtime=1000086400))
        self.assertEqual(set(overlapping_history_dates(items)),{'A'})

    def test_new_trial_empty_txt_replaces_old_ski_matching_defects(self):
        import tempfile,json,os
        from pathlib import Path
        from core.gather import load_latest_process_blocks
        from core.its_validation import load_history
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);old=root/'old.txt';new=root/'new.txt';history=root/'ivs.json'
            old.write_text('1,RX-,RX-01\nRX-,RX-01,1,1,T,SM03\nRX-,RX-01,EOS\n')
            new.write_text('1,RX-,RX-01\nRX-,RX-01,EOS\n')
            os.utime(old,(100,100));os.utime(new,(300,300))
            history.write_text(json.dumps({'strip_history':{'schema':'dmt-its-history-v1','events':[
                {'strip_id':'RX-01','machine':'A','trial':'3','source_mtime':90},
                {'strip_id':'RX-01','machine':'A','trial':'6','source_mtime':200}]}}))
            epochs=load_history([history]);blocks,_=load_latest_process_blocks([old,new],'RX-',{}, {'RX-01':{(1,1)}},its_history=epochs)
            self.assertEqual(blocks['RX-01'].defect_lines,[])
            self.assertEqual(epochs['RX-01']['trial'],'6')

    def test_export_clock_uses_original_source_not_new_export_time(self):
        import tempfile,hashlib,json
        from pathlib import Path
        from core.its_validation import source_metadata
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'gather.txt';path.write_text('output')
            path.with_suffix('.txt.its.json').write_text(json.dumps({'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                'strips':{'RX-01':{'source_mtime':100,'sources':['original.txt']}}}))
            self.assertEqual(source_metadata(path,'RX-01')['source_mtime'],100)
            path.write_text('changed');self.assertIsNone(source_metadata(path,'RX-01'))

    def test_end_to_end_9998_false_positive_and_later_fvi_ski_refresh(self):
        import tempfile,json,os
        from pathlib import Path
        from core.models import DefectRecord
        from core.ivs import records_to_json
        from core.yield_calc import calculate_yield_summary_by_process
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            def txt(name,rows,stamp):
                p=root/name;p.write_text('1,RX-,RX-01\n'+''.join(f'RX-,RX-01,{x},{y},T,{c}\n' for x,y,c in rows)+'RX-,RX-01,EOS\n');os.utime(p,(stamp,stamp));return p
            ivs=txt('ivs.txt',[(1,1,'9998')],210);vrs=txt('vrs.txt',[],300);fvi=txt('fvi.txt',[(2,1,'OT08')],350)
            record=DefectRecord('RX-01','A','M','RX-','6','1','Unit','FC','0','0','9998','9998','','F',1,1,0,0,'/ivs.xml',[])
            payload=records_to_json('M','RX-',[record]);payload['strip_history']={'schema':'dmt-its-history-v1','events':[{'strip_id':'RX-01','machine':'A','trial':'6','source_mtime':200}]}
            source=root/'ivs.json';source.write_text(json.dumps(payload));ski=root/'final.ski';ski.write_text('');os.utime(ski,(320,320))
            def calculate(vrs_files,fvi_files):return calculate_yield_summary_by_process(vrs_files,fvi_files,[ivs],[source],None,4,3,None,ski)
            self.assertEqual(calculate([vrs],[])['its_validation']['strips'][0]['status'],'PASS')
            vrs.write_text('1,RX-,RX-01\nRX-,RX-01,1,1,T,9998\nRX-,RX-01,EOS\n');os.utime(vrs,(300,300))
            self.assertFalse(calculate([vrs],[])['its_validation']['auto_pass'])
            vrs.write_text('1,RX-,RX-01\nRX-,RX-01,EOS\n');os.utime(vrs,(300,300))
            self.assertFalse(calculate([],[])['its_validation']['auto_pass'])
            pending=calculate([vrs],[fvi]);self.assertEqual(pending['its_validation']['strips'][0]['status'],'SKI_REFRESH_REQUIRED')
            self.assertEqual(pending['its_validation']['strips'][0]['newer_than_ski_stages'],['FVI'])
            ski.write_text('RX-01,2,1\n');os.utime(ski,(400,400))
            final=calculate([vrs],[fvi]);self.assertTrue(final['its_validation']['auto_pass']);self.assertEqual(final['report_code_distribution'],{'OT08':1})

    def test_same_epoch_partial_merge_requires_fresh_exact_ski_and_never_revives_empty_latest(self):
        from core.its_validation import epoch_candidate
        from core.models import GatherStrip
        old=GatherStrip('RX-','RX-01',['RX-,RX-01,1,1,T,SM03'],source_mtime=200)
        new=GatherStrip('RX-','RX-01',['RX-,RX-01,2,1,T,OT08'],source_mtime=300)
        self.assertEqual(len(epoch_candidate(old,new,set(),{(1,1),(2,1)},400).defect_lines),2)
        self.assertEqual(len(epoch_candidate(old,new,set(),{(1,1),(2,1)},250).defect_lines),1)
        new.defect_lines=[];self.assertEqual(epoch_candidate(old,new,set(),{(1,1)},400).defect_lines,[])

    def test_stale_ski_with_proven_retired_defects_is_refresh_not_source_missing(self):
        old=validate_strip({},set(),{(1,1)},history_verified=True,ski_scope_verified=True,
            ski_time=100,epoch_start=200,retired_coords={(1,1)})
        self.assertEqual(old['status'],'SKI_REFRESH_REQUIRED')
        fresh=validate_strip({},set(),{(1,1)},history_verified=True,ski_scope_verified=True,
            ski_time=300,epoch_start=200,retired_coords={(1,1)})
        self.assertEqual(fresh['status'],'SOURCE_MISSING')
