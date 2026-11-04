"""Inspection Stats Tab UI."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QFileDialog, QDateEdit,
    QFormLayout, QMessageBox, QLineEdit, QApplication,
    QGroupBox, QDialog, QTabWidget, QAbstractItemView,
)
from PySide6.QtCore import QDate, Qt

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font as XLFont
    _OPENPYXL_OK = True
except ImportError:
    _OPENPYXL_OK = False

from core.i18n import _
from ui.settings_helper import save_line_edit, restore_line_edit
from core.inspection_stats import gather_inspection_stats


# ── helpers ───────────────────────────────────────────────────────────────────

def _table_to_sheet(table: QTableWidget, ws, string_cols: set | None = None) -> None:
    """Write a QTableWidget into an openpyxl worksheet (row 1 = headers)."""
    string_cols = string_cols or set()
    col_count = table.columnCount()

    headers = [
        table.horizontalHeaderItem(c).text() if table.horizontalHeaderItem(c) else ""
        for c in range(col_count)
    ]
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=c, value=h)
        cell.font = XLFont(bold=True)

    for r in range(table.rowCount()):
        for c in range(col_count):
            item = table.item(r, c)
            val = item.text() if item else ""
            if c not in string_cols and val.lstrip("-").isdigit():
                val = int(val)
            ws.cell(row=r + 2, column=c + 1, value=val)


def _save_workbook(wb, parent, default_name: str) -> None:
    """Open a save-file dialog and persist the workbook."""
    path, _ = QFileDialog.getSaveFileName(
        parent, _("엑셀 저장"), default_name, "Excel Files (*.xlsx)"
    )
    if not path:
        return
    if not path.lower().endswith(".xlsx"):
        path += ".xlsx"
    try:
        wb.save(path)
        QMessageBox.information(parent, _("저장 완료"), _("엑셀 파일이 성공적으로 저장되었습니다."))
    except PermissionError:
        QMessageBox.critical(
            parent, _("저장 실패"),
            _("파일에 접근할 수 없습니다. 파일이 이미 열려있는지 확인하세요.\n") + path,
        )
    except Exception as e:
        import traceback
        QMessageBox.critical(parent, _("저장 오류"), f"{e}\n{traceback.format_exc()}")


# ── Detail dialog ─────────────────────────────────────────────────────────────

class _EmpDetailDialog(QDialog):
    """Per-file raw records for a single employee (IVS + VRS tabs)."""

    HEADERS = ["날짜", "파일 경로", "모델", "LOT", "타입", "Strip 수", "Unit 수", "불량 수", "불량 코드 요약"]

    def __init__(self, emp_id: str, ivs_records: list, vrs_records: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"검사원 {emp_id}  —  집계 상세 데이터")
        self.resize(1050, 600)

        layout = QVBoxLayout(self)

        summary = QLabel(
            f"IVS <b>{len(ivs_records):,}</b>건 &nbsp;|&nbsp; VRS <b>{len(vrs_records):,}</b>건"
        )
        layout.addWidget(summary)

        self._tabs = QTabWidget()
        self._ivs_table = self._build_table(ivs_records)
        self._vrs_table = self._build_table(vrs_records)
        self._tabs.addTab(self._ivs_table, f"IVS  ({len(ivs_records):,}건)")
        self._tabs.addTab(self._vrs_table, f"VRS  ({len(vrs_records):,}건)")
        layout.addWidget(self._tabs)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        if _OPENPYXL_OK:
            btn_xl = QPushButton(_("엑셀 내보내기"))
            btn_xl.clicked.connect(self._export_excel)
            btn_row.addWidget(btn_xl)
        close_btn = QPushButton(_("닫기"))
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _build_table(self, records: list) -> QTableWidget:
        table = QTableWidget(len(records), len(self.HEADERS))
        table.setHorizontalHeaderLabels(self.HEADERS)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.setSortingEnabled(False)  # disable while populating

        for i, rec in enumerate(records):
            table.setItem(i, 0, QTableWidgetItem(rec["date"]))
            table.setItem(i, 1, QTableWidgetItem(rec["file"]))
            table.setItem(i, 2, QTableWidgetItem(rec["model"]))
            table.setItem(i, 3, QTableWidgetItem(rec["lot"]))
            table.setItem(i, 4, QTableWidgetItem(rec["type"]))
            table.setItem(i, 5, QTableWidgetItem(str(rec["strip_count"])))
            table.setItem(i, 6, QTableWidgetItem(str(rec["unit_count"])))
            table.setItem(i, 7, QTableWidgetItem(str(rec["defect_count"])))
            table.setItem(i, 8, QTableWidgetItem(rec["defect_summary"]))

        table.setSortingEnabled(True)
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def _export_excel(self) -> None:
        wb = Workbook()
        ws_ivs = wb.active
        ws_ivs.title = "IVS"
        _table_to_sheet(self._ivs_table, ws_ivs)

        ws_vrs = wb.create_sheet("VRS")
        _table_to_sheet(self._vrs_table, ws_vrs)

        title = self.windowTitle().replace("  —  ", "_").replace(" ", "")
        _save_workbook(wb, self, f"{title}.xlsx")


# ── Main tab ──────────────────────────────────────────────────────────────────

class InspectionStatsTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._results: list = []
        self._build_ui()
        self._load_settings()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Date range
        date_layout = QHBoxLayout()
        date_layout.addWidget(QLabel(_("시작 날짜:")))
        self.start_date_in = QDateEdit()
        self.start_date_in.setCalendarPopup(True)
        self.start_date_in.setDisplayFormat("yyyy-MM-dd")
        self.start_date_in.setDate(QDate.currentDate().addDays(-7))
        date_layout.addWidget(self.start_date_in)
        date_layout.addWidget(QLabel(_("끝 날짜:")))
        self.end_date_in = QDateEdit()
        self.end_date_in.setCalendarPopup(True)
        self.end_date_in.setDisplayFormat("yyyy-MM-dd")
        self.end_date_in.setDate(QDate.currentDate())
        date_layout.addWidget(self.end_date_in)
        date_layout.addStretch()
        layout.addLayout(date_layout)

        # IVS Strip 경로 (110.15.136.x)
        ivs_config = QGroupBox(_("IVS Strip 경로 설정  (110.15.136.x)"))
        ivs_form = QFormLayout(ivs_config)
        self.ivs_ip_in = QLineEdit("108")
        self.ivs_path_in = QLineEdit("bdm_kinsus\\BDM_BACKUP")
        ivs_form.addRow(_("대상 IP (쉼표, 범위 지원)"), self.ivs_ip_in)
        ivs_form.addRow(_("베이스 경로"), self.ivs_path_in)
        layout.addWidget(ivs_config)

        # IVS Unit/BGA 경로 (192.168.0.x — unit 제품은 별도 대역)
        ivs_unit_config = QGroupBox(_("IVS Unit/BGA 경로 설정  (192.168.0.x)"))
        ivs_unit_form = QFormLayout(ivs_unit_config)
        self.ivs_unit_ip_in = QLineEdit("121-130")
        self.ivs_unit_path_in = QLineEdit("bdm_kinsus")
        ivs_unit_form.addRow(_("대상 IP (쉼표, 범위 지원)"), self.ivs_unit_ip_in)
        ivs_unit_form.addRow(_("베이스 경로"), self.ivs_unit_path_in)
        layout.addWidget(ivs_unit_config)

        # VRS path config
        vrs_config = QGroupBox(_("VRS 경로 설정  (192.168.0.x)"))
        vrs_form = QFormLayout(vrs_config)
        self.vrs_ip_in = QLineEdit("121-130")
        self.vrs_path_in = QLineEdit("VRSData\\ResultData\\BDM_KINSUS\\BDM_Backup")
        vrs_form.addRow(_("대상 IP (쉼표, 범위 지원)"), self.vrs_ip_in)
        vrs_form.addRow(_("베이스 경로"), self.vrs_path_in)
        layout.addWidget(vrs_config)

        # Action buttons
        action_layout = QHBoxLayout()
        self.btn_search = QPushButton(_("검색 및 집계"))
        self.btn_search.clicked.connect(self.calculate)
        action_layout.addWidget(self.btn_search)
        self.btn_export = QPushButton(_("엑셀 다운로드"))
        self.btn_export.clicked.connect(self.export_excel)
        if not _OPENPYXL_OK:
            self.btn_export.setEnabled(False)
            self.btn_export.setToolTip("openpyxl 라이브러리가 설치되지 않았습니다.")
        action_layout.addWidget(self.btn_export)
        action_layout.addStretch()
        layout.addLayout(action_layout)

        hint = QLabel(_("※ 행을 클릭하면 해당 검사원의 집계 상세 데이터를 확인할 수 있습니다."))
        hint.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(hint)

        # Result table — 10 columns
        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels([
            _("랭킹"), _("사원번호"), _("총 Strip"), _("총 Unit (BGA)"),
            _("연속 검사일수"), _("불량 수 (170제외)"),
            _("IVS Strip"), _("IVS Unit"), _("VRS Strip"), _("VRS Unit"),
        ])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.cellClicked.connect(self._on_row_clicked)
        layout.addWidget(self.table, stretch=1)

    def _load_settings(self) -> None:
        restore_line_edit(self.ivs_ip_in,      "inspection_stats/ivs_ip",       "108")
        restore_line_edit(self.ivs_path_in,    "inspection_stats/ivs_path",     "bdm_kinsus\\BDM_BACKUP")
        restore_line_edit(self.ivs_unit_ip_in, "inspection_stats/ivs_unit_ip",  "121-130")
        restore_line_edit(self.ivs_unit_path_in,"inspection_stats/ivs_unit_path","bdm_kinsus")
        restore_line_edit(self.vrs_ip_in,      "inspection_stats/vrs_ip",       "121-130")
        restore_line_edit(self.vrs_path_in,    "inspection_stats/vrs_path",     "VRSData\\ResultData\\BDM_KINSUS\\BDM_Backup")

    def _parse_ips(self, ip_str: str, mode: str) -> list:
        ips: list = []
        prefix = "192.168.0." if mode == "VRS" else "110.15.136."
        for part in [p.strip() for p in ip_str.split(",") if p.strip()]:
            if "-" not in part:
                ips.append(part if "." in part else f"{prefix}{part}")
                continue
            start, end = [p.strip() for p in part.split("-", 1)]
            base_ip = prefix
            if "." in start:
                base_ip = ".".join(start.split(".")[:-1]) + "."
                start = start.split(".")[-1]
            if "." in end:
                end = end.split(".")[-1]
            for value in range(int(start), int(end) + 1):
                ips.append(f"{base_ip}{value}")
        return sorted(set(ips))

    def _build_paths(self, ip_str: str, base_path: str, mode: str) -> list:
        sub = base_path.strip().strip("\\")
        return [Path(f"\\\\{ip}\\{sub}") for ip in self._parse_ips(ip_str, mode)]

    def calculate(self) -> None:
        save_line_edit(self.ivs_ip_in,       "inspection_stats/ivs_ip")
        save_line_edit(self.ivs_path_in,     "inspection_stats/ivs_path")
        save_line_edit(self.ivs_unit_ip_in,  "inspection_stats/ivs_unit_ip")
        save_line_edit(self.ivs_unit_path_in,"inspection_stats/ivs_unit_path")
        save_line_edit(self.vrs_ip_in,       "inspection_stats/vrs_ip")
        save_line_edit(self.vrs_path_in,     "inspection_stats/vrs_path")

        # Strip IVS (110.15.136.x) + Unit IVS (192.168.0.x) 합산
        ivs_paths = (
            self._build_paths(self.ivs_ip_in.text(),      self.ivs_path_in.text(),      "IVS")
            + self._build_paths(self.ivs_unit_ip_in.text(), self.ivs_unit_path_in.text(), "VRS")
        )
        vrs_paths = self._build_paths(self.vrs_ip_in.text(), self.vrs_path_in.text(), "VRS")

        start_dt = datetime.combine(self.start_date_in.date().toPython(), datetime.min.time())
        end_dt = datetime.combine(self.end_date_in.date().toPython(), datetime.max.time())

        self.btn_search.setText(_("집계 중..."))
        self.btn_search.setEnabled(False)
        QApplication.processEvents()

        try:
            results = gather_inspection_stats(ivs_paths, vrs_paths, start_dt, end_dt)
            self._results = results
            self.table.setRowCount(len(results))
            for i, row in enumerate(results):
                self.table.setItem(i, 0, QTableWidgetItem(f"{i + 1}"))
                self.table.setItem(i, 1, QTableWidgetItem(row["emp_id"]))
                self.table.setItem(i, 2, QTableWidgetItem(str(row["total_strips"])))
                self.table.setItem(i, 3, QTableWidgetItem(str(row["total_units"])))
                self.table.setItem(i, 4, QTableWidgetItem(str(row["consecutive_days"])))
                self.table.setItem(i, 5, QTableWidgetItem(str(row["defect_count"])))
                self.table.setItem(i, 6, QTableWidgetItem(str(row["ivs_strips"])))
                self.table.setItem(i, 7, QTableWidgetItem(str(row["ivs_units"])))
                self.table.setItem(i, 8, QTableWidgetItem(str(row["vrs_strips"])))
                self.table.setItem(i, 9, QTableWidgetItem(str(row["vrs_units"])))
            self.table.resizeColumnsToContents()
            QMessageBox.information(self, _("집계 완료"), _("선택된 기간 내의 검사 집계가 완료되었습니다."))
        except Exception as e:
            import traceback
            QMessageBox.critical(self, _("집계 오류"), f"{e}\n{traceback.format_exc()}")
        finally:
            self.btn_search.setText(_("검색 및 집계"))
            self.btn_search.setEnabled(True)

    def _on_row_clicked(self, row: int, _col: int) -> None:
        if row < 0 or row >= len(self._results):
            return
        data = self._results[row]
        dlg = _EmpDetailDialog(
            data["emp_id"],
            data.get("ivs_records", []),
            data.get("vrs_records", []),
            self,
        )
        dlg.exec()

    def export_excel(self) -> None:
        if not _OPENPYXL_OK:
            QMessageBox.critical(self, _("오류"), _("openpyxl 라이브러리가 필요합니다.\npip install openpyxl"))
            return
        if self.table.rowCount() == 0:
            QMessageBox.warning(self, _("경고"), _("내보낼 데이터가 없습니다. 먼저 집계를 수행해주세요."))
            return

        wb = Workbook()
        ws = wb.active
        ws.title = "Inspection Stats"
        _table_to_sheet(self.table, ws, string_cols={1})
        _save_workbook(wb, self, "검사_통계_리스트.xlsx")
