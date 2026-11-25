from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from core.collection import iter_blocks
from core.inspection_history import filename_info
from core.report_trace import file_version
from core.vrs_mismatch_export import build_correction, prepare_correction, save_correction


class VrsMismatchExportTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.first = root / 'vrs1' / 'BDM_KINSUS' / 'BDM_Backup'
        self.second = root / 'vrs2' / 'BDM_KINSUS' / 'BDM_Backup'
        for path in (self.first, self.second):
            path.mkdir(parents=True)
        self.ski = root / 'final.ski'
        self.ski.write_text('RX1-01,1,1\n')
        self.summary = {
            'lot_id': 'RX1-',
            'report_trace': {
                'ski_path': str(self.ski),
                'file_versions': {str(self.ski): file_version(self.ski)},
                'strips': {
                    'RX1-01': {
                        'ski_coords': [[1,1], [2,2], [8,2]],
                        'bbt_coords': [[8,2]],
                        'processes': {
                            'VRS': {'defect_lines': [
                                'RX1-,RX1-01,1,1,B,EN01',
                                'RX1-,RX1-01,4,2,B,EN02',
                                'RX1-,RX1-01,8,2,T,OT14',
                            ]},
                            'FVI': {'defect_lines': [
                                'RX1-,RX1-01,4,2,T,170',
                                'RX1-,RX1-01,2,2,T,SM04',
                                'RX1-,RX1-01,6,2,T,1111',
                            ]},
                            'IVS': {'defect_lines': ['RX1-,RX1-01,4,2,T,111']},
                        },
                    },
                    'RX1-02': {'processes': {'IVS': {'defect_lines': ['RX1-,RX1-02,1,1,T,SM09']}}},
                },
            },
            'whole_strip_review': [{'strip_id':'RX1-01', 'process':'FVI', 'status':'archived'}],
        }
        self.rows = [
            {'strip_id':'RX1-01', 'txt_only':[(4,2),(8,2)], 'ski_only':[(2,2),(3,2)]},
            {'strip_id':'RX1-02', 'txt_only':[(1,1)], 'ski_only':[]},
        ]

    def test_one_result_keeps_real_code_face_existing_vrs_and_the_file_terminators(self):
        correction = build_correction(self.summary, list(reversed(self.rows)))
        self.assertEqual((correction.strips,correction.corrected,correction.retained,correction.common),(2,4,1,1))
        self.assertEqual(correction.missing,(('RX1-01',3,2),))
        self.assertEqual(correction.body, (
            b'4,RX1-,RX1-01\r\nRX1-,RX1-01,1,1,B,EN01\r\nRX1-,RX1-01,2,2,T,SM04\r\n'
            b'RX1-,RX1-01,4,2,B,EN02\r\nRX1-,RX1-01,6,2,T,1111\r\nRX1-,RX1-01,EOS\r\n'
            b'1,RX1-,RX1-02\r\nRX1-,RX1-02,1,1,T,SM09\r\nRX1-,RX1-02,EOS\r\n\r\nRX1-,EOL\r\n'
        ))
        blocks = list(iter_blocks(correction.body.decode('ascii').splitlines()))
        self.assertEqual([len(block['lines']) for block in blocks], [4,1])

    def test_writes_only_to_first_vrs_parent_with_operator_999_and_refuses_overwrite(self):
        with patch('core.vrs_mismatch_export.configured_roots', return_value={'VRS':[self.first,self.second]}):
            plan = prepare_correction('MODEL-1', self.summary, self.rows)
            self.assertEqual(plan.destination.parent,self.first.parent)
            self.assertRegex(plan.destination.name, r'^MODEL-1_RX1-_999_\d{6}\.txt$')
            metadata = filename_info(plan.destination, 'RX1-')
            self.assertEqual((metadata['model'],metadata['operator'],metadata['time_basis']),
                             ('MODEL-1','999','file_mtime_only'))
            saved = save_correction('MODEL-1', self.summary, plan)
            self.assertEqual(saved.read_bytes(),plan.correction.body)
            with self.assertRaises(FileExistsError):
                save_correction('MODEL-1', self.summary, plan)
            self.ski.write_text('RX1-01,2,2\nRX1-01,3,2\n')
            with self.assertRaises(ValueError):
                save_correction('MODEL-1', self.summary, plan)
        self.assertEqual(list(self.first.iterdir()),[])
        self.assertEqual(list(self.second.parent.glob('*.txt')),[])
