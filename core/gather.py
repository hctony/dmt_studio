"""Gathering TXT file parsing and merge logic."""
from __future__ import annotations
from core.i18n import _

import re
from collections import defaultdict
from pathlib import Path

from core.models import GatherStrip
from core.utils import read_text_lines

# ---------------------------------------------------------------------------
# Module-level caches (cleared on restart, keyed by path/mtime/size/lot)
# ---------------------------------------------------------------------------
GATHER_FILE_CACHE: dict[tuple[str, float, int, str], dict[str, GatherStrip]] = {}
GATHER_TXT_LIST_CACHE: dict[tuple[str, float], list[Path]] = {}


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def unique_lines(lines: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for line in lines:
        if line in seen:
            continue
        result.append(line)
        seen.add(line)
    return result


def parse_xy_from_gather_line(line: str) -> tuple[int, int] | None:
    parts = [part.strip() for part in line.split(",")]
    if len(parts) < 4:
        return None
    try:
        return int(parts[2]), int(parts[3])
    except ValueError:
        return None


def parse_gather_code(line: str) -> str:
    parts = [part.strip() for part in line.split(",")]
    return parts[5] if len(parts) >= 6 and parts[5] else "UNKNOWN"


def gather_line_coords(lines: list[str]) -> set[tuple[int, int]]:
    return {coord for line in lines if (coord := parse_xy_from_gather_line(line)) is not None}


def format_xy_coords(coords: set[tuple[int, int]]) -> list[str]:
    return [f"X:{x:03d} Y:{y:03d}" for x, y in sorted(coords, key=lambda item: (item[1], item[0]))]


def coords_by_code(lines: list[str]) -> dict[str, set[tuple[int, int]]]:
    result: dict[str, set[tuple[int, int]]] = defaultdict(set)
    for line in lines:
        coord = parse_xy_from_gather_line(line)
        if coord is None:
            continue
        result[parse_gather_code(line)].add(coord)
    return result


def merge_code_coords(target: dict[str, set[tuple[int, int]]], source: dict[str, set[tuple[int, int]]]) -> None:
    for code, coords in source.items():
        target[code].update(coords)


def make_full_t_lines(lot: str, strip_id: str, x_count: int, y_count: int, code: str, existing_lines: list[str] | None = None) -> list[str]:
    lines: list[str] = []
    existing_map: dict[tuple[int, int], str] = {}
    if existing_lines:
        for line in existing_lines:
            coord = parse_xy_from_gather_line(line)
            if coord is not None:
                existing_map[coord] = line

    for y in range(1, y_count + 1):
        for x in range(1, x_count + 1):
            coord = (x, y)
            if coord in existing_map:
                lines.append(existing_map[coord])
            else:
                lines.append(f"{lot},{strip_id},{x},{y},T,{code}")
    return lines


# ---------------------------------------------------------------------------
# GatherStrip comparison / merge
# ---------------------------------------------------------------------------

def should_replace_gather_strip(current: GatherStrip, candidate: GatherStrip) -> bool:
    # 빈 블록이 기존의 정상 블록을 덮어쓰는 것을 방지
    if len(candidate.defect_lines) == 0 and len(current.defect_lines) > 0:
        return False
    if candidate.source_mtime != current.source_mtime:
        return candidate.source_mtime > current.source_mtime
    return candidate.source_order >= current.source_order


def score_gather_strip_against_ski(
    item: GatherStrip,
    bbt_map: dict[str, set[tuple[int, int]]],
    ski_map: dict[str, set[tuple[int, int]]],
) -> tuple[int, int, int, int] | None:
    strip_id = item.strip_id.strip().upper()
    ski_coords = set(ski_map.get(strip_id, set()))
    if not ski_coords:
        return None
    expected_added = ski_coords - set(bbt_map.get(strip_id, set()))
    coords = gather_line_coords(item.defect_lines)
    missing = len(expected_added - coords)
    extra = len(coords - expected_added)
    return (missing + extra, extra, missing, abs(len(coords) - len(expected_added)))


def should_replace_gather_strip_smart(
    current: GatherStrip,
    candidate: GatherStrip,
    bbt_map: dict[str, set[tuple[int, int]]] | None = None,
    ski_map: dict[str, set[tuple[int, int]]] | None = None,
) -> bool:
    if ski_map:
        current_score = score_gather_strip_against_ski(current, bbt_map or {}, ski_map)
        candidate_score = score_gather_strip_against_ski(candidate, bbt_map or {}, ski_map)
        if current_score is not None and candidate_score is not None and current_score != candidate_score:
            return candidate_score < current_score
    return should_replace_gather_strip(current, candidate)


def clone_gather_strip(item: GatherStrip) -> GatherStrip:
    return GatherStrip(
        lot=item.lot,
        strip_id=item.strip_id,
        defect_lines=list(item.defect_lines),
        sources=list(item.sources),
        xout_applied=item.xout_applied,
        source_mtime=item.source_mtime,
        source_order=item.source_order,
        replaced_count=item.replaced_count,
    )


def merge_gather_strips(a: GatherStrip, b: GatherStrip) -> GatherStrip:
    return GatherStrip(
        lot=a.lot or b.lot,
        strip_id=a.strip_id or b.strip_id,
        defect_lines=unique_lines(a.defect_lines + b.defect_lines),
        sources=unique_lines(a.sources + b.sources),
        xout_applied=a.xout_applied or b.xout_applied,
        source_mtime=max(a.source_mtime, b.source_mtime),
        source_order=max(a.source_order, b.source_order),
        replaced_count=a.replaced_count + b.replaced_count,
    )


# ---------------------------------------------------------------------------
# Payload serialisation
# ---------------------------------------------------------------------------

def gather_strip_to_payload(item: GatherStrip) -> dict:
    coords = gather_line_coords(item.defect_lines)
    return {
        "lot": item.lot,
        "strip_id": item.strip_id,
        "defect_lines": list(item.defect_lines),
        "coords": format_xy_coords(coords),
        "coord_count": len(coords),
        "line_count": len(item.defect_lines),
        "sources": list(item.sources),
        "source_mtime": item.source_mtime,
        "source_order": item.source_order,
    }


def gather_strip_from_payload(payload: dict) -> GatherStrip:
    return GatherStrip(
        lot=str(payload.get("lot", "")),
        strip_id=str(payload.get("strip_id", "")),
        defect_lines=[str(line) for line in payload.get("defect_lines", [])],
        sources=[str(source) for source in payload.get("sources", [])],
        source_mtime=float(payload.get("source_mtime", 0.0) or 0.0),
        source_order=int(payload.get("source_order", 0) or 0),
    )


def build_gather_duplicate_conflict(
    current: GatherStrip,
    candidate: GatherStrip,
    bbt_map: dict[str, set[tuple[int, int]]] | None = None,
    ski_map: dict[str, set[tuple[int, int]]] | None = None,
) -> dict:
    strip_id = current.strip_id.strip().upper()
    current_coords = gather_line_coords(current.defect_lines)
    candidate_coords = gather_line_coords(candidate.defect_lines)
    bbt_coords = set((bbt_map or {}).get(strip_id, set()))
    ski_coords = set((ski_map or {}).get(strip_id, set()))
    return {
        "strip_id": strip_id,
        "current": gather_strip_to_payload(current),
        "candidate": gather_strip_to_payload(candidate),
        "current_only": format_xy_coords(current_coords - candidate_coords),
        "candidate_only": format_xy_coords(candidate_coords - current_coords),
        "common": format_xy_coords(current_coords & candidate_coords),
        "bbt_coords": format_xy_coords(bbt_coords),
        "ski_coords": format_xy_coords(ski_coords),
        "current_score": score_gather_strip_against_ski(current, bbt_map or {}, ski_map or {}),
        "candidate_score": score_gather_strip_against_ski(candidate, bbt_map or {}, ski_map or {}),
    }


# ---------------------------------------------------------------------------
# File parsing with cache
# ---------------------------------------------------------------------------

def parse_gather_file(path: Path, lot: str, stats: dict | None = None) -> dict[str, GatherStrip]:
    try:
        stat_info = path.stat()
        source_mtime = stat_info.st_mtime
        cache_key = (str(path), stat_info.st_mtime_ns, stat_info.st_size, lot.strip().upper(), stat_info.st_ctime_ns)
        cached = GATHER_FILE_CACHE.get(cache_key)
        if cached is not None:
            if stats is not None: stats['memory_hits'] = stats.get('memory_hits', 0) + 1
            return cached
    except OSError:
        return {}

    result: dict[str, GatherStrip] = {}
    from core.gather_index import read_indexed_lines
    lines = read_indexed_lines(path, stat_info, lot, read_text_lines, stats)
    current_lot = ""
    current_strip = ""
    current_lines: list[str] = []
    block_order = 0

    def flush() -> None:
        nonlocal current_lot, current_strip, current_lines, block_order
        if not current_strip:
            return
        source = str(path)
        block_order += 1
        key = current_strip.upper()
        candidate = GatherStrip(
            lot=current_lot,
            strip_id=current_strip,
            defect_lines=unique_lines(current_lines),
            sources=[source],
            source_mtime=source_mtime,
            source_order=block_order,
        )
        target = result.get(key)
        if target is None:
            result[key] = candidate
        elif should_replace_gather_strip(target, candidate):
            candidate.sources = unique_lines(target.sources + candidate.sources)
            candidate.replaced_count = target.replaced_count + 1
            result[key] = candidate
        else:
            target.replaced_count += 1
            if source not in target.sources:
                target.sources.append(source)

    expected_lot = lot.strip().upper()
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split(",")]
        if parts[-1].upper() in {"EOS", "EOL"}:
            flush()
            current_lot = ""
            current_strip = ""
            current_lines = []
            continue

        if len(parts) == 3 and parts[0].isdigit():
            flush()
            header_lot = parts[1].upper()
            strip_id = parts[2].upper()
            if not expected_lot or header_lot == expected_lot or strip_id.startswith(expected_lot):
                current_lot = parts[1]
                current_strip = parts[2]
                current_lines = []
            else:
                current_lot = ""
                current_strip = ""
                current_lines = []
            continue

        if current_strip and len(parts) >= 5:
            found_strip = False
            for p in parts[:2]:
                p_upper = p.upper()
                if p_upper == current_strip.upper() or current_strip.upper().endswith(p_upper) or current_strip.upper().startswith(p_upper):
                    found_strip = True
                    break
            if found_strip:
                current_lines.append(line)
    flush()
    if cache_key[0]:
        GATHER_FILE_CACHE[cache_key] = result
    return result


def cached_txt_files(path: Path, lookback_days: int = 0, stats: dict | None = None, recursive: bool = True) -> list[Path]:
    import os, time
    from core.gather_index import recent_file
    try:
        # Descendant directory changes do not update the root's timestamp.
        # Re-enumerate metadata; the expensive TXT parsing remains cached separately.
        stamps = {}
        pending = [path]
        cutoff = time.time() - lookback_days * 86400 if lookback_days else None
        while pending:
            directory = pending.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        if recursive: pending.append(Path(entry.path))
                    elif entry.name.lower().endswith('.txt') and entry.is_file():
                        info = entry.stat()
                        if cutoff is not None and not recent_file(info, cutoff):
                            if stats is not None: stats['old_skipped'] = stats.get('old_skipped', 0) + 1
                            continue
                        stamps[Path(entry.path)] = info
        files = list(stamps)
        cache_key = (str(path), tuple(sorted((str(item), info.st_mtime_ns, info.st_size) for item, info in stamps.items())))
        cached = GATHER_TXT_LIST_CACHE.get(cache_key)
        if cached is not None:
            return cached
    except OSError:
        return []
    files.sort(key=lambda item: (stamps[item].st_mtime_ns, str(item)))
    for old_key in list(GATHER_TXT_LIST_CACHE):
        if old_key[0] == str(path):
            del GATHER_TXT_LIST_CACHE[old_key]
    GATHER_TXT_LIST_CACHE[cache_key] = files
    return files


def find_parent_gather_hits(path: Path, lot: str, max_hits: int = 20, direct_only: bool = False, lookback_days: int = 0, stats: dict | None = None) -> list[str]:
    parent = path.parent
    if not parent or parent == path or not parent.exists():
        return []
    hits: list[str] = []
    try:
        txt_files = cached_txt_files(parent, lookback_days, stats, recursive=not direct_only)
    except OSError:
        return []
    for txt_file in txt_files:
        if path in txt_file.parents:
            continue
        try:
            if lookback_days:
                import time
                from core.gather_index import recent_file
                if not recent_file(txt_file.stat(), time.time()-lookback_days*86400):
                    continue
            parsed = parse_gather_file(txt_file, lot, stats)
        except Exception:
            continue
        if parsed:
            hits.append(str(txt_file))
            if len(hits) >= max_hits:
                break
    return hits


def build_gather_output(strips: list[GatherStrip], lot: str) -> str:
    chunks: list[str] = []
    for item in sorted(strips, key=lambda strip: strip.strip_id):
        chunks.append(f"{len(item.defect_lines)},{lot},{item.strip_id}")
        chunks.extend(item.defect_lines)
        chunks.append(f"{lot},{item.strip_id},EOS")
    return "\n".join(chunks) + ("\n" if chunks else "")


# ---------------------------------------------------------------------------
# Latest-process block loading
# ---------------------------------------------------------------------------

def load_latest_process_blocks(
    txt_paths: list[Path],
    lot_id: str,
    bbt_map: dict[str, set[tuple[int, int]]] | None = None,
    ski_map: dict[str, set[tuple[int, int]]] | None = None,
    selected_sources: dict[str, str] | None = None,
) -> tuple[dict[str, GatherStrip], int]:
    merged: dict[str, GatherStrip] = {}
    replaced_count = 0
    for txt_path in sorted(txt_paths, key=lambda path: (path.stat().st_mtime, str(path))):
        parsed = parse_gather_file(txt_path, lot_id)
        for strip_id, item in parsed.items():
            candidate = clone_gather_strip(item)
            target = merged.get(strip_id.upper())
            if target is None:
                merged[strip_id.upper()] = candidate
                if selected_sources is not None:
                    selected_sources[strip_id.upper()] = str(txt_path)
            elif should_replace_gather_strip_smart(target, candidate, bbt_map, ski_map):
                candidate.sources = unique_lines(target.sources + candidate.sources)
                candidate.replaced_count += target.replaced_count + 1
                merged[strip_id.upper()] = candidate
                if selected_sources is not None:
                    selected_sources[strip_id.upper()] = str(txt_path)
                replaced_count += 1
            else:
                target.replaced_count += candidate.replaced_count + 1
                target.sources = unique_lines(target.sources + candidate.sources)
    return merged, replaced_count


def summarize_gather_block_by_process(process: str, block: GatherStrip | None) -> dict:
    if block is None:
        return {
            "process": process,
            "coord_count": 0,
            "line_count": 0,
            "codes": {},
            "sources": [],
            "defect_lines": [],
        }
    by_code = coords_by_code(block.defect_lines)
    return {
        "process": process,
        "coord_count": len(gather_line_coords(block.defect_lines)),
        "line_count": len(block.defect_lines),
        "codes": {code: format_xy_coords(coords) for code, coords in sorted(by_code.items())},
        "sources": list(block.sources),
        "defect_lines": list(block.defect_lines),
    }


def assign_coords_by_process_priority(
    by_process_code: dict[str, dict[str, set[tuple[int, int]]]],
    bbt_coords: set[tuple[int, int]],
    priority: list[str],
) -> dict[str, dict[str, set[tuple[int, int]]]]:
    assigned: dict[str, dict[str, set[tuple[int, int]]]] = {process: defaultdict(set) for process in priority}
    occupied = set(bbt_coords)
    for process in priority:
        for code, coords in by_process_code.get(process, {}).items():
            available = coords - occupied
            if not available:
                continue
            assigned[process][code].update(available)
            occupied.update(available)
    return assigned


def score_gather_coords_against_ski(
    strip_id: str,
    coords: set[tuple[int, int]],
    bbt_map: dict[str, set[tuple[int, int]]],
    ski_map: dict[str, set[tuple[int, int]]],
) -> tuple[int, int, int, int] | None:
    ski_coords = set(ski_map.get(strip_id.strip().upper(), set()))
    if not ski_coords:
        return None
    expected_added = ski_coords - set(bbt_map.get(strip_id.strip().upper(), set()))
    missing = len(expected_added - coords)
    extra = len(coords - expected_added)
    return (missing + extra, extra, missing, abs(len(coords) - len(expected_added)))


def choose_exact_ski_duplicate_choice(
    current: GatherStrip,
    candidate: GatherStrip,
    bbt_map: dict[str, set[tuple[int, int]]] | None = None,
    ski_map: dict[str, set[tuple[int, int]]] | None = None,
    allowance: int | None = None,
    x_count: int | None = None,
    y_count: int | None = None,
) -> str | None:
    if not ski_map:
        return None
    strip_id = current.strip_id.strip().upper()
    ski_coords = set(ski_map.get(strip_id, set()))
    if not ski_coords:
        return None
    bbt_coords = set((bbt_map or {}).get(strip_id, set()))
    current_coords = gather_line_coords(current.defect_lines)
    candidate_coords = gather_line_coords(candidate.defect_lines)
    merged = merge_gather_strips(current, candidate)
    merged_coords = gather_line_coords(merged.defect_lines)

    current_exact = (bbt_coords | current_coords) == ski_coords
    candidate_exact = (bbt_coords | candidate_coords) == ski_coords
    merge_exact = (bbt_coords | merged_coords) == ski_coords
    if current_exact and candidate_exact:
        return "candidate" if should_replace_gather_strip(current, candidate) else "current"
    if current_exact:
        return "current"
    if candidate_exact:
        return "candidate"
    if merge_exact:
        return "merge"
    if allowance is not None and len(ski_coords) > allowance:
        return "merge"
    if merged_coords and merged_coords <= ski_coords:
        return "merge"
    if (
        allowance is not None
        and x_count
        and y_count
        and is_full_unit_coords(ski_coords, x_count, y_count)
        and len(bbt_coords | merged_coords) > allowance
    ):
        return "merge"
    return None


def format_parent_warnings_text(warnings: list[dict]) -> str:
    if not warnings:
        return ""
    lines = ["", _("[게더링 누락 점검] 지정 베이스 경로 밖 상위 폴더에서 LOT 데이터가 발견된 TXT:")]
    for warning in warnings:
        lines.append(f"- 기준 경로: {warning['path']} ({warning.get('scope', _('상위 경로'))})")
        lines.extend(f"  {path}" for path in warning["parent_hits"])
    return "\n".join(lines)
