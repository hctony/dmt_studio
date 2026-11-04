import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.gather import cached_txt_files
from core.skipdata import load_skipdata_folder, load_skipdata_code_folder
from ui.settings_helper import dialog_dir_for_file

class CompatibilityTests(unittest.TestCase):
    def test_nested_file_added_without_root_timestamp_change(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            nested = root / 'nested'
            nested.mkdir()
            first = nested / 'first.txt'
            first.write_text('one')
            self.assertEqual(cached_txt_files(root), [first])
            stamp = root.stat()
            second = nested / 'second.txt'
            second.write_text('two')
            os.utime(root, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            self.assertEqual(set(cached_txt_files(root)), {first, second})

    def test_older_rpt_changed_below_maximum_timestamp(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            a, b = root / 'a.rpt', root / 'b.rpt'
            a.write_text('[A]\nX:1 Y:2 = NG - C1', encoding='ascii')
            b.write_text('[B]\nX:3 Y:4 = NG - C2', encoding='ascii')
            os.utime(a, (1000, 1000)); os.utime(b, (3000, 3000))
            self.assertEqual(load_skipdata_folder(root)['A'], {(1, 2)})
            load_skipdata_code_folder(root)
            a.write_text('[A]\nX:5 Y:6 = NG - C3', encoding='ascii')
            os.utime(a, (2000, 2000))
            self.assertEqual(load_skipdata_folder(root)['A'], {(5, 6)})
            self.assertEqual(load_skipdata_code_folder(root)['A']['C3'], {(5, 6)})

    def test_dialog_path_helpers(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(dialog_dir_for_file(str(Path(folder) / 'input.json')), folder)

    def test_persistent_bridge_keeps_cache_between_requests(self):
        with tempfile.TemporaryDirectory() as folder:
            ski = Path(folder) / 'input.ski'
            ski.write_text('ABC-01,1,2', encoding='ascii')
            requests = [{'id':1,'method':'ski.load','args':{'path':str(ski)}}, {'id':2,'method':'cache.status'}, {'id':3,'method':'cache.clear'}, {'id':4,'method':'cache.status'}]
            result = subprocess.run([sys.executable, str(Path(__file__).with_name('legacy_bridge.py'))], input=''.join(json.dumps(r)+'\n' for r in requests), capture_output=True, text=True, encoding='utf-8', timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            responses = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual(responses[0]['result']['ABC-01'], [[1,2]])
            self.assertEqual(responses[1]['result']['ski'], 1)
            self.assertEqual(responses[3]['result']['ski'], 0)

if __name__ == '__main__': unittest.main()
