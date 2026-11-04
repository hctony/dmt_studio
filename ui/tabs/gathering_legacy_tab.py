from PySide6.QtWidgets import QWidget, QVBoxLayout, QGroupBox, QFormLayout, QLineEdit, QPushButton, QHBoxLayout, QSplitter, QTableWidget, QTextEdit, QMessageBox, QTabWidget, QFileDialog, QTableWidgetItem
from core.i18n import _
import re
from collections import Counter
from pathlib import Path
from config import DEFAULT_MODEL
from core.utils import parse_excluded_strip_ids, infer_lot_id_from_values
from ui.common_widgets import start_line_edit_lot_lookup
from ui.settings_helper import restore_line_edit, save_line_edit
from workers.gather_workers import GatheringWorker


class GatheringLegacyTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.worker: Optional[GatheringWorker] = None
        self.search_thread: Optional[StripDefectSearchThread] = None
        self.loader_thread: Optional[ImageLoaderThread] = None
        self.output_text = ""
        self.summary: dict = {}
        self.ivs_image_records: list[dict] = []
        self.filtered_ivs_image_records: list[dict] = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        config = QGroupBox(_("게더링 집계"))
        form = QFormLayout(config)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["VRS", "FVI"])
        self.mode_combo.currentTextChanged.connect(self.on_mode_changed)
        self.ip_in = QLineEdit("121-130")
        self.path_in = QLineEdit("VRSData\\ResultData\\BDM_KINSUS\\BDM_Backup")
        self.model_in = QLineEdit(DEFAULT_MODEL)
        self.lot_in = QLineEdit()
        self.lot_in.setPlaceholderText(_("예: RX2262084400-"))
        self.x_count_in = QLineEdit("22")
        self.y_count_in = QLineEdit("4")
        self.allowance_in = QLineEdit("13")
        self.fill_code_in = QLineEdit("170")
        self.exclude_codes_in = QLineEdit()
        self.exclude_codes_in.setPlaceholderText(_("예: 9998,SM04,170"))
        self.exclude_json_in = QLineEdit()
        self.ivs_json_in = QLineEdit()
        self.ivs_json_in = QLineEdit()
        self.exclude_json_in.setPlaceholderText(_("JSON 또는 txt/csv: 한 줄/쉼표/공백 구분 StripID"))
        browse_json = QPushButton(_("JSON 선택"))
        browse_json.clicked.connect(self.choose_exclude_json)
        json_row = QHBoxLayout()
        json_row.addWidget(self.exclude_json_in)
        json_row.addWidget(browse_json)

        form.addRow(_("모드"), self.mode_combo)
        form.addRow(_("대상 IP"), self.ip_in)
        form.addRow(_("검색 베이스 경로"), self.path_in)
        form.addRow(_("모델명"), self.model_in)
        form.addRow("LOT", self.lot_in)
        form.addRow(_("X 좌표 수"), self.x_count_in)
        form.addRow(_("Y 좌표 수"), self.y_count_in)
        form.addRow(_("허용수"), self.allowance_in)
        form.addRow(_("전체처리 코드"), self.fill_code_in)
        form.addRow(_("제외 불량"), self.exclude_codes_in)
        form.addRow(_("제외 StripID JSON"), json_row)
        layout.addWidget(config)

        buttons = QHBoxLayout()
        self.btn_start = QPushButton(_("집계 시작"))
        self.btn_stop = QPushButton(_("중지"))
        self.btn_export = QPushButton(_("TXT 내보내기"))
        self.btn_stop.setEnabled(False)
        self.btn_export.setEnabled(False)
        self.btn_start.clicked.connect(self.start)
        self.btn_stop.clicked.connect(self.stop)
        self.btn_export.clicked.connect(self.export_txt)
        buttons.addWidget(self.btn_start)
        buttons.addWidget(self.btn_stop)
        buttons.addWidget(self.btn_export)
        buttons.addStretch()
        layout.addLayout(buttons)

        if False and self.mode == "IVS":
            image_box = QGroupBox(_("IVS 불량 이미지 조회"))
            image_layout = QVBoxLayout(image_box)
            filter_row = QHBoxLayout()
            self.ivs_strip_in = QLineEdit()
            self.ivs_strip_in.setPlaceholderText(_("예: RX1261062300-1E03"))
            self.ivs_defect_filter_in = QLineEdit()
            self.ivs_defect_filter_in.setPlaceholderText(_("불량명 필터 예: SM03, 170"))
            self.btn_ivs_image_search = QPushButton(_("이미지 검색"))
            self.btn_ivs_image_search.clicked.connect(self.search_ivs_images)
            filter_row.addWidget(QLabel("StripID"))
            filter_row.addWidget(self.ivs_strip_in)
            filter_row.addWidget(QLabel(_("불량명")))
            filter_row.addWidget(self.ivs_defect_filter_in)
            filter_row.addWidget(self.btn_ivs_image_search)
            image_layout.addLayout(filter_row)

            image_body = QHBoxLayout()
            self.ivs_image_list = QListWidget()
            self.ivs_image_list.itemSelectionChanged.connect(self.on_ivs_record_selected)
            image_body.addWidget(self.ivs_image_list, stretch=1)
            image_col = QVBoxLayout()
            self.ivs_image_labels = []
            for title in ("Sub2", "Main", "Sub1"):
                label = QLabel(title)
                label.setAlignment(Qt.AlignCenter)
                label.setMinimumSize(220, 160)
                label.setStyleSheet("border: 1px solid #777;")
                image_col.addWidget(label)
                self.ivs_image_labels.append(label)
            image_body.addLayout(image_col, stretch=2)
            image_layout.addLayout(image_body)
            layout.addWidget(image_box)

        splitter = QSplitter(Qt.Vertical)
        self.summary_table = QTableWidget(0, 2)
        self.summary_table.setHorizontalHeaderLabels([_("X-out 수"), _("Strip 수")])
        splitter.addWidget(self.summary_table)
        self.result = QTextEdit()
        self.result.setReadOnly(True)
        splitter.addWidget(self.result)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, stretch=1)

    def on_mode_changed(self, mode: str) -> None:
        if mode == "VRS":
            self.ip_in.setText("121-130")
            self.path_in.setText("VRSData\\ResultData\\BDM_KINSUS\\BDM_Backup")
        else:
            self.ip_in.setText("101-110")
            self.path_in.setText("FVIData\\ResultData\\BDM_KINSUS\\BDM_Backup\\{model}")

    def choose_exclude_json(self) -> None:
        path, _ext = QFileDialog.getOpenFileName(self, _("제외 StripID JSON 선택"), dialog_dir_for_file(self.exclude_json_in.text()), "JSON Files (*.json)")
        if path:
            self.exclude_json_in.setText(path)

    def start(self) -> None:
        try:
            x_count = int(self.x_count_in.text().strip())
            y_count = int(self.y_count_in.text().strip())
            allowance = int(self.allowance_in.text().strip())
        except ValueError:
            QMessageBox.warning(self, _("입력 오류"), _("X/Y 좌표 수와 허용수는 숫자여야 합니다."))
            return

        lot = self.lot_in.text().strip()
        if not lot:
            QMessageBox.warning(self, _("입력 오류"), _("LOT를 입력하세요."))
            return

        try:
            excluded_strip_ids = parse_excluded_strip_ids(self.exclude_json_in.text().strip())
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, _("제외 JSON 오류"), str(exc))
            return

        excluded_codes = {code.strip() for code in re.split(r"[,;\s]+", self.exclude_codes_in.text()) if code.strip()}
        excluded_codes.discard(self.fill_code_in.text().strip() or "170")
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_export.setEnabled(False)
        self.output_text = ""
        self.result.clear()
        self.summary_table.setRowCount(0)
        self.worker = GatheringWorker(
            mode=self.mode_combo.currentText(),
            ip_list_str=self.ip_in.text().strip(),
            base_path_template=self.path_in.text().strip(),
            model=self.model_in.text().strip(),
            lot=lot,
            x_count=x_count,
            y_count=y_count,
            allowance=allowance,
            fill_code=self.fill_code_in.text().strip() or "170",
            excluded_codes=excluded_codes,
            excluded_strip_ids=excluded_strip_ids,
        )
        self.worker.progress.connect(self.log)
        self.worker.finished.connect(self.on_finished)
        self.worker.failed.connect(self.on_failed)
        self.worker.start()

    def stop(self) -> None:
        if self.worker:
            self.worker.stop()

    def on_finished(self, strips: list[GatherStrip], output: str, summary: dict) -> None:
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_export.setEnabled(True)
        strips, output, summary = self.apply_duplicate_choices(strips, output, summary)
        self.output_text = output
        self.summary = summary
        self.result.setPlainText(
            _("집계 완료\n") +
            f"최종 Strip: {summary['total_strips']}개\n" +
            f"StripID JSON 제외: {summary['excluded_strip_count']}개\n" +
            f"허용수 초과 폐기/전체처리: {summary['discarded_strip_count']}개\n\n"
            + output[:20000]
        )
        if summary.get("parent_warnings"):
            QMessageBox.warning(
                self,
                _("게더링 누락 의심"),
                format_parent_warnings_text(summary.get("parent_warnings", []))[:6000],
            )
        distribution = summary["xout_distribution"]
        self.summary_table.setRowCount(len(distribution))
        for row, (xout_count, strip_count) in enumerate(distribution.items()):
            self.summary_table.setItem(row, 0, QTableWidgetItem(str(xout_count)))
            self.summary_table.setItem(row, 1, QTableWidgetItem(str(strip_count)))
        self.summary_table.resizeColumnsToContents()

    def on_failed(self, message: str) -> None:
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        QMessageBox.critical(self, _("집계 실패"), message)

    def search_ivs_images(self) -> None:
        if self.mode != "IVS":
            return
        strip_id = self.ivs_strip_in.text().strip().upper()
        model = self.parent_tab.model_in.text().strip()
        lot = self.parent_tab.lot_in.text().strip()
        if not strip_id:
            QMessageBox.warning(self, _("입력 오류"), _("이미지를 조회할 StripID를 입력하세요."))
            return
        if not model or not lot:
            QMessageBox.warning(self, _("입력 오류"), _("공통 설정의 모델명과 LOT를 입력하세요."))
            return
        self.btn_ivs_image_search.setEnabled(False)
        self.ivs_image_list.clear()
        for label in self.ivs_image_labels:
            label.clear()
        self.result.append(f"[IVS 이미지 검색] {strip_id}")
        self.search_thread = StripDefectSearchThread(model, lot, strip_id)
        self.search_thread.progress.connect(self.log)
        self.search_thread.finished.connect(self.on_ivs_image_search_finished)
        self.search_thread.failed.connect(self.on_ivs_image_search_failed)
        self.search_thread.start()

    def on_ivs_image_search_finished(self, payload: dict) -> None:
        self.btn_ivs_image_search.setEnabled(True)
        records = list(payload.get("nas_defects", []))
        self.ivs_image_records = records
        self.refresh_ivs_image_records()
        self.result.append(f"[IVS 이미지 검색 완료] 원본 {len(records)}개 / 표시 {len(self.filtered_ivs_image_records)}개")

    def on_ivs_image_search_failed(self, message: str) -> None:
        self.btn_ivs_image_search.setEnabled(True)
        QMessageBox.critical(self, _("IVS 이미지 검색 실패"), message)

    def refresh_ivs_image_records(self) -> None:
        filters = [
            token.strip().upper()
            for token in re.split(r"[,;\s]+", self.ivs_defect_filter_in.text())
            if token.strip()
        ]
        self.filtered_ivs_image_records = []
        for record in self.ivs_image_records:
            haystack = " ".join(
                str(record.get(key, "")).upper()
                for key in ("ivs_1", "code_main", "code_sub")
            )
            if filters and not any(token in haystack for token in filters):
                continue
            self.filtered_ivs_image_records.append(record)

        self.ivs_image_list.blockSignals(True)
        self.ivs_image_list.clear()
        for record in self.filtered_ivs_image_records:
            item = QListWidgetItem(
                f"{record.get('ivs_1', '')} {record.get('code_main', '')}/{record.get('code_sub', '')} "
                f"U{record.get('unit_idx', '')} {record.get('face', '')} "
                f"X:{record.get('strip_pt_x', '')} Y:{record.get('strip_pt_y', '')} "
                f"{record.get('machine', '')}/{record.get('trial', '')}/{record.get('strip_folder', '')}"
            )
            self.ivs_image_list.addItem(item)
        self.ivs_image_list.blockSignals(False)
        if self.filtered_ivs_image_records:
            self.ivs_image_list.setCurrentRow(0)

    def on_ivs_record_selected(self) -> None:
        if self.mode != "IVS":
            return
        row = self.ivs_image_list.currentRow()
        if row < 0 or row >= len(self.filtered_ivs_image_records):
            return
        record = self.filtered_ivs_image_records[row]
        images = [str(path) for path in record.get("images", []) if str(path)]
        self.result.append(
            f"[IVS 이미지 선택] {record.get('ivs_1', '')} {record.get('code_main', '')}/{record.get('code_sub', '')} "
            f"U{record.get('unit_idx', '')} X:{record.get('strip_pt_x', '')} Y:{record.get('strip_pt_y', '')}"
        )
        if not images:
            for label in self.ivs_image_labels:
                label.setText(_("이미지 경로 없음"))
            return
        self.loader_thread = ImageLoaderThread(images)
        self.loader_thread.finished.connect(self.on_ivs_images_loaded)
        self.loader_thread.failed.connect(lambda msg: self.result.append(f"IVS 이미지 로드 실패: {msg}"))
        self.loader_thread.start()

    def on_ivs_images_loaded(self, paths: list[str], images: list[bytes]) -> None:
        for label, path, data in zip(self.ivs_image_labels, paths, images):
            label.clear()
            if not data:
                label.setText(f"이미지 없음\n{path}")
                continue
            pix = QPixmap()
            if pix.loadFromData(QByteArray(data)):
                label.setPixmap(pix.scaled(label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                label.setText(f"디코드 실패\n{path}")

    def export_txt(self) -> None:
        if not self.output_text:
            QMessageBox.warning(self, _("대상 없음"), _("먼저 집계를 실행하세요."))
            return
        lot = self.lot_in.text().strip()
        path, _ext = QFileDialog.getSaveFileName(
            self,
            _("게더링 TXT 저장"),
            f"Gather_{self.mode_combo.currentText()}_{lot}.txt",
            "Text Files (*.txt)",
        )
        if not path:
            return
        Path(path).write_text(self.output_text, encoding="utf-8")
        QMessageBox.information(self, _("저장 완료"), path)

    def log(self, message: str) -> None:
        self.result.append(message)
