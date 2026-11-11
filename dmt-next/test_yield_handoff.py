import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from core import yield_handoff as handoff
from ui.tabs import yield_tab


class YieldHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = {}
        for target, name, value in [
            (handoff, 'DATA_DIR', self.root),
            (yield_tab, 'setting_text', lambda k, default='': self.settings.get(k, default)),
            (yield_tab, 'save_setting_text', lambda k, v: self.settings.update({k: v})),
            (yield_tab, 'restore_line_edit', lambda *a: None),
            (yield_tab, 'save_line_edit', lambda *a: None),
            (yield_tab, 'load_yield_model_settings', lambda: {})]:
            p = patch.object(target, name, value); p.start(); self.addCleanup(p.stop)
        self.tab = yield_tab.YieldTab()
        self.addCleanup(self.tab.deleteLater)
        self.config = dict(x_count=22, y_count=4, allowance=13, skipdata_path=r'\\nas\BBT\LOT-', ski_path=r'\\nas\LOT-.ski')

    def gather(self, mode='VRS', lot='LOT-', output='completed'):
        self.assertTrue(handoff.remember('MODEL', lot, kind=mode, output=output, config=self.config))

    def test_all_completed_stages_fill_paths_quantities_and_keep_strip_population(self):
        ivs = {'model': 'MODEL', 'lot_id': 'LOT-', 'records': [], 'strips': {}}
        ids = {'model': 'MODEL', 'lot_id': 'LOT-', 'strip_ids': ['LOT-01', 'LOT-02']}
        handoff.remember_payload(ivs, 'ivs_json')
        handoff.remember_payload(ids, 'strip_json')
        for mode in ('VRS', 'FVI', 'IVS'):
            self.gather(mode)
        self.tab.apply_workflow_inputs()
        for field in ('vrs_txt_in', 'fvi_txt_in', 'ivs_txt_in'):
            self.assertEqual(Path(getattr(self.tab, field).text()).read_text(), 'completed')
        self.assertEqual(json.loads(Path(self.tab.strip_json_in.text()).read_text())['strip_ids'], ids['strip_ids'])
        self.assertEqual(json.loads(Path(self.tab.ivs_json_in.text()).read_text())['records'], [])
        self.assertEqual(self.tab.units_per_strip_in.text(), '88')
        self.assertEqual(self.tab.xout_limit_in.text(), '13')
        self.assertEqual(self.tab.skipdata_in.text(), self.config['skipdata_path'])
        self.assertEqual(self.tab.ski_in.text(), self.config['ski_path'])

    def test_new_lot_clears_old_process_files_and_old_strip_json(self):
        self.gather('VRS'); self.gather('IVS')
        handoff.remember_payload({'model': 'MODEL', 'lot_id': 'LOT-', 'strip_ids': []}, 'strip_json')
        self.tab.apply_workflow_inputs()
        self.tab.txt_in.setText('old generic.txt')
        self.gather('FVI', 'NEW-')
        self.tab.apply_workflow_inputs()
        for field in ('vrs_txt_in', 'ivs_txt_in', 'strip_json_in', 'txt_in'):
            self.assertEqual(getattr(self.tab, field).text(), '')
        self.assertTrue(self.tab.fvi_txt_in.text())

    def test_manual_edits_survive_navigation_but_new_stage_updates_only_its_fields(self):
        self.gather(); self.tab.apply_workflow_inputs()
        self.tab.vrs_txt_in.setText('manual.txt')
        self.tab.apply_workflow_inputs()
        self.assertEqual(self.tab.vrs_txt_in.text(), 'manual.txt')
        self.gather('IVS'); self.tab.apply_workflow_inputs()
        self.assertEqual(self.tab.vrs_txt_in.text(), 'manual.txt')
        self.assertTrue(self.tab.ivs_txt_in.text())
        self.tab.apply_workflow_inputs(force=True)
        self.assertNotEqual(self.tab.vrs_txt_in.text(), 'manual.txt')

    def test_repeated_completion_creates_immutable_snapshot_and_new_revision(self):
        self.gather(output='old'); self.tab.apply_workflow_inputs()
        previous = Path(self.tab.vrs_txt_in.text())
        self.gather(output='new'); self.tab.apply_workflow_inputs()
        self.assertNotEqual(previous, Path(self.tab.vrs_txt_in.text()))
        self.assertEqual(previous.read_text(), 'old')
        self.assertEqual(Path(self.tab.vrs_txt_in.text()).read_text(), 'new')

    def test_manifest_survives_restart_and_invalid_manifest_is_ignored(self):
        self.gather(); self.tab.apply_workflow_inputs()
        tab = yield_tab.YieldTab(); self.addCleanup(tab.deleteLater)
        tab.vrs_txt_in.setText('persisted manual.txt')
        tab.apply_workflow_inputs()
        self.assertEqual(tab.vrs_txt_in.text(), 'persisted manual.txt')
        (self.root/'yield-handoff/latest.json').write_text('{broken')
        self.assertEqual(handoff.latest(), {})

    def test_empty_identity_does_not_register_or_replace_previous_context(self):
        self.gather()
        self.assertFalse(handoff.remember_payload({'lot_id': 'OTHER-'}, 'ivs_json'))
        self.assertEqual(handoff.latest()['lot'], 'LOT-')

    def test_verified_ivs_population_supplies_all_strip_ids_and_yield_units(self):
        from core.ivs_population import population_payload
        payload={'model':'MODEL','lot_id':'LOT-','records':[],'population':population_payload(['LOT-01','LOT-02','LOT-03'],64)}
        handoff.remember_payload(payload,'ivs_json');self.tab.apply_workflow_inputs()
        self.assertEqual(json.loads(Path(self.tab.strip_json_in.text()).read_text())['strip_ids'],['LOT-01','LOT-02','LOT-03'])
        self.assertEqual(self.tab.units_per_strip_in.text(),'64')

    def test_incomplete_ivs_population_does_not_replace_last_completed_input(self):
        self.gather();previous=handoff.latest()
        self.assertFalse(handoff.remember_payload({'model':'MODEL','lot_id':'OTHER-','records':[],
            'population':{'complete':False,'parse_errors':1}},'ivs_json'))
        self.assertEqual(handoff.latest(),previous)

    def test_completed_ivs_and_strip_tab_callbacks_register_original_payloads(self):
        from ui.tabs.ivs_tab import IvsStudioTab
        from ui.tabs.strip_id_tab import StripIdTab
        ivs = IvsStudioTab(); strips = StripIdTab()
        self.addCleanup(ivs.deleteLater); self.addCleanup(strips.deleteLater)
        ivs.on_scan_finished({'schema':'dmt-studio-ivs-v1', 'model':'MODEL', 'lot_id':'LOT-', 'records':[], 'strips':{}})
        strips.on_finished({'model':'MODEL', 'lot_id':'LOT-', 'strip_ids':['LOT-01']})
        self.tab.apply_workflow_inputs()
        self.assertTrue(self.tab.ivs_json_in.text())
        self.assertTrue(self.tab.strip_json_in.text())

    def test_cancelled_or_stopped_gathering_never_replaces_handoff(self):
        from ui.tabs.gathering_tab import GatheringTab, GatheringModePage
        from types import SimpleNamespace
        self.gather(output='previous')
        before = handoff.latest()
        tab = GatheringTab(); self.addCleanup(tab.deleteLater)
        page = next(p for p in tab.findChildren(GatheringModePage) if p.mode == 'VRS')
        page.run_context = dict(self.config, model='MODEL', lot='OTHER-')
        page.worker = SimpleNamespace(_stop=True)
        summary = dict(total_strips=0, excluded_strip_count=0, discarded_strip_count=0)
        with patch.object(page, 'apply_duplicate_choices', return_value=([], 'partial', summary)), patch.object(page, 'refresh_summary'):
            page.on_finished([], 'partial', summary)
        self.assertEqual(handoff.latest(), before)
        with patch.object(page, 'apply_duplicate_choices', return_value=([], '', {'duplicate_review_cancelled':True})):
            page.on_finished([], '', {})
        self.assertEqual(handoff.latest(), before)

