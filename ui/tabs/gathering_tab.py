"""Gathering collection tabs and dialogs."""
from __future__ import annotations
from core.i18n import _

import re
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QRadioButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from config import DEFAULT_MODEL
from core.gather import build_gather_output, clone_gather_strip, gather_strip_from_payload, make_full_t_lines, merge_gather_strips
from core.models import GatherStrip
from core.utils import infer_lot_id_from_values, parse_excluded_strip_ids
from ui.common_widgets import start_line_edit_lot_lookup
from ui.settings_helper import restore_line_edit, save_line_edit
from workers.gather_workers import GatheringWorker
from workers.ivs_workers import ImageLoaderThread, StripDefectSearchThread
from workers.strip_workers import ModelLotListThread


class GatherDuplicateDialog(QDialog):
    def __init__(self, conflict: dict, index: int, total: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"중복 Strip 선택 {index}/{total} - {conflict.get('strip_id', '')}")
        self.resize(980, 720)
        self.choice = "auto"
        layout = QVBoxLayout(self)
        title = QLabel(f"StripID: {conflict.get('strip_id', '')}")
        title.setWordWrap(True)
        layout.addWidget(title)

        body = QTextEdit()
        body.setReadOnly(True)
        body.setPlainText(self.format_conflict(conflict))
        layout.addWidget(body, stretch=1)

        self.radio_auto = QRadioButton(_("자동 선택 유지"))
        self.radio_current = QRadioButton(_("기존 데이터 선택"))
        self.radio_candidate = QRadioButton(_("신규 데이터 선택"))
        self.radio_merge = QRadioButton(_("두 데이터 Merge"))
        self.radio_auto.setChecked(True)
        radio_row = QHBoxLayout()
        for radio in (self.radio_auto, self.radio_current, self.radio_candidate, self.radio_merge):
            radio_row.addWidget(radio)
        layout.addLayout(radio_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self) -> None:  # type: ignore[override]
        if self.radio_current.isChecked():
            self.choice = "current"
        elif self.radio_candidate.isChecked():
            self.choice = "candidate"
        elif self.radio_merge.isChecked():
            self.choice = "merge"
        else:
            self.choice = "auto"
        super().accept()

    def format_conflict(self, conflict: dict) -> str:
        current = conflict.get("current", {})
        candidate = conflict.get("candidate", {})
        lines = [
            _("[기존 데이터]"),
            f"좌표 {current.get('coord_count', 0)}개 / 라인 {current.get('line_count', 0)}개",
            f"소스: {', '.join(current.get('sources', [])) or '-'}",
            f"SKI 점수: {conflict.get('current_score')}",
            "\n".join(current.get("defect_lines", [])[:120]) or "-",
            "",
            _("[신규 데이터]"),
            f"좌표 {candidate.get('coord_count', 0)}개 / 라인 {candidate.get('line_count', 0)}개",
            f"소스: {', '.join(candidate.get('sources', [])) or '-'}",
            f"SKI 점수: {conflict.get('candidate_score')}",
            "\n".join(candidate.get("defect_lines", [])[:120]) or "-",
            "",
            _("[좌표 차이]"),
            f"공통: {', '.join(conflict.get('common', [])[:80]) or '-'}",
            f"기존에만: {', '.join(conflict.get('current_only', [])[:80]) or '-'}",
            f"신규에만: {', '.join(conflict.get('candidate_only', [])[:80]) or '-'}",
            "",
            _("[보조 뷰 - BBT/Skipdata 좌표]"),
            ", ".join(conflict.get("bbt_coords", [])[:160]) or "-",
            "",
            _("[보조 뷰 - 최종 SKI 좌표]"),
            ", ".join(conflict.get("ski_coords", [])[:160]) or "-",
        ]
        return "\n".join(lines)


class GatherReviewDialog(QDialog):
    def __init__(self, plan, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("중복 Strip 개별 검수"))
        self.resize(1150, 800)
        self.plan = plan
        self.choices = [entry['recommended'] for entry in plan]
        layout = QVBoxLayout(self)
        self.overview = QLabel()
        layout.addWidget(self.overview)
        layout.addWidget(QLabel(_("목록에서 원하는 Strip만 선택해 검수하세요. 좌표가 같아도 불량 코드가 다르면 확인이 필요합니다.")))
        self.strip_list = QListWidget()
        self.strip_list.currentRowChanged.connect(self.show_strip)
        layout.addWidget(self.strip_list)
        self.candidate_combo = QComboBox()
        self.candidate_combo.currentIndexChanged.connect(self.choose)
        layout.addWidget(self.candidate_combo)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        layout.addWidget(self.details)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        for entry in plan:
            self.strip_list.addItem(entry['strip_id'])
        self.refresh_status()
        self.strip_list.setCurrentRow(next((i for i, c in enumerate(self.choices) if c is None), 0))

    def refresh_status(self):
        pending = sum(c is None for c in self.choices)
        self.overview.setText(_("전체 / 확인 필요") + f": {len(self.plan)} / {pending}")
        for i, entry in enumerate(self.plan):
            status = _("검수 필요") if self.choices[i] is None else entry['reason'] if self.choices[i] == entry['recommended'] else _("개별 선택 완료")
            self.strip_list.item(i).setText(f"{entry['strip_id']} | {status}")

    def show_strip(self, row):
        if row < 0:
            return
        entry = self.plan[row]
        self.candidate_combo.blockSignals(True)
        self.candidate_combo.clear()
        self.candidate_combo.addItem(_("선택하세요"), None)
        self.candidate_combo.addItem(_("기존 자동 결과 유지 (개별 확인)"), 'auto')
        for i, payload in enumerate(entry['candidates']):
            name = _("Merge 후보") if i >= entry['original_count'] else _("데이터 후보") + f" {i+1}"
            exact = set(payload['coords']) | set(entry['bbt_coords']) == set(entry['ski_coords']) if entry['ski_coords'] else False
            self.candidate_combo.addItem(f"{name} | " + (_("SKI 좌표 일치") if exact else _("SKI 미일치 또는 없음")), i)
        self.candidate_combo.setCurrentIndex(self.candidate_combo.findData(self.choices[row]))
        self.candidate_combo.blockSignals(False)
        self.show_details(row)

    def choose(self, index):
        row = self.strip_list.currentRow()
        if row < 0:
            return
        self.choices[row] = self.candidate_combo.currentData()
        self.refresh_status()
        self.show_details(row)

    def show_details(self, row):
        entry = self.plan[row]
        choice = self.choices[row]
        payload = entry['candidates'][choice if isinstance(choice, int) else 0]
        combined = set(payload['coords']) | set(entry['bbt_coords'])
        ski = set(entry['ski_coords'])
        text = _("판정 근거") + ': ' + _(entry['reason']) + '\n'
        text += _("최종 SKI 대비 누락 좌표") + ': ' + (', '.join(sorted(ski-combined)) or '-') + '\n'
        text += _("최종 SKI 외 추가 좌표") + ': ' + (', '.join(sorted(combined-ski)) or '-') + '\n'
        conflict = dict(entry, current=entry['candidates'][0], candidate=payload)
        current_coords = set(conflict['current']['coords'])
        candidate_coords = set(payload['coords'])
        conflict.update(common=sorted(current_coords & candidate_coords), current_only=sorted(current_coords-candidate_coords), candidate_only=sorted(candidate_coords-current_coords))
        def score(coords):
            if not ski:
                return None
            missing, extra = len((ski-set(entry['bbt_coords']))-coords), len(coords-(ski-set(entry['bbt_coords'])))
            return (missing+extra, extra, missing, abs(len(coords)-len(ski-set(entry['bbt_coords']))))
        conflict.update(current_score=score(current_coords), candidate_score=score(candidate_coords))
        text += GatherDuplicateDialog.format_conflict(self, conflict)
        if not ski:
            text = _("최종 SKI 좌표가 없어 일치 여부를 검증할 수 없습니다.") + '\n' + text
        self.details.setPlainText(text)

    def accept(self):
        if any(choice is None for choice in self.choices):
            QMessageBox.information(self, _("검수 필요"), _("확인이 필요한 Strip을 선택해 검수하거나 기존 자동 결과 유지를 명시적으로 선택하세요."))
            return
        super().accept()


class ReworkDateDialog(QDialog):
    def __init__(self, dates: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("재작업 일자 선택"))
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(_("데이터에 여러 날짜가 포함되어 있습니다.\n시작할 기준 일자를 선택하세요.\n선택한 일자 이전의 데이터는 집계에서 제외됩니다.")))
        self.combo = QComboBox()
        self.combo.addItems(dates)
        layout.addWidget(self.combo)
        
        btn_layout = QHBoxLayout()
        btn_ok = QPushButton(_("선택 적용"))
        btn_all = QPushButton(_("전체 적용 (필터 없음)"))
        btn_ok.clicked.connect(self.accept)
        btn_all.clicked.connect(self.reject)
        btn_layout.addWidget(btn_ok)
        btn_layout.addWidget(btn_all)
        layout.addLayout(btn_layout)

    def get_selected_date(self) -> str:
        return self.combo.currentText()


class BatchProgressDialog(QDialog):
    def __init__(self, modes: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("공정 일괄 추출 진행 현황"))
        self.resize(600, 400)
        layout = QVBoxLayout(self)
        
        self.lbl_status = QLabel(_("준비 중..."))
        layout.addWidget(self.lbl_status)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, len(modes))
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)
        
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        layout.addWidget(self.log_edit)
        
        self.btn_close = QPushButton(_("닫기"))
        self.btn_close.setEnabled(False)
        self.btn_close.clicked.connect(self.accept)
        layout.addWidget(self.btn_close)
        
    def set_current_mode(self, idx: int, total: int, mode: str) -> None:
        self.lbl_status.setText(f"[{idx}/{total}] {mode} " + _("공정 집계 및 추출 진행 중..."))
        self.progress_bar.setValue(idx - 1)
        
    def append_log(self, text: str) -> None:
        self.log_edit.append(text)
        
    def finish(self, total: int) -> None:
        self.lbl_status.setText(_("모든 공정 일괄 추출 완료!"))
        self.progress_bar.setValue(total)
        self.btn_close.setEnabled(True)


class GatheringModePage(QWidget):
    def __init__(self, mode: str, parent_tab: "GatheringTab") -> None:
        super().__init__()
        self.mode = mode
        self.parent_tab = parent_tab
        self.worker: Optional[GatheringWorker] = None
        self.output_text = ""
        self.summary: dict = {}
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        config = QGroupBox(f"{self.mode} 경로 설정")
        form = QFormLayout(config)
        if self.mode == "VRS":
            default_ip = "121-130"
        elif self.mode == "FVI":
            default_ip = "101-110"
        else:
            default_ip = "108"
        if self.mode == "VRS":
            default_path = "VRSData\\ResultData\\BDM_KINSUS\\BDM_Backup"
        elif self.mode == "FVI":
            default_path = "bdm_fkinsus\\BDM_Backup"
        else:
            default_path = "bdm_kinsus\\BDM_BACKUP"
        self.ip_in = QLineEdit(default_ip)
        self.path_in = QLineEdit(default_path)
        restore_line_edit(self.ip_in, f"gathering/{self.mode}/ip", default_ip)
        restore_line_edit(self.path_in, f"gathering/{self.mode}/path", default_path)
        form.addRow(_("대상 IP"), self.ip_in)
        form.addRow(_("검색 베이스 경로"), self.path_in)
        layout.addWidget(config)

        buttons = QHBoxLayout()
        self.btn_start = QPushButton(f"{self.mode} 집계 시작")
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

        splitter = QSplitter(Qt.Vertical)
        self.summary_table = QTableWidget(0, 2)
        self.summary_table.setHorizontalHeaderLabels([_("X-out 수"), _("Strip 수")])
        splitter.addWidget(self.summary_table)
        self.status_table = QTableWidget(0, 4)
        self.status_table.setHorizontalHeaderLabels([_("경로"), _("상태"), _("TXT 수"), _("매칭 Strip")])
        splitter.addWidget(self.status_table)
        self.result = QTextEdit()
        self.result.setReadOnly(True)
        splitter.addWidget(self.result)
        splitter.setStretchFactor(2, 2)
        layout.addWidget(splitter, stretch=1)

    def start(self) -> None:
        config = self.parent_tab.read_config()
        if not config:
            return
        config["ip_list_str"] = self.ip_in.text().strip()
        config["base_path_template"] = self.path_in.text().strip()
        save_line_edit(self.ip_in, f"gathering/{self.mode}/ip")
        save_line_edit(self.path_in, f"gathering/{self.mode}/path")
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_export.setEnabled(False)
        self.output_text = ""
        self.result.clear()
        self.summary_table.setRowCount(0)
        self.status_table.setRowCount(0)
        self.worker = GatheringWorker(mode=self.mode, **config)
        self.worker.progress.connect(self.log)
        self.worker.finished.connect(self.on_finished)
        self.worker.failed.connect(self.on_failed)
        self.worker.date_selection_required.connect(self.on_date_selection_required)
        self.worker.start()

    def on_date_selection_required(self, dates: list[str]) -> None:
        dialog = ReworkDateDialog(dates, self)
        if dialog.exec() == QDialog.Accepted:
            selected = dialog.get_selected_date()
            from datetime import datetime
            timestamp = datetime.strptime(selected, "%Y-%m-%d").timestamp()
            self.worker.set_rework_date(timestamp)
        else:
            self.worker.set_rework_date(0.0)

    def stop(self) -> None:
        if self.worker:
            self.worker.stop()

    def on_finished(self, strips: list[GatherStrip], output: str, summary: dict) -> None:
        strips, output, summary = self.apply_duplicate_choices(strips, output, summary)
        if summary.get('duplicate_review_cancelled'):
            self.btn_start.setEnabled(True)
            self.btn_stop.setEnabled(False)
            self.btn_export.setEnabled(False)
            self.result.setPlainText(_("중복 검수가 취소되어 결과를 저장하지 않았습니다."))
            return
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_export.setEnabled(True)
        self.output_text = output
        self.summary = summary
        warning_text = self.format_parent_warnings(summary.get("parent_warnings", []))
        cache = summary.get("cache", {})
        self.result.setPlainText(
            f"{self.mode} 집계 완료\n"
            f"최종 Strip: {summary['total_strips']}개\n"
            f"StripID JSON 제외: {summary['excluded_strip_count']}개\n"
            f"허용수 초과 폐기/전체처리: {summary['discarded_strip_count']}개\n"
            f"중복 Strip 최신 데이터 갱신: {summary.get('replaced_strip_count', 0)}개\n"
            + _("중복 Strip 자동 선택 / 개별 선택") + f": {summary.get('auto_duplicate_selection_count', 0)} / {summary.get('manual_duplicate_selection_count', 0)}\n"
            + f"캐시: 파일 {cache.get('file_entries', 0)}개 / 경로 {cache.get('txt_list_entries', 0)}개\n"
            f"{warning_text}\n"
            + output[:20000]
        )
        self.refresh_summary(summary)

    def apply_duplicate_choices(
        self,
        strips: list[GatherStrip],
        output: str,
        summary: dict,
    ) -> tuple[list[GatherStrip], str, dict]:
        conflicts = summary.get("duplicate_conflicts", [])
        if not conflicts:
            return strips, output, summary
        from core.duplicate_review import build_review_plan
        eligible = {strip.strip_id.upper() for strip in strips}
        plan = build_review_plan([c for c in conflicts if c['strip_id'].upper() in eligible])
        if not plan:
            return strips, output, summary
        dialog = GatherReviewDialog(plan, self)
        if dialog.exec() != QDialog.Accepted:
            return strips, output, dict(summary, duplicate_review_cancelled=True)
        strip_map = {strip.strip_id.upper(): clone_gather_strip(strip) for strip in strips}
        changed = 0
        auto_count = 0
        decisions = []
        for entry, choice in zip(plan, dialog.choices):
            decisions.append(dict(strip_id=entry['strip_id'], choice=choice, reason=entry['reason']))
            if choice == 'auto':
                continue
            selected = gather_strip_from_payload(entry['candidates'][choice])
            selected = self.prepare_manual_selected_strip(selected)
            strip_map[selected.strip_id.upper()] = selected
            changed += 1
            auto_count += int(choice == entry['recommended'])
        if not changed:
            return strips, output, dict(summary, duplicate_review_decisions=decisions)
        final = sorted(strip_map.values(), key=lambda item: item.strip_id)
        new_output = build_gather_output(final, self.parent_tab.lot_in.text().strip())
        xout_counter = Counter(len(item.defect_lines) for item in final)
        new_summary = dict(summary)
        new_summary["manual_duplicate_selection_count"] = changed - auto_count
        new_summary["auto_duplicate_selection_count"] = auto_count
        new_summary["duplicate_review_decisions"] = decisions
        new_summary["total_strips"] = len(final)
        new_summary["discarded_strip_count"] = sum(item.xout_applied for item in final)
        new_summary["xout_distribution"] = dict(sorted(xout_counter.items()))
        return final, new_output, new_summary

    def prepare_manual_selected_strip(self, item: GatherStrip) -> GatherStrip:
        excluded_codes = {code.strip() for code in re.split(r"[,;\s]+", self.parent_tab.exclude_codes_in.text()) if code.strip()}
        fill_code = self.parent_tab.fill_code_in.text().strip() or "170"
        excluded_codes.discard(fill_code)
        item.defect_lines = [line for line in item.defect_lines if line.split(",")[-1].strip() not in excluded_codes]
        try:
            allowance = int(self.parent_tab.allowance_in.text().strip())
            x_count = int(self.parent_tab.x_count_in.text().strip())
            y_count = int(self.parent_tab.y_count_in.text().strip())
        except ValueError:
            return item
        if len(item.defect_lines) > allowance:
            item.defect_lines = make_full_t_lines(self.parent_tab.lot_in.text().strip(), item.strip_id, x_count, y_count, fill_code, item.defect_lines)
            item.xout_applied = True
        return item

    def format_parent_warnings(self, warnings: list[dict]) -> str:
        if not warnings:
            return ""
        lines = ["", _("[게더링 누락 점검] 지정 베이스 경로 밖 상위 폴더에서 LOT 데이터가 발견된 TXT:")]
        for warning in warnings:
            lines.append(f"- 기준 경로: {warning['path']} ({warning.get('scope', _('상위 경로'))})")
            lines.extend(f"  {path}" for path in warning["parent_hits"])
        return "\n".join(lines)

    def refresh_summary(self, summary: dict) -> None:
        distribution = summary["xout_distribution"]
        self.summary_table.setRowCount(len(distribution))
        for row, (xout_count, strip_count) in enumerate(distribution.items()):
            self.summary_table.setItem(row, 0, QTableWidgetItem(str(xout_count)))
            self.summary_table.setItem(row, 1, QTableWidgetItem(str(strip_count)))
        self.summary_table.resizeColumnsToContents()

        status_rows = summary.get("status", [])
        self.status_table.setRowCount(len(status_rows))
        for row, status in enumerate(status_rows):
            self.status_table.setItem(row, 0, QTableWidgetItem(str(status["path"])))
            self.status_table.setItem(row, 1, QTableWidgetItem(str(status["status"])))
            self.status_table.setItem(row, 2, QTableWidgetItem(str(status["txt_files"])))
            self.status_table.setItem(row, 3, QTableWidgetItem(str(status["matched_strips"])))
        self.status_table.resizeColumnsToContents()

    def on_failed(self, message: str) -> None:
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        QMessageBox.critical(self, f"{self.mode} 집계 실패", message)

    def export_txt(self) -> None:
        if not self.output_text:
            QMessageBox.warning(self, _("대상 없음"), _("먼저 집계를 실행하세요."))
            return
        lot = self.parent_tab.lot_in.text().strip()
        path, _ext = QFileDialog.getSaveFileName(
            self,
            _("게더링 TXT 저장"),
            f"Gather_{self.mode}_{lot}.txt",
            "Text Files (*.txt)",
        )
        if not path:
            return
        Path(path).write_text(self.output_text, encoding="utf-8")
        QMessageBox.information(self, _("저장 완료"), path)

    def log(self, message: str) -> None:
        self.result.append(message)


class GatheringTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.lot_lookup_thread: Optional[ModelLotListThread] = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        config = QGroupBox(_("게더링 공통 설정"))
        form = QFormLayout(config)
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
        self.exclude_json_in.setPlaceholderText(_("StripID 추출 JSON 경로"))
        self.skipdata_in = QLineEdit()
        self.ski_in = QLineEdit()
        restore_line_edit(self.model_in, "gathering/model", DEFAULT_MODEL)
        restore_line_edit(self.lot_in, "gathering/lot")
        restore_line_edit(self.x_count_in, "gathering/x_count", "22")
        restore_line_edit(self.y_count_in, "gathering/y_count", "4")
        restore_line_edit(self.allowance_in, "gathering/allowance", "13")
        restore_line_edit(self.fill_code_in, "gathering/fill_code", "170")
        restore_line_edit(self.exclude_codes_in, "gathering/exclude_codes")
        restore_line_edit(self.exclude_json_in, "gathering/exclude_json")
        restore_line_edit(self.skipdata_in, "gathering/skipdata")
        restore_line_edit(self.ski_in, "gathering/ski")
        browse_json = QPushButton(_("JSON 선택"))
        browse_json.clicked.connect(self.choose_exclude_json)
        json_row = QHBoxLayout()
        json_row.addWidget(self.exclude_json_in)
        json_row.addWidget(browse_json)
        skipdata_row = QHBoxLayout()
        skipdata_row.addWidget(self.skipdata_in)
        btn_skipdata = QPushButton(_("BBT/Skipdata 폴더"))
        btn_skipdata.clicked.connect(self.choose_skipdata)
        skipdata_row.addWidget(btn_skipdata)
        ski_row = QHBoxLayout()
        ski_row.addWidget(self.ski_in)
        btn_ski = QPushButton(_("SKI 파일"))
        btn_ski.clicked.connect(self.choose_ski)
        ski_row.addWidget(btn_ski)
        lot_row = QHBoxLayout()
        lot_row.addWidget(self.lot_in)
        btn_lot_auto = QPushButton(_("LOT 자동"))
        btn_lot_auto.clicked.connect(self.auto_lot)
        lot_row.addWidget(btn_lot_auto)
        self.btn_lot_lookup = QPushButton(_("LOT 검색"))
        self.btn_lot_lookup.clicked.connect(self.load_lots)
        lot_row.addWidget(self.btn_lot_lookup)
        self.lot_in.editingFinished.connect(self.on_lot_changed_smart)
        form.addRow(_("모델명"), self.model_in)
        form.addRow("LOT", lot_row)
        form.addRow(_("X 좌표 수"), self.x_count_in)
        form.addRow(_("Y 좌표 수"), self.y_count_in)
        form.addRow(_("허용수"), self.allowance_in)
        form.addRow(_("전체처리 코드"), self.fill_code_in)
        form.addRow(_("제외 불량"), self.exclude_codes_in)
        form.addRow(_("제외 StripID JSON"), json_row)
        form.addRow(_("BBT/Skipdata 폴더"), skipdata_row)
        form.addRow(_("최종 SKI 파일"), ski_row)
        layout.addWidget(config)
        
        batch_group = QGroupBox(_("공정 일괄 추출"))
        batch_layout = QHBoxLayout(batch_group)
        self.chk_vrs = QCheckBox("VRS")
        self.chk_fvi = QCheckBox("FVI")
        self.chk_ivs = QCheckBox("IVS")
        self.chk_vrs.setChecked(True)
        self.chk_fvi.setChecked(True)
        self.chk_ivs.setChecked(True)
        
        self.btn_batch_export = QPushButton(_("일괄 추출 실행"))
        self.btn_batch_export.clicked.connect(self.run_batch_export)
        
        batch_layout.addWidget(self.chk_vrs)
        batch_layout.addWidget(self.chk_fvi)
        batch_layout.addWidget(self.chk_ivs)
        batch_layout.addStretch()
        batch_layout.addWidget(self.btn_batch_export)
        
        layout.addWidget(batch_group)

        tabs = QTabWidget()
        tabs.addTab(GatheringModePage("VRS", self), "VRS")
        tabs.addTab(GatheringModePage("FVI", self), "FVI")
        tabs.addTab(GatheringModePage("IVS", self), "IVS")
        layout.addWidget(tabs, stretch=1)

    def choose_exclude_json(self) -> None:
        path, _ext = QFileDialog.getOpenFileName(self, _("제외 StripID JSON 선택"), "", "JSON Files (*.json)")
        if path:
            self.exclude_json_in.setText(path)
            save_line_edit(self.exclude_json_in, "gathering/exclude_json")

    def choose_skipdata(self) -> None:
        path = QFileDialog.getExistingDirectory(self, _("BBT/Skipdata 폴더 선택"))
        if path:
            self.skipdata_in.setText(path)
            save_line_edit(self.skipdata_in, "gathering/skipdata")

    def choose_ski(self) -> None:
        path, _ext = QFileDialog.getOpenFileName(self, _("최종 SKI 파일 선택"), "", "SKI Files (*.ski);;All Files (*.*)")
        if path:
            self.ski_in.setText(path)
            save_line_edit(self.ski_in, "gathering/ski")

    def auto_lot(self) -> None:
        lot_id = infer_lot_id_from_values(self.exclude_json_in.text(), self.lot_in.text())
        if lot_id:
            self.lot_in.setText(lot_id)
            save_line_edit(self.lot_in, "gathering/lot")
            self.on_lot_changed_smart()
        else:
            self.load_lots()

    def load_lots(self) -> None:
        start_line_edit_lot_lookup(
            self,
            self.model_in.text(),
            self.lot_in,
            "gathering/lot",
            [self.btn_lot_lookup],
            None,
        )

    def get_exclude_strip_ids(self) -> set[str]:
        json_path = self.exclude_json_in.text().strip()
        if not json_path:
            return set()
        return parse_excluded_strip_ids(json_path)
        
    def run_batch_export(self) -> None:
        modes = []
        if self.chk_vrs.isChecked(): modes.append("VRS")
        if self.chk_fvi.isChecked(): modes.append("FVI")
        if self.chk_ivs.isChecked(): modes.append("IVS")
        if not modes:
            QMessageBox.warning(self, _("선택 없음"), _("일괄 추출할 공정을 하나 이상 선택하세요."))
            return
            
        lot = self.lot_in.text().strip()
        if not lot:
            QMessageBox.warning(self, _("입력 오류"), _("LOT 번호를 입력하세요."))
            return
            
        out_dir = QFileDialog.getExistingDirectory(self, _("저장 폴더 선택"))
        if not out_dir:
            return
            
        self.batch_modes = modes
        self.batch_out_dir = Path(out_dir)
        self.batch_current_idx = 0
        self.btn_batch_export.setEnabled(False)
        
        self.batch_dialog = BatchProgressDialog(modes, self)
        self.batch_dialog.show()
        
        self._run_next_batch()

    def _run_next_batch(self) -> None:
        if self.batch_current_idx >= len(self.batch_modes):
            self.batch_dialog.finish(len(self.batch_modes))
            self.btn_batch_export.setEnabled(True)
            return
            
        mode = self.batch_modes[self.batch_current_idx]
        self.batch_dialog.set_current_mode(self.batch_current_idx + 1, len(self.batch_modes), mode)
        
        config = self.read_config()
        if not config:
            self.btn_batch_export.setEnabled(True)
            return
            
        from ui.settings_helper import setting_text
        default_ip = "121-130" if mode == "VRS" else "101-110" if mode == "FVI" else "108"
        default_path = (
            "VRSData\\ResultData\\BDM_KINSUS\\BDM_Backup"
            if mode == "VRS"
            else "bdm_fkinsus\\BDM_Backup"
            if mode == "FVI"
            else "bdm_kinsus\\BDM_BACKUP"
        )
        config["ip_list_str"] = setting_text(f"gathering/{mode}/ip", default_ip)
        config["base_path_template"] = setting_text(f"gathering/{mode}/path", default_path)
        
        self.batch_worker = GatheringWorker(mode=mode, **config)
        self.batch_worker.progress.connect(self.batch_dialog.append_log)
        self.batch_worker.finished.connect(self._on_batch_finished)
        self.batch_worker.failed.connect(self._on_batch_failed)
        self.batch_worker.date_selection_required.connect(self._on_batch_date_selection_required)
        self.batch_worker.start()
        
    def _on_batch_date_selection_required(self, dates: list[str]) -> None:
        mode = self.batch_modes[self.batch_current_idx]
        dialog = ReworkDateDialog(dates, self)
        dialog.setWindowTitle(f"{mode} - " + _("재작업 일자 선택"))
        if dialog.exec() == QDialog.Accepted:
            selected = dialog.get_selected_date()
            from datetime import datetime
            timestamp = datetime.strptime(selected, "%Y-%m-%d").timestamp()
            self.batch_worker.set_rework_date(timestamp)
        else:
            self.batch_worker.set_rework_date(0.0)

    def _on_batch_finished(self, strips: list, output: str, summary: dict) -> None:
        mode = self.batch_modes[self.batch_current_idx]
        for page in self.findChildren(GatheringModePage):
            if page.mode == mode:
                strips, output, summary = page.apply_duplicate_choices(strips, output, summary)
                break
        if summary.get('duplicate_review_cancelled'):
            self.batch_dialog.append_log(_("중복 검수가 취소되어 결과를 저장하지 않았습니다."))
            self.btn_batch_export.setEnabled(True)
            return
        lot = self.lot_in.text().strip()
        out_file = self.batch_out_dir / f"Gather_{mode}_{lot}.txt"
        out_file.write_text(output, encoding="utf-8")
        
        self.batch_dialog.append_log(f"★ {mode} 공정 추출 완료: {out_file}\n")
        self.batch_current_idx += 1
        self._run_next_batch()
        
    def _on_batch_failed(self, msg: str) -> None:
        mode = self.batch_modes[self.batch_current_idx]
        self.batch_dialog.append_log(f"❌ {mode} 공정 추출 실패:\n{msg}\n")
        QMessageBox.critical(self, _("일괄 추출 오류"), f"{mode} 추출 중 오류 발생:\n{msg}")
        self.btn_batch_export.setEnabled(True)

    def on_lot_changed_smart(self) -> None:
        lot = self.lot_in.text().strip()
        if not lot:
            return
        from core.utils import auto_find_bbt_skipdata_path, auto_find_ski_file_path
        path = auto_find_bbt_skipdata_path(lot)
        if path:
            self.skipdata_in.setText(path)
            from ui.settings_helper import save_line_edit, save_setting_text
            save_line_edit(self.skipdata_in, "gathering/skipdata")
            save_setting_text("yield/skipdata", path)
            
        ski_path = auto_find_ski_file_path(lot)
        if ski_path:
            self.ski_in.setText(ski_path)
            # Only save to settings to globally share with YieldTab
            try:
                from ui.settings_helper import save_line_edit, save_setting_text
                save_line_edit(self.ski_in, "gathering/ski")
                save_setting_text("yield/ski", ski_path)
            except ImportError:
                pass

    def read_config(self) -> Optional[dict]:
        try:
            x_count = int(self.x_count_in.text().strip())
            y_count = int(self.y_count_in.text().strip())
            allowance = int(self.allowance_in.text().strip())
        except ValueError:
            QMessageBox.warning(self, _("입력 오류"), _("X/Y 좌표 수와 허용수는 숫자여야 합니다."))
            return None
        lot = self.lot_in.text().strip()
        if not lot:
            QMessageBox.warning(self, _("입력 오류"), _("LOT를 입력하세요."))
            return None
        try:
            excluded_strip_ids = parse_excluded_strip_ids(self.exclude_json_in.text().strip())
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, _("제외 JSON 오류"), str(exc))
            return None
        excluded_codes = {code.strip() for code in re.split(r"[,;\s]+", self.exclude_codes_in.text()) if code.strip()}
        excluded_codes.discard(self.fill_code_in.text().strip() or "170")
        save_line_edit(self.model_in, "gathering/model")
        save_line_edit(self.lot_in, "gathering/lot")
        save_line_edit(self.x_count_in, "gathering/x_count")
        save_line_edit(self.y_count_in, "gathering/y_count")
        save_line_edit(self.allowance_in, "gathering/allowance")
        save_line_edit(self.fill_code_in, "gathering/fill_code")
        save_line_edit(self.exclude_codes_in, "gathering/exclude_codes")
        save_line_edit(self.exclude_json_in, "gathering/exclude_json")
        save_line_edit(self.skipdata_in, "gathering/skipdata")
        save_line_edit(self.ski_in, "gathering/ski")
        return {
            "model": self.model_in.text().strip(),
            "lot": lot,
            "x_count": x_count,
            "y_count": y_count,
            "allowance": allowance,
            "fill_code": self.fill_code_in.text().strip() or "170",
            "excluded_codes": excluded_codes,
            "excluded_strip_ids": excluded_strip_ids,
            "skipdata_path": self.skipdata_in.text().strip(),
            "ski_path": self.ski_in.text().strip(),
        }
