from contextlib import closing
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core import xml_followup
from core.yield_calc import calculate_yield_summary_by_process


class XmlFollowupTests(unittest.TestCase):
    def test_txt_only_population_and_high_rate_filter(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            'DMT_CACHE_DIR': str(Path(folder) / 'cache'),
        }):
            root = Path(folder)
            txt = root / 'gather.txt'
            txt.write_text(
                '1,RX-,RX-01\nRX-,RX-01,1,1,T,SM03\nRX-,RX-01,2,1,T,9998\n'
                'RX-,RX-01,3,1,T,SM03\nRX-,RX-01,EOS\n'
                '1,RX-,RX-02\nRX-,RX-02,EOS\n', encoding='utf-8',
            )
            ski = root / 'result.ski'
            ski.write_text('RX-01,1,1\nRX-01,3,1\n')
            rpt = root / 'rpt'
            rpt.mkdir()
            (rpt / 'RX-01.rpt').write_text('X:001 Y:001 = 1 - BBT\n')
            summary = calculate_yield_summary_by_process([txt], [], [], [], None, 8, 7, rpt, ski, txt_first=True)
            self.assertEqual(summary['total_strips'], 2)
            self.assertEqual(summary['gross_total_units'], 16)
            self.assertEqual(summary['report_code_distribution'], {'OT14': 1, 'SM03': 1})
            self.assertEqual(summary['ot14_excluded_units'], 1)
            self.assertIn('RX-02', summary['xml_followup_evidence'])
            self.assertIsNone(xml_followup.enqueue('MODEL', summary, []))
            self.assertIsNone(xml_followup.enqueue('MODEL', summary, ['OT06 4.0%']))
            self.assertEqual(xml_followup.list_jobs(), [])
            previous = xml_followup.enqueue('MODEL', summary, ['SM03 6.67%'])
            self.assertEqual(xml_followup.enqueue('MODEL', summary, ['SM03 6.67%']), previous)
            newest = xml_followup.enqueue('OTHER', summary, ['SM03 6.67%'])
            self.assertEqual([row['id'] for row in xml_followup.list_jobs()], [newest, previous])
            self.assertEqual([row['id'] for row in xml_followup.list_jobs('model','rx')], [previous])
            first = xml_followup.claim()
            self.assertEqual(first['id'], previous)
            self.assertIsNone(xml_followup.claim())
            baseline = json.loads(first['baseline'])
            self.assertEqual(baseline['kind'], xml_followup.IMAGE_JOB)
            self.assertEqual(baseline['image_assignments'], {'RX-01': [[3, 1, 'SM03']]})
            self.assertEqual(baseline['strip_count'], 2)
            self.assertEqual(set(baseline['evidence']), {'RX-01'})
            xml_followup.cancel_images(previous)
            self.assertTrue(xml_followup.is_cancelling(previous))
            self.assertIsNone(xml_followup.claim())
            xml_followup.finish(previous, 'images_ready', {'image_counts': {'SM03': 1}}, {'records': [1]})
            cancelled = xml_followup.read_job(previous, include_xml=True)
            self.assertEqual(cancelled['status'], 'pending_images')
            self.assertEqual(cancelled['xml'], '')
            self.assertEqual(xml_followup.claim()['id'], newest)

    def test_recovery_defers_old_full_scans_and_preserves_completed_history(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'DMT_CACHE_DIR': folder + '/cache'}):
            baseline = {'strips': ['RX-01', 'RX-02'], 'units': 8, 'total_units': 16, 'scope': {},
                        'evidence': {'RX-01': {'width': 4, 'coords': [[1, 1]]}},
                        'high': ['SM03'], 'image_assignments': {'RX-01': [[1, 1, 'SM03']]}}
            with closing(xml_followup.connect()) as db, db:
                for identity, state, evidence in (
                    ('active', 'running', baseline),
                    ('queued', 'queued', {**baseline, 'high': []}),
                    ('completed', 'difference', baseline),
                ):
                    db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)',
                               (identity, 'MODEL', 'RX-', 1, 1, state, json.dumps(evidence), '{}', ''))
            xml_followup.recover()
            self.assertEqual(xml_followup.read_job('queued')['status'], 'images_skipped')
            self.assertEqual(xml_followup.read_job('active')['status'], 'pending_images')
            completed = xml_followup.read_job('completed')
            self.assertEqual(completed['status'], 'difference')
            self.assertEqual(json.loads(completed['baseline']), baseline)
            self.assertIsNone(xml_followup.claim())
            xml_followup.request_images('active')
            self.assertEqual(xml_followup.claim()['id'], 'active')
            xml_followup.recover()
            self.assertEqual(xml_followup.read_job('active')['status'], 'pending_images')
            self.assertIsNone(xml_followup.claim())

    def test_vrs_final_code_uses_images_from_pending_xml_coordinate(self):
        from core.models import DefectRecord
        baseline = {'image_assignments': {'RX-01': [[1, 1, 'OT06']]},
                    'evidence': {'RX-01': {'width': 4}}}
        original = DefectRecord('RX-01','A','M','RX-','T','1','Unit','T','0','0',
                                '9998','9998','','F',0,0,0,0,'data.xml',['example.bmp'])
        selected = xml_followup.image_records(baseline, [original])
        self.assertEqual([record.ivs_1 for record in selected], ['OT06'])
        self.assertEqual(original.ivs_1, '9998')

    def test_top_three_uses_report_counts_and_waits_for_an_explicit_request(self):
        from core.report_trace import SKI_UNKNOWN_ALLOCATED

        summary = {'lot_id': 'RX-', 'units_per_strip': 88, 'total_units': 1000, 'report_trace': {
            'selected_strip_ids': ['RX-01'],
            'strips': {'RX-01': {'x_count': 22}},
            'groups': [{'strip_id': 'RX-01', 'code': code, 'coords': [[x, 1]], 'reason': reason}
                       for x, code, reason in ((1, '170', 'gather'), (2, 'EN00', 'gather'), (3, 'SM03', 'gather'),
                                               (4, 'SM04', 'gather'), (5, 'SM03', SKI_UNKNOWN_ALLOCATED),
                                               (6, '1111', 'gather'), (7, 'OT14', 'bbt'))]}}
        counts = {'170': 11, '1111': 7, 'EN00': 10, 'SM03': 5, 'SM04': 5, 'OT14': 110}
        report = {'final_defects_by_code_including_bbt': {code: {'count': n} for code, n in counts.items()}}
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'DMT_CACHE_DIR': folder + '/cache'}):
            identity = xml_followup.register_top('MODEL', summary, report)
            self.assertEqual(xml_followup.register_top('MODEL', summary, report), identity)
            self.assertEqual(xml_followup.list_jobs()[0]['kind'], xml_followup.TOP_IMAGE_JOB)
            row = xml_followup.read_job(identity)
            self.assertEqual(row['status'], 'pending_images')
            baseline = json.loads(row['baseline'])
            self.assertEqual(baseline['ranking'], [['OT06', 18], ['EN00', 10], ['SM03', 5]])
            self.assertEqual(baseline['image_assignments'], {'RX-01': [[1, 1, 'OT06'], [2, 1, 'EN00'],
                                                                    [3, 1, 'SM03'], [6, 1, 'OT06']]})
            self.assertIsNone(xml_followup.claim())
            xml_followup.request_images(identity)
            xml_followup.recover()
            self.assertEqual(xml_followup.claim()['id'], identity)
