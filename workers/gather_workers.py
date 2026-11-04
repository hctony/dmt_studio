"""Gathering-related QThread workers."""
from __future__ import annotations
from core.i18n import _

import traceback
from collections import Counter
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from config import APP_SETTINGS
from core.models import GatherStrip
from core.gather import (
    GATHER_FILE_CACHE, GATHER_TXT_LIST_CACHE,
    parse_gather_file, cached_txt_files, find_parent_gather_hits,
    clone_gather_strip, should_replace_gather_strip_smart,
    score_gather_strip_against_ski, unique_lines,
    build_gather_duplicate_conflict, build_gather_output, make_full_t_lines,
    gather_line_coords, coords_by_code,
)
from core.skipdata import load_skipdata_folder, load_ski_file
from core.utils import lot_from_strip_id


def _setting_text(key: str, default: str = "") -> str:
    value = APP_SETTINGS.value(key, default)
    return str(value) if value is not None else default


class GatheringWorker(QThread):
    progress = Signal(str)
    finished = Signal(list, str, dict)
    failed = Signal(str)
    date_selection_required = Signal(list)  # list of str (YYYY-MM-DD)

    def __init__(
        self,
        mode: str,
        ip_list_str: str,
        base_path_template: str,
        model: str,
        lot: str,
        x_count: int,
        y_count: int,
        allowance: int,
        fill_code: str,
        excluded_codes: set[str],
        excluded_strip_ids: set[str],
        skipdata_path: str = "",
        ski_path: str = "",
        lookback_days: int = 0,
    ):
        super().__init__()
        self.mode = mode
        self.ip_list_str = ip_list_str
        self.base_path_template = base_path_template
        self.model = model
        self.lot = lot
        self.x_count = x_count
        self.y_count = y_count
        self.allowance = allowance
        self.fill_code = fill_code
        self.excluded_codes = excluded_codes
        self.excluded_strip_ids = excluded_strip_ids
        self.skipdata_path = skipdata_path
        self.ski_path = ski_path
        self.lookback_days = max(0, int(lookback_days))
        self._stop = False
        import threading
        self.rework_event = threading.Event()
        self.rework_start_timestamp = 0.0

    def set_rework_date(self, timestamp: float) -> None:
        self.rework_start_timestamp = timestamp
        self.rework_event.set()

    def stop(self) -> None:
        self._stop = True
        self.rework_event.set()

    def run(self) -> None:
        try:
            merged: dict[str, GatherStrip] = {}
            paths = self.build_paths()
            self.progress.emit(f"{self.mode} 검색 시작: {len(paths)}개 PC 경로")
            self.progress.emit(f"[SEARCH] " + (f"최근 {self.lookback_days}일" if self.lookback_days else "전체 기간") + " / 생성·수정 시각 기준 / LOT 공통 디스크 캐시 사용")
            search_stats = {}
            txt_counts = {}
            status: list[dict] = []
            parent_warnings: list[dict] = []
            duplicate_conflicts: list[dict] = []
            replaced_strip_count = 0
            smart_replaced_count = 0
            bbt_map = load_skipdata_folder(Path(self.skipdata_path)) if self.skipdata_path and Path(self.skipdata_path).is_dir() else {}
            ski_map = load_ski_file(Path(self.ski_path)) if self.ski_path and Path(self.ski_path).is_file() else {}
            if ski_map:
                self.progress.emit(f"[SMART] SKI 기준 중복 선택 활성화: SKI {len(ski_map)} strip / BBT {len(bbt_map)} strip")

            parsed_by_path: dict[Path, list[tuple[Path, GatherStrip]]] = {}

            for index, path in enumerate(paths, start=1):
                if self._stop:
                    break
                if not path.exists():
                    self.progress.emit(f"[SKIP] 경로 없음: {path}")
                    status.append({"path": str(path), "status": _("경로 없음"), "txt_files": 0, "matched_strips": 0})
                    continue
                self.progress.emit(f"[{index}/{len(paths)}] {path} TXT 검색")
                txt_files = cached_txt_files(path, self.lookback_days, search_stats)
                txt_counts[path] = len(txt_files)
                direct_parent_hits = find_parent_gather_hits(path, self.lot, direct_only=True, lookback_days=self.lookback_days, stats=search_stats)
                if direct_parent_hits:
                    parent_warnings.append({"path": str(path), "parent_hits": direct_parent_hits, "scope": _("1단계 상위 폴더")})
                    self.progress.emit(f"[게더링 누락 점검] 1단계 상위 폴더 TXT에서 LOT 데이터 발견: {path.parent}")
                    for hit in direct_parent_hits[:5]:
                        self.progress.emit(f"  - {hit}")
                if not txt_files:
                    hits = direct_parent_hits or find_parent_gather_hits(path, self.lot, lookback_days=self.lookback_days, stats=search_stats)
                    if hits:
                        if not direct_parent_hits:
                            parent_warnings.append({"path": str(path), "parent_hits": hits, "scope": _("상위 경로")})
                        self.progress.emit(f"[게더링 확인] 지정 경로에는 TXT 없음, 상위 경로에서 LOT 데이터 발견: {path}")
                        for hit in hits[:5]:
                            self.progress.emit(f"  - {hit}")
                    else:
                        self.progress.emit(f"[NO DATA] TXT 없음: {path}")
                    status.append({"path": str(path), "status": _("TXT 없음"), "txt_files": 0, "matched_strips": 0})
                    continue

                parsed_for_path = []
                for txt_file in txt_files:
                    if self._stop:
                        break
                    try:
                        parsed = parse_gather_file(txt_file, self.lot, search_stats)
                    except Exception as exc:
                        self.progress.emit(f"파싱 실패: {txt_file} ({exc})")
                        continue
                    for strip_id, item in parsed.items():
                        parsed_for_path.append((txt_file, clone_gather_strip(item)))
                parsed_by_path[path] = parsed_for_path
                self.progress.emit(f"[CACHE] 메모리 재사용 {search_stats.get('memory_hits',0)} / 디스크 재사용 {search_stats.get('disk_hits',0)} / 새로 읽음 {search_stats.get('remote_reads',0)} / 기간 제외 {search_stats.get('old_skipped',0)}")

            # 날짜 비교를 위한 파싱된 모든 아이템 수집
            all_items = [item for items in parsed_by_path.values() for _, item in items]
            if all_items:
                from datetime import datetime
                unique_dates = {datetime.fromtimestamp(item.source_mtime).strftime("%Y-%m-%d") for item in all_items}
                if len(unique_dates) > 1:
                    self.progress.emit(f"[재작업 체크] 데이터에 여러 날짜가 존재합니다. 사용자 확인 대기 중...")
                    self.date_selection_required.emit(sorted(list(unique_dates)))
                    self.rework_event.wait()
                    if self._stop:
                        return
                    if self.rework_start_timestamp > 0.0:
                        self.progress.emit(f"[필터링] 선택된 날짜 기준 필터링 적용")

            for path in paths:
                if self._stop:
                    break
                if path not in parsed_by_path:
                    continue
                
                txt_files_count = txt_counts.get(path, 0)
                matched_before = len(merged)
                replaced_before = replaced_strip_count
                
                for txt_file, candidate in parsed_by_path[path]:
                    if self.rework_start_timestamp > 0.0 and candidate.source_mtime < self.rework_start_timestamp:
                        continue
                    key = candidate.strip_id.upper()
                    target = merged.get(key)
                    if target is None:
                        merged[key] = candidate
                    else:
                        duplicate_conflicts.append(build_gather_duplicate_conflict(target, candidate, bbt_map, ski_map))
                        if not should_replace_gather_strip_smart(target, candidate, bbt_map, ski_map):
                            target.replaced_count += candidate.replaced_count + 1
                            target.sources = unique_lines(target.sources + candidate.sources)
                            continue
                        if ski_map and score_gather_strip_against_ski(target, bbt_map, ski_map) != score_gather_strip_against_ski(candidate, bbt_map, ski_map):
                            smart_replaced_count += 1
                        candidate.sources = unique_lines(target.sources + candidate.sources)
                        candidate.replaced_count += target.replaced_count + 1
                        merged[key] = candidate
                        replaced_strip_count += 1
                matched_after = len(merged)
                matched_delta = matched_after - matched_before
                replaced_delta = replaced_strip_count - replaced_before
                status.append(
                    {
                        "path": str(path),
                        "status": ("OK" if matched_delta or replaced_delta else _("대상 LOT 데이터 없음"))
                        + (f" / 갱신 {replaced_delta}개" if replaced_delta else ""),
                        "txt_files": txt_files_count,
                        "matched_strips": matched_delta,
                    }
                )
                if not matched_delta and txt_files_count > 0:
                    self.progress.emit(f"[NO DATA] 대상 LOT 데이터 없음: {path} (TXT {txt_files_count}개)")

            final: list[GatherStrip] = []
            excluded_strip_count = 0
            discarded_strip_count = 0
            xout_counter: Counter[int] = Counter()

            for strip_id, item in merged.items():
                if strip_id.upper() in self.excluded_strip_ids:
                    excluded_strip_count += 1
                    continue
                item.defect_lines = [line for line in item.defect_lines if line.split(",")[-1].strip() not in self.excluded_codes]
                if len(item.defect_lines) > self.allowance:
                    discarded_strip_count += 1
                    item.defect_lines = make_full_t_lines(self.lot, item.strip_id, self.x_count, self.y_count, self.fill_code, item.defect_lines)
                    item.xout_applied = True
                xout_counter[len(item.defect_lines)] += 1
                final.append(item)

            output = build_gather_output(final, self.lot)
            summary = {
                "total_strips": len(final),
                "excluded_strip_count": excluded_strip_count,
                "discarded_strip_count": discarded_strip_count,
                "replaced_strip_count": replaced_strip_count,
                "smart_replaced_count": smart_replaced_count,
                "xout_distribution": dict(sorted(xout_counter.items())),
                "status": status,
                "parent_warnings": parent_warnings,
                "duplicate_conflicts": duplicate_conflicts,
                "cache": {
                    **search_stats,
                    "file_entries": len(GATHER_FILE_CACHE),
                    "txt_list_entries": len(GATHER_TXT_LIST_CACHE),
                },
            }
            self.finished.emit(final, output, summary)
        except Exception:
            self.failed.emit(traceback.format_exc())

    def build_paths(self) -> list[Path]:
        actual_sub_path = self.base_path_template.replace("{model}", self.model).replace("{mode}", self.mode).strip().strip("\\")
        paths: list[Path] = []
        for ip in self.parse_ips():
            paths.append(Path(f"\\\\{ip}\\{actual_sub_path}"))
        return paths

    def parse_ips(self) -> list[str]:
        ips: list[str] = []
        for part in [p.strip() for p in self.ip_list_str.split(",") if p.strip()]:
            if "-" not in part:
                ips.append(part if "." in part else f"{self.default_ip_prefix()}{part}")
                continue
            start, end = [p.strip() for p in part.split("-", 1)]
            base_ip = self.default_ip_prefix()
            if "." in start:
                base_ip = ".".join(start.split(".")[:-1]) + "."
                start = start.split(".")[-1]
            if "." in end:
                end = end.split(".")[-1]
            for value in range(int(start), int(end) + 1):
                ips.append(f"{base_ip}{value}")
        return sorted(set(ips))

    def default_ip_prefix(self) -> str:
        return "192.168.0." if self.mode.upper() == "FVI" else "110.15.136."


class StripGatherSearchThread(QThread):
    progress = Signal(str)
    finished = Signal(list)
    failed = Signal(str)

    def __init__(self, strip_id: str, model: str):
        super().__init__()
        self.strip_id = strip_id.strip().upper()
        self.model = model.strip()

    def build_paths(self, mode: str) -> list[Path]:
        default_ip = "121-130" if mode == "VRS" else "101-110" if mode == "FVI" else "108"
        default_path = (
            "VRSData\\ResultData\\BDM_KINSUS\\BDM_Backup"
            if mode == "VRS"
            else "bdm_fkinsus\\BDM_Backup"
            if mode == "FVI"
            else "bdm_kinsus\\BDM_BACKUP"
        )
        ip_text = _setting_text(f"gathering/{mode}/ip", default_ip)
        path_template = _setting_text(f"gathering/{mode}/path", default_path)
        worker = GatheringWorker(
            mode,
            ip_text,
            path_template,
            self.model,
            lot_from_strip_id(self.strip_id),
            0,
            0,
            0,
            "170",
            set(),
            set(),
        )
        return worker.build_paths()

    def scan_txt_file(self, mode: str, txt_file: Path, scope: str) -> dict | None:
        try:
            parsed = parse_gather_file(txt_file, lot_from_strip_id(self.strip_id))
        except Exception as exc:
            return {"mode": mode, "scope": scope, "path": str(txt_file), "error": str(exc)}
        block = parsed.get(self.strip_id)
        if not block:
            return None
        return {
            "mode": mode,
            "scope": scope,
            "path": str(txt_file),
            "strip_id": self.strip_id,
            "defect_line_count": len(block.defect_lines),
            "coord_count": len(gather_line_coords(block.defect_lines)),
            "codes": {code: len(coords) for code, coords in sorted(coords_by_code(block.defect_lines).items())},
            "defect_lines": list(block.defect_lines[:200]),
        }

    def scan_path(self, mode: str, base_path: Path) -> list[dict]:
        results: list[dict] = []
        if not base_path.exists():
            results.append({"mode": mode, "scope": _("지정 경로"), "path": str(base_path), "error": _("경로 없음")})
            return results
        scopes = [(_("지정 경로"), base_path)]
        parent = base_path.parent
        if parent and parent != base_path:
            scopes.append((_("1단계 상위 폴더"), parent))
        seen_files: set[str] = set()
        for scope, path in scopes:
            try:
                txt_files = sorted(
                    path.rglob("*.txt") if scope == _("지정 경로") else path.glob("*.txt"),
                    key=lambda item: str(item),
                )
            except OSError as exc:
                results.append({"mode": mode, "scope": scope, "path": str(path), "error": str(exc)})
                continue
            for txt_file in txt_files:
                key = str(txt_file).upper()
                if key in seen_files:
                    continue
                seen_files.add(key)
                hit = self.scan_txt_file(mode, txt_file, scope)
                if hit:
                    results.append(hit)
        return results

    def run(self) -> None:
        try:
            all_results: list[dict] = []
            for mode in ("IVS", "VRS", "FVI"):
                paths = self.build_paths(mode)
                self.progress.emit(f"[{mode}] {len(paths)}개 경로 검색")
                for path in paths:
                    all_results.extend(self.scan_path(mode, path))
            self.finished.emit(all_results)
        except Exception:
            self.failed.emit(traceback.format_exc())
