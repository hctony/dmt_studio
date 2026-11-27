"""Shared inspection history and on-demand image actions on A PC."""
from contextlib import suppress
from datetime import datetime
import json
import os
import sqlite3
import threading

from PySide6.QtCore import QThread, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from core import xml_followup
from core.cancellation import OperationCancelled, check_cancelled
from core.i18n import _
from ui.worker_lifetime import start_worker


class ImageLookupWorker(QThread):
    dmt_background = True

    def __init__(self, job):
        super().__init__()
        self.job = job
        self.cancel = threading.Event()
        self.ssh = None
        self.stage = _('3% 이상 불량 이미지 조회')
        self.progress(0, 0, job['lot'])

    def progress(self, value, maximum, detail):
        self.dmt_progress = {'stage': self.stage, 'detail': detail,
                             'value': value, 'max': maximum}

    def stop(self):
        if self.cancel.is_set():
            return
        self.cancel.set()
        self.dmt_progress = {**self.dmt_progress, 'stage': _('이미지 조회 중지 중')}
        ssh = self.ssh
        if ssh is not None:
            def interrupt():
                with suppress(Exception):
                    ssh.close()
            threading.Thread(target=interrupt, daemon=True).start()

    def set_client(self, ssh):
        self.ssh = ssh
        check_cancelled(self.cancel.is_set)

    def run(self):
        from core.nas import connect
        from core.xml_image_lookup import lookup_images

        sftp = payload = None
        try:
            baseline = json.loads(self.job['baseline'])
            if baseline.get('kind') not in xml_followup.IMAGE_JOB_KINDS:
                # A client of an older build may have added a job since recovery.
                xml_followup.recover()
                return
            if baseline['kind'] == xml_followup.TOP_IMAGE_JOB:
                self.stage = _('TOP 3 불량 이미지 조회')
                self.progress(0, 0, self.job['lot'])
            check_cancelled(self.cancel.is_set)
            _, sftp = connect(on_client=self.set_client)
            records, details = lookup_images(sftp, self.job['model'], self.job['lot'], baseline,
                                             self.cancel.is_set, self.progress)
            status = 'images_ready' if records else 'images_missing'
            if records:
                payload = {'schema': 'dmt-studio-ivs-v1', 'records': records}
        except OperationCancelled:
            status, details = 'pending_images', {}
        except Exception as error:
            status, details = 'images_failed', {'error': str(error)}
        finally:
            for connection in (sftp, self.ssh):
                if connection is not None:
                    with suppress(Exception):
                        connection.close()
            self.ssh = None
        if self.cancel.is_set():
            status, details, payload = 'pending_images', {}, None
        xml_followup.finish(self.job['id'], status, details, payload)


STATUS = {
    'pending_images': '이미지 조회 대기', 'queued': '대기 중', 'running': '이미지 조회 중',
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
        layout = QVBoxLayout(self)
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
        self.lookup = QPushButton(_('3% 이상 불량 이미지 조회'))
        self.lookup.clicked.connect(self.request_lookup)
        self.stop_lookup = QPushButton(_('이미지 조회 중지'))
        self.stop_lookup.clicked.connect(self.cancel_lookup)
        self.download = QPushButton(_('3% 이상 불량 이미지 다운로드'))
        self.download.clicked.connect(self.download_images)
        actions = QHBoxLayout()
        for button in (self.lookup, self.stop_lookup, self.download):
            button.setEnabled(False)
            actions.addWidget(button)
        layout.addLayout(actions)
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
            if self.server and self.timer.isActive():
                if not self.recovered:
                    xml_followup.recover()
                    self.recovered = True
                if self.worker and self.worker.isRunning():
                    if xml_followup.is_cancelling(self.worker.job['id']):
                        self.worker.stop()
                else:
                    job = xml_followup.claim()
                    if job:
                        self.worker = ImageLookupWorker(job)
                        start_worker(self, self.worker)
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

    def request_lookup(self):
        xml_followup.request_images(self.selection_id())
        self.tick()

    def cancel_lookup(self):
        xml_followup.cancel_images(self.selection_id())
        self.tick()

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

    def show_details(self):
        identity = self.selection_id()
        entry = xml_followup.read_job(identity) if identity else None
        self.lookup.setEnabled(False)
        self.stop_lookup.setEnabled(False)
        self.download.setEnabled(False)
        if not entry:
            self.detail.clear()
            return
        details = json.loads(entry['details'])
        baseline = json.loads(entry['baseline'])
        image_job = baseline.get('kind') in xml_followup.IMAGE_JOB_KINDS
        top_job = baseline.get('kind') == xml_followup.TOP_IMAGE_JOB
        self.lookup.setText(_('TOP 3 불량 이미지 조회') if top_job else _('3% 이상 불량 이미지 조회'))
        self.download.setText(_('TOP 3 불량 이미지 다운로드') if top_job else _('3% 이상 불량 이미지 다운로드'))
        status = entry['status']
        strip_count = baseline.get('strip_count', len(baseline.get('strips', [])))
        lines = [_(STATUS[entry['status']]),
                 _('성적서 집계 Strip / Unit') + f": {strip_count} / {baseline.get('total_units', 0)}"]
        if image_job:
            lines.append(_('이미지 대상 Strip') + f": {len(baseline['image_assignments'])}")
        if top_job:
            lines.append(_('TOP 3 불량 Unit 수') + ': ' +
                         ', '.join(f'{code}: {count:,}' for code, count in baseline['ranking']))
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
        if details.get('image_counts'):
            lines.append((_('TOP 3 불량 이미지 후보') if top_job else _('3% 이상 불량 이미지 후보')) + ': ' +
                         ', '.join(f'{code}: {count}' for code, count in details['image_counts'].items()))
        if details.get('error'):
            lines.append(details['error'])
        self.detail.setPlainText('\n'.join(lines))
        self.lookup.setEnabled(image_job and bool(baseline['image_assignments'])
                               and status in {'pending_images', 'images_missing', 'images_failed'})
        self.stop_lookup.setEnabled(image_job and status in {'queued', 'running'})
        self.download.setEnabled(bool(entry['xml'] and any(details.get('image_counts', {}).values())))

    def download_images(self):
        from core.ivs import load_records_from_payload
        from core.image_catalog import ImageCatalog
        from ui.image_range import ImageRangeDialog, TopImageDownloadDialog
        entry = xml_followup.read_job(self.selection_id(), include_xml=True)
        if not entry or not entry['xml']:
            return
        records = load_records_from_payload(json.loads(entry['xml']))
        baseline = json.loads(entry['baseline'])
        if baseline.get('kind') not in xml_followup.IMAGE_JOB_KINDS:
            records = xml_followup.image_records(baseline, records)
        catalog = ImageCatalog(records)
        if baseline.get('kind') == xml_followup.TOP_IMAGE_JOB:
            self.range_dialog = TopImageDownloadDialog(baseline['ranking'], catalog.counts(), parent=self)
            self.range_dialog.submitted.connect(lambda quantities: self.save_top_images(catalog, quantities))
        else:
            self.catalog = catalog
            self.range_dialog = ImageRangeDialog(self.catalog.counts(), download=True, parent=self)
            self.range_dialog.submitted.connect(self.save_image_range)
        self.range_dialog.show()

    def save_top_images(self, catalog, quantities):
        from workers.ivs_workers import TopImageDownloadThread

        directory = QFileDialog.getExistingDirectory(self, _('이미지 저장 폴더'))
        if not directory:
            return
        candidates = {code: catalog.download_records(code, 1, catalog.counts()[code]) for code in quantities}
        worker = TopImageDownloadThread(candidates, quantities, directory)
        def show_saved(saved):
            lines = [f'{code}: {saved[code]}/{total}' for code, total in quantities.items()]
            if saved != quantities:
                lines.append(_('세 조명이 모두 있는 세트만 저장했습니다.'))
            QMessageBox.information(self, _('다운로드 완료'), '\n'.join(lines))
        worker.finished.connect(show_saved)
        worker.failed.connect(lambda failure: QMessageBox.warning(self, _('다운로드 실패'), failure))
        start_worker(self, worker)

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
