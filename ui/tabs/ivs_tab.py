"""IVS Analysis/Query tab."""
from __future__ import annotations
from core.i18n import _

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QByteArray
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QTextEdit, QVBoxLayout, QWidget,
)

from config import DEFAULT_MODEL
from core.models import DefectRecord
from core.ivs import build_summary, records_to_json, load_records_from_payload
from core.utils import infer_lot_id_from_values
from ui.settings_helper import restore_line_edit, save_line_edit
from ui.common_widgets import start_line_edit_lot_lookup
from ui.worker_lifetime import start_worker
from workers.ivs_workers import (
    IvsScanThread, StripDefectSearchThread, ImageLoaderThread, DownloadThread,
)
from workers.strip_workers import TrialListThread, ModelLotListThread


class IvsStudioTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[DefectRecord] = []
        self.filtered_records: list[DefectRecord] = []
        self.scan_thread: Optional[IvsScanThread] = None
        self.trial_list_thread: Optional[TrialListThread] = None
        self.lot_lookup_thread: Optional[ModelLotListThread] = None
        self.strip_search_thread: Optional[StripDefectSearchThread] = None
        self.loader_thread: Optional[ImageLoaderThread] = None
        self.download_thread: Optional[DownloadThread] = None
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        config_box = QGroupBox(_("IVS 스캔"))
        form = QFormLayout(config_box)
        self.model_in = QLineEdit(DEFAULT_MODEL)
        self.lot_in = QLineEdit()
        self.lot_in.setPlaceholderText(_("예: RX225E169100-"))
        self.trial_path_in = QLineEdit()
        self.trial_path_in.setPlaceholderText("/Pixel/ResultData/Data/AFVI1/7M70-006/RX.../test_tony")
        restore_line_edit(self.model_in, "ivs/model", DEFAULT_MODEL)
        restore_line_edit(self.lot_in, "ivs/lot")
        restore_line_edit(self.trial_path_in, "ivs/trial_path")
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
        self.units_per_strip_in = QLineEdit()
        self.units_per_strip_in.setPlaceholderText(_("모델 설정 또는 NAS 행·열에서 자동 확인"))
        self.restore_ivs_units()
        self.model_in.editingFinished.connect(self.restore_ivs_units)
        self.units_per_strip_in.editingFinished.connect(self.update_ivs_rates)
        form.addRow(_("스트립당 Unit 수"), self.units_per_strip_in)
        root.addWidget(config_box)

        buttons = QHBoxLayout()
        self.btn_scan = QPushButton(_("IVS 스캔"))
        self.btn_stop = QPushButton(_("중지"))
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_scan)
        self.btn_load = QPushButton(_("JSON 열기"))
        self.btn_save = QPushButton(_("JSON 저장"))
        self.btn_save.setEnabled(False)
        self.btn_download = QPushButton(_("불량 그룹 선택 이미지 다운로드"))
        self.btn_download.setEnabled(False)
        self.btn_scan.clicked.connect(self.start_scan)
        self.btn_load.clicked.connect(self.load_json)
        self.btn_save.clicked.connect(self.save_json)
        self.btn_download.clicked.connect(self.download_filtered)
        buttons.addWidget(self.btn_scan)
        buttons.addWidget(self.btn_stop)
        buttons.addWidget(self.btn_load)
        buttons.addWidget(self.btn_save)
        buttons.addWidget(self.btn_download)
        buttons.addStretch()
        root.addLayout(buttons)

        search_box = QGroupBox(_("StripID 불량 이미지 검색"))
        search_row = QHBoxLayout(search_box)
        self.strip_search_in = QLineEdit()
        self.strip_search_in.setPlaceholderText(_("예: RX1261062300-1E03"))
        self.defect_search_in = QLineEdit()
        self.defect_search_in.setPlaceholderText(_("불량명 필터 예: SM03, 170"))
        self.btn_strip_search = QPushButton(_("검색"))
        self.btn_strip_search.clicked.connect(self.search_strip_images)
        search_row.addWidget(QLabel("StripID"))
        search_row.addWidget(self.strip_search_in)
        search_row.addWidget(QLabel(_("불량명")))
        search_row.addWidget(self.defect_search_in)
        search_row.addWidget(self.btn_strip_search)
        root.addWidget(search_box)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setProperty('dmt_presentation', 'inspection-split')
        left = QWidget()
        left_lay = QVBoxLayout(left)
        self.summary_label = QLabel(_("대기 중"))
        self.summary_label.setWordWrap(True)
        left_lay.addWidget(self.summary_label)
        self.summary_table = QTableWidget(0, 4)
        self.summary_table.setHorizontalHeaderLabels([_("불량"), _("Unit 수"), _("불량 내 구성비"), _("단일 불량률 (전체 Unit 기준)")])
        self.summary_table.cellClicked.connect(self.open_defect_images)
        left_lay.addWidget(self.summary_table)
        left_lay.addWidget(QLabel(_("다운로드 불량 그룹 (불량율 높은 순)")))
        self.download_group_list = QListWidget()
        self.download_group_list.itemChanged.connect(self.on_download_group_changed)
        left_lay.addWidget(self.download_group_list)
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel(_("필터")))
        self.filter_combo = QComboBox()
        self.filter_combo.currentTextChanged.connect(self.apply_filter)
        filter_row.addWidget(self.filter_combo)
        left_lay.addLayout(filter_row)
        self.defect_list = QListWidget()
        self.defect_list.itemSelectionChanged.connect(self.on_record_selected)
        left_lay.addWidget(self.defect_list)
        navigation = QHBoxLayout()
        self.btn_image_prev = QPushButton(_("A 이전 이미지"))
        self.btn_image_next = QPushButton(_("D 다음 이미지"))
        self.btn_image_prev.clicked.connect(lambda: self.step_image(-1))
        self.btn_image_next.clicked.connect(lambda: self.step_image(1))
        navigation.addWidget(self.btn_image_prev)
        navigation.addWidget(self.btn_image_next)
        left_lay.addLayout(navigation)
        splitter.addWidget(left)

        right = QWidget()
        right.setProperty('dmt_presentation', 'inspection-images')
        right_lay = QVBoxLayout(right)
        image_row = QHBoxLayout()
        self.image_labels = []
        for title in ("Sub2", "Main", "Sub1"):
            label = QLabel(title)
            label.setProperty('dmt_image_slot', len(self.image_labels))
            label.setAlignment(Qt.AlignCenter)
            label.setMinimumSize(240, 220)
            label.setStyleSheet("border: 1px solid #777;")
            image_row.addWidget(label)
            self.image_labels.append(label)
        right_lay.addLayout(image_row)
        self.info = QTextEdit()
        self.info.setReadOnly(True)
        right_lay.addWidget(self.info)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, stretch=1)

    def start_scan(self) -> None:
        if self.scan_thread and self.scan_thread.isRunning():
            return
        model = self.model_in.text().strip()
        lot = self.lot_in.text().strip()
        trial_path = self.trial_path_in.text().strip()
        if not trial_path and (not model or not lot):
            QMessageBox.warning(self, _("입력 오류"), _("LOT 전체 스캔은 모델명과 LOT가 필요합니다."))
            return
        save_line_edit(self.model_in, "ivs/model")
        save_line_edit(self.lot_in, "ivs/lot")
        save_line_edit(self.trial_path_in, "ivs/trial_path")
        self.btn_scan.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_load.setEnabled(False)
        self.btn_save.setEnabled(False)
        self.btn_download.setEnabled(False)
        self._scan_stop_requested = False
        self.info.clear()
        self.records = []
        self.current_payload = None
        self.refresh_view()
        try:
            units = int(self.units_per_strip_in.text().strip() or 0)
            if units < 0:
                raise ValueError()
        except ValueError:
            self.finish_scan_controls()
            QMessageBox.warning(self, _("입력 오류"), _("스트립당 Unit 수는 양의 정수로 입력하세요."))
            return
        self.scan_thread = IvsScanThread(model, lot, trial_path, units)
        self.scan_thread.progress.connect(self.log)
        self.scan_thread.finished.connect(self.on_scan_finished)
        self.scan_thread.failed.connect(self.on_scan_failed)
        self.scan_thread.cancelled.connect(self.on_scan_cancelled)
        start_worker(self, self.scan_thread)

    def stop_scan(self) -> None:
        if self.scan_thread and self.scan_thread.isRunning():
            self._scan_stop_requested = True
            self.btn_stop.setEnabled(False)
            self.scan_thread.stop()
            self.log(_("IVS 수집 중지 요청 — NAS 연결을 정리하는 중입니다."))

    def finish_scan_controls(self) -> None:
        from PySide6.QtCore import QTimer
        self.btn_stop.setEnabled(False)
        if self.scan_thread and self.scan_thread.isRunning():
            QTimer.singleShot(50, self.finish_scan_controls)
            return
        self.btn_scan.setEnabled(True)
        self.btn_load.setEnabled(True)

    def on_scan_cancelled(self) -> None:
        self.finish_scan_controls()
        self.btn_save.setEnabled(False)
        self.btn_download.setEnabled(False)
        self.log(_("IVS 수집이 중지되었습니다. 부분 결과는 수율 계산에 연결하지 않습니다."))

    def auto_lot(self) -> None:
        lot_id = infer_lot_id_from_values(self.trial_path_in.text(), self.lot_in.text())
        if lot_id:
            self.lot_in.setText(lot_id)
            save_line_edit(self.lot_in, "ivs/lot")
        else:
            self.load_lots()

    def load_lots(self) -> None:
        start_line_edit_lot_lookup(
            self, self.model_in.text(), self.lot_in, "ivs/lot",
            [self.btn_lot_lookup], self.log,
        )

    def load_trials(self) -> None:
        model = self.model_in.text().strip()
        lot = self.lot_in.text().strip()
        if not model or not lot:
            QMessageBox.warning(self, _("입력 오류"), _("Trial 목록은 모델명과 LOT가 필요합니다."))
            return
        save_line_edit(self.model_in, "ivs/model")
        save_line_edit(self.lot_in, "ivs/lot")
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
            self.trial_combo.addItem(_("LOT 전체 스캔"))
            for path in paths:
                self.trial_combo.addItem(path)
        else:
            self.trial_combo.addItem(_("Trial 없음"))
        self.trial_combo.blockSignals(False)
        self.trial_path_in.clear()
        self.log(f"Trial {len(paths)}개 로드")

    def on_trials_failed(self, message: str) -> None:
        self.btn_load_trials.setEnabled(True)
        QMessageBox.critical(self, _("Trial 목록 실패"), message)

    def on_trial_selected(self, text: str) -> None:
        if text.startswith("/") or text.startswith("//") or text.startswith("\\\\"):
            self.trial_path_in.setText(text)
            save_line_edit(self.trial_path_in, "ivs/trial_path")
        elif text == _("LOT 전체 스캔"):
            self.trial_path_in.clear()
            save_line_edit(self.trial_path_in, "ivs/trial_path")

    def on_scan_finished(self, payload: dict) -> None:
        if getattr(self, '_scan_stop_requested', False):
            self.on_scan_cancelled()
            return
        self.finish_scan_controls()
        self.btn_save.setEnabled(True)
        self.records = load_records_from_payload(payload)
        self.current_payload = payload
        units = payload.get('population', {}).get('units_per_strip')
        if units:
            self.units_per_strip_in.setText(str(units))
        from core.yield_handoff import remember_payload
        remember_payload(payload, 'ivs_json')
        self.refresh_view()
        self.log(_("IVS 스캔 완료"))

    def on_scan_failed(self, message: str) -> None:
        self.finish_scan_controls()
        QMessageBox.critical(self, _("스캔 실패"), message)

    def load_json(self) -> None:
        path, _ext = QFileDialog.getOpenFileName(self, _("IVS JSON 열기"), "", "JSON Files (*.json)")
        if not path:
            return
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            self.records = load_records_from_payload(payload)
            self.current_payload = payload
            if payload.get('model'):
                self.model_in.setText(payload['model'])
            if payload.get('lot_id'):
                self.lot_in.setText(payload['lot_id'])
            self.restore_ivs_units()
            if payload.get('population', {}).get('units_per_strip'):
                self.units_per_strip_in.setText(str(payload['population']['units_per_strip']))
            if 'population' not in payload:
                # Older IVS JSONs contain only defect strips. A separately
                # completed StripID extraction can supply the full population.
                from core.yield_handoff import latest
                from core.ivs_population import population_payload
                from core.yield_calc import load_strip_id_json
                previous = latest()
                if previous.get('model') == str(payload.get('model', '')).upper() and previous.get('lot') == str(payload.get('lot_id', '')).upper():
                    source = previous.get('fields', {}).get('strip_json_in', {}).get('value')
                    if source and Path(source).is_file():
                        lot, ids = load_strip_id_json(Path(source))
                        if lot.upper() == str(payload.get('lot_id', '')).upper():
                            payload['population'] = population_payload(ids, int(self.units_per_strip_in.text() or 0))
            from core.yield_handoff import remember_payload
            remember_payload(payload, 'ivs_json')
            self.btn_save.setEnabled(True)
            self.refresh_view()
        except Exception as exc:
            QMessageBox.critical(self, _("JSON 오류"), str(exc))

    def save_json(self) -> None:
        if not self.records:
            QMessageBox.warning(self, _("저장 오류"), _("저장할 데이터가 없습니다. 먼저 스캔을 실행하세요."))
            return
        lot = self.records[0].lot if self.records else self.lot_in.text().strip()
        model = self.records[0].model if self.records else self.model_in.text().strip()
        path, _ext = QFileDialog.getSaveFileName(self, _("IVS JSON 저장"), f"{lot}_dmt_ivs.json", "JSON Files (*.json)")
        if not path:
            return
        try:
            payload = records_to_json(model, lot, self.records, (getattr(self, 'current_payload', None) or {}).get('population'))
            Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self.log(f"JSON 저장 완료: {path}")
            QMessageBox.information(self, _("저장 완료"), f"정상적으로 저장되었습니다:\n{path}")
        except Exception as exc:
            import traceback
            QMessageBox.critical(self, _("오류"), f"{str(exc)}\n{traceback.format_exc()}")

    def restore_ivs_units(self) -> None:
        from core.ivs_population import model_units
        units = model_units(self.model_in.text().strip())
        self.units_per_strip_in.setText(str(units) if units else '')

    def update_ivs_rates(self) -> None:
        from core.ivs_population import population_payload
        from ui.settings_helper import load_yield_model_settings, save_yield_model_settings
        try:
            units = int(self.units_per_strip_in.text().strip() or 0)
            if units < 0:
                raise ValueError()
        except ValueError:
            return
        model = self.model_in.text().strip()
        if units > 0 and model:
            data = load_yield_model_settings()
            data.setdefault(model, {})['units_per_strip'] = str(units)
            save_yield_model_settings(data)
        payload = getattr(self, 'current_payload', None) or {}
        original = payload.get('population')
        if original and str(payload.get('model', '')).upper() == model.upper():
            population = population_payload(original.get('strip_ids', []), units, original.get('nas_grid_units', []),
                                            original.get('missing_barcode_folders', 0), original.get('scope', 'lot'),
                                            original.get('parse_errors', 0))
            payload['population'] = population
            payload['summary'] = build_summary(self.records, population['total_units'])
            from core.yield_handoff import remember_payload
            remember_payload(payload, 'ivs_json')
        self.refresh_view()

    def refresh_view(self) -> None:
        population = (getattr(self, 'current_payload', None) or {}).get('population', {})
        total_units = population.get('total_units')
        # A numerator outside the verified population indicates inconsistent data.
        population_ids = set(population.get('strip_ids', []))
        if total_units and any(r.is_rate_defect and r.strip_id not in population_ids for r in self.records):
            total_units = None
        summary = build_summary(self.records, total_units)
        self.summary_label.setText(
            (_("전체 {total_records}건 | 불량 Unit {total_defect_units_excluding_rework_dummy}개 | ") +
             _("Rework(9998) {rework_9998_count}건 | Dummy {dummy_records}건 | Dummy 9999 {dummy_9999_count}건")).format(**summary)
        )
        if total_units:
            self.summary_label.setText(self.summary_label.text() + '\n' +
                (_("선택 Trial 전체 Unit") if population.get('scope') == 'trial' else _("LOT 전체 Unit")) +
                f": {total_units} ({population['strip_count']} × {population['units_per_strip']})")
        else:
            self.summary_label.setText(self.summary_label.text() + '\n' + _("전체 Strip 수·Unit 수 또는 NAS 행·열을 확인할 수 없어 단일 불량률을 표시하지 않습니다."))
            if population.get('unit_conflict'):
                self.summary_label.setText(self.summary_label.text() + '\n' + _("NAS 행·열과 설정된 Unit 수가 일치하지 않습니다.") +
                    f" NAS: {population.get('nas_grid_units', [])} / Unit: {population.get('units_per_strip', 0)}")
        rows = summary["by_defect_type"]
        self.summary_table.setRowCount(len(rows))
        for row, item in enumerate(rows):
            self.summary_table.setItem(row, 0, QTableWidgetItem(str(item["ivs_1"])))
            self.summary_table.setItem(row, 1, QTableWidgetItem(str(item["defect_units"])))
            self.summary_table.setItem(row, 2, QTableWidgetItem(f"{item['rate_percent']}%"))
            rate = item['single_defect_rate_percent']
            self.summary_table.setItem(row, 3, QTableWidgetItem(f"{rate:.6f}%" if rate is not None else '—'))
        self.summary_table.resizeColumnsToContents()
        self.refresh_download_groups(rows)
        self.filter_combo.blockSignals(True)
        self.filter_combo.clear()
        self.filter_combo.addItem(_("전체"))
        self.filter_combo.addItem(_("불량만(9998/Dummy 제외)"))
        self.filter_combo.addItem("Rework 9998")
        self.filter_combo.addItem("Dummy")
        self.filter_combo.addItem("Dummy 9999")
        for code in sorted({record.ivs_1 for record in self.records}):
            self.filter_combo.addItem(f"불량 {code}")
        self.filter_combo.blockSignals(False)
        self.apply_filter(self.filter_combo.currentText())
        self.btn_download.setEnabled(bool(self.records))

    def refresh_download_groups(self, rows: list[dict]) -> None:
        self.download_group_list.blockSignals(True)
        self.download_group_list.clear()
        for item in rows:
            code = str(item["ivs_1"])
            list_item = QListWidgetItem(f"{code} | Unit {item['defect_units']} | {item['rate_percent']}%")
            list_item.setData(Qt.UserRole, code)
            list_item.setFlags(list_item.flags() | Qt.ItemIsUserCheckable)
            list_item.setCheckState(Qt.Checked)
            self.download_group_list.addItem(list_item)
        self.download_group_list.blockSignals(False)

    def apply_filter(self, text: str) -> None:
        if text == _("불량만(9998/Dummy 제외)"):
            self.filtered_records = [r for r in self.records if r.is_rate_defect]
        elif text == "Rework 9998":
            self.filtered_records = [r for r in self.records if r.is_rework]
        elif text == "Dummy":
            self.filtered_records = [r for r in self.records if r.is_dummy]
        elif text == "Dummy 9999":
            self.filtered_records = [r for r in self.records if r.is_dummy_9999]
        elif text.startswith(_("불량 ")):
            code = text.split(" ", 1)[1]
            self.filtered_records = [r for r in self.records if r.ivs_1 == code]
        else:
            self.filtered_records = list(self.records)
        from posixpath import normpath
        grouped = {}
        for index, record in enumerate(self.filtered_records):
            paths = tuple(sorted({normpath(p.strip().replace("\\", "/")) for p in record.images if p.strip()}))
            key = ('images', paths) if paths else ('record', index)
            grouped.setdefault(key, []).append(record)
        self.image_record_groups = list(grouped.values())
        self.filtered_records = [group[0] for group in self.image_record_groups]
        self.defect_list.clear()
        for record in self.filtered_records:
            self.defect_list.addItem(QListWidgetItem(
                f"[{record.ivs_1}] {record.strip_id} {record.container} U{record.unit_idx} "
                f"{record.face} p{record.img_merge_idx} ({record.machine}/{record.trial}/{record.strip_folder})"
            ))

    def step_image(self, direction: int) -> None:
        count = self.defect_list.count()
        if not count:
            return
        row = self.defect_list.currentRow()
        target = (0 if direction > 0 else count - 1) if row < 0 else max(0, min(count - 1, row + direction))
        self.defect_list.setCurrentRow(target)
        self.defect_list.setProperty('dmt_image_page', target // 200 * 200)

    def search_strip_images(self) -> None:
        strip_id = self.strip_search_in.text().strip().upper()
        filters = [
            token.strip().upper()
            for token in re.split(r"[,;\s]+", self.defect_search_in.text())
            if token.strip()
        ]
        if not strip_id:
            QMessageBox.warning(self, _("입력 오류"), _("검색할 StripID를 입력하세요."))
            return
        local_matches = [
            record for record in self.records
            if record.strip_id.upper() == strip_id and self.record_matches_defect_filter(record, filters)
        ]
        if local_matches:
            self.show_search_records(local_matches, f"JSON/현재 데이터 검색: {len(local_matches)}개")
            return
        model = self.model_in.text().strip()
        lot = self.lot_in.text().strip()
        if not model or not lot:
            QMessageBox.warning(self, _("입력 오류"), _("NAS 검색을 위해 모델명과 LOT를 입력하세요."))
            return
        self.btn_strip_search.setEnabled(False)
        self.log(f"[NAS StripID 검색] {strip_id}")
        self.strip_search_thread = StripDefectSearchThread(model, lot, strip_id)
        self.strip_search_thread.progress.connect(self.log)
        self.strip_search_thread.finished.connect(lambda payload: self.on_strip_image_search_finished(payload, filters))
        self.strip_search_thread.failed.connect(self.on_strip_image_search_failed)
        start_worker(self, self.strip_search_thread)

    def record_matches_defect_filter(self, record: DefectRecord, filters: list[str]) -> bool:
        if not filters:
            return True
        haystack = " ".join([record.ivs_1, record.code_main, record.code_sub]).upper()
        return any(token in haystack for token in filters)

    def show_search_records(self, records: list[DefectRecord], message: str) -> None:
        self.filtered_records = records
        self.defect_list.clear()
        for record in self.filtered_records:
            self.defect_list.addItem(QListWidgetItem(
                f"[{record.ivs_1}] {record.strip_id} {record.container} U{record.unit_idx} "
                f"{record.face} p{record.img_merge_idx} ({record.machine}/{record.trial}/{record.strip_folder})"
            ))
        self.log(message)
        self.btn_download.setEnabled(bool(self.selected_download_group_codes()) and bool(self.records))
        if self.filtered_records:
            self.defect_list.setCurrentRow(0)

    def on_strip_image_search_finished(self, payload: dict, filters: list[str]) -> None:
        self.btn_strip_search.setEnabled(True)
        records = []
        for item in payload.get("nas_defects", []):
            try:
                records.append(DefectRecord(**item))
            except TypeError:
                continue
        records = [record for record in records if self.record_matches_defect_filter(record, filters)]
        self.show_search_records(records, f"NAS StripID 검색 완료: {len(records)}개")

    def on_strip_image_search_failed(self, message: str) -> None:
        self.btn_strip_search.setEnabled(True)
        QMessageBox.critical(self, _("StripID 검색 실패"), message)

    def on_download_group_changed(self) -> None:
        self.btn_download.setEnabled(bool(self.records))

    def selected_download_group_codes(self) -> set[str]:
        codes: set[str] = set()
        for index in range(self.download_group_list.count()):
            item = self.download_group_list.item(index)
            if item.checkState() == Qt.Checked:
                codes.add(str(item.data(Qt.UserRole)))
        return codes

    def selected_download_records(self) -> list[DefectRecord]:
        selected_codes = self.selected_download_group_codes()
        return [record for record in self.records if record.is_rate_defect and record.ivs_1 in selected_codes]

    def download_group_code_options(self) -> list[str]:
        rows = build_summary(self.records).get("by_defect_type", [])
        return [str(item["ivs_1"]) for item in rows if str(item.get("ivs_1", "")) not in {"", "0", "9998"}]

    def open_defect_images(self, row: int, column: int) -> None:
        item = self.summary_table.item(row, 0)
        if item is None:
            return
        self.filter_combo.setCurrentText(f"불량 {item.text()}")
        if self.defect_list.count():
            if self.defect_list.currentRow() == 0:
                self.on_record_selected()
            else:
                self.defect_list.setCurrentRow(0)

    def on_record_selected(self) -> None:
        from PySide6.QtCore import QTimer
        self._image_request = getattr(self, '_image_request', 0) + 1
        request = self._image_request
        for label in self.image_labels:
            label.setProperty('dmt_full_pixmap', None)
            label.clear()
            label.setText(_("이미지를 선택하세요"))
        row = self.defect_list.currentRow()
        if row < 0 or row >= len(self.filtered_records):
            return
        record = self.filtered_records[row]
        details = asdict(record)
        groups = getattr(self, 'image_record_groups', [])
        if row < len(groups) and len(groups[row]) > 1:
            details['image_record_count'] = len(groups[row])
            details['image_records'] = [asdict(item) for item in groups[row]]
        self.info.setPlainText(json.dumps(details, ensure_ascii=False, indent=2))
        if not record.images:
            for label in self.image_labels:
                label.setText(_("저장된 이미지 경로 없음"))
            return
        for label in self.image_labels:
            label.setText(_("이미지 불러오는 중…"))
        if self.loader_thread and self.loader_thread.isRunning():
            if not getattr(self, '_image_retry', False):
                self._image_retry = True
                def retry():
                    self._image_retry = False
                    if getattr(self, '_image_started_request', None) != self._image_request:
                        self.on_record_selected()
                QTimer.singleShot(100, retry)
            return
        from posixpath import normpath
        unique_paths = []
        seen_paths = set()
        for path in record.images:
            if not path.strip():
                continue
            key = normpath(path.strip().replace("\\", "/"))
            if key not in seen_paths:
                seen_paths.add(key)
                unique_paths.append(path)
        self.loader_thread = ImageLoaderThread(unique_paths)
        self._image_started_request = request
        self.loader_thread.finished.connect(lambda paths, images: self.on_images_loaded(paths, images) if request == self._image_request else None)
        self.loader_thread.failed.connect(lambda msg: self.on_image_load_failed(msg) if request == self._image_request else None)
        start_worker(self, self.loader_thread)

    def on_image_load_failed(self, message: str) -> None:
        for label in self.image_labels:
            label.setText(_("이미지 로드 실패 — NAS 연결과 경로를 확인하세요"))
        self.log(f"이미지 로드 실패: {message}")

    def on_images_loaded(self, paths: list[str], images: list[bytes]) -> None:
        for label in self.image_labels:
            label.setProperty('dmt_full_pixmap', None)
            label.clear()
            label.setText(_("이미지 없음"))
        for label, path, data in zip(self.image_labels, paths, images):
            label.clear()
            if not data:
                label.setText(f"이미지 없음\n{path}")
                continue
            pix = QPixmap()
            if pix.loadFromData(QByteArray(data)):
                label.setProperty('dmt_full_pixmap', pix)
                label.setPixmap(pix.scaled(label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                label.setText(f"디코딩 실패\n{path}")

    def download_filtered(self) -> None:
        checked_codes = self.selected_download_group_codes()
        options = [code for code in self.download_group_code_options() if not checked_codes or code in checked_codes]
        if not options:
            options = self.download_group_code_options()
        if not options:
            QMessageBox.warning(self, _("대상 없음"), _("다운로드할 불량 그룹이 없습니다."))
            return
        selected_code, ok = QInputDialog.getItem(
            self, _("다운로드 불량 그룹 선택"), _("다운로드할 불량 그룹"), options, 0, False,
        )
        if not ok or not selected_code:
            return
        records = [record for record in self.records if record.is_rate_defect and record.ivs_1 == selected_code]
        if not records:
            QMessageBox.warning(self, _("대상 없음"), f"{selected_code} 그룹에 다운로드할 이미지가 없습니다.")
            return
        save_dir = QFileDialog.getExistingDirectory(self, _("이미지 저장 폴더"))
        if not save_dir:
            return
        self.btn_download.setEnabled(False)
        self.download_thread = DownloadThread(records, save_dir, selected_code)
        self.download_thread.progress.connect(self.log)
        self.download_thread.finished.connect(self.on_download_finished)
        self.download_thread.failed.connect(self.on_download_failed)
        start_worker(self, self.download_thread)

    def on_download_finished(self, saved: int) -> None:
        self.btn_download.setEnabled(True)
        QMessageBox.information(self, _("다운로드 완료"), f"{saved}개 이미지 저장 완료")

    def on_download_failed(self, message: str) -> None:
        self.btn_download.setEnabled(True)
        QMessageBox.critical(self, _("다운로드 실패"), message)

    def log(self, message: str) -> None:
        self.info.append(message)
