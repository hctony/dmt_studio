"""Global configuration and shared singletons."""
from __future__ import annotations
from core.i18n import _

import os
import re
from pathlib import Path

from PySide6.QtCore import QSettings

DATA_DIR = Path(os.environ.get('DMT_DATA_DIR', '.'))
NAS_KEYS = {'DMT_NAS_HOST', 'DMT_NAS_PORT', 'DMT_NAS_USER', 'DMT_NAS_PASSWORD', 'DMT_NAS_ROOT'}


def read_env_file(path):
    values = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding='utf-8-sig').splitlines():
        if raw.strip() and not raw.lstrip().startswith('#') and '=' in raw:
            key, value = raw.split('=', 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[key.strip()] = value
    return values


local_values = read_env_file(DATA_DIR / '.env')
server_env = os.environ.get('DMT_SERVER_NAS_ENV')
server_values = read_env_file(Path(server_env)) if server_env else {}
for key, value in {**server_values, **local_values}.items():
    if key.startswith('DMT_') and key not in NAS_KEYS:
        os.environ.setdefault(key, value)

nas_values = server_values if server_env else local_values


def nas_value(key, default):
    return nas_values.get(key, os.getenv(key, default))


NAS_HOST = nas_value("DMT_NAS_HOST", "192.168.0.5")
NAS_PORT = int(nas_value("DMT_NAS_PORT", "22"))
NAS_USER = nas_value("DMT_NAS_USER", "admin")
NAS_PASSWORD = nas_value("DMT_NAS_PASSWORD", "")
NAS_ROOT = nas_value("DMT_NAS_ROOT", "/Pixel/ResultData/Data")
DEFAULT_MODEL = os.getenv("DMT_MODEL", "7M70-006")
MACHINES = ["AFVI1", "AFVI2", "AFVI3"]
IMAGE_EXTENSIONS = (".bmp", ".jpg", ".jpeg")

if os.environ.get('DMT_SETTINGS_FILE'):
    settings_file = Path(os.environ['DMT_SETTINGS_FILE'])
    new_settings = not settings_file.exists()
    APP_SETTINGS = QSettings(str(settings_file), QSettings.IniFormat)
    seed = DATA_DIR / 'settings-seed.json'
    if new_settings and seed.is_file():
        import json
        for key, value in json.loads(seed.read_text(encoding='utf-8')).items():
            APP_SETTINGS.setValue(key, value)
        APP_SETTINGS.sync()
else:
    APP_SETTINGS = QSettings("DMT", "DMT Analysis Studio")
YIELD_MODEL_SETTINGS_PATH = DATA_DIR / "yield_model_settings.json"

LOT_ID_RE = re.compile(r"\b[A-Z]{2}[A-Z0-9]+-")

