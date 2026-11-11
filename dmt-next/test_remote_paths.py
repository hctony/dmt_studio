import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from remote_paths import validate_input


class RemotePathTests(unittest.TestCase):
    def test_settings_are_isolated_between_real_processes(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = "import config;config.APP_SETTINGS.setValue('test/identity',__import__('sys').argv[1]);config.APP_SETTINGS.sync();print(config.APP_SETTINGS.value('test/identity'))"
            for client in ('B', 'C'):
                folder = root/client; folder.mkdir()
                result = subprocess.run([sys.executable, '-c', script, client], cwd=str(Path(__file__).resolve().parent.parent), env={**os.environ, 'DMT_DATA_DIR':str(folder), 'DMT_SETTINGS_FILE':str(folder/'settings.ini')}, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(result.stdout.strip(),client)
            self.assertIn('identity=B',(root/'B/settings.ini').read_text())
            self.assertIn('identity=C',(root/'C/settings.ini').read_text())

    def test_resource_sampler_reports_real_process_and_network_counters(self):
        import threading
        from resource_monitor import start_monitor
        ready=threading.Event();events=[]
        def emit(event):
            events.append(event);ready.set()
        stopped=start_monitor(emit)
        try:
            self.assertTrue(ready.wait(5),'No resource sample')
            self.assertEqual(events[0]['event'],'resources',events)
            self.assertEqual(events[0]['resources']['pid'],os.getpid())
            self.assertGreater(events[0]['resources']['rssBytes'],0)
            self.assertGreaterEqual(events[0]['resources']['hostReceiveBytesPerSecond'],0)
        finally:
            stopped.set()

    def test_paths_are_bounded_even_when_typed_or_imported_into_widgets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root/'session'; data.mkdir()
            sources = root/'sources'; sources.mkdir()
            widget = object()
            class Controller:
                def input_widgets(self):
                    return [('6/YieldTab:/vrs_txt_in', widget)]
            with patch.dict(os.environ, {'DMT_DATA_DIR':str(data), 'DMT_ALLOWED_ROOTS':json.dumps([str(sources)])}):
                validate_input(Controller(), widget, str(sources/'source.txt'))
                validate_input(Controller(), widget, str(data/'outputs/result.json'))
                for value in (str(root/'other.txt'), str(sources/'.env'), '../private.txt', str(sources/'../other.txt')):
                    with self.assertRaises(ValueError, msg=value):
                        validate_input(Controller(), widget, value)

    def test_gathering_share_suffix_and_nas_trial_are_supported(self):
        widget = object()
        class Controller:
            def input_widgets(self):
                return [('3/GatheringModePage:VRS/path_in', widget)]
        validate_input(Controller(), widget, r'VRSData\ResultData\BDM_KINSUS\BDM_Backup')
        with self.assertRaises(ValueError):
            validate_input(Controller(), widget, r'..\private')
