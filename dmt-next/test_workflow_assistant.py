import json
import tempfile
import unittest
from pathlib import Path
from workflow_assistant import WorkflowHistory

PATH='6/YieldTab:/vrs_txt_in'
VALUE='6/YieldTab:/units_per_strip_in'

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.h=WorkflowHistory(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def run_lot(self,lot,fields=None,model='M1',outcome='success'):
        self.h.start(model,lot,'test')
        self.h.record('action',fields or {PATH:f'D:\\data\\{lot}\\VRS_{lot}.txt',VALUE:'64'})
        self.h.finish(outcome,'checked')
    def test_three_distinct_successes_learn_template(self):
        for lot in ['AA01-','AA02-','AA03-']:self.run_lot(lot)
        items={r['key']:r for r in self.h.recommend('M1','AA04-')}
        # LOT tokens preceded by '_' are separate tokens and are safely replaced.
        self.assertEqual(items[PATH]['value'],'D:\\data\\AA04-\\VRS_AA04-.txt')
        self.assertEqual(items[PATH]['support'],3);self.assertEqual(items[VALUE]['confidence'],'high')
    def test_repeated_same_lot_never_counts_as_three(self):
        for _ in range(3):self.run_lot('AA01-')
        self.assertTrue(all(r['confidence']=='review' and r['support']==1 for r in self.h.recommend('M1','AA04-')))
    def test_failed_and_other_models_do_not_train(self):
        self.run_lot('AA01-',outcome='failed');self.run_lot('AA02-',model='M2')
        self.assertEqual(self.h.recommend('M1','AA04-'),[])
    def test_conflicts_require_review_and_ties_are_not_guessed(self):
        self.run_lot('AA01-',{VALUE:'64'});self.run_lot('AA02-',{VALUE:'88'})
        self.assertEqual(self.h.recommend('M1','AA04-'),[])
        self.run_lot('AA03-',{VALUE:'64'})
        self.assertEqual(self.h.recommend('M1','AA04-')[0]['confidence'],'review')
    def test_interrupted_runs_are_preserved_but_excluded(self):
        self.h.start('M1','AA01-','test');self.h.record('action',{VALUE:'64'})
        reloaded=WorkflowHistory(self.temp.name)
        self.assertEqual(reloaded.records()[0]['outcome'],'interrupted');self.assertEqual(reloaded.recommend('M1','AA04-'),[])
    def test_secrets_not_exported_and_unknown_fields_not_learned(self):
        self.h.secrets=['private-pass']
        self.h.start('M1','AA01-','test');self.h.record('action',{VALUE:'64','password':'private-pass'},message='password=private-pass')
        self.h.finish('success','private-pass')
        serialized=json.dumps(self.h.export());self.assertNotIn('private-pass',serialized)
        self.assertNotIn('password',self.h.records()[0]['fields'])
        json.loads(next(self.h.root.glob('run-*.json')).read_text(encoding='utf8'))
    def test_latest_success_per_lot_and_model_isolated(self):
        self.run_lot('AA01-',{VALUE:'64'});self.run_lot('AA01-',{VALUE:'88'})
        self.assertEqual(self.h.recommend('M1','AA04-')[0]['value'],'88')
        self.assertEqual(self.h.recommend('M2','AA04-'),[])
    def test_prefix_collision_does_not_rewrite_other_lot(self):
        self.run_lot('AA1',{PATH:'D:\\AA10\\file.txt'})
        self.assertEqual(self.h.recommend('M1','AA2')[0]['value'],'D:\\AA10\\file.txt')
    def test_empty_run_cannot_be_confirmed_successful(self):
        self.h.start('M1','AA01-','test')
        with self.assertRaises(ValueError):self.h.finish('success')
    def test_preferences_survive_restart(self):
        self.h.set_preferences(True);self.assertTrue(WorkflowHistory(self.temp.name).preferences['autofill'])

if __name__=='__main__':unittest.main()
