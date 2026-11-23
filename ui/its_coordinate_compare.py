"""Compare the captured SKI, RPT and process TXT for each discrepant Strip."""
from functools import partial
from pathlib import Path

from PySide6.QtCore import QThread, Signal, Slot
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QDialog, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QMessageBox, QPushButton, QTextEdit, QVBoxLayout

from core.gather import format_xy_coords, parse_xy_from_gather_line
from core.i18n import _
from core.ivs_result_transfer import configured_roots, inspect_transfer, move_transfer, source_candidates
from ui.worker_lifetime import start_worker


class TransferWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, operation, detail):
        super().__init__()
        self.operation = operation
        self.dmt_progress = {'stage': _('IVS 결과 TXT 이동'), 'detail': detail, 'value': 0, 'max': 0}

    def run(self):
        try:
            self.completed.emit(self.operation())
        except Exception as error:
            self.failed.emit(str(error))


class ItsCoordinateCompare(QDialog):
    def __init__(self, summary, rows, strip_id, parent):
        super().__init__(parent)
        self.setWindowTitle(_('불일치 좌표 확인'))
        self.setProperty('dmt_presentation', 'its-coordinate-compare')
        self.setModal(True)
        self.resize(1200, 760)
        self.trace = summary['report_trace']
        self.lot = summary['lot_id']
        self.model = parent.model_in.text().strip()
        self.summary = summary
        self.transfer_pending = False
        self.moved_sources = set()
        self.transfer_roots = None
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
        self.move = QPushButton(_('IVS 원본 TXT를 VRS로 이동'))
        self.move.clicked.connect(self.choose_transfer)
        for control in (self.previous, self.next, self.position):
            controls.addWidget(control)
        controls.addStretch()
        controls.addWidget(self.move)
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
        if self.transfer_pending:
            return
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
        self.previous.setEnabled(not self.transfer_pending and self.index > 0)
        self.next.setEnabled(not self.transfer_pending and self.index + 1 < len(self.rows))
        ivs = info.get('processes', {}).get('IVS', {})
        self.move.setEnabled(not self.transfer_pending and bool(row.get('ivs_txt_only'))
                             and bool(self.transfer_source_values(ivs)))

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

    def transfer_source_values(self, ivs):
        values = [path for path in [*ivs.get('selected_origins', []), ivs.get('selected_source')]
                  if path and Path(path) not in self.moved_sources]
        return source_candidates(values, self.transfer_roots['IVS']) if self.transfer_roots else values

    def choose_transfer(self):
        row = self.rows[self.index]
        strip = row['strip_id']
        ivs = self.trace['strips'].get(strip, {}).get('processes', {}).get('IVS', {})
        try:
            roots = configured_roots(self.model)
            self.transfer_roots = roots
            sources = self.transfer_source_values(ivs)
            if not sources or not roots['VRS']:
                raise ValueError(_('이 Strip의 IVS 설비 원본 또는 VRS 호기를 찾지 못했습니다.'))
        except (OSError, KeyError, TypeError, ValueError) as error:
            self.transfer_failed(str(error))
            return
        source = sources[0]
        if len(sources) > 1:
            choice, ok = QInputDialog.getItem(self, _('IVS 원본 TXT 선택'), strip, [str(path) for path in sources], 0, False)
            if not ok:
                return
            source = next((path for path in sources if str(path) == choice), None)
            if source is None:
                return
        stations = {f'VRS #{number} · {path}': path for number, path in enumerate(roots['VRS'], 1)}
        choice, ok = QInputDialog.getItem(self, _('이동할 VRS 호기 선택'), strip, list(stations), 0, False)
        if not ok or choice not in stations:
            return
        coords = {tuple(coord) for coord in row['ivs_txt_only']}
        self.transfer_context = (strip, coords)
        self.start_transfer(partial(inspect_transfer, source, stations[choice], roots, self.lot, strip, coords),
                            self.confirm_transfer)

    def start_transfer(self, operation, completed):
        self.transfer_pending = True
        self.refresh()
        worker = TransferWorker(operation, self.transfer_context[0])
        worker.completed.connect(completed)
        worker.failed.connect(self.transfer_failed)
        start_worker(self, worker)

    @Slot(object)
    def confirm_transfer(self, plan):
        strip, coords = self.transfer_context
        strips = ', '.join(plan.strips[:20])
        if len(plan.strips) > 20:
            strips += ' …'
        message = _('파일명과 내용 그대로 전체 TXT를 이동합니다. IVS 폴더에서 원본이 사라집니다.')
        if len(plan.strips) > 1:
            message += '\n' + _('이 TXT에 들어 있는 다른 Strip도 함께 이동됩니다.')
        answer = QMessageBox.question(
            self, _('IVS 결과 TXT 이동 확인'),
            f'{message}\n\n{plan.source}\n→ {plan.destination}\n\nStrip ({len(plan.strips)}): {strips}',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            self.transfer_pending = False
            self.refresh()
            return
        self.start_transfer(partial(self.execute_transfer, plan, strip, coords), self.transfer_completed)

    def execute_transfer(self, plan, strip, coords):
        target = move_transfer(plan, configured_roots(self.model), self.lot, strip, coords)
        return plan.source, target

    @Slot(object)
    def transfer_completed(self, result):
        source, target = result
        self.moved_sources.add(source)
        self.summary['ivs_transfer_requires_recalculation'] = True
        from core.report_review import prepare_summary
        prepare_summary(self.summary)
        self.transfer_pending = False
        self.refresh()
        QMessageBox.information(self, _('IVS 결과 TXT 이동 완료'), str(target) + '\n\n' +
                                _('ITS 게더링에서 반영한 뒤 최신 SKI와 공정 TXT를 다시 불러오세요.'))

    @Slot(str)
    def transfer_failed(self, error):
        self.transfer_pending = False
        self.refresh()
        QMessageBox.warning(self, _('IVS 결과 TXT 이동 실패'), error)

    def reject(self):
        if not self.transfer_pending:
            super().reject()

    def closeEvent(self, event):
        if self.transfer_pending:
            event.ignore()
        else:
            super().closeEvent(event)
