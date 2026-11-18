import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import paramiko
from core import nas


class NasAuthSettingsTests(unittest.TestCase):
    def test_local_and_remote_use_saved_server_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = root / 'session'
            session.mkdir()
            (root / '.env').write_text(
                'DMT_NAS_HOST=nas.example\nDMT_NAS_USER=operator\nDMT_NAS_PASSWORD=" value=with\'quote "',
                encoding='utf-8-sig',
            )
            (session / '.env').write_text('DMT_NAS_USER=old-user\nDMT_NAS_PASSWORD=old-password', encoding='utf-8')
            env = {key: value for key, value in os.environ.items() if not key.startswith('DMT_')}
            env.update(DMT_NAS_USER='inherited-user', DMT_NAS_PASSWORD='inherited-password')
            script = 'import config,json; print(json.dumps([config.NAS_HOST, config.NAS_USER, config.NAS_PASSWORD]))'
            for path, extras in ((root, {}), (session, {'DMT_SERVER_NAS_ENV': str(root / '.env')})):
                output = subprocess.check_output([sys.executable, '-c', script], text=True,
                    cwd=Path(__file__).resolve().parent.parent, env={**env, **extras, 'DMT_DATA_DIR': str(path)})
                self.assertEqual(json.loads(output), ['nas.example', 'operator', " value=with'quote "])

    def test_auth_error_closes_connection_and_gives_actionable_message(self):
        with patch('core.nas.paramiko.SSHClient') as factory:
            client = factory.return_value
            client.connect.side_effect = paramiko.AuthenticationException('rejected')
            with self.assertRaises(nas.NasAuthenticationError) as result:
                nas.connect()
            self.assertIn(nas.NAS_HOST, str(result.exception))
            self.assertNotIn('rejected', str(result.exception))
            client.close.assert_called_once()
            client.open_sftp.assert_not_called()
