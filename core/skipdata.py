"""BBT skipdata (.rpt) and SKI file loading with caching."""
from __future__ import annotations
from core.i18n import _

import re
from collections import Counter, defaultdict
from pathlib import Path

from core.utils import read_text_lines

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------
RPT_STRIP_RE = re.compile(r"^\[([^\]]+)\]\s*$")
RPT_XY_RE = re.compile(r"\bX\s*:\s*(\d+)\s+Y\s*:\s*(\d+)\s*=", re.IGNORECASE)
RPT_CODE_RE = re.compile(r"-\s*([A-Za-z0-9_]+)\s*$")

# ---------------------------------------------------------------------------
# Module-level caches
# ---------------------------------------------------------------------------
SKIPDATA_CACHE: dict[tuple, dict[str, set[tuple[int, int]]]] = {}
SKIPDATA_SOURCE_CACHE: dict[tuple, dict[str, list[str]]] = {}
SKIPDATA_CODE_CACHE: dict[tuple, dict[str, dict[str, set[tuple[int, int]]]]] = {}
SKI_FILE_CACHE: dict[tuple[str, float, int], dict[str, set[tuple[int, int]]]] = {}


def rpt_fingerprint(path: Path, files: list[Path]) -> tuple:
    # A maximum timestamp misses edits to older files and same-count replacements.
    return (str(path), tuple(sorted((str(f), f.stat().st_mtime_ns, f.stat().st_size) for f in files)))


def load_skipdata_folder(path: Path | None, source_paths=None) -> dict[str, set[tuple[int, int]]]:
    if path is None or not str(path).strip():
        return {}
    if not path.is_dir():
        raise ValueError(f"Skipdata 폴더가 아닙니다: {path}")
    rpt_files = list(path.glob("*.rpt"))
    cache_key = rpt_fingerprint(path, rpt_files)
    cached = SKIPDATA_CACHE.get(cache_key)
    if cached is not None and cache_key in SKIPDATA_SOURCE_CACHE:
        if source_paths is not None:
            source_paths.update(SKIPDATA_SOURCE_CACHE[cache_key])
        return cached

    result: dict[str, set[tuple[int, int]]] = defaultdict(set)
    sources = defaultdict(set)
    for rpt_file in rpt_files:
        current_strip = rpt_file.stem.strip().upper()
        for raw in read_text_lines(rpt_file):
            line = raw.strip()
            if not line:
                continue
            strip_match = RPT_STRIP_RE.match(line)
            if strip_match:
                current_strip = strip_match.group(1).strip().upper()
                continue
            xy_match = RPT_XY_RE.search(line)
            if current_strip and xy_match:
                result[current_strip].add((int(xy_match.group(1)), int(xy_match.group(2))))
                sources[current_strip].add(str(rpt_file))

    normalized = {strip_id: coords for strip_id, coords in result.items() if coords}
    SKIPDATA_CACHE[cache_key] = normalized
    SKIPDATA_SOURCE_CACHE[cache_key] = {strip: sorted(paths) for strip, paths in sources.items()}
    if source_paths is not None:
        source_paths.update(SKIPDATA_SOURCE_CACHE[cache_key])
    return normalized


def load_skipdata_code_folder(path: Path | None) -> dict[str, dict[str, set[tuple[int, int]]]]:
    if path is None or not str(path).strip():
        return {}
    if not path.is_dir():
        raise ValueError(f"Skipdata 폴더가 아닙니다: {path}")
    rpt_files = list(path.glob("*.rpt"))
    cache_key = rpt_fingerprint(path, rpt_files)
    cached = SKIPDATA_CODE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    result: dict[str, dict[str, set[tuple[int, int]]]] = defaultdict(lambda: defaultdict(set))
    for rpt_file in rpt_files:
        current_strip = rpt_file.stem.strip().upper()
        for raw in read_text_lines(rpt_file):
            line = raw.strip()
            if not line:
                continue
            strip_match = RPT_STRIP_RE.match(line)
            if strip_match:
                current_strip = strip_match.group(1).strip().upper()
                continue
            xy_match = RPT_XY_RE.search(line)
            if not current_strip or not xy_match:
                continue
            code_match = RPT_CODE_RE.search(line)
            code = code_match.group(1).upper() if code_match else "UNKNOWN"
            result[current_strip][code].add((int(xy_match.group(1)), int(xy_match.group(2))))

    normalized = {
        strip_id: {code: coords for code, coords in by_code.items() if coords}
        for strip_id, by_code in result.items()
        if by_code
    }
    SKIPDATA_CODE_CACHE[cache_key] = normalized
    return normalized


def load_ski_file(path: Path | None) -> dict[str, set[tuple[int, int]]]:
    if path is None or not str(path).strip():
        return {}
    if not path.is_file():
        raise ValueError(f"SKI 파일이 아닙니다: {path}")
    stat_info = path.stat()
    cache_key = (str(path), stat_info.st_mtime, stat_info.st_size)
    cached = SKI_FILE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    result: dict[str, set[tuple[int, int]]] = defaultdict(set)
    for raw in read_text_lines(path):
        line = raw.strip()
        if not line or ":" in line:
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 3:
            continue
        strip_id = parts[0].upper()
        try:
            result[strip_id].add((int(parts[1]), int(parts[2])))
        except ValueError:
            continue
    normalized = {strip_id: coords for strip_id, coords in result.items() if coords}
    SKI_FILE_CACHE[cache_key] = normalized
    return normalized


def build_ski_xout_validation(
    ski_map: dict[str, set[tuple[int, int]]],
    strip_ids: set[str],
    units_per_strip: int,
    lot_id: str = "",
) -> dict:
    if not ski_map:
        return {}
    lot_key = lot_id.strip().upper()
    ski_lot_strip_ids = {
        strip_id for strip_id in ski_map if not lot_key or strip_id.startswith(lot_key)
    }
    validation_strip_ids = set(strip_ids) | ski_lot_strip_ids
    rows: list[dict] = []
    distribution: Counter[int] = Counter()
    full_xout_strips: list[str] = []
    total_ski_units = 0
    for strip_id in sorted(validation_strip_ids):
        count = min(len(ski_map.get(strip_id, set())), units_per_strip)
        total_ski_units += count
        distribution[count] += 1
        is_full = count == units_per_strip
        if is_full:
            full_xout_strips.append(strip_id)
        rows.append(
            {
                "strip_id": strip_id,
                "ski_xout_count": count,
                "status": _("허용수초과 스트립") if is_full else _("검증"),
            }
        )
    return {
        "ski_strip_count": sum(1 for strip_id in validation_strip_ids if ski_map.get(strip_id)),
        "ski_total_units": total_ski_units,
        "ski_distribution": dict(sorted(distribution.items())),
        "ski_full_xout_strip_count": len(full_xout_strips),
        "ski_full_xout_strips": full_xout_strips,
        "ski_only_strips": sorted(ski_lot_strip_ids - set(strip_ids)),
        "rows": rows,
    }


def classify_ivs_dummy_9999_strips(
    candidate_strips: set[str] | None,
    ski_map: dict[str, set[tuple[int, int]]],
    units_per_strip: int,
    gather_strip_ids: set[str] | None = None,
) -> dict:
    candidates = {strip_id.strip().upper() for strip_id in (candidate_strips or set()) if strip_id.strip()}
    gather_strip_ids = gather_strip_ids or set()
    applied: list[str] = []
    recovered: list[str] = []
    unverified: list[str] = []
    details: list[dict] = []
    for strip_id in sorted(candidates):
        has_ski = bool(ski_map)
        ski_count = min(len(ski_map.get(strip_id, set())), units_per_strip) if has_ski else None
        has_gathering_data = strip_id in gather_strip_ids
        if not has_ski:
            applied.append(strip_id)
            unverified.append(strip_id)
            status = _("SKI 미선택으로 양폐 적용")
        elif ski_count >= units_per_strip:
            applied.append(strip_id)
            status = _("SKI 기준 양폐 적용")
        else:
            recovered.append(strip_id)
            status = _("SKI 기준 복구/취소")
        details.append(
            {
                "strip_id": strip_id,
                "ski_xout_count": ski_count,
                "has_gathering_data": has_gathering_data,
                "status": status,
            }
        )
    return {
        "candidate_strips": sorted(candidates),
        "applied_strips": applied,
        "recovered_strips": recovered,
        "unverified_strips": unverified,
        "details": details,
    }


def build_bbt_full_unit_summary(
    bbt_map: dict[str, set[tuple[int, int]]],
    units_per_strip: int,
    lot_id: str = "",
    expected_strip_ids: set[str] | None = None,
) -> dict:
    if not bbt_map:
        return {}
    lot_key = lot_id.strip().upper()
    candidate_ids = set(expected_strip_ids or set())
    if not candidate_ids:
        candidate_ids = {
            strip_id for strip_id in bbt_map if not lot_key or strip_id.startswith(lot_key)
        }
    full_strips = sorted(
        strip_id for strip_id in candidate_ids if len(bbt_map.get(strip_id, set())) >= units_per_strip
    )
    return {
        "strip_count": len(full_strips),
        "strip_ids": full_strips,
        "unit_count": len(full_strips) * units_per_strip,
    }
