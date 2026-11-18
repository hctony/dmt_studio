import concurrent.futures
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import inspection_history as h


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'DMT_CACHE_DIR': str(self.root/'cache'), 'DMT_REMOTE_SESSION': '0'})
        self.env.start()
        self.body = ('88,RX1267071100-,RX1267071100-3002\r\n'
            'RX1267071100-,RX1267071100-3002,1,1,T,DR01\r\n'
            'RX1267071100-,RX1267071100-3002,EOS\r\n'
            '88,RX1267071100-,RX1267071100-3003\r\n'
            'RX1267071100-,RX1267071100-3003,EOS\r\n').encode()
        self.obj = self.root/'object.txt'; self.obj.write_bytes(self.body)
        self.file = dict(object=str(self.obj), path=r'\\factory\result\7P05-P37_RX1267071100-_admin_2026-11-18 132845.txt',
            sha256=hashlib.sha256(self.body).hexdigest(), size=len(self.body), mtime_ns=1794979725000000000)

    def tearDown(self):
        self.env.stop(); self.temp.cleanup()

    def test_filename_operator_and_time_are_separate_from_mtime(self):
        info = h.filename_info(self.file['path'], 'RX1267071100-')
        self.assertEqual(info['operator'], 'admin')
        self.assertEqual(info['inspection_time'], '2026-11-18 13:28:45')
        old = h.filename_info('7P05-P37_RX1267071100-_192_010345.TXT', 'RX1267071100-')
        self.assertEqual(old['operator'], '192'); self.assertEqual(old['inspection_time'], '')
        self.assertEqual(h.filename_info(self.file['path'], 'OTHER-')['operator'], '')

    def test_original_retained_after_source_and_snapshot_deleted(self):
        h.record_file(self.file, 'RX1267071100-', 'IVS')
        self.obj.unlink()
        rows = h.search(strip='rx1267071100-3002', operator='admin')
        self.assertEqual(len(rows), 1)
        row, body, block = h.original(rows[0]['id'])
        self.assertEqual(body, self.body); self.assertIn('DR01', block)
        self.assertNotIn('3003', block)
        zero = h.search(strip='RX1267071100-3003')[0]
        self.assertEqual(zero['defects'], 0)
        self.assertTrue(h.original(zero['id'])[2].endswith('EOS'))

    def test_repeat_collection_deduplicates_but_rework_and_process_survive(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: h.record_file(self.file, 'RX1267071100-', 'IVS'), range(8)))
        self.assertEqual(len(h.search(lot='RX1267071100-')), 2)
        changed = dict(self.file, mtime_ns=self.file['mtime_ns']+1000000000)
        h.record_file(changed, 'RX1267071100-', 'IVS')
        h.record_file(self.file, 'RX1267071100-', 'FVI')
        self.assertEqual(len(h.search(strip='RX1267071100-3002')), 3)

    def test_hash_and_remote_source_permissions_checked(self):
        h.record_file(self.file, 'RX1267071100-', 'IVS')
        row = h.search(strip='RX1267071100-3002')[0]
        with patch.dict(os.environ, {'DMT_REMOTE_SESSION':'1'}), patch('remote_paths.allowed_path', return_value=False):
            self.assertEqual(h.search(lot='RX1267071100-'), [])
            with self.assertRaises(PermissionError): h.original(row['id'])
        self.obj.write_bytes(b'changed')
        with self.assertRaises(ValueError): h.record_file(dict(self.file, mtime_ns=1), 'RX1267071100-', 'IVS')


if __name__ == '__main__': unittest.main()
