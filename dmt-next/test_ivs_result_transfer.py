from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from core.ivs_result_transfer import inspect_transfer, move_transfer, source_candidates


class IvsResultTransferTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.ivs = root / 'ivs'
        self.vrs = root / 'vrs'
        self.ivs.mkdir()
        self.vrs.mkdir()
        self.roots = {'IVS': [self.ivs], 'VRS': [self.vrs]}
        self.source = self.ivs / 'MODEL_RX1-_admin_2026-11-23 112233.TXT'
        self.source.write_bytes(
            b'\xef\xbb\xbf1,RX1-,RX1-01\r\nRX1-,RX1-01,2,1,T,SM03\r\nRX1-,RX1-01,EOS\r\n'
            b'2,RX1-,RX1-02\r\nRX1-,RX1-02,EOS\r\n'
        )
        self.coords = {(2,1)}
        self.destination = self.vrs / self.source.name

    def inspect(self):
        return inspect_transfer(self.source, self.vrs, self.roots, 'RX1-', 'RX1-01', self.coords)

    def move(self, plan):
        return move_transfer(plan, self.roots, 'RX1-', 'RX1-01', self.coords)

    def test_move_keeps_exact_filename_bytes_and_filesystem_timestamp(self):
        body = self.source.read_bytes()
        mtime = self.source.stat().st_mtime_ns
        plan = self.inspect()
        self.assertEqual(plan.strips, ('RX1-01', 'RX1-02'))
        self.assertEqual(self.move(plan), self.destination)
        self.assertFalse(self.source.exists())
        self.assertEqual(self.destination.read_bytes(), body)
        self.assertEqual(self.destination.stat().st_mtime_ns, mtime)

    def test_existing_destination_and_changed_source_keep_original(self):
        plan = self.inspect()
        self.destination.write_bytes(b'existing data')
        with self.assertRaises(FileExistsError):
            self.move(plan)
        self.assertEqual(self.destination.read_bytes(), b'existing data')
        self.destination.unlink()
        self.source.write_bytes(self.source.read_bytes() + b'\r\n')
        with self.assertRaises(ValueError):
            self.move(plan)
        self.assertTrue(self.source.exists())
        self.assertFalse(self.destination.exists())

    def test_failed_copy_keeps_original_and_removes_staging_file(self):
        plan = self.inspect()
        with patch('core.ivs_result_transfer.shutil.copyfileobj', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.move(plan)
        self.assertTrue(self.source.exists())
        self.assertEqual(list(self.vrs.iterdir()), [])

    def test_failed_source_removal_rolls_back_only_the_new_destination(self):
        plan = self.inspect()
        unlink = Path.unlink
        def refuse_source(path, *args, **kwargs):
            if path == plan.resolved_source:
                raise PermissionError('source is open')
            return unlink(path, *args, **kwargs)
        with patch.object(type(self.source), 'unlink', refuse_source):
            with self.assertRaises(PermissionError):
                self.move(plan)
        self.assertTrue(self.source.exists())
        self.assertEqual(list(self.vrs.iterdir()), [])

    def test_rejects_snapshots_other_stations_and_unmatched_coordinates(self):
        other = self.vrs.parent / 'unapproved'
        other.mkdir()
        self.assertEqual(source_candidates([other/'snapshot.txt', self.source], [self.ivs]), [self.source])
        with self.assertRaises(PermissionError):
            inspect_transfer(self.source, other, self.roots, 'RX1-', 'RX1-01', self.coords)
        with self.assertRaises(ValueError):
            inspect_transfer(self.source, self.vrs, self.roots, 'RX1-', 'RX1-01', {(4,1)})
