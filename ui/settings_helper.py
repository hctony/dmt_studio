"""QSettings helpers and yield model settings I/O."""
from __future__ import annotations
from core.i18n import _

import json
import os
from pathlib import Path
from core.utils import first_path_text

from PySide6.QtWidgets import QLineEdit

from config import APP_SETTINGS, YIELD_MODEL_SETTINGS_PATH


def setting_text(key: str, default: str = "") -> str:
    value = APP_SETTINGS.value(key, default)
    return str(value) if value is not None else default


def restore_line_edit(widget: QLineEdit, key: str, default: str | None = None) -> None:
    value = setting_text(key, default if default is not None else widget.text())
    if value:
        widget.setText(value)


def save_line_edit(widget: QLineEdit, key: str) -> None:
    APP_SETTINGS.setValue(key, widget.text())


def save_setting_text(key: str, value: str) -> None:
    APP_SETTINGS.setValue(key, value)


def load_yield_model_settings(path=None) -> dict:
    path = YIELD_MODEL_SETTINGS_PATH if path is None else path
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def gathering_model_settings(model: str) -> dict:
    def find(settings):
        return next((fields for name, fields in settings.items()
                     if name.casefold() == model.casefold() and isinstance(fields, dict)), {})

    server_env = os.environ.get('DMT_SERVER_NAS_ENV')
    shared = load_yield_model_settings(Path(server_env).with_name('yield_model_settings.json')) if server_env else {}
    saved = find(load_yield_model_settings())
    defaults = find(shared)
    return {key: saved[key] if saved.get(key) not in (None, '') else defaults.get(key, '')
            for key in ('x_count', 'y_count', 'units_per_strip', 'xout_limit')}


def save_yield_model_settings(payload: dict) -> None:
    from core.yield_handoff import _write
    _write(YIELD_MODEL_SETTINGS_PATH, json.dumps(payload, ensure_ascii=False, indent=2))


def dialog_dir_for_file(current_text: str) -> str:
    text = first_path_text(current_text)
    if not text:
        return ""
    path = Path(text)
    if path.is_dir():
        return str(path)
    parent = path.parent
    return str(parent) if str(parent) not in {"", "."} else ""


def dialog_dir_for_folder(current_text: str) -> str:
    text = first_path_text(current_text)
    if not text:
        return ""
    path = Path(text)
    if path.is_file():
        parent = path.parent
        return str(parent) if str(parent) not in {"", "."} else ""
    if path.is_dir():
        parent = path.parent
        return str(parent) if str(parent) not in {"", "."} else str(path)
    parent = path.parent
    return str(parent) if str(parent) not in {"", "."} else ""
