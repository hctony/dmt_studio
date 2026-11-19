"""Shared verification history, driven by one background worker on A PC."""
from datetime import datetime
import json
import os
import sqlite3

from PySide6.QtCore import QThread, QTimer, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from core import xml_followup
from core.i18n import _
from ui.worker_lifetime import start_worker


class VerificationWorker(QThread):
    def __init__(self, job):
        super().__init__()
        self.job = job

    def run(self):
        from workers.ivs_workers import IvsScanThread
        identity = self.job['id']
        try:
            baseline = json.loads(self.job['baseline'])
            scan = IvsScanThread(self.job['model'], self.job['lot'], units_per_strip=baseline['units'],
                                 verification_scope=baseline['scope'])
            self.dmt_progress = {'stage': _('후속 XML 검증'), 'detail': self.job['lot'], 'value': 0, 'max': 0}
            output = []
            errors = []
            scan.finished.connect(output.append, Qt.DirectConnection)
            scan.failed.connect(errors.append, Qt.DirectConnection)
            scan.run()
            if not output:
                raise RuntimeError(errors[0].splitlines()[-1] if errors else 'XML_FOLLOWUP_NO_RESULT')
            status, details = xml_followup.compare(baseline, output[0])
            xml_followup.finish(identity, status, details, output[0])
        except Exception as error:
            xml_followup.finish(identity, 'failed', {'error': str(error)})


STATUS = {'queued': '대기 중', 'running': '검증 중', 'verified': '검증 완료',
          'difference': '차이 발견', 'incomplete': '검증 미완료', 'failed': '검증 실패'}


class XmlHistoryTab(QWidget):
    def __init__(self):
        super().__init__()
        self.entries = []
        layout = QVBoxLayout(self)
        search = QHBoxLayout()
        self.model_filter = QLineEdit()
        self.lot_filter = QLineEdit()
        refresh = QPushButton(_('새로고침'))
        refresh.clicked.connect(self.refresh)
        for widget in (QLabel(_('모델')), self.model_filter, QLabel('LOT'), self.lot_filter, refresh):
            search.addWidget(widget)
        layout.addLayout(search)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([_('처리 시각'), _('모델'), 'LOT', _('검증 상태'), _('검증 갱신 시각')])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.itemSelectionChanged.connect(self.show_details)
        layout.addWidget(self.table, 2)
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        layout.addWidget(self.detail, 2)
        self.download = QPushButton(_('3% 이상 불량 이미지 다운로드'))
        self.download.setEnabled(False)
        self.download.clicked.connect(self.download_images)
        layout.addWidget(self.download)
        for field in (self.model_filter, self.lot_filter):
            field.editingFinished.connect(self.refresh)

        self.worker = None
        self.server = os.environ.get('DMT_REMOTE_SESSION') != '1' and os.environ.get('DMT_XML_QUEUE') == '1'
        self.recovered = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(2500)

    def tick(self):
        try:
            if self.server and not (self.worker and self.worker.isRunning()):
                if not self.recovered:
                    xml_followup.recover()
                    self.recovered = True
                job = xml_followup.claim()
                if job:
                    self.worker = VerificationWorker(job)
                    start_worker(self, self.worker)
            if self.isVisible():
                self.refresh()
        except (OSError, sqlite3.Error) as error:
            if self.isVisible():
                self.detail.setPlainText(str(error))

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
                      row['model'], row['lot'], _(STATUS[row['status']]),
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

    def show_details(self):
        entry = xml_followup.read_job(self.selection_id()) if self.selection_id() else None
        self.download.setEnabled(False)
        if not entry:
            self.detail.clear()
            return
        details = json.loads(entry['details'])
        baseline = json.loads(entry['baseline'])
        lines = [_(STATUS[entry['status']]),
                 _('성적서 집계 Strip / Unit') + f": {len(baseline['strips'])} / {baseline['total_units']}"]
        groups = [('xml_only', 'XML에만 있는 Strip'), ('txt_only', 'TXT에만 있는 Strip'),
                  ('older_txt', '최신 XML보다 오래된 공정 TXT'), ('xml_only_coords', 'XML에만 기록된 불량 좌표')]
        for key, label in groups:
            if key in details:
                found = details[key]
                lines.append(_(label) + f': {len(found)}')
                lines.extend(found)
        if details.get('image_counts'):
            lines.append(_('3% 이상 불량 이미지 후보') + ': ' +
                         ', '.join(f'{code}: {count}' for code, count in details['image_counts'].items()))
        if details.get('error'):
            lines.append(details['error'])
        self.detail.setPlainText('\n'.join(lines))
        self.download.setEnabled(bool(entry['xml'] and any(details.get('image_counts', {}).values())))

    def download_images(self):
        from core.ivs import load_records_from_payload
        from core.image_catalog import ImageCatalog
        from ui.image_range import ImageRangeDialog
        entry = xml_followup.read_job(self.selection_id(), include_xml=True)
        if not entry or not entry['xml']:
            return
        records = load_records_from_payload(json.loads(entry['xml']))
        self.catalog = ImageCatalog(xml_followup.image_records(json.loads(entry['baseline']), records))
        self.range_dialog = ImageRangeDialog(self.catalog.counts(), download=True, parent=self)
        self.range_dialog.submitted.connect(self.save_image_range)
        self.range_dialog.show()

    def save_image_range(self, code, start, end):
        from workers.ivs_workers import DownloadThread
        directory = QFileDialog.getExistingDirectory(self, _('이미지 저장 폴더'))
        if not directory:
            return
        worker = DownloadThread(self.catalog.download_records(code, start, end), directory, code)
        worker.finished.connect(lambda saved: QMessageBox.information(
            self, _('다운로드 완료'), str(saved)))
        worker.failed.connect(lambda failure: QMessageBox.warning(self, _('다운로드 실패'), failure))
        start_worker(self, worker)
