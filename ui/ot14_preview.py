"""Page through the OT14 coordinate CSV and original RPT rows."""
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from core.i18n import _


class Ot14Preview(QDialog):
    page_size = 200

    def __init__(self, rows, save_csv, parent):
        super().__init__(parent)
        self.setWindowTitle(_('OT14 좌표·RPT 원본 보기'))
        self.resize(1200, 700)
        self.rows = rows
        self.offset = 0
        layout = QVBoxLayout(self)
        filters = QHBoxLayout()
        self.kind = QComboBox()
        self.kind.addItem(_('OT14 반영 좌표'), 'final')
        self.kind.addItem(_('전체 대조 좌표'), 'OT14_COORDINATE')
        self.kind.addItem(_('RPT 원본 행'), 'RPT_RAW')
        self.strip_filter = QLineEdit()
        self.strip_filter.setPlaceholderText(_('StripID 검색'))
        filters.addWidget(self.kind)
        filters.addWidget(self.strip_filter)
        layout.addLayout(filters)

        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setProperty('dmt_literal_cells', True)
        layout.addWidget(self.table, 1)
        controls = QHBoxLayout()
        self.previous = QPushButton(_('이전'))
        self.next = QPushButton(_('다음'))
        self.status = QLabel()
        save = QPushButton(_('OT14 좌표·RPT 원본 CSV'))
        close = QPushButton(_('닫기'))
        for widget in (self.previous, self.status, self.next, save, close):
            controls.addWidget(widget)
        layout.addLayout(controls)

        self.kind.currentIndexChanged.connect(self.filter_rows)
        self.strip_filter.textChanged.connect(self.filter_rows)
        self.previous.clicked.connect(lambda: self.change_page(-self.page_size))
        self.next.clicked.connect(lambda: self.change_page(self.page_size))
        save.clicked.connect(save_csv)
        close.clicked.connect(self.reject)
        self.filter_rows()

    def filter_rows(self):
        kind = self.kind.currentData()
        strip = self.strip_filter.text().strip().upper()
        self.filtered = [
            row for row in self.rows
            if (not strip or strip in row['strip'].upper())
            and (row['row_type'] == kind or
                 (kind == 'final' and row['row_type'] == 'OT14_COORDINATE' and row['final_ot14'] == 1))
        ]
        self.offset = 0
        self.show_page()

    def change_page(self, delta):
        self.offset += delta
        self.show_page()

    def show_page(self):
        common = [('strip', 'StripID'), ('x', 'X'), ('y', 'Y'), ('final_ot14', _('OT14 반영'))]
        if self.kind.currentData() == 'RPT_RAW':
            fields = common + [('line_number', _('행 번호')), ('original_code', _('원본 코드')),
                               ('raw_line', _('RPT 원문')), ('parse_status', _('검증 상태')), ('source', _('원본 경로'))]
        else:
            fields = common + [('rpt_occurrences', _('RPT 출현 수')), ('duplicate_rows', _('중복 행 수')),
                               ('in_ski', _('SKI 포함')), ('parse_status', _('검증 상태')), ('source', _('원본 경로'))]
        page = self.filtered[self.offset:self.offset + self.page_size]
        self.table.clear()
        self.table.setColumnCount(len(fields))
        self.table.setHorizontalHeaderLabels([label for key, label in fields])
        self.table.setRowCount(len(page))
        for index, row in enumerate(page):
            for col, (key, label) in enumerate(fields):
                self.table.setItem(index, col, QTableWidgetItem(str(row.get(key, ''))))
        total = len(self.filtered)
        self.status.setText(f'{self.offset + 1 if total else 0}–{self.offset + len(page)} / {total}')
        self.previous.setEnabled(self.offset > 0)
        self.next.setEnabled(self.offset + len(page) < total)
