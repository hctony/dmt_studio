"""Strip ID / Trial / Barcode / Model-Lot QThread workers."""
from __future__ import annotations
from core.i18n import _

import traceback
import time
from collections import defaultdict
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from core.nas import (
    connect, iter_dirs, iter_xml_files_recursive, folder_index,
    list_lot_trials, list_models, list_lots,
    parse_trial_context, load_bundle_infos, find_fc_file, read_barcode_from_xml,
)


class StripIdThread(QThread):
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
                lot = trials[0].lot
            else:
                lot = self.lot
                trials = list_lot_trials(self.model, lot, sftp)
            unique_ids: set[str] = set()
            mixed_lots: list[dict] = []
            for ctx in trials:
                bundles = load_bundle_infos(ctx.path, sftp)
                if not bundles:
                    self.progress.emit(f"[SKIP] {ctx.machine}/{ctx.trial}: Bundle*.ini 없음")
                    continue
                valid_indexes = {idx for bundle in bundles if bundle.viamc_name.strip() for idx in bundle.indexes}
                trial_found = 0
                trial_no_fc = 0
                trial_no_barcode = 0
                trial_non_bundle_index = 0
                for strip_folder in iter_dirs(sftp, ctx.path):
                    idx = folder_index(strip_folder)
                    if idx is None:
                        continue
                    fc_path = find_fc_file(f"{ctx.path}/{strip_folder}", strip_folder, sftp)
                    if not fc_path:
                        trial_no_fc += 1
                        continue
                    barcode = read_barcode_from_xml(fc_path, sftp)
                    if barcode:
                        strip_id = barcode.strip().upper()
                        unique_ids.add(strip_id)
                        trial_found += 1
                        if valid_indexes and idx not in valid_indexes:
                            trial_non_bundle_index += 1
                        expected_lot = lot.strip().upper()
                        if expected_lot and "-" in strip_id and not strip_id.startswith(expected_lot):
                            detected_lot = strip_id.rsplit("-", 1)[0] + "-"
                            mixed_lots.append(
                                {
                                    "expected_lot": expected_lot,
                                    "detected_lot": detected_lot,
                                    "strip_id": strip_id,
                                    "machine": ctx.machine,
                                    "trial": ctx.trial,
                                    "strip_folder": strip_folder,
                                    "fc_xml": fc_path,
                                }
                            )
                    else:
                        trial_no_barcode += 1
                self.progress.emit(
                    f"[{ctx.machine}/{ctx.trial}] 발견 {trial_found}개, 누적 고유 {len(unique_ids)}개 "
                    f"(FC 없음 {trial_no_fc}, barcode 없음 {trial_no_barcode}, 유효 bundle 밖 {trial_non_bundle_index})"
                )
            ids = sorted(unique_ids)
            self.finished.emit(
                {
                    "model": trials[0].model if self.trial_path.strip() and trials else self.model,
                    "lot_id": lot,
                    "strip_count": len(ids),
                    "strip_ids": ids,
                    "mixed_lot_count": len(mixed_lots),
                    "mixed_lots": mixed_lots,
                }
            )
        except Exception:
            self.failed.emit(traceback.format_exc())
        finally:
            if sftp:
                sftp.close()
            if ssh:
                ssh.close()


class TrialListThread(QThread):
    progress = Signal(str)
    finished = Signal(list)
    failed = Signal(str)

    def __init__(self, model: str, lot: str):
        super().__init__()
        self.model = model
        self.lot = lot

    def run(self) -> None:
        ssh = sftp = None
        try:
            ssh, sftp = connect()
            trials = list_lot_trials(self.model, self.lot, sftp)
            self.finished.emit([ctx.path for ctx in trials])
        except Exception:
            self.failed.emit(traceback.format_exc())
        finally:
            if sftp:
                sftp.close()
            if ssh:
                ssh.close()


class FullXmlBarcodeScanThread(QThread):
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
        started=time.monotonic()
        try:
            ssh, sftp = connect()
            connection_seconds=time.monotonic()-started
            if getattr(sftp,'_dmt_scan_session',False) is True:
                sftp.bypass_xml_cache=getattr(self,'bypass_xml_cache',False)
            if self.trial_path.strip():
                trials = [parse_trial_context(self.trial_path)]
                model = trials[0].model
                lot = trials[0].lot
            else:
                model = self.model
                lot = self.lot
                trials = list_lot_trials(model, lot, sftp)

            records: list[dict] = []
            unique_ids: set[str] = set()
            barcode_locations: dict[str, list[dict]] = defaultdict(list)
            total_xml = 0
            no_barcode = 0

            self.progress.emit(f"전체 XML barcode 검색 시작: Trial {len(trials)}개")
            for ctx in trials:
                trial_xml = 0
                trial_found = 0
                for xml_path in iter_xml_files_recursive(ctx.path, sftp):
                    total_xml += 1
                    trial_xml += 1
                    barcode = read_barcode_from_xml(xml_path, sftp)
                    if not barcode:
                        no_barcode += 1
                        continue
                    strip_id = barcode.strip().upper()
                    unique_ids.add(strip_id)
                    trial_found += 1
                    record = {
                        "strip_id": strip_id,
                        "machine": ctx.machine,
                        "model": ctx.model,
                        "lot": ctx.lot,
                        "trial": ctx.trial,
                        "xml_path": xml_path,
                        "is_fc_xml": Path(xml_path).name.lower().endswith("fc.xml"),
                    }
                    records.append(record)
                    barcode_locations[strip_id].append(record)
                self.progress.emit(
                    f"[{ctx.machine}/{ctx.trial}] XML {trial_xml}개, barcode {trial_found}개, 누적 고유 {len(unique_ids)}개"
                )

            duplicates = {
                strip_id: locations
                for strip_id, locations in sorted(barcode_locations.items())
                if len(locations) > 1
            }
            payload = {
                    "schema": "dmt-studio-full-xml-barcode-v1",
                    "model": model,
                    "lot_id": lot,
                    "trial_count": len(trials),
                    "xml_count": total_xml,
                    "barcode_record_count": len(records),
                    "no_barcode_xml_count": no_barcode,
                    "unique_strip_count": len(unique_ids),
                    "strip_ids": sorted(unique_ids),
                    "duplicate_strip_count": len(duplicates),
                    "duplicates": duplicates,
                    "records": records,
                }
            if getattr(sftp,'_dmt_scan_session',False) is True:
                payload['scan_metrics']=sftp.report()
                payload['scan_metrics'].update(elapsed_seconds=round(time.monotonic()-started,4),connection_seconds=round(connection_seconds,4))
                metrics=payload['scan_metrics']
                self.progress.emit(_('[XML 성능]')+f" {metrics['elapsed_seconds']:.2f}s · "+_('XML 원격 읽기')+f": {metrics['xml_reads']} · "+_('XML 캐시 재사용')+f": {metrics['xml_cache_hits']}")
            self.finished.emit(payload)
        except Exception:
            self.failed.emit(traceback.format_exc())
        finally:
            if sftp:
                sftp.close()
            if ssh:
                ssh.close()


class ModelLotListThread(QThread):
    finished = Signal(str, list)
    failed = Signal(str)

    def __init__(self, mode: str, model: str = ""):
        super().__init__()
        self.mode = mode
        self.model = model

    def run(self) -> None:
        ssh = sftp = None
        try:
            ssh, sftp = connect()
            if self.mode == "models":
                values = list_models(sftp)
            else:
                values = list_lots(self.model, sftp)
            self.finished.emit(self.mode, values)
        except Exception:
            self.failed.emit(traceback.format_exc())
        finally:
            if sftp:
                sftp.close()
            if ssh:
                ssh.close()
