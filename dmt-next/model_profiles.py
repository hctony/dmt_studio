"""Import the original Studio model JSON without depending on local data paths."""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone

FIELDS = {
    'units_per_strip': 'units_per_strip_in', 'xout_limit': 'xout_limit_in',
    'vrs_txt_paths': 'vrs_txt_in', 'fvi_txt_paths': 'fvi_txt_in',
    'ivs_txt_paths': 'ivs_txt_in', 'ivs_json_paths': 'ivs_json_in',
    'skipdata_path': 'skipdata_in', 'ski_path': 'ski_in',
}


def validate(payload):
    if not isinstance(payload, dict) or not payload:
        raise ValueError('모델 설정 JSON이 비어 있거나 형식이 다릅니다. / 機種設定 JSON 為空或格式不符。')
    result = {}
    for model, values in payload.items():
        if not isinstance(model, str) or not model.strip() or model != model.strip() or not isinstance(values, dict):
            raise ValueError('모델 이름 또는 설정 형식 오류 / 機種名稱或設定格式錯誤')
        profile = {}
        for key in FIELDS:
            if key not in values:
                continue
            value = values[key]
            if isinstance(value, bool) or not isinstance(value, (str, int)) or (key not in ('units_per_strip', 'xout_limit') and not isinstance(value, str)):
                raise ValueError(f'{model}: {key} — 잘못된 값 / 無效的值')
            value = str(value).strip()
            if key in ('units_per_strip', 'xout_limit') and value:
                if not value.isascii() or not value.isdigit() or (key == 'units_per_strip' and int(value) == 0):
                    raise ValueError(f'{model}: {key} — 수량 오류 / 數量錯誤')
            profile[key] = value
        if not profile:
            raise ValueError(f'{model}: 지원하는 모델 설정이 없습니다. / 沒有支援的機種設定。')
        result[model] = profile
    return result


class ModelProfiles:
    def __init__(self, controller):
        self.controller = controller

    @property
    def path(self):
        from config import YIELD_MODEL_SETTINGS_PATH
        return YIELD_MODEL_SETTINGS_PATH

    def read(self):
        if not self.path.exists():
            return {}
        data = json.loads(self.path.read_text(encoding='utf-8-sig'))
        if not isinstance(data, dict):
            raise ValueError('기존 설정 파일 형식 오류 / 現有設定檔格式錯誤')
        return data

    def status(self):
        tab = self.controller.window.centralWidget().widget(6)
        return {'profiles': self.read(), 'selected': tab.model_in.text().strip()}

    def ensure_idle(self):
        if self.controller.busy() or self.controller.action_depth:
            raise ValueError('작업 완료 후 사용하세요. / 請先完成工作。')

    def apply(self, model, gathering=False):
        self.ensure_idle()
        profiles = self.read()
        if model not in profiles:
            raise ValueError('저장된 모델을 선택하세요. / 請選擇已儲存的機種。')
        profile = validate({model: profiles[model]})[model]
        tabs = self.controller.window.centralWidget()
        tab = tabs.widget(6)
        tab.model_in.setText(model)
        # An explicit blank belongs to this model; never leak another model's path.
        for key, attr in FIELDS.items():
            if key in profile:
                getattr(tab, attr).setText(profile[key])
        if gathering:
            gather = tabs.widget(3)
            gather.model_in.setText(model)
            for key, attr in [('xout_limit', 'allowance_in'), ('skipdata_path', 'skipdata_in'), ('ski_path', 'ski_in')]:
                if key in profile:
                    getattr(gather, attr).setText(profile[key])
        self.controller.save_inputs()
        return {'model': model, 'gathering': bool(gathering)}

    def import_data(self, payload):
        self.ensure_idle()
        incoming = validate(payload)
        existing = self.read()
        merged = dict(existing)
        for model, profile in incoming.items():
            previous = existing.get(model, {})
            if not isinstance(previous, dict):
                raise ValueError(f'{model}: 기존 설정 형식 오류 / 現有設定格式錯誤')
            merged[model] = {**previous, **profile}
            validate({model: merged[model]})
        root = self.path.parent
        root.mkdir(parents=True, exist_ok=True)
        backup = root / 'settings-backups' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup.mkdir(parents=True)
        for source in (self.path, self.controller.input_path):
            if source.is_file():
                shutil.copy2(source, backup / source.name)
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temp, self.path)
        current = self.controller.window.centralWidget().widget(6).model_in.text().strip()
        selected = current if current in incoming else next(iter(incoming))
        self.apply(selected)
        return {**self.status(), 'count': len(incoming), 'backup': str(backup)}
