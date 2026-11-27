"""IVS-related QThread workers."""
from __future__ import annotations
from core.i18n import _

import traceback
import json
import threading
import time
from contextlib import suppress
from dataclasses import asdict
from itertools import chain
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QSaveFile, QThread, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtCore import Qt

from core.models import DefectRecord
from core.nas import (
    connect, iter_dirs, folder_index,
    load_valid_bundle_indexes,
    list_lot_trials, parse_trial_context, find_fc_file, read_barcode_from_xml,
)
from core.ivs import parse_ivs_xml, records_to_json, find_ivs_files
from core.image_catalog import ImageCatalog, image_key
from core.report_codes import followup_code
from core.utils import sanitize_filename
from core.cancellation import OperationCancelled, check_cancelled


class IvsScanThread(QThread):
    progress = Signal(str)
    finished = Signal(dict)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, model: str, lot: str, trial_path: str = "", units_per_strip=None, *, verification_scope=None):
        super().__init__()
        self.model = model
        self.lot = lot
        self.trial_path = trial_path
        self.verification_scope = verification_scope
        self._cancel = threading.Event()
        self._ssh = None
        from core.ivs_population import model_units
        self.units_per_strip = model_units(model) if units_per_strip is None else int(units_per_strip or 0)
        self.dmt_progress = {'stage': 'NAS 연결 중', 'value': 0, 'max': 0, 'detail': ''}

    def stop(self):
        self._cancel.set()
        self.requestInterruption()
        self.dmt_progress = {**self.dmt_progress, 'stage': 'IVS 수집 중지 중'}
        ssh = self._ssh
        if ssh is not None:
            # Closing the SSH transport releases blocked SFTP reads. Keep this
            # off the GUI thread, and let run() perform its normal cleanup.
            def interrupt():
                try:
                    ssh.close()
                except Exception:
                    pass
            threading.Thread(target=interrupt, daemon=True).start()

    def run(self) -> None:
        ssh = sftp = None
        started=time.monotonic()
        try:
            check_cancelled(self._cancel.is_set)
            from core.rework_scope import load as load_scope, cutoff
            requested=parse_trial_context(self.trial_path) if self.trial_path.strip() else None
            scope_model=requested.model if requested else self.model
            scope_lot=requested.lot if requested else self.lot
            scope=load_scope(scope_model,scope_lot) if self.verification_scope is None else self.verification_scope
            if scope.get('enabled') and scope.get('reuse_xml'):
                from core.yield_handoff import context
                from core.ivs import latest_payload
                import json
                source=context(scope_model,scope_lot).get('fields',{}).get('ivs_json_in',{}).get('value')
                if not source or not Path(source).is_file():
                    raise ValueError(_('유지할 IVS JSON이 없습니다. 기존 JSON을 먼저 열어 주세요.'))
                payload=latest_payload(json.loads(Path(source).read_text(encoding='utf-8-sig')))
                if payload.get('model','').upper()!=scope_model.upper() or payload.get('lot_id','').upper()!=scope_lot.upper():
                    raise ValueError('IVS_BASELINE_LOT_MISMATCH')
                payload['rework_scope']=scope
                payload['reused_xml_baseline']=str(source)
                self.finished.emit(payload)
                return
            ssh, sftp = connect()
            connection_seconds=time.monotonic()-started
            self._ssh = ssh
            if getattr(sftp,'_dmt_scan_session',False) is True:
                sftp.should_stop=self._cancel.is_set
                sftp.bypass_xml_cache=getattr(self,'bypass_xml_cache',False)
                sftp._dmt_lazy_images=True
            check_cancelled(self._cancel.is_set)
            if self.trial_path.strip():
                trials = [parse_trial_context(self.trial_path)]
                model = trials[0].model
                lot = trials[0].lot
            else:
                model = self.model
                lot = self.lot
                trials = list_lot_trials(model, lot, sftp, self._cancel.is_set)
            self.progress.emit(f"Trial {len(trials)}개 스캔 시작")

            records: list[DefectRecord] = []
            strip_history=[]
            strip_ids = set()
            grid_units = set()
            missing_barcodes = parse_errors = 0
            parsed_xml_count = 0
            def observed_strip(barcode, grid):
                barcode = barcode.strip().upper()
                if barcode and barcode != 'UNKNOWN' and (not lot or barcode.startswith(lot.strip().upper())):
                    strip_ids.add(barcode)
                    if grid:
                        grid_units.add(grid)
                    return True
                return False
            for trial_index, ctx in enumerate(trials, 1):
                check_cancelled(self._cancel.is_set)
                detail = f'Trial {trial_index}/{len(trials)} · {ctx.machine}/{ctx.trial}'
                self.dmt_progress = {'stage': '폴더 검색 중', 'value': 0, 'max': 0, 'detail': detail}
                valid_indexes = load_valid_bundle_indexes(ctx.path, sftp)
                if not valid_indexes:
                    self.progress.emit(f"[SKIP] {ctx.machine}/{ctx.trial}: ViaMCName 유효 Bundle 없음")
                folders = [folder for folder in iter_dirs(sftp, ctx.path)
                           if folder_index(folder) is not None]
                xml_count = 0
                self.dmt_progress = {'stage': '현재 Trial 진행률', 'value': 0, 'max': len(folders), 'detail': detail}
                for folder_index_done, strip_folder in enumerate(folders):
                    check_cancelled(self._cancel.is_set)
                    strip_path = f"{ctx.path}/{strip_folder}"
                    folder_barcodes = []
                    def collect(barcode, grid):
                        nonlocal parsed_xml_count
                        parsed_xml_count += 1
                        if observed_strip(barcode, grid):
                            folder_barcodes.append(barcode)
                            try:
                                metadata=sftp.metadata(xml_path) if getattr(sftp,'_dmt_scan_session',False) is True else sftp.stat(xml_path)
                                stamp=metadata.st_mtime
                                stamp=float(stamp) if isinstance(stamp,(int,float)) else None
                            except (OSError,AttributeError):stamp=None
                            strip_history.append({'strip_id':barcode.strip().upper(),'machine':ctx.machine,'trial':ctx.trial,
                                'strip_folder':strip_folder,'source_xml':xml_path,'source_mtime':stamp,'clock_basis':'source_file_mtime'})
                    xml_files = find_ivs_files(strip_path, sftp) if folder_index(strip_folder) in valid_indexes else []
                    if cutoff(scope):
                        selected_xml=[]
                        for candidate_xml in xml_files:
                            meta=sftp.metadata(candidate_xml) if getattr(sftp,'_dmt_scan_session',False) is True else sftp.stat(candidate_xml)
                            if float(meta.st_mtime)>=cutoff(scope):selected_xml.append(candidate_xml)
                        if xml_files and not selected_xml:
                            continue
                        xml_files=selected_xml
                        # A bare FC file cannot establish a post-reentry inspection.
                        if not xml_files:continue
                    for xml_path in xml_files:
                        check_cancelled(self._cancel.is_set)
                        self.dmt_progress = {'stage': '현재 Trial 진행률', 'value': folder_index_done, 'max': len(folders), 'detail': f'{detail} · Strip {folder_index_done}/{len(folders)} · XML {xml_count} · {strip_folder}'}
                        before = parsed_xml_count
                        parsed = parse_ivs_xml(xml_path, sftp, ctx, strip_folder, self.progress.emit, self._cancel.is_set, collect)
                        if parsed_xml_count == before:
                            parse_errors += 1
                        xml_count += 1
                        if parsed:
                            records.extend(parsed)
                    if not folder_barcodes:
                        check_cancelled(self._cancel.is_set)
                        fc = find_fc_file(strip_path, strip_folder, sftp)
                        barcode = read_barcode_from_xml(fc, sftp) if fc else None
                        check_cancelled(self._cancel.is_set)
                        if barcode and observed_strip(barcode, 0):
                            if folder_index(strip_folder) in valid_indexes and xml_files:
                                # Barcode is readable via FC, but this occurrence's
                                # inspection XML failed. Do not resurrect an older NG.
                                for failed_xml in xml_files:
                                    try:
                                        meta=sftp.metadata(failed_xml) if getattr(sftp,'_dmt_scan_session',False) is True else sftp.stat(failed_xml)
                                        stamp=float(meta.st_mtime) if isinstance(meta.st_mtime,(int,float)) else None
                                    except (OSError,AttributeError):stamp=None
                                    strip_history.append({'strip_id':barcode.strip().upper(),'machine':ctx.machine,'trial':ctx.trial,
                                        'strip_folder':strip_folder,'source_xml':failed_xml,'source_mtime':stamp,
                                        'clock_basis':'source_file_mtime','parse_failed':True})
                            elif folder_index(strip_folder) in valid_indexes:
                                try:
                                    metadata=sftp.metadata(fc) if getattr(sftp,'_dmt_scan_session',False) is True else sftp.stat(fc)
                                    stamp=metadata.st_mtime
                                    stamp=float(stamp) if isinstance(stamp,(int,float)) else None
                                except (OSError,AttributeError):stamp=None
                                strip_history.append({'strip_id':barcode.strip().upper(),'machine':ctx.machine,'trial':ctx.trial,
                                    'strip_folder':strip_folder,'source_xml':fc,'source_mtime':stamp,
                                    'clock_basis':'source_file_mtime','source_kind':'valid_bundle_fc_only'})
                        elif fc or folder_index(strip_folder) in valid_indexes:
                            missing_barcodes += 1
                    self.dmt_progress = {'stage': '현재 Trial 진행률', 'value': folder_index_done + 1, 'max': len(folders), 'detail': f'{detail} · Strip {folder_index_done + 1}/{len(folders)} · XML {xml_count}'}
                self.progress.emit(f"[{ctx.machine}/{ctx.trial}] 누적 {len(records)}건")

            check_cancelled(self._cancel.is_set)
            from core.ivs_population import population_payload
            missing_barcodes += len({record.strip_id for record in records if record.is_rate_defect} - strip_ids)
            population = population_payload(strip_ids, self.units_per_strip, grid_units, missing_barcodes,
                                            'trial' if self.trial_path.strip() else 'lot', parse_errors)
            population['xml_count'] = parsed_xml_count
            from core.ivs_latest import select_latest
            history_records=records
            records,selection=select_latest(records,strip_history)
            payload = records_to_json(model, lot, records, population)
            payload['rework_scope']=scope
            payload['history_records']=[asdict(r) for r in history_records]
            payload['latest_selection']=selection
            payload['strip_history']={'schema':'dmt-its-history-v1','complete':not missing_barcodes and not parse_errors and selection['complete'],'events':strip_history}
            self.progress.emit(_('Barcode별 최신 작업 반영')+f": {len(selection['selected'])} / "+_('이전 작업 제외')+f": {selection['replaced_occurrences']}")
            for issue in selection['issues'][:30]:self.progress.emit(_('작업 시각 확인 필요')+f": {issue['strip_id']} · {issue['reason']}")
            if getattr(sftp,'_dmt_scan_session',False) is True:
                payload['scan_metrics']=sftp.report()
                payload['scan_metrics'].update(elapsed_seconds=round(time.monotonic()-started,4),connection_seconds=round(connection_seconds,4))
                metrics=payload['scan_metrics']
                self.progress.emit(_('[IVS 성능]')+f" {metrics['elapsed_seconds']:.2f}s · "+_('XML 원격 읽기')+f": {metrics['xml_reads']} · "+_('XML 캐시 재사용')+f": {metrics['xml_cache_hits']} · "+_('폴더 조회')+f": {metrics['directory_reads']}")
            check_cancelled(self._cancel.is_set)
            self.finished.emit(payload)
        except OperationCancelled:
            self.cancelled.emit()
        except Exception:
            if self._cancel.is_set():
                self.cancelled.emit()
            else:
                self.failed.emit(traceback.format_exc())
        finally:
            for connection in (sftp, ssh):
                if connection:
                    try:
                        connection.close()
                    except Exception:
                        pass
            self._ssh = None


class StripDefectSearchThread(QThread):
    progress = Signal(str)
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, model: str, lot: str, strip_id: str):
        super().__init__()
        self.model = model
        self.lot = lot
        self.strip_id = strip_id.strip().upper()

    def run(self) -> None:
        ssh = sftp = None
        try:
            ssh, sftp = connect()
            trials = list_lot_trials(self.model, self.lot, sftp)
            records: list[dict] = []
            matched_folders: list[dict] = []
            for ctx in trials:
                for strip_folder in iter_dirs(sftp, ctx.path):
                    fc_path = find_fc_file(f"{ctx.path}/{strip_folder}", strip_folder, sftp)
                    if not fc_path:
                        continue
                    barcode = read_barcode_from_xml(fc_path, sftp)
                    if not barcode or barcode.strip().upper() != self.strip_id:
                        continue
                    matched_folders.append(
                        {
                            "machine": ctx.machine,
                            "trial": ctx.trial,
                            "strip_folder": strip_folder,
                            "fc_xml": fc_path,
                        }
                    )
                    self.progress.emit(f"매칭: {ctx.machine}/{ctx.trial}/{strip_folder}")
                    strip_path = f"{ctx.path}/{strip_folder}"
                    for xml_path in find_ivs_files(strip_path, sftp):
                        for record in parse_ivs_xml(xml_path, sftp, ctx, strip_folder):
                            records.append(asdict(record))
            self.finished.emit(
                {
                    "strip_id": self.strip_id,
                    "matched_folders": matched_folders,
                    "nas_defects": records,
                }
            )
        except Exception:
            self.failed.emit(traceback.format_exc())
        finally:
            if sftp:
                sftp.close()
            if ssh:
                ssh.close()


class ImageLoaderThread(QThread):
    finished = Signal(list, list)
    failed = Signal(str)

    def __init__(self, paths: list[str]):
        super().__init__()
        self.paths = paths

    def run(self) -> None:
        ssh = sftp = None
        try:
            ssh, sftp = connect()
            images: list[bytes] = []
            for path in self.paths[:3]:
                try:
                    if getattr(sftp,'_dmt_scan_session',False) is True:path=sftp.resolve_image(path)
                    with sftp.open(path, "rb") as fh:
                        images.append(fh.read())
                except Exception:
                    images.append(b"")
            self.finished.emit(self.paths, images)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if sftp:
                sftp.close()
            if ssh:
                ssh.close()


def label_image(image, label):
    image = image.convertToFormat(QImage.Format_RGB32)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.TextAntialiasing)
    font = QFont("Arial", max(14, image.width() // 45))
    font.setBold(True)
    painter.setFont(font)
    text_rect = painter.fontMetrics().boundingRect(0, 0, image.width(), image.height(), Qt.TextWordWrap, label)
    margin = 8
    bg_rect = text_rect.adjusted(-margin, -margin, margin, margin)
    bg_rect.moveTopLeft(bg_rect.topLeft())
    painter.fillRect(bg_rect, QColor(0, 0, 0, 180))
    painter.setPen(QColor(255, 255, 255))
    painter.drawText(text_rect.adjusted(0, 0, margin, margin), Qt.TextWordWrap, label)
    painter.end()
    return image


class DownloadThread(QThread):
    progress = Signal(str)
    finished = Signal(int)
    failed = Signal(str)

    def __init__(self, records: list[DefectRecord], save_dir: str, defect_code: str):
        super().__init__()
        self.records = records
        self.save_dir = save_dir
        self.defect_code = defect_code

    def annotated_image_bytes(self, data: bytes, label: str) -> bytes:
        image = QImage.fromData(data)
        if image.isNull():
            return data
        image = label_image(image, label)
        buffer = QByteArray()
        qbuffer = QBuffer(buffer)
        qbuffer.open(QIODevice.WriteOnly)
        image.save(qbuffer, "PNG")
        return bytes(buffer)

    def run(self) -> None:
        ssh = sftp = None
        saved = 0
        try:
            ssh, sftp = connect()
            out_dir = Path(self.save_dir) / sanitize_filename(self.defect_code)
            out_dir.mkdir(parents=True, exist_ok=True)
            used_names: set[str] = set()
            for i, record in enumerate(self.records, start=1):
                for light, remote_path in zip(("LIGHT_0", "LIGHT_1", "LIGHT_2"), record.images):
                    base_name = sanitize_filename(
                        f"{record.lot}_{record.strip_id}_U{record.unit_idx}_{record.face}_p{record.img_merge_idx}_"
                        f"{light}_{record.machine}_T{record.trial}_{record.strip_folder}_{record.ivs_1}_{i:04d}"
                    )
                    file_name = f"{base_name}.png"
                    suffix = 1
                    while file_name in used_names or (out_dir / file_name).exists():
                        file_name = f"{base_name}_{suffix:02d}.png"
                        suffix += 1
                    used_names.add(file_name)
                    out_file = out_dir / file_name
                    try:
                        if getattr(sftp,'_dmt_scan_session',False) is True:remote_path=sftp.resolve_image(remote_path)
                        with sftp.open(remote_path, "rb") as remote_fh:
                            data = remote_fh.read()
                        label = (
                            f"LOT: {record.lot}\nStrip: {record.strip_id}\nUnit: {record.unit_idx}  "
                            f"Light: {light}\nCode: {record.ivs_1}  {record.machine} T{record.trial}"
                        )
                        out_file.write_bytes(self.annotated_image_bytes(data, label))
                        saved += 1
                    except Exception as exc:
                        self.progress.emit(f"저장 실패: {remote_path} ({exc})")
                self.progress.emit(f"{i}/{len(self.records)} 다운로드")
            self.finished.emit(saved)
        except Exception:
            self.failed.emit(traceback.format_exc())
        finally:
            if sftp:
                sftp.close()
            if ssh:
                ssh.close()


class FollowupImageDownloadThread(QThread):
    """Fill a request from cached candidates first, opening more NAS folders only as needed."""

    completed = Signal(dict, bool)
    failed = Signal(str)
    dmt_background = True

    def __init__(self, candidates, quantities, directory, *, combined, job=None, baseline=None):
        super().__init__()
        self.candidates = candidates
        self.quantities = {code: size for code, size in quantities.items() if size > 0 and followup_code(code)}
        self.directory = Path(directory)
        self.combined = combined
        self.job = job
        self.baseline = baseline
        self.cancel = threading.Event()
        self.ssh = None
        self.run_id = uuid4().hex[:12]
        title = 'TOP 3 불량 이미지 다운로드' if combined else '3% 이상 불량 이미지 다운로드'
        self.dmt_progress = {'stage': _(title), 'value': 0, 'max': sum(self.quantities.values()), 'detail': ''}

    def stop(self):
        if self.cancel.is_set():
            return
        self.cancel.set()
        self.dmt_progress = {**self.dmt_progress, 'stage': _('이미지 다운로드 중지 중')}
        ssh = self.ssh
        if ssh is not None:
            def interrupt():
                with suppress(Exception):
                    ssh.close()
            threading.Thread(target=interrupt, daemon=True).start()

    def set_client(self, ssh):
        self.ssh = ssh
        check_cancelled(self.cancel.is_set)

    def read_images(self, sftp, record):
        paths = []
        for number, path in enumerate(record.images[:3]):
            if not path:
                continue
            check_cancelled(self.cancel.is_set)
            try:
                if getattr(sftp, '_dmt_scan_session', False) is True:
                    path = sftp.resolve_image(path)
                    sftp.metadata(path)
                paths.append((number, path))
            except (FileNotFoundError, PermissionError):
                continue
        if self.combined and len({path for _, path in paths}) != 3:
            return []
        images = []
        for number, path in paths:
            check_cancelled(self.cancel.is_set)
            try:
                with sftp.open(path, 'rb') as source:
                    image = QImage.fromData(source.read())
                if not image.isNull():
                    images.append((number, image))
            except (FileNotFoundError, PermissionError):
                continue
        return images if not self.combined or len(images) == 3 else []

    def compose(self, record, images):
        header_height = 44
        combined = QImage(sum(image.width() for _, image in images),
                          max(image.height() for _, image in images) + header_height,
                          QImage.Format_RGB32)
        if combined.isNull():
            raise OSError('IMAGE_COMPOSITION_FAILED')
        combined.fill(Qt.black)
        painter = QPainter(combined)
        font = QFont('Arial')
        font.setPixelSize(14)
        painter.setFont(font)
        painter.setPen(Qt.white)
        painter.drawText(6, 17, f'{record.lot} · {record.strip_id} · U{record.unit_idx} · {record.face} · {record.ivs_1}')
        left = 0
        for number, image in images:
            painter.drawText(left + 6, 38, f'LIGHT_{number}')
            painter.drawImage(left, header_height, image)
            left += image.width()
        painter.end()
        return combined

    def save_images(self, record, images, index):
        folder = self.directory / sanitize_filename(record.ivs_1)
        folder.mkdir(parents=True, exist_ok=True)
        name = sanitize_filename(f'{record.lot}_{record.strip_id}_U{record.unit_idx}_{record.face}_'
                                 f'p{record.img_merge_idx}_{record.machine}_T{record.trial}_{record.strip_folder}_'
                                 f'{self.run_id}_{index:04d}')
        if self.combined:
            outputs = [(name, self.compose(record, images))]
        else:
            outputs = [(f'{name}_LIGHT_{number}', label_image(image, (
                f'LOT: {record.lot}\nStrip: {record.strip_id}\nUnit: {record.unit_idx}  '
                f'Light: LIGHT_{number}\nCode: {record.ivs_1}  {record.machine} T{record.trial}')))
                for number, image in images]
        for filename, image in outputs:
            output = folder / (filename + '.png')
            staging = folder / ('.' + filename + '.tmp')
            destination = QSaveFile(str(staging))
            if not destination.open(QIODevice.WriteOnly):
                raise OSError(destination.errorString())
            if not image.save(destination, 'PNG'):
                destination.cancelWriting()
                raise OSError(destination.errorString())
            if not destination.commit():
                raise OSError(destination.errorString())
            try:
                staging.replace(output)
            finally:
                staging.unlink(missing_ok=True)

    def progress(self, detail):
        self.dmt_progress = {**self.dmt_progress, 'detail': detail}

    def cached_records(self):
        if not self.job or not self.job.get('xml'):
            return chain.from_iterable(self.candidates.get(code, ()) for code in self.quantities)
        from core import xml_followup
        from core.ivs import load_records_from_payload

        entry = xml_followup.read_job(self.job['id'], include_xml=True)
        records = load_records_from_payload(json.loads(entry['xml'])) if entry and entry['xml'] else []
        if self.baseline.get('kind') not in xml_followup.IMAGE_JOB_KINDS:
            records = xml_followup.image_records(self.baseline, records)
        catalog = ImageCatalog(record for record in records if record.ivs_1 in self.quantities)
        return (group[0] for code in self.quantities for group in catalog.by_code.get(code, []))

    def run(self):
        from core.xml_image_lookup import iter_image_samples

        sftp = failure = None
        saved = dict.fromkeys(self.quantities, 0)
        remaining = set(saved)
        if not remaining:
            self.completed.emit(saved, self.cancel.is_set())
            return
        used = set()
        try:
            check_cancelled(self.cancel.is_set)
            cached = self.cached_records()
            check_cancelled(self.cancel.is_set)
            _, sftp = connect(on_client=self.set_client)
            if getattr(sftp, '_dmt_scan_session', False) is True:
                sftp.should_stop = self.cancel.is_set
            extra = iter_image_samples(sftp, self.job['model'], self.job['lot'], self.baseline,
                                       remaining, self.cancel.is_set, self.progress) if self.job else ()
            for record in chain(cached, extra):
                check_cancelled(self.cancel.is_set)
                code = record.ivs_1
                if code not in remaining:
                    continue
                key = image_key(record)
                if key in used:
                    continue
                used.add(key)
                self.progress(f'{code} · {record.strip_id} · {record.machine}/{record.trial}')
                images = self.read_images(sftp, record)
                if not images:
                    continue
                check_cancelled(self.cancel.is_set)
                self.save_images(record, images, saved[code] + 1)
                saved[code] += 1
                self.dmt_progress = {**self.dmt_progress, 'value': sum(saved.values())}
                if saved[code] == self.quantities[code]:
                    remaining.remove(code)
                    if not remaining:
                        break
        except OperationCancelled:
            pass
        except Exception:
            if not self.cancel.is_set():
                failure = traceback.format_exc()
        finally:
            for connection in (sftp, self.ssh):
                if connection is not None:
                    with suppress(Exception):
                        connection.close()
            self.ssh = None
        if failure:
            self.failed.emit(failure)
        else:
            self.completed.emit(saved, self.cancel.is_set())
