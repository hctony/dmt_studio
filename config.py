"""Global configuration and shared singletons."""
from __future__ import annotations
from core.i18n import _

import os
import re
from pathlib import Path

from PySide6.QtCore import QSettings

DATA_DIR = Path(os.environ.get('DMT_DATA_DIR', '.'))
env_path = DATA_DIR / '.env'
if env_path.is_file():
    for raw in env_path.read_text(encoding='utf-8-sig').splitlines():
        if raw.strip() and not raw.lstrip().startswith('#') and '=' in raw:
            key, value = raw.split('=', 1)
            if key.strip().startswith('DMT_'):
                os.environ.setdefault(key.strip(), value.strip().strip('\"').strip("'"))

NAS_HOST = os.getenv("DMT_NAS_HOST", "192.168.0.5")
NAS_PORT = int(os.getenv("DMT_NAS_PORT", "22"))
NAS_USER = os.getenv("DMT_NAS_USER", "admin")
NAS_PASSWORD = os.getenv("DMT_NAS_PASSWORD", "")
NAS_ROOT = os.getenv("DMT_NAS_ROOT", "/Pixel/ResultData/Data")
DEFAULT_MODEL = os.getenv("DMT_MODEL", "7M70-006")
MACHINES = ["AFVI1", "AFVI2", "AFVI3"]
IMAGE_EXTENSIONS = (".bmp", ".jpg", ".jpeg")

APP_SETTINGS = QSettings("DMT", "DMT Analysis Studio")
YIELD_MODEL_SETTINGS_PATH = DATA_DIR / "yield_model_settings.json"

LOT_ID_RE = re.compile(r"\b[A-Z]{2}[A-Z0-9]+-")

