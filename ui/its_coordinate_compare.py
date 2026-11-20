"""Compare the captured SKI, RPT and process TXT for each discrepant Strip."""
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QDialog, QGroupBox, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout

from core.gather import format_xy_coords, parse_xy_from_gather_line
from core.i18n import _


class ItsCoordinateCompare(QDialog):
    def __init__(self, summary, rows, strip_id, parent):
        super().__init__(parent)
        self.setWindowTitle(_('불일치 좌표 확인'))
        self.setProperty('dmt_presentation', 'its-coordinate-compare')
        self.setModal(True)
        self.resize(1200, 760)
        self.trace = summary['report_trace']
        self.lot = summary['lot_id']
        self.rows = rows
        self.scrap_rows = {}
        for row in summary.get('whole_strip_review', []):
            self.scrap_rows.setdefault(row['strip_id'], []).append(row)
        self.index = next((index for index, row in enumerate(rows) if row['strip_id'] == strip_id), 0)

        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.previous = QPushButton(_('이전 (A)'))
        self.next = QPushButton(_('다음 (D)'))
        self.previous.setProperty('dmt_presentation', 'its-compare-previous')
        self.next.setProperty('dmt_presentation', 'its-compare-next')
        self.position = QLabel()
        self.previous.clicked.connect(lambda: self.step(-1))
        self.next.clicked.connect(lambda: self.step(1))
        close = QPushButton(_('닫기'))
        close.clicked.connect(self.reject)
        for control in (self.previous, self.next, self.position):
            controls.addWidget(control)
        controls.addStretch()
        controls.addWidget(close)
        layout.addLayout(controls)

        panels = QHBoxLayout()
        self.ski_view = self.add_panel(panels, _('최종 SKI · Skipdata 좌표'))
        self.txt_view = self.add_panel(panels, _('SKI에 반영되지 않은 공정 TXT 원문'))
        layout.addLayout(panels, 1)
        self.previous_key = QShortcut(QKeySequence('A'), self)
        self.next_key = QShortcut(QKeySequence('D'), self)
        self.previous_key.activated.connect(self.previous.click)
        self.next_key.activated.connect(self.next.click)
        self.refresh()

    @staticmethod
    def add_panel(layout, title):
        panel = QGroupBox(title)
        box = QVBoxLayout(panel)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setProperty('dmt_literal_text', True)
        box.addWidget(text)
        layout.addWidget(panel, 1)
        return text

    @staticmethod
    def coordinate_lines(label, coords):
        return [f"{label}: {len(coords)}", ', '.join(format_xy_coords(coords)) or '-']

    def step(self, direction):
        index = self.index + direction
        if 0 <= index < len(self.rows):
            self.index = index
            self.refresh()

    def refresh(self):
        row = self.rows[self.index]
        strip = row['strip_id']
        info = self.trace['strips'].get(strip, {})
        ski = {tuple(coord) for coord in info.get('ski_coords', [])}
        bbt = {tuple(coord) for coord in info.get('bbt_coords', [])}
        ski_only = {tuple(coord) for coord in row.get('ski_only', [])}
        txt_only = {tuple(coord) for coord in row.get('txt_only', [])}
        scrap = {item['process']: item for item in self.scrap_rows.get(strip, [])}
        self.position.setText(f"{self.index + 1}/{len(self.rows)}  ·  LOT {self.lot}  ·  {strip}")
        self.previous.setEnabled(self.index > 0)
        self.next.setEnabled(self.index + 1 < len(self.rows))

        ski_lines = ['SKI: ' + self.trace.get('ski_path', ''), '',
                     *self.coordinate_lines(_('최종 SKI 좌표'), ski), '',
                     *self.coordinate_lines(_('SKI에만 있는 좌표'), ski_only), '',
                     *self.coordinate_lines(_('BBT/Skipdata 좌표'), bbt)]
        ski_lines.extend(self.trace.get('bbt_sources', {}).get(strip, []))
        self.ski_view.setPlainText('\n'.join(ski_lines))

        lines = []
        outside_ski = set(txt_only)
        for process, data in info.get('processes', {}).items():
            selected = data.get('selected_source')
            paths = [selected] if selected else data.get('sources', [])
            raw = data.get('defect_lines', [])
            if not (paths or raw):
                continue
            unmatched = {parse_xy_from_gather_line(line) for line in raw} - ski - bbt - {None} if process in scrap else txt_only
            outside_ski.update(unmatched)
            lines.extend(['', f'[{process}]', *paths])
            origins = [path for path in data.get('selected_origins', []) if path not in paths]
            if origins:
                lines.extend([_('수집 원본 경로'), *origins])
            if process in scrap:
                label = _('검증용 TXT 백업 완료 · 해당 Strip 기록 집계 제외') if scrap[process]['status'] == 'archived' else _('Skipdata 갱신 필요')
                lines.append(label)
            lines.extend([line for line in raw if parse_xy_from_gather_line(line) in unmatched]
                         or [_('이 공정에서 미반영 좌표의 TXT 원문을 찾지 못했습니다.')])
        self.txt_view.setPlainText('\n'.join([*self.coordinate_lines(_('TXT에만 있는 좌표'), outside_ski), *lines]))
