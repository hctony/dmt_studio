"""Shared inspection history and quantity-limited image downloads."""
from datetime import datetime
import json
import os
import sqlite3

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from core import xml_followup
from core.i18n import _
from core.report_codes import followup_code
from ui.worker_lifetime import start_worker


STATUS = {
    'pending_images': '다운로드 요청 가능', 'queued': '대기 중', 'running': '이미지 조회 중',
    'cancelling': '이미지 조회 중지 중', 'images_ready': '이미지 후보 준비 완료',
    'images_missing': '이미지 후보 없음', 'images_skipped': '3% 이상 대상 없음',
    'images_failed': '이미지 조회 실패',
    'verified': '검증 완료', 'difference': '차이 발견', 'incomplete': '검증 미완료', 'failed': '검증 실패',
}
KIND_LABELS = {xml_followup.IMAGE_JOB: '3% 이상', xml_followup.TOP_IMAGE_JOB: 'TOP 3'}


class XmlHistoryTab(QWidget):
    def __init__(self):
        super().__init__()
        self.entries = []
        self.worker = None
        layout = QVBoxLayout(self)
        actions = QHBoxLayout()
        self.top_download = QPushButton(_('TOP 3 불량 이미지 다운로드'))
        self.high_download = QPushButton(_('3% 이상 불량 이미지 다운로드'))
        for button, kind in ((self.top_download, xml_followup.TOP_IMAGE_JOB),
                             (self.high_download, xml_followup.IMAGE_JOB)):
            button.setProperty('dmt_presentation', 'defect-download-button')
            button.clicked.connect(lambda checked=False, mode=kind: self.open_selected_request(mode))
            actions.addWidget(button)
        self.stop_download = QPushButton(_('이미지 다운로드 중지'))
        self.stop_download.setEnabled(False)
        self.stop_download.clicked.connect(self.cancel_download)
        actions.addWidget(self.stop_download)
        actions.addStretch()
        layout.addLayout(actions)
        self.download_status = QLabel()
        self.download_status.setWordWrap(True)
        self.download_status.hide()
        layout.addWidget(self.download_status)

        search = QHBoxLayout()
        self.model_filter = QLineEdit()
        self.lot_filter = QLineEdit()
        refresh = QPushButton(_('새로고침'))
        refresh.clicked.connect(self.refresh)
        for widget in (QLabel(_('모델')), self.model_filter, QLabel('LOT'), self.lot_filter, refresh):
            search.addWidget(widget)
        layout.addLayout(search)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([_('처리 시각'), _('모델'), 'LOT', _('유형'), _('검증 상태'), _('검증 갱신 시각')])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.itemSelectionChanged.connect(self.show_details)
        layout.addWidget(self.table, 2)
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        layout.addWidget(self.detail, 2)
        for field in (self.model_filter, self.lot_filter):
            field.editingFinished.connect(self.refresh)

        self.server = os.environ.get('DMT_REMOTE_SESSION') != '1' and os.environ.get('DMT_XML_QUEUE') == '1'
        self.recovered = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(2500)

    def tick(self):
        try:
            if self.server and self.timer.isActive() and not self.recovered:
                xml_followup.recover(defer_queued=True)
                self.recovered = True
            if self.isVisible():
                self.refresh()
        except (OSError, sqlite3.Error) as error:
            if self.isVisible():
                self.detail.setPlainText(str(error))

    def stop_background(self):
        self.timer.stop()
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            return self.worker
        return None

    def refresh(self):
        entries = xml_followup.list_jobs(self.model_filter.text().strip(), self.lot_filter.text().strip())
        if entries == self.entries:
            return
        selected = self.selection_id()
        self.entries = entries
        self.table.blockSignals(True)
        self.table.setRowCount(len(entries))
        for index, row in enumerate(entries):
            values = [datetime.fromtimestamp(row['created']).isoformat(' ', timespec='seconds'),
                      row['model'], row['lot'], _(KIND_LABELS.get(row['kind'], '후속 XML 검증')), _(STATUS[row['status']]),
                      datetime.fromtimestamp(row['updated']).isoformat(' ', timespec='seconds')]
            for column, value in enumerate(values):
                self.table.setItem(index, column, QTableWidgetItem(value))
        if entries:
            self.table.selectRow(next((i for i, row in enumerate(entries) if row['id'] == selected), 0))
        self.table.blockSignals(False)
        self.show_details()

    def selection_id(self):
        row = self.table.currentRow()
        return self.entries[row]['id'] if 0 <= row < len(self.entries) else None

    def open_selected_request(self, kind):
        title = self.top_download.text() if kind == xml_followup.TOP_IMAGE_JOB else self.high_download.text()
        row = self.table.currentRow()
        if not 0 <= row < len(self.entries):
            QMessageBox.information(self, title, _('이력에서 LOT를 선택하거나 수율 계산 화면에서 이미지를 여세요.'))
            return
        selected = self.entries[row]
        identity = selected['id'] if selected['kind'] == kind else (
            xml_followup.latest_image_job_id(selected['model'], selected['lot'], kind))
        if not identity and kind == xml_followup.IMAGE_JOB and not selected['kind']:
            identity = selected['id']
        if not identity:
            QMessageBox.information(self, title,
                                    _('수율 계산에서 이 LOT의 데이터를 불러와 계산한 뒤 다운로드 버튼을 누르세요.'))
            return
        self.open_image_request(identity)

    def open_image_request(self, identity):
        from ui.image_range import FollowupImageRequestDialog

        if self.worker and self.worker.isRunning():
            return
        entry = xml_followup.read_job(identity)
        if not entry:
            return
        if not any(row['id'] == identity for row in self.entries):
            self.model_filter.setText(entry['model'])
            self.lot_filter.setText(entry['lot'])
            self.refresh()
        for index, row in enumerate(self.entries):
            if row['id'] == identity:
                self.table.selectRow(index)
                break

        baseline = json.loads(entry['baseline'])
        kind = baseline.get('kind')
        combined = kind == xml_followup.TOP_IMAGE_JOB
        title = self.top_download.text() if combined else self.high_download.text()
        codes = [code for code, _count in baseline.get('ranking', [])] if combined else baseline.get('high', [])
        codes = [code for code in codes if followup_code(code)]
        mapped = {row[2] for rows in baseline.get('image_assignments', {}).values() for row in rows}
        reported = json.loads(entry['details']).get('image_counts', {}) if entry['xml'] else {}
        available = (mapped | {code for code, count in reported.items() if count}) & set(codes)
        if not available:
            QMessageBox.information(self, title, _('선택한 유형에 다운로드할 불량 코드가 없습니다.'))
            return
        self.range_dialog = FollowupImageRequestDialog(title, codes, available, combined=combined, parent=self)
        self.range_dialog.submitted.connect(
            lambda quantities: self.start_download(entry, baseline, quantities, combined))
        self.range_dialog.show()

    def start_download(self, entry, baseline, quantities, combined):
        from workers.ivs_workers import FollowupImageDownloadThread

        directory = QFileDialog.getExistingDirectory(self, _('이미지 저장 폴더'))
        if not directory or (self.worker and self.worker.isRunning()):
            return
        self.worker = FollowupImageDownloadThread({}, quantities, directory, combined=combined,
                                                   job=entry, baseline=baseline)
        self.worker.completed.connect(self.show_download_result)
        self.worker.failed.connect(self.show_download_error)
        self.worker.finished.connect(self.download_finished)
        self.set_download_active(True)
        self.set_download_status(_('이미지 다운로드 중') + ' · ' + entry['lot'])
        start_worker(self, self.worker)

    def set_download_status(self, message):
        self.download_status.setText(message)
        self.download_status.show()

    def set_download_active(self, active):
        self.top_download.setEnabled(not active)
        self.high_download.setEnabled(not active)
        self.stop_download.setEnabled(active)

    def cancel_download(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.stop_download.setEnabled(False)
            self.set_download_status(_('이미지 다운로드 중지 중'))

    def download_finished(self):
        self.set_download_active(False)

    def show_download_result(self, saved, cancelled):
        summary = ', '.join(f'{code} {count}/{self.worker.quantities[code]}' for code, count in saved.items())
        title = _('이미지 다운로드가 중지되었습니다.') if cancelled else _('다운로드 완료')
        self.set_download_status(f'{title} · {self.worker.job["lot"]} · {summary}')

    def show_download_error(self, message):
        self.set_download_status(_('다운로드 실패') + ' · ' + message.splitlines()[-1])

    def show_details(self):
        identity = self.selection_id()
        entry = xml_followup.read_job(identity) if identity else None
        if not entry:
            self.detail.clear()
            return
        details = json.loads(entry['details'])
        baseline = json.loads(entry['baseline'])
        image_job = baseline.get('kind') in xml_followup.IMAGE_JOB_KINDS
        top_job = baseline.get('kind') == xml_followup.TOP_IMAGE_JOB
        strip_count = baseline.get('strip_count', len(baseline.get('strips', [])))
        lines = [_(STATUS[entry['status']]),
                 _('성적서 집계 Strip / Unit') + f": {strip_count} / {baseline.get('total_units', 0)}"]
        if image_job:
            strips = sum(any(followup_code(row[2]) for row in rows) for rows in baseline['image_assignments'].values())
            lines.append(_('이미지 대상 Strip') + f": {strips}")
        if top_job:
            lines.append(_('TOP 3 불량 Unit 수') + ': ' + ', '.join(
                f'{code}: {count:,}' for code, count in baseline['ranking'] if followup_code(code)))
        groups = [('xml_only', 'XML에만 있는 Strip'), ('txt_only', 'TXT에만 있는 Strip'),
                  ('older_txt', '최신 XML보다 오래된 공정 TXT'), ('xml_only_coords', 'XML에만 기록된 불량 좌표'),
                  ('image_unmatched_strips', '이미지 후보를 찾지 못한 Strip')]
        for key, label in groups:
            if key in details:
                found = details[key]
                lines.append(_(label) + f': {len(found)}')
                lines.extend(found)
        if details.get('image_unidentified_folders'):
            lines.append(_('바코드를 확인하지 못한 폴더') + f": {details['image_unidentified_folders']}")
        counts = {code: count for code, count in details.get('image_counts', {}).items() if followup_code(code)}
        if counts:
            lines.append((_('TOP 3 불량 이미지 후보') if top_job else _('3% 이상 불량 이미지 후보')) + ': ' +
                         ', '.join(f'{code}: {count}' for code, count in counts.items()))
        if details.get('error'):
            lines.append(details['error'])
        self.detail.setPlainText('\n'.join(lines))
