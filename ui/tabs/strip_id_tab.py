"""StripID extraction tab."""
from __future__ import annotations
from core.i18n import _

import json
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from config import DEFAULT_MODEL
from core.utils import infer_lot_id_from_values
from ui.common_widgets import start_line_edit_lot_lookup
from ui.settings_helper import restore_line_edit, save_line_edit
from workers.strip_workers import ModelLotListThread, StripIdThread, TrialListThread


class StripIdTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.payload: dict | None = None
        self.thread: Optional[StripIdThread] = None
        self.trial_list_thread: Optional[TrialListThread] = None
        self.lot_lookup_thread: Optional[ModelLotListThread] = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        form_box = QGroupBox(_("StripID 추출"))
        form = QFormLayout(form_box)
        self.model_in = QLineEdit(DEFAULT_MODEL)
        self.lot_in = QLineEdit()
        self.trial_path_in = QLineEdit()
        self.trial_path_in.setPlaceholderText("/Pixel/ResultData/Data/AFVI1/7M70-006/RX.../test_tony")
        restore_line_edit(self.model_in, "strip_id/model", DEFAULT_MODEL)
        restore_line_edit(self.lot_in, "strip_id/lot")
        restore_line_edit(self.trial_path_in, "strip_id/trial_path")
        trial_select_row = QHBoxLayout()
        self.trial_combo = QComboBox()
        self.trial_combo.addItem(_("Trial 목록을 먼저 불러오세요"))
        self.trial_combo.currentTextChanged.connect(self.on_trial_selected)
        self.btn_load_trials = QPushButton(_("Trial 목록 불러오기"))
        self.btn_load_trials.clicked.connect(self.load_trials)
        trial_select_row.addWidget(self.trial_combo)
        trial_select_row.addWidget(self.btn_load_trials)
        lot_row = QHBoxLayout()
        lot_row.addWidget(self.lot_in)
        btn_lot_auto = QPushButton(_("LOT 자동"))
        btn_lot_auto.clicked.connect(self.auto_lot)
        lot_row.addWidget(btn_lot_auto)
        self.btn_lot_lookup = QPushButton(_("LOT 검색"))
        self.btn_lot_lookup.clicked.connect(self.load_lots)
        lot_row.addWidget(self.btn_lot_lookup)
        form.addRow(_("모델명"), self.model_in)
        form.addRow("LOT", lot_row)
        form.addRow(_("Trial 선택"), trial_select_row)
        form.addRow(_("특정 Trial 경로"), self.trial_path_in)
        layout.addWidget(form_box)

        row = QHBoxLayout()
        self.btn_scan = QPushButton(_("StripID 추출"))
        self.btn_save = QPushButton(_("JSON 저장"))
        self.btn_save.setEnabled(False)
        self.btn_scan.clicked.connect(self.start_scan)
        self.btn_save.clicked.connect(self.save_json)
        row.addWidget(self.btn_scan)
        row.addWidget(self.btn_save)
        row.addStretch()
        layout.addLayout(row)

        self.result = QTextEdit()
        self.result.setReadOnly(True)
        layout.addWidget(self.result, stretch=1)

    def start_scan(self) -> None:
        model = self.model_in.text().strip()
        lot = self.lot_in.text().strip()
        trial_path = self.trial_path_in.text().strip()
        if not trial_path and (not model or not lot):
            QMessageBox.warning(self, _("입력 오류"), _("LOT 전체 스캔은 모델명과 LOT가 필요합니다."))
            return
        save_line_edit(self.model_in, "strip_id/model")
        save_line_edit(self.lot_in, "strip_id/lot")
        save_line_edit(self.trial_path_in, "strip_id/trial_path")
        self.btn_scan.setEnabled(False)
        self.result.clear()
        self.thread = StripIdThread(model, lot, trial_path)
        self.thread.progress.connect(self.result.append)
        self.thread.finished.connect(self.on_finished)
        self.thread.failed.connect(self.on_failed)
        self.thread.start()

    def auto_lot(self) -> None:
        lot_id = infer_lot_id_from_values(self.trial_path_in.text(), self.lot_in.text())
        if lot_id:
            self.lot_in.setText(lot_id)
            save_line_edit(self.lot_in, "strip_id/lot")
        else:
            self.load_lots()

    def load_lots(self) -> None:
        start_line_edit_lot_lookup(
            self,
            self.model_in.text(),
            self.lot_in,
            "strip_id/lot",
            [self.btn_lot_lookup],
            self.result.append,
        )

    def load_trials(self) -> None:
        model = self.model_in.text().strip()
        lot = self.lot_in.text().strip()
        if not model or not lot:
            QMessageBox.warning(self, _("입력 오류"), _("Trial 목록은 모델명과 LOT가 필요합니다."))
            return
        save_line_edit(self.model_in, "strip_id/model")
        save_line_edit(self.lot_in, "strip_id/lot")
        self.btn_load_trials.setEnabled(False)
        self.trial_combo.clear()
        self.trial_combo.addItem(_("Trial 목록 로딩 중..."))
        self.trial_list_thread = TrialListThread(model, lot)
        self.trial_list_thread.finished.connect(self.on_trials_loaded)
        self.trial_list_thread.failed.connect(self.on_trials_failed)
        self.trial_list_thread.start()

    def on_trials_loaded(self, paths: list[str]) -> None:
        self.btn_load_trials.setEnabled(True)
        self.trial_combo.blockSignals(True)
        self.trial_combo.clear()
        if paths:
            self.trial_combo.addItem(_("LOT 전체 스캔"))
            for path in paths:
                self.trial_combo.addItem(path)
        else:
            self.trial_combo.addItem(_("Trial 없음"))
        self.trial_combo.blockSignals(False)
        self.trial_path_in.clear()
        self.result.append(f"Trial {len(paths)}개 로드")

    def on_trials_failed(self, message: str) -> None:
        self.btn_load_trials.setEnabled(True)
        QMessageBox.critical(self, _("Trial 목록 실패"), message)

    def on_trial_selected(self, text: str) -> None:
        if text.startswith("/") or text.startswith("//") or text.startswith("\\\\"):
            self.trial_path_in.setText(text)
            save_line_edit(self.trial_path_in, "strip_id/trial_path")
        elif text == _("LOT 전체 스캔"):
            self.trial_path_in.clear()
            save_line_edit(self.trial_path_in, "strip_id/trial_path")

    def on_finished(self, payload: dict) -> None:
        self.btn_scan.setEnabled(True)
        self.btn_save.setEnabled(True)
        self.payload = payload
        from core.yield_handoff import remember_payload
        remember_payload(payload, 'strip_json')
        self.result.setPlainText(json.dumps(payload, ensure_ascii=False, indent=2))

    def on_failed(self, message: str) -> None:
        self.btn_scan.setEnabled(True)
        QMessageBox.critical(self, _("추출 실패"), message)

    def save_json(self) -> None:
        if not self.payload:
            return
        lot = self.payload.get("lot_id", "strip_ids")
        path, _ext = QFileDialog.getSaveFileName(self, _("StripID JSON 저장"), f"{lot}_strip_ids.json", "JSON Files (*.json)")
        if not path:
            return
        Path(path).write_text(json.dumps(self.payload, ensure_ascii=False, indent=2), encoding="utf-8")
        QMessageBox.information(self, _("저장 완료"), path)
