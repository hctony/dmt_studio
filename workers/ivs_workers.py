"""IVS-related QThread workers."""
from __future__ import annotations
from core.i18n import _

import traceback
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QThread, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtCore import Qt

from core.models import DefectRecord
from core.nas import (
    connect, iter_dirs, folder_index,
    load_valid_bundle_indexes,
    list_lot_trials, parse_trial_context, find_fc_file, read_barcode_from_xml,
)
from core.ivs import parse_ivs_xml, records_to_json, find_ivs_files
from core.utils import sanitize_filename


class IvsScanThread(QThread):
    progress = Signal(str)
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, model: str, lot: str, trial_path: str = ""):
        super().__init__()
        self.model = model
        self.lot = lot
        self.trial_path = trial_path

    def run(self) -> None:
        ssh = sftp = None
        try:
            ssh, sftp = connect()
            if self.trial_path.strip():
                trials = [parse_trial_context(self.trial_path)]
                model = trials[0].model
                lot = trials[0].lot
            else:
                model = self.model
                lot = self.lot
                trials = list_lot_trials(model, lot, sftp)
            self.progress.emit(f"Trial {len(trials)}개 스캔 시작")

            records: list[DefectRecord] = []
            for ctx in trials:
                valid_indexes = load_valid_bundle_indexes(ctx.path, sftp)
                if not valid_indexes:
                    self.progress.emit(f"[SKIP] {ctx.machine}/{ctx.trial}: ViaMCName 유효 Bundle 없음")
                    continue
                for strip_folder in iter_dirs(sftp, ctx.path):
                    idx = folder_index(strip_folder)
                    if idx is None or idx not in valid_indexes:
                        continue
                    strip_path = f"{ctx.path}/{strip_folder}"
                    for xml_path in find_ivs_files(strip_path, sftp):
                        parsed = parse_ivs_xml(xml_path, sftp, ctx, strip_folder, self.progress.emit)
                        if parsed:
                            records.extend(parsed)
                self.progress.emit(f"[{ctx.machine}/{ctx.trial}] 누적 {len(records)}건")

            self.finished.emit(records_to_json(model, lot, records))
        except Exception:
            self.failed.emit(traceback.format_exc())
        finally:
            if sftp:
                sftp.close()
            if ssh:
                ssh.close()


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
        image = image.convertToFormat(QImage.Format_RGB32)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.TextAntialiasing)
        font = QFont("Arial", max(14, image.width() // 45))
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        text_rect = metrics.boundingRect(0, 0, image.width(), image.height(), Qt.TextWordWrap, label)
        margin = 8
        bg_rect = text_rect.adjusted(-margin, -margin, margin, margin)
        bg_rect.moveTopLeft(bg_rect.topLeft())
        painter.fillRect(bg_rect, QColor(0, 0, 0, 180))
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(text_rect.adjusted(0, 0, margin, margin), Qt.TextWordWrap, label)
        painter.end()
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
