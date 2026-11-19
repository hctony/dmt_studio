import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core import xml_followup
from core.yield_calc import calculate_yield_summary_by_process


class XmlFollowupTests(unittest.TestCase):
    def test_txt_only_population_and_shared_serial_verification(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            'DMT_CACHE_DIR': str(Path(folder) / 'cache'),
        }):
            root = Path(folder)
            txt = root / 'gather.txt'
            txt.write_text(
                '1,RX-,RX-01\nRX-,RX-01,1,1,T,SM03\nRX-,RX-01,2,1,T,9998\nRX-,RX-01,EOS\n'
                '1,RX-,RX-02\nRX-,RX-02,EOS\n', encoding='utf-8',
            )
            ski = root / 'result.ski'
            ski.write_text('RX-01,1,1\n')
            rpt = root / 'rpt'
            rpt.mkdir()
            (rpt / 'RX-01.rpt').write_text('X:001 Y:001 = 1 - BBT\n')
            summary = calculate_yield_summary_by_process([txt], [], [], [], None, 8, 7, rpt, ski, txt_first=True)
            self.assertEqual(summary['total_strips'], 2)
            self.assertEqual(summary['gross_total_units'], 16)
            self.assertEqual(summary['report_code_distribution'], {'OT14': 1})
            self.assertEqual(summary['ot14_excluded_units'], 1)
            self.assertIn('RX-02', summary['xml_followup_evidence'])
            previous = xml_followup.enqueue('MODEL', summary, ['OT06 4.0%'])
            newest = xml_followup.enqueue('MODEL', {**summary, 'lot_id': 'OTHER-'}, [])
            self.assertEqual([row['id'] for row in xml_followup.list_jobs()], [newest, previous])
            self.assertEqual([row['id'] for row in xml_followup.list_jobs('model','rx')], [previous])
            first = xml_followup.claim()
            self.assertEqual(first['id'], previous)
            self.assertIsNone(xml_followup.claim())
            xml_followup.recover()
            self.assertEqual(xml_followup.claim()['id'], previous)
            baseline = json.loads(first['baseline'])
            stamp = txt.stat().st_mtime
            payload = {'schema': 'dmt-studio-ivs-v1', 'records': [], 'population': {
                'complete': True, 'strip_ids': ['RX-01', 'RX-03']},
                'strip_history': {'complete': True},
                'latest_selection': {'selected': {'RX-01': {'epoch_start': stamp + 1}}}}
            status, details = xml_followup.compare(baseline, payload)
            self.assertEqual(status, 'difference')
            self.assertEqual(details['xml_only'], ['RX-03'])
            self.assertEqual(details['txt_only'], ['RX-02'])
            self.assertEqual(details['older_txt'], ['VRS RX-01'])
            self.assertEqual(details['image_counts'], {'OT06': 0})
            xml_followup.finish(previous, status, details, payload)
            self.assertEqual(xml_followup.read_job(previous)['status'], 'difference')
            self.assertEqual(xml_followup.claim()['id'], newest)

    def test_vrs_final_code_uses_images_from_pending_xml_coordinate(self):
        from core.models import DefectRecord
        baseline = {'image_assignments': {'RX-01': [[1, 1, 'OT06']]},
                    'evidence': {'RX-01': {'width': 4}}}
        original = DefectRecord('RX-01','A','M','RX-','T','1','Unit','T','0','0',
                                '9998','9998','','F',0,0,0,0,'data.xml',['example.bmp'])
        selected = xml_followup.image_records(baseline, [original])
        self.assertEqual([record.ivs_1 for record in selected], ['OT06'])
        self.assertEqual(original.ivs_1, '9998')
