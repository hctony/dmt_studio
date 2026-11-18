"""Search the durable A-PC operator archive without rereading factory shares."""
from datetime import datetime
from pathlib import Path, PureWindowsPath

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QComboBox, QPushButton, QTableWidget, QTableWidgetItem,
    QTextEdit, QMessageBox, QFileDialog, QAbstractItemView)
from core.i18n import _
from core import inspection_history as history


class InspectionHistoryTab(QWidget):
    def __init__(self):
        super().__init__()
        self.rows = []
        layout = QVBoxLayout(self)
        note = QLabel(_('집계 시점의 원본을 보존합니다. 이력이 없으면 미검사가 아니라 미수집일 수 있습니다. 파일명 시각과 수정 시각을 구분해 확인하세요.'))
        note.setWordWrap(True); layout.addWidget(note)
        filters = QHBoxLayout()
        self.lot_in = QLineEdit(); self.strip_in = QLineEdit(); self.operator_in = QLineEdit()
        self.mode_in = QComboBox(); self.mode_in.addItem(_('전체 공정'), '')
        for stage in ('IVS','VRS','FVI'):self.mode_in.addItem(stage,stage)
        for label, widget in [('LOT', self.lot_in), ('StripID', self.strip_in),
                              (_('작업자'), self.operator_in), (_('공정'), self.mode_in)]:
            filters.addWidget(QLabel(label)); filters.addWidget(widget)
        self.btn_search = QPushButton(_('검색')); self.btn_search.clicked.connect(self.search)
        filters.addWidget(self.btn_search); layout.addLayout(filters)
        self.status = QLabel(); layout.addWidget(self.status)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels([_('공정'), 'LOT', 'StripID', _('작업자'),
            _('파일명 검사 시각'), _('파일 수정 시각'), _('불량 라인 수'), _('원본 경로')])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self.show_original)
        layout.addWidget(self.table, 2)
        self.detail = QTextEdit(); self.detail.setReadOnly(True); layout.addWidget(self.detail, 2)
        self.btn_export = QPushButton(_('선택 이력의 원본 TXT 저장'))
        self.btn_export.clicked.connect(self.export_original); layout.addWidget(self.btn_export)
        for widget in [self.lot_in, self.strip_in, self.operator_in]:
            widget.returnPressed.connect(self.search)

    def search(self):
        try:
            self.rows = history.search(self.lot_in.text(), self.strip_in.text(),
                                       self.operator_in.text(), self.mode_in.currentData(), 501)
            more = len(self.rows) > 500; self.rows = self.rows[:500]
            self.table.clearSelection(); self.detail.clear()
            self.table.setRowCount(len(self.rows))
            for index, row in enumerate(self.rows):
                values = [row['mode'], row['lot'], row['strip'], row['operator'] or _('확인 불가'),
                    row['inspection_time'] or _('확인 불가'),
                    datetime.fromtimestamp(row['source_mtime_ns'] / 1e9).isoformat(' ', timespec='seconds'),
                    row['defects'], row['source_path']]
                for col, value in enumerate(values):
                    self.table.setItem(index, col, QTableWidgetItem(str(value)))
            self.status.setText(_('조회 건수') + ': ' + str(len(self.rows)) +
                (' · ' + _('500건 초과: StripID 또는 작업자로 범위를 좁히세요.') if more else ''))
            if self.rows: self.table.selectRow(0)
        except Exception as error:
            self.rows = []; self.table.setRowCount(0); self.detail.clear()
            QMessageBox.warning(self, _('검색 실패'), _('LOT, StripID 또는 작업자를 입력하세요.') if str(error)=='LOT_OR_STRIP_REQUIRED' else str(error))

    def selected(self):
        row = self.table.currentRow()
        if not self.table.selectedItems() or not 0 <= row < len(self.rows):
            raise ValueError(_('이력을 선택하세요.'))
        return history.original(self.rows[row]['id'])

    def show_original(self):
        if not self.table.selectedItems(): return
        try:
            row, body, block = self.selected()
            first = datetime.fromtimestamp(row['first_seen']).isoformat(' ', timespec='seconds')
            self.detail.setPlainText(_('원본 경로') + ': ' + row['source_path'] + '\nSHA256: ' + row['hash'] +
                '\n' + _('최초 수집 시각') + ': ' + first + '\n' + _('시각 근거') + ': ' + _(history.TIME_LABELS.get(row['time_basis'],row['time_basis'])) +
                '\n' + _('해당 Strip 원본 블록') + f" ({row['start_line']}–{row['end_line']})\n\n" + block)
        except Exception as error:
            self.detail.setPlainText(str(error))

    def export_original(self):
        try:
            row, body, block = self.selected()
            target, _filter = QFileDialog.getSaveFileName(self, _('선택 이력의 원본 TXT 저장'),
                PureWindowsPath(row['source_path']).name, 'TXT (*.txt)')
            if target:
                # Save the full, byte-identical source, not the merged output.
                Path(target).write_bytes(body)
                QMessageBox.information(self, _('저장 완료'), target)
        except Exception as error:
            QMessageBox.warning(self, _('저장 실패'), str(error))
