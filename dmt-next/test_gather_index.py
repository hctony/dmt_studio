import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from core import gather, gather_index


class GatherIndexTests(unittest.TestCase):
    def test_different_lots_and_restart_do_not_reread_unchanged_file(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(gather_index,'DATA_DIR',Path(folder)):
            path=Path(folder)/'multi.TXT'
            path.write_text('1,A-,A-01\nA-,A-01,1,1,T,C1\nA-,A-01,EOS\n1,B-,B-01\nB-,B-01,2,1,T,C2\nB-,B-01,EOS\n')
            with patch.object(gather,'read_text_lines',wraps=gather.read_text_lines) as reader:
                self.assertIn('A-01',gather.parse_gather_file(path,'A-'))
                self.assertIn('B-01',gather.parse_gather_file(path,'B-'))
                self.assertEqual(reader.call_count,1)
                import subprocess, sys
                result=subprocess.run([sys.executable,'-c',"from pathlib import Path; from unittest.mock import patch; from core.gather import parse_gather_file; import sys\nwith patch('core.gather.read_text_lines',side_effect=AssertionError('network body reread')): assert 'B-01' in parse_gather_file(Path(sys.argv[1]),'B-')",str(path)],env=dict(os.environ,DMT_DATA_DIR=folder),capture_output=True,text=True,timeout=20)
                self.assertEqual(result.returncode,0,result.stderr)
                gather.GATHER_FILE_CACHE.clear()
                self.assertIn('B-01',gather.parse_gather_file(path,'B-'))
                self.assertEqual(gather.parse_gather_file(path,'MISSING-'),{})
                self.assertEqual(reader.call_count,1)
                path.write_text('1,NEW-,NEW-01\nNEW-,NEW-01,3,1,T,C3\nNEW-,NEW-01,EOS\n')
                self.assertIn('NEW-01',gather.parse_gather_file(path,'NEW-'))
                self.assertEqual(reader.call_count,2)

    def test_recent_uses_creation_or_modification_not_folder_time(self):
        self.assertFalse(gather_index.recent_file(SimpleNamespace(st_birthtime=10,st_mtime=20,st_ctime=30),100))
        self.assertTrue(gather_index.recent_file(SimpleNamespace(st_birthtime=110,st_mtime=20,st_ctime=30),100))
        self.assertTrue(gather_index.recent_file(SimpleNamespace(st_birthtime=10,st_mtime=110,st_ctime=30),100))

    def test_period_filter_and_full_history_include_uppercase_txt(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);old=root/'old.TXT';new=root/'new.txt'
            old.write_text('old');new.write_text('new');os.utime(old,(1000,1000))
            with patch.object(gather_index,'recent_file',side_effect=lambda info,cutoff:info.st_mtime>=cutoff):
                self.assertEqual(gather.cached_txt_files(root,31),[new])
            self.assertEqual(set(gather.cached_txt_files(root,0)),{old,new})

    def test_cache_storage_failure_falls_back_to_original_file(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(gather_index,'DATA_DIR',Path(folder)):
            (Path(folder)/'cache').write_text('blocked')
            path=Path(folder)/'file.txt';path.write_text('content')
            self.assertEqual(gather_index.read_indexed_lines(path,path.stat(),'A',lambda p:p.read_text().splitlines()),['content'])
