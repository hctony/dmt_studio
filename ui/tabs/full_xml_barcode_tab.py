"""Full XML barcode scan and comparison tab."""
from __future__ import annotations
from ui.worker_lifetime import start_worker
from core.i18n import _

import json
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import (
    QComboBox,
    QCheckBox,
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
from core.yield_calc import compare_full_xml_barcode_with_strip_json
from ui.common_widgets import start_line_edit_lot_lookup
from ui.settings_helper import restore_line_edit, save_line_edit
from workers.strip_workers import FullXmlBarcodeScanThread, ModelLotListThread, TrialListThread


class FullXmlBarcodeTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.payload: dict | None = None
        self.thread: Optional[FullXmlBarcodeScanThread] = None
        self.trial_list_thread: Optional[TrialListThread] = None
        self.lot_lookup_thread: Optional[ModelLotListThread] = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        form_box = QGroupBox(_("전체 XML Barcode 검색"))
        form = QFormLayout(form_box)
        self.model_in = QLineEdit(DEFAULT_MODEL)
        self.lot_in = QLineEdit()
        self.trial_path_in = QLineEdit()
        self.trial_path_in.setPlaceholderText("/Pixel/ResultData/Data/AFVI1/7M70-006/RX.../test_tony")
        restore_line_edit(self.model_in, "full_xml/model", DEFAULT_MODEL)
        restore_line_edit(self.lot_in, "full_xml/lot")
        restore_line_edit(self.trial_path_in, "full_xml/trial_path")

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
        self.force_xml=QCheckBox(_('XML 강제 다시 읽기 (캐시 무시)'))
        form.addRow(self.force_xml)
        layout.addWidget(form_box)

        row = QHBoxLayout()
        self.btn_scan = QPushButton(_("전체 XML Barcode 검색"))
        self.btn_save = QPushButton(_("JSON 저장"))
        self.btn_save.setEnabled(False)
        self.btn_scan.clicked.connect(self.start_scan)
        self.btn_save.clicked.connect(self.save_json)
        row.addWidget(self.btn_scan)
        row.addWidget(self.btn_save)
        row.addStretch()
        layout.addLayout(row)

        compare_box = QGroupBox(_("XML Barcode / StripID JSON 비교"))
        compare_form = QFormLayout(compare_box)
        self.compare_xml_json_in = QLineEdit()
        self.compare_strip_json_in = QLineEdit()
        restore_line_edit(self.compare_xml_json_in, "full_xml/compare_xml_json")
        restore_line_edit(self.compare_strip_json_in, "full_xml/compare_strip_json")

        compare_xml_row = QHBoxLayout()
        compare_xml_row.addWidget(self.compare_xml_json_in)
        btn_compare_xml = QPushButton(_("XML Barcode JSON 선택"))
        btn_compare_xml.clicked.connect(self.choose_compare_xml_json)
        compare_xml_row.addWidget(btn_compare_xml)

        compare_strip_row = QHBoxLayout()
        compare_strip_row.addWidget(self.compare_strip_json_in)
        btn_compare_strip = QPushButton(_("StripID JSON 선택"))
        btn_compare_strip.clicked.connect(self.choose_compare_strip_json)
        compare_strip_row.addWidget(btn_compare_strip)

        self.btn_compare_json = QPushButton(_("누락 StripID 비교"))
        self.btn_compare_json.clicked.connect(self.compare_json_files)
        compare_form.addRow("XML Barcode JSON", compare_xml_row)
        compare_form.addRow("StripID JSON", compare_strip_row)
        compare_form.addRow("", self.btn_compare_json)
        layout.addWidget(compare_box)

        self.result = QTextEdit()
        self.result.setReadOnly(True)
        layout.addWidget(self.result, stretch=1)

    def start_scan(self) -> None:
        model = self.model_in.text().strip()
        lot = self.lot_in.text().strip()
        trial_path = self.trial_path_in.text().strip()
        if not trial_path and (not model or not lot):
            QMessageBox.warning(self, _("입력 오류"), _("LOT 전체 검색은 모델명과 LOT가 필요합니다."))
            return
        save_line_edit(self.model_in, "full_xml/model")
        save_line_edit(self.lot_in, "full_xml/lot")
        save_line_edit(self.trial_path_in, "full_xml/trial_path")
        self.btn_scan.setEnabled(False)
        self.btn_save.setEnabled(False)
        self.result.clear()
        self.payload = None
        self.thread = FullXmlBarcodeScanThread(model, lot, trial_path)
        self.thread.bypass_xml_cache=self.force_xml.isChecked()
        self.thread.progress.connect(self.result.append)
        self.thread.finished.connect(self.on_finished)
        self.thread.failed.connect(self.on_failed)
        start_worker(self, self.thread)

    def auto_lot(self) -> None:
        lot_id = infer_lot_id_from_values(self.trial_path_in.text(), self.lot_in.text())
        if lot_id:
            self.lot_in.setText(lot_id)
            save_line_edit(self.lot_in, "full_xml/lot")
        else:
            self.load_lots()

    def load_lots(self) -> None:
        start_line_edit_lot_lookup(
            self,
            self.model_in.text(),
            self.lot_in,
            "full_xml/lot",
            [self.btn_lot_lookup],
            self.result.append,
        )

    def choose_compare_xml_json(self) -> None:
        path, _ext = QFileDialog.getOpenFileName(self, _("XML Barcode JSON 선택"), "", "JSON Files (*.json)")
        if path:
            self.compare_xml_json_in.setText(path)
            save_line_edit(self.compare_xml_json_in, "full_xml/compare_xml_json")

    def choose_compare_strip_json(self) -> None:
        path, _ext = QFileDialog.getOpenFileName(self, _("StripID JSON 선택"), "", "JSON Files (*.json)")
        if path:
            self.compare_strip_json_in.setText(path)
            save_line_edit(self.compare_strip_json_in, "full_xml/compare_strip_json")

    def compare_json_files(self) -> None:
        strip_json_path = Path(self.compare_strip_json_in.text().strip())
        xml_json_text = self.compare_xml_json_in.text().strip()
        if not strip_json_path.is_file():
            QMessageBox.warning(self, _("입력 오류"), _("StripID JSON 파일을 선택하세요."))
            return
        try:
            if xml_json_text:
                xml_json_path = Path(xml_json_text)
                if not xml_json_path.is_file():
                    QMessageBox.warning(self, _("입력 오류"), _("XML Barcode JSON 파일을 선택하세요."))
                    return
                full_xml_payload = json.loads(xml_json_path.read_text(encoding="utf-8-sig"))
            elif self.payload:
                full_xml_payload = self.payload
            else:
                QMessageBox.warning(self, _("입력 오류"), _("XML Barcode JSON 파일을 선택하거나 먼저 전체 XML Barcode 검색을 실행하세요."))
                return
            save_line_edit(self.compare_xml_json_in, "full_xml/compare_xml_json")
            save_line_edit(self.compare_strip_json_in, "full_xml/compare_strip_json")
            summary = compare_full_xml_barcode_with_strip_json(full_xml_payload, strip_json_path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, _("비교 실패"), str(exc))
            return
        self.show_compare_summary(summary)

    def show_compare_summary(self, summary: dict) -> None:
        lines = [
            f"LOT: {summary['lot_id']}",
            f"XML Barcode 전체 고유 ID: {summary['xml_all_count']}개",
            f"XML Barcode LOT 일치 StripID: {summary['xml_lot_count']}개",
            f"StripID JSON Strip: {summary['strip_json_count']}개",
            f"공통 StripID: {summary['matched_count']}개",
            f"XML Barcode에만 있는 StripID: {len(summary['xml_only'])}개",
            f"StripID JSON에만 있는 StripID: {len(summary['strip_json_only'])}개",
            f"LOT prefix 불일치/비-strip ID 제외: {len(summary['ignored_non_lot'])}개",
            "",
            _("[XML Barcode에만 있는 StripID]"),
            ", ".join(summary["xml_only"]) if summary["xml_only"] else "-",
            "",
            _("[StripID JSON에만 있는 StripID]"),
            ", ".join(summary["strip_json_only"]) if summary["strip_json_only"] else "-",
            "",
            _("[XML Barcode에만 있는 StripID 위치]"),
        ]
        if summary["xml_only"]:
            for strip_id in summary["xml_only"]:
                locations = summary["xml_only_locations"].get(strip_id, [])
                lines.append(f"{strip_id}: {len(locations)}건")
                for item in locations[:20]:
                    lines.append(
                        "  "
                        + " / ".join(
                            str(item.get(key, ""))
                            for key in ("machine", "trial", "xml_path")
                            if item.get(key)
                        )
                    )
                if len(locations) > 20:
                    lines.append(f"  ... {len(locations) - 20}건 생략")
        else:
            lines.append("-")
        if summary["ignored_non_lot"]:
            lines.extend(
                [
                    "",
                    _("[LOT prefix 불일치/비-strip ID 제외 상위 100개]"),
                    ", ".join(summary["ignored_non_lot"][:100]),
                ]
            )
        self.result.setPlainText("\n".join(lines))

    def load_trials(self) -> None:
        model = self.model_in.text().strip()
        lot = self.lot_in.text().strip()
        if not model or not lot:
            QMessageBox.warning(self, _("입력 오류"), _("Trial 목록은 모델명과 LOT가 필요합니다."))
            return
        save_line_edit(self.model_in, "full_xml/model")
        save_line_edit(self.lot_in, "full_xml/lot")
        self.btn_load_trials.setEnabled(False)
        self.trial_combo.clear()
        self.trial_combo.addItem(_("Trial 목록 로딩 중..."))
        self.trial_list_thread = TrialListThread(model, lot)
        self.trial_list_thread.finished.connect(self.on_trials_loaded)
        self.trial_list_thread.failed.connect(self.on_trials_failed)
        start_worker(self, self.trial_list_thread)

    def on_trials_loaded(self, paths: list[str]) -> None:
        self.btn_load_trials.setEnabled(True)
        self.trial_combo.blockSignals(True)
        self.trial_combo.clear()
        if paths:
            self.trial_combo.addItem(_("LOT 전체 검색"))
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
            save_line_edit(self.trial_path_in, "full_xml/trial_path")
        elif text == _("LOT 전체 검색"):
            self.trial_path_in.clear()
            save_line_edit(self.trial_path_in, "full_xml/trial_path")

    def on_finished(self, payload: dict) -> None:
        self.btn_scan.setEnabled(True)
        self.btn_save.setEnabled(True)
        self.payload = payload
        duplicate_preview = {
            strip_id: locations[:5]
            for strip_id, locations in list(payload["duplicates"].items())[:50]
        }
        summary = {
            "model": payload["model"],
            "lot_id": payload["lot_id"],
            "trial_count": payload["trial_count"],
            "xml_count": payload["xml_count"],
            "barcode_record_count": payload["barcode_record_count"],
            "no_barcode_xml_count": payload["no_barcode_xml_count"],
            "unique_strip_count": payload["unique_strip_count"],
            "duplicate_strip_count": payload["duplicate_strip_count"],
            "duplicate_preview": duplicate_preview,
        }
        self.result.setPlainText(json.dumps(summary, ensure_ascii=False, indent=2))

    def on_failed(self, message: str) -> None:
        self.btn_scan.setEnabled(True)
        QMessageBox.critical(self, _("전체 XML Barcode 검색 실패"), message)

    def save_json(self) -> None:
        if not self.payload:
            return
        lot = self.payload.get("lot_id", "full_xml_barcode")
        path, _ext = QFileDialog.getSaveFileName(self, _("전체 XML Barcode JSON 저장"), f"{lot}_full_xml_barcodes.json", "JSON Files (*.json)")
        if not path:
            return
        Path(path).write_text(json.dumps(self.payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.compare_xml_json_in.setText(path)
        save_line_edit(self.compare_xml_json_in, "full_xml/compare_xml_json")
        QMessageBox.information(self, _("저장 완료"), path)
