"""Internationalization (i18n) translation support."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QSettings

class Translator:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Translator, cls).__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        import sys
        self.settings = QSettings("DMT", "StudioApp")
        self.current_lang = self.settings.value("ui/language", "ko", type=str)
        if getattr(sys, 'frozen', False):
            base_dir = Path(sys._MEIPASS)
        else:
            base_dir = Path(__file__).resolve().parent.parent
            
        self.locales_dir = base_dir / "resources" / "locales"
        if not getattr(sys, 'frozen', False):
            self.locales_dir.mkdir(parents=True, exist_ok=True)
            
        self.translations: dict[str, str] = {}
        self.load_language(self.current_lang)

    def load_language(self, lang_code: str) -> None:
        """Loads a target language dictionary."""
        self.current_lang = lang_code
        self.settings.setValue("ui/language", lang_code)
        
        path = self.locales_dir / f"{lang_code}.json"
        if not path.is_file():
            self.translations = {}
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                self.translations = json.load(f)
        except Exception:
            self.translations = {}

    def gettext(self, text: str) -> str:
        """Translate a string to the current language."""
        return self.translations.get(text, text)

def _(text: str) -> str:
    """Global convenience hook for translation."""
    return Translator().gettext(text)
