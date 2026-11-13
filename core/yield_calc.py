"""Yield calculation: gathering TXT parsing and summary building."""
from __future__ import annotations
from core.yield_rates import production_units, verified_population_ids
from core.i18n import _

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

from core.models import GatherStrip, YieldStripBlock
from core.ivs import load_ivs_records_by_strip
from core.gather import (
    gather_line_coords,
    format_xy_coords,
    parse_xy_from_gather_line,
    parse_gather_code,
    coords_by_code,
    merge_code_coords,
    assign_coords_by_process_priority,
    summarize_gather_block_by_process,
    load_latest_process_blocks,
)
from core.skipdata import (
    load_skipdata_folder,
    load_skipdata_code_folder,
    load_ski_file,
    build_ski_xout_validation,
    build_bbt_full_unit_summary,
    classify_ivs_dummy_9999_strips,
)
from core.utils import read_text_lines, unit_idx_to_xy, infer_x_count_for_strip, full_unit_coords


def merge_process_code_distribution(process_code_distribution: dict) -> dict:
    by_process: Counter[str] = Counter()
    by_code: Counter[str] = Counter()
    for key, count in process_code_distribution.items():
        text = str(key)
        try:
            amount = int(count)
        except (TypeError, ValueError):
            continue
        if ":" in text:
            process, code = text.split(":", 1)
        else:
            process, code = "", text
        if process:
            by_process[process] += amount
        by_code[code] += amount
    return {
        "by_process": dict(sorted(by_process.items())),
        "by_defect_code": dict(sorted(by_code.items())),
        "by_process_and_defect_code": {
            str(key): int(value)
            for key, value in sorted(process_code_distribution.items())
        },
    }


def split_170_added_distribution(process_code_distribution: dict) -> dict:
    normal_process_code = {}
    code_170_process_code = {}
    for key, count in process_code_distribution.items():
        text = str(key)
        code = text.split(":", 1)[1] if ":" in text else text
        if code == "170":
            code_170_process_code[text] = int(count)
        else:
            normal_process_code[text] = int(count)
    return {
        "normal": merge_process_code_distribution(normal_process_code),
        "code_170": merge_process_code_distribution(code_170_process_code),
    }


# ---------------------------------------------------------------------------
# Parsing yield TXT (per-strip block structure)
# ---------------------------------------------------------------------------

def parse_yield_txt(path: Path) -> dict[str, YieldStripBlock]:
    strips: dict[str, YieldStripBlock] = {}
    current_lot = ""
    current_strip = ""
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_lot, current_strip, current_lines
        if not current_strip:
            return
        block = strips.setdefault(current_strip.upper(), YieldStripBlock(lot=current_lot, strip_id=current_strip))
        if block.block_count:
            block.duplicate_in_source_count += 1
        block.block_count += 1
        for line in current_lines:
            if line not in block.defect_lines:
                block.defect_lines.append(line)

    try:
        lines = read_text_lines(path)
    except OSError:
        return {}

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split(",")]
        tail = parts[-1].upper()
        if tail in {"EOS", "EOL"}:
            flush()
            current_lot = ""
            current_strip = ""
            current_lines = []
            continue
        if len(parts) == 3 and parts[0].isdigit():
            flush()
            current_lot = parts[1]
            current_strip = parts[2]
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
    return strips


# ---------------------------------------------------------------------------
# Strip ID JSON helpers
# ---------------------------------------------------------------------------

def load_strip_id_json(path: Path) -> tuple[str, set[str]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    lot_id = str(payload.get("lot_id", "")).strip().upper()
    raw_ids = payload.get("strip_ids", [])
    if not isinstance(raw_ids, list):
        raise ValueError(_("StripID JSON에는 strip_ids 배열이 필요합니다."))
    return lot_id, {str(item).strip().upper() for item in raw_ids if str(item).strip()}


def maybe_load_strip_id_json(path: Path | None) -> tuple[str, set[str]]:
    if path is None or not str(path).strip() or not path.is_file():
        return "", set()
    return load_strip_id_json(path)


def lot_id_from_blocks(blocks: Iterable[GatherStrip | YieldStripBlock], fallback: str = "") -> str:
    for block in blocks:
        lot = str(block.lot).strip().upper()
        if lot:
            return lot
        strip_id = str(block.strip_id).strip().upper()
        if "-" in strip_id:
            return strip_id.rsplit("-", 1)[0] + "-"
        elif strip_id.isdigit() and len(strip_id) >= 15:
            return strip_id[:-5]
    return fallback.strip().upper()


def mixed_lot_strips(lot_id: str, strip_ids: set[str]) -> list[str]:
    lot_key = lot_id.strip().upper()
    if not lot_key:
        return []
    return sorted(strip_id for strip_id in strip_ids if not strip_id.startswith(lot_key))


# ---------------------------------------------------------------------------
# Validation builders
# ---------------------------------------------------------------------------

def build_strip_json_validation(strip_json_ids: set[str], gather_ids: set[str], lot_id: str = "") -> dict:
    if not strip_json_ids:
        return {
            "json_strip_count": 0,
            "gather_strip_count": len(gather_ids),
            "json_only_strips": [],
            "gather_only_strips": [],
            "json_mixed_lot_strips": [],
            "gather_mixed_lot_strips": mixed_lot_strips(lot_id, gather_ids),
        }
    return {
        "json_strip_count": len(strip_json_ids),
        "gather_strip_count": len(gather_ids),
        "json_only_strips": sorted(strip_json_ids - gather_ids),
        "gather_only_strips": sorted(gather_ids - strip_json_ids),
        "json_mixed_lot_strips": mixed_lot_strips(lot_id, strip_json_ids),
        "gather_mixed_lot_strips": mixed_lot_strips(lot_id, gather_ids),
    }


def build_bbt_validation(
    bbt_map: dict[str, set[tuple[int, int]]],
    used_strip_ids: set[str],
    gather_coords_by_strip: dict[str, set[tuple[int, int]]],
    ski_map: dict[str, set[tuple[int, int]]] | None = None,
    units_per_strip: int = 0,
    xout_limit: int = 0,
) -> dict:
    ski_map = ski_map or {}
    used_bbt_strips = sorted(strip_id for strip_id in used_strip_ids if bbt_map.get(strip_id))
    bbt_only_strips = sorted(set(bbt_map) - used_strip_ids)
    total_used_bbt_units = sum(len(bbt_map.get(strip_id, set())) for strip_id in used_strip_ids)
    total_all_bbt_units = sum(len(coords) for coords in bbt_map.values())
    duplicate_with_gather_units = 0
    missing_in_ski_units = 0
    extra_in_ski_units = 0
    mismatch_rows: list[dict] = []
    for strip_id in sorted(used_strip_ids):
        bbt_coords = set(bbt_map.get(strip_id, set()))
        if not bbt_coords:
            continue
        gather_coords = set(gather_coords_by_strip.get(strip_id, set()))
        ski_coords = set(ski_map.get(strip_id, set()))
        duplicate_coords = bbt_coords & gather_coords
        duplicate_with_gather_units += len(duplicate_coords)
        expected_coords = bbt_coords | gather_coords
        missing_coords = expected_coords - ski_coords if ski_map else set()
        extra_coords = ski_coords - expected_coords if ski_map else set()
        ski_full_scrap = bool(units_per_strip and len(ski_coords) >= units_per_strip and len(expected_coords) > xout_limit)
        if ski_full_scrap:
            continue
        missing_in_ski_units += len(missing_coords)
        extra_in_ski_units += len(extra_coords)
        if missing_coords or extra_coords:
            mismatch_rows.append(
                {
                    "strip_id": strip_id,
                    "bbt_count": len(bbt_coords),
                    "gather_count": len(gather_coords),
                    "expected_count": len(expected_coords),
                    "ski_count": len(ski_coords),
                    "gather_duplicate_count": len(duplicate_coords),
                    "ski_missing_count": len(missing_coords),
                    "ski_extra_count": len(extra_coords),
                    "gather_duplicate_coords": format_xy_coords(duplicate_coords),
                    "ski_missing_coords": format_xy_coords(missing_coords),
                    "ski_extra_coords": format_xy_coords(extra_coords),
                }
            )
    return {
        "bbt_total_strips": len(bbt_map),
        "bbt_total_units": total_all_bbt_units,
        "used_bbt_strip_count": len(used_bbt_strips),
        "used_bbt_units": total_used_bbt_units,
        "bbt_only_strip_count": len(bbt_only_strips),
        "bbt_only_strips": bbt_only_strips,
        "duplicate_with_gather_units": duplicate_with_gather_units,
        "missing_in_ski_units": missing_in_ski_units,
        "extra_in_ski_units": extra_in_ski_units,
        "mismatch_rows": mismatch_rows,
    }


def sample_bbt_rpt_folder(path: Path, limit: int = 20) -> dict:
    from core.skipdata import RPT_STRIP_RE, RPT_XY_RE
    if not path.is_dir():
        return {"exists": False, "path": str(path), "rpt_files": 0, "samples": [], "errors": [_("폴더가 아닙니다.")]}
    rpt_files = sorted(path.glob("*.rpt"))
    samples: list[dict] = []
    errors: list[str] = []
    for rpt_file in rpt_files[:limit]:
        try:
            lines = read_text_lines(rpt_file)
            strip_headers = []
            coord_count = 0
            for raw in lines:
                line = raw.strip()
                strip_match = RPT_STRIP_RE.match(line)
                if strip_match:
                    strip_headers.append(strip_match.group(1).strip().upper())
                if RPT_XY_RE.search(line):
                    coord_count += 1
            samples.append(
                {
                    "file": str(rpt_file),
                    "stem": rpt_file.stem.upper(),
                    "strip_headers": strip_headers[:5],
                    "coord_count": coord_count,
                    "line_count": len(lines),
                }
            )
        except Exception as exc:
            errors.append(f"{rpt_file}: {exc}")
    return {
        "exists": True,
        "path": str(path),
        "rpt_files": len(rpt_files),
        "samples": samples,
        "errors": errors,
    }


def compare_full_xml_barcode_with_strip_json(full_xml_payload: dict, strip_json_path: Path) -> dict:
    lot_id, strip_json_ids = load_strip_id_json(strip_json_path)
    if not lot_id:
        lot_id = str(full_xml_payload.get("lot_id", "")).strip().upper()
    raw_xml_ids = full_xml_payload.get("strip_ids", [])
    if not isinstance(raw_xml_ids, list):
        raise ValueError(_("전체 XML Barcode JSON에는 strip_ids 배열이 필요합니다."))
    xml_all_ids = {str(item).strip().upper() for item in raw_xml_ids if str(item).strip()}
    xml_lot_ids = {strip_id for strip_id in xml_all_ids if lot_id and strip_id.startswith(lot_id)}

    records_by_strip: dict[str, list[dict]] = defaultdict(list)
    for record in full_xml_payload.get("records", []):
        if not isinstance(record, dict):
            continue
        strip_id = str(record.get("strip_id", "")).strip().upper()
        if strip_id:
            records_by_strip[strip_id].append(record)
    duplicates = full_xml_payload.get("duplicates", {})
    if isinstance(duplicates, dict):
        for strip_id, locations in duplicates.items():
            key = str(strip_id).strip().upper()
            if key and key not in records_by_strip and isinstance(locations, list):
                records_by_strip[key] = [item for item in locations if isinstance(item, dict)]

    xml_only = sorted(xml_lot_ids - strip_json_ids)
    strip_json_only = sorted(strip_json_ids - xml_lot_ids)
    ignored_non_lot = sorted(xml_all_ids - xml_lot_ids)
    xml_only_locations = {strip_id: records_by_strip.get(strip_id, []) for strip_id in xml_only}
    return {
        "lot_id": lot_id,
        "xml_all_count": len(xml_all_ids),
        "xml_lot_count": len(xml_lot_ids),
        "strip_json_count": len(strip_json_ids),
        "matched_count": len(xml_lot_ids & strip_json_ids),
        "xml_only": xml_only,
        "strip_json_only": strip_json_only,
        "ignored_non_lot": ignored_non_lot,
        "xml_only_locations": xml_only_locations,
    }


# ---------------------------------------------------------------------------
# Effective X-out calculation
# ---------------------------------------------------------------------------

def calculate_effective_xout(
    defect_lines: list[str],
    skip_coords: set[tuple[int, int]],
) -> tuple[int, int, int, int, int]:
    if not skip_coords:
        return len(defect_lines), 0, len(defect_lines), len(defect_lines), 0
    defect_coords = {coord for line in defect_lines if (coord := parse_xy_from_gather_line(line)) is not None}
    if not defect_coords:
        return len(skip_coords) + len(defect_lines), len(skip_coords), len(defect_lines), len(defect_lines), 0
    duplicate_count = len(defect_coords & skip_coords)
    added_count = len(defect_coords - skip_coords)
    return len(skip_coords | defect_coords), len(skip_coords), added_count, len(defect_coords), duplicate_count


# ---------------------------------------------------------------------------
# Missing strip helpers
# ---------------------------------------------------------------------------

def missing_strip_ids_from_files(strip_json_path: Path, txt_paths: list[Path]) -> tuple[str, list[str]]:
    lot_id, expected_strip_ids = load_strip_id_json(strip_json_path)
    found_strip_ids: set[str] = set()
    for txt_path in txt_paths:
        found_strip_ids.update(parse_yield_txt(txt_path).keys())
    return lot_id, sorted(expected_strip_ids - found_strip_ids)


def gather_lines_for_strip(txt_paths: list[Path], strip_id: str) -> tuple[list[str], int]:
    strip_key = strip_id.strip().upper()
    lines: list[str] = []
    block_count = 0
    for txt_path in txt_paths:
        block = parse_yield_txt(txt_path).get(strip_key)
        if not block:
            continue
        block_count += block.block_count
        for line in block.defect_lines:
            if line not in lines:
                lines.append(line)
    return lines, block_count


def gather_line_sources_for_strip(txt_paths: list[Path], strip_id: str) -> tuple[list[dict], int]:
    strip_key = strip_id.strip().upper()
    rows: list[dict] = []
    block_count = 0
    for txt_path in txt_paths:
        block = parse_yield_txt(txt_path).get(strip_key)
        if not block:
            continue
        block_count += block.block_count
        for line in block.defect_lines:
            coord = parse_xy_from_gather_line(line)
            rows.append(
                {
                    "path": str(txt_path),
                    "line": line,
                    "coord": coord,
                    "code": parse_gather_code(line),
                }
            )
    return rows, block_count


def gather_coords_for_strip(txt_paths: list[Path], strip_id: str) -> tuple[set[tuple[int, int]], int]:
    lines, block_count = gather_lines_for_strip(txt_paths, strip_id)
    coords = {coord for line in lines if (coord := parse_xy_from_gather_line(line)) is not None}
    return (coords if coords else set()), block_count


def analyze_ski_validation(
    strip_json_path: Path,
    txt_paths: list[Path],
    skipdata_path: Path,
    ski_path: Path,
) -> dict:
    lot_id, expected_strip_ids = load_strip_id_json(strip_json_path)
    bbt_map = load_skipdata_folder(skipdata_path)
    ski_map = load_ski_file(ski_path)
    blocks_by_path = [parse_yield_txt(txt_path) for txt_path in txt_paths]

    rows: list[dict] = []
    bbt_only_strips: list[str] = []
    matched_strips: list[str] = []
    mismatch_rows: list[dict] = []
    missing_in_ski: list[str] = []

    for strip_id in sorted(expected_strip_ids):
        bbt_coords = set(bbt_map.get(strip_id, set()))
        gather_coords: set[tuple[int, int]] = set()
        block_count = 0
        for blocks in blocks_by_path:
            block = blocks.get(strip_id)
            if not block:
                continue
            block_count += block.block_count
            for line in block.defect_lines:
                coord = parse_xy_from_gather_line(line)
                if coord is not None:
                    gather_coords.add(coord)
        added_coords = gather_coords - bbt_coords
        expected_final = bbt_coords | gather_coords
        ski_coords = set(ski_map.get(strip_id, set()))
        missing_coords = expected_final - ski_coords
        extra_coords = ski_coords - expected_final
        is_match = not missing_coords and not extra_coords
        bbt_only = bool(bbt_coords) and not added_coords and ski_coords == bbt_coords
        if bbt_only:
            bbt_only_strips.append(strip_id)
        if is_match:
            matched_strips.append(strip_id)
        else:
            if expected_final and not ski_coords:
                missing_in_ski.append(strip_id)
            mismatch_rows.append(
                {
                    "strip_id": strip_id,
                    "bbt_count": len(bbt_coords),
                    "gather_count": len(gather_coords),
                    "added_count": len(added_coords),
                    "expected_count": len(expected_final),
                    "ski_count": len(ski_coords),
                    "missing_count": len(missing_coords),
                    "extra_count": len(extra_coords),
                    "missing_coords": format_xy_coords(missing_coords),
                    "extra_coords": format_xy_coords(extra_coords),
                }
            )
        rows.append(
            {
                "strip_id": strip_id,
                "bbt_count": len(bbt_coords),
                "gather_count": len(gather_coords),
                "added_count": len(added_coords),
                "expected_count": len(expected_final),
                "ski_count": len(ski_coords),
                "block_count": block_count,
                "match": is_match,
                "bbt_only": bbt_only,
            }
        )

    ski_only_strips = sorted(set(ski_map) - expected_strip_ids)
    return {
        "lot_id": lot_id,
        "total_strips": len(expected_strip_ids),
        "ski_strip_count": len(ski_map),
        "bbt_only_strips": bbt_only_strips,
        "matched_strips": matched_strips,
        "mismatch_rows": mismatch_rows,
        "missing_in_ski": missing_in_ski,
        "ski_only_strips": ski_only_strips,
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# Main yield summary functions
# ---------------------------------------------------------------------------

def calculate_yield_summary(
    txt_path: Path,
    strip_json_path: Path,
    units_per_strip: int,
    xout_limit: int,
    skipdata_path: Path | None = None,
) -> dict:
    return calculate_yield_summary_many([txt_path], strip_json_path, units_per_strip, xout_limit, skipdata_path)


def calculate_yield_summary_many(
    txt_paths: list[Path],
    strip_json_path: Path | None,
    units_per_strip: int,
    xout_limit: int,
    skipdata_path: Path | None = None,
    ski_path: Path | None = None,
    ivs_dummy_scrap_strips: set[str] | None = None,
) -> dict:
    json_lot_id, strip_json_ids = maybe_load_strip_id_json(strip_json_path)
    skipdata = load_skipdata_folder(skipdata_path)
    ski_map = load_ski_file(ski_path) if ski_path and ski_path.is_file() else {}
    blocks: dict[str, YieldStripBlock] = {}
    vrs_fvi_merged_strips: set[str] = set()
    for txt_path in txt_paths:
        parsed = parse_yield_txt(txt_path)
        for strip_id, block in parsed.items():
            target = blocks.setdefault(strip_id, YieldStripBlock(lot=block.lot, strip_id=block.strip_id))
            source = str(txt_path)
            if target.source_files and source not in target.source_files:
                vrs_fvi_merged_strips.add(strip_id)
            target.source_files.add(source)
            target.duplicate_in_source_count += block.duplicate_in_source_count
            target.block_count += block.block_count
            for line in block.defect_lines:
                if line not in target.defect_lines:
                    target.defect_lines.append(line)
    dummy_9999_validation = classify_ivs_dummy_9999_strips(
        ivs_dummy_scrap_strips,
        ski_map,
        units_per_strip,
        set(blocks),
    )
    ivs_dummy_scrap_strips = set(dummy_9999_validation["applied_strips"])
    observed_strip_ids = set(blocks) | ivs_dummy_scrap_strips
    population_ids = verified_population_ids(strip_json_path, units_per_strip)
    expected_strip_ids = observed_strip_ids | population_ids
    lot_id = lot_id_from_blocks(blocks.values(), json_lot_id)
    total_strips = len(expected_strip_ids)
    total_units = production_units(total_strips, units_per_strip)

    mixed_strips = [
        strip_id for strip_id in blocks
        if lot_id and not strip_id.startswith(lot_id)
    ]
    duplicate_strips = [
        strip_id for strip_id, block in blocks.items()
        if block.duplicate_in_source_count > 0
    ]
    validation = build_strip_json_validation(strip_json_ids, observed_strip_ids, lot_id)
    txt_only_strips = validation["gather_only_strips"]
    missing_strips = validation["json_only_strips"]
    skipdata_only_strips = sorted(set(skipdata) - expected_strip_ids)

    xout_distribution: Counter[int] = Counter()
    exceeded_strips: list[str] = []
    good_strip_ids: list[str] = []
    defective_units = 0
    total_bbt_units = 0
    total_added_units = 0
    total_gather_units = 0
    total_bbt_duplicate_units = 0
    good_strip_defective_units = 0
    bbt_strip_count = 0
    gather_coords_by_strip: dict[str, set[tuple[int, int]]] = {}
    per_strip_rows: list[dict] = []

    for strip_id in sorted(expected_strip_ids):
        block = blocks.get(strip_id)
        bbt_xout_count = 0
        added_xout_count = 0
        gather_xout_count = 0
        bbt_duplicate_count = 0
        if block:
            xout_count, bbt_xout_count, added_xout_count, gather_xout_count, bbt_duplicate_count = calculate_effective_xout(
                block.defect_lines,
                skipdata.get(strip_id, set()),
            )
        else:
            bbt_xout_count = len(skipdata.get(strip_id, set()))
            xout_count = bbt_xout_count
            gather_xout_count = 0
        block_coords = gather_line_coords(block.defect_lines) if block else set()
        gather_coords_by_strip[strip_id] = block_coords
        if bbt_xout_count:
            bbt_strip_count += 1
        total_bbt_units += bbt_xout_count
        total_added_units += added_xout_count
        total_gather_units += gather_xout_count
        total_bbt_duplicate_units += bbt_duplicate_count
        ski_defects = min(len(ski_map.get(strip_id, set())), units_per_strip) if ski_map else xout_count
        is_ivs_dummy_scrap = strip_id in ivs_dummy_scrap_strips
        effective_xout_count = units_per_strip if is_ivs_dummy_scrap else xout_count
        xout_distribution[effective_xout_count] += 1
        is_ski_full_scrap_by_limit = bool(ski_map and ski_defects >= units_per_strip and effective_xout_count > xout_limit)
        is_scrap = effective_xout_count >= units_per_strip or is_ski_full_scrap_by_limit
        if is_scrap:
            exceeded_strips.append(strip_id)
            defective_units += units_per_strip
        else:
            good_strip_ids.append(strip_id)
            defective_units += ski_defects
            good_strip_defective_units += ski_defects
        per_strip_rows.append(
            {
                "strip_id": strip_id,
                "xout_count": effective_xout_count,
                "ski_xout_count": ski_defects,
                "ski_status": "허용수초과 스트립" if ski_defects == units_per_strip else "검증",
                "raw_xout_count": gather_xout_count,
                "skipdata_xout_count": bbt_duplicate_count,
                "bbt_xout_count": bbt_xout_count,
                "added_xout_count": added_xout_count,
                "bbt_duplicate_count": bbt_duplicate_count,
                "ivs_dummy_9999_scrap": is_ivs_dummy_scrap,
                "status": "양폐 strip" if is_scrap else "양품 strip",
                "block_count": block.block_count if block else 0,
            }
        )

    good_units = max(total_units - defective_units, 0)
    clean_txt_strips = sorted(
        strip_id for strip_id in blocks
        if strip_id not in set(mixed_strips) and strip_id not in set(duplicate_strips)
    )
    return {
        "lot_id": lot_id,
        "total_strips": total_strips,
        "total_units": total_units,
        "good_units": good_units,
        "defective_units": defective_units,
        "unit_yield_percent": round((good_units / total_units * 100), 4) if total_units else 0.0,
        "good_strips": len(good_strip_ids),
        "bad_strips": len(exceeded_strips),
        "scrap_strip_ids": sorted(exceeded_strips),
        "scrap_units": len(exceeded_strips) * units_per_strip,
        "total_bbt_units": total_bbt_units,
        "bbt_strip_count": bbt_strip_count,
        "total_added_units": total_added_units,
        "total_gather_units": total_gather_units,
        "total_bbt_duplicate_units": total_bbt_duplicate_units,
        "good_strip_defective_units": good_strip_defective_units,
        "scrap_strips": len(exceeded_strips),
        "ivs_dummy_9999_scrap_strips": sorted(ivs_dummy_scrap_strips),
        "ivs_dummy_9999_scrap_strip_count": len(ivs_dummy_scrap_strips),
        "ivs_dummy_9999_scrap_units": len(ivs_dummy_scrap_strips) * units_per_strip,
        "ivs_dummy_9999_validation": dummy_9999_validation,
        "xout_limit": xout_limit,
        "units_per_strip": units_per_strip,
        "xout_distribution": dict(sorted(xout_distribution.items())),
        "mixed_strips": sorted(mixed_strips),
        "duplicate_strips": sorted(duplicate_strips),
        "merged_strips": sorted(vrs_fvi_merged_strips),
        "merged_strip_count": len(vrs_fvi_merged_strips),
        "clean_txt_strip_count": len(clean_txt_strips),
        "clean_txt_strips": clean_txt_strips,
        "txt_only_strips": txt_only_strips,
        "missing_strips": missing_strips,
        "strip_json_validation": validation,
        "skipdata_strip_count": len(skipdata),
        "skipdata_only_strips": skipdata_only_strips,
        "exceeded_strips": exceeded_strips,
        "per_strip_rows": per_strip_rows,
        "ski_validation": build_ski_xout_validation(ski_map, expected_strip_ids, units_per_strip, lot_id),
        "bbt_full_unit_summary": build_bbt_full_unit_summary(skipdata, units_per_strip, lot_id),
        "bbt_validation": build_bbt_validation(skipdata, expected_strip_ids, gather_coords_by_strip, ski_map, units_per_strip, xout_limit),
    }

def build_yield_report_aggregation(summary: dict, model_name: str = "") -> dict:
    total_units = int(summary.get("total_units", 0) or 0)
    ok_units = int(summary.get("good_units", 0) or 0)
    ng_units = int(summary.get("defective_units", 0) or 0)
    process_code_distribution = {
        str(key): int(value)
        for key, value in (summary.get("report_process_code_distribution") or summary.get("process_code_distribution") or {}).items()
    }
    dummy_sm03_units = int((summary.get("report_code_distribution") or {}).get("SM03_DUMMY9999", summary.get("dummy_9999_sm03_units", 0)) or 0)
    real_sm03_units = int((summary.get("report_code_distribution") or {}).get("SM03", 0) or 0)
    xout_170_units = int((summary.get("report_code_distribution") or {}).get("170", 0) or 0)
    existing_bbt_codes = {
        str(key): int(value)
        for key, value in (
            {"OT14": (summary.get("report_process_code_distribution") or {}).get("BBT:OT14", 0)}
            if summary.get("report_code_distribution")
            else summary.get("existing_bbt_code_distribution") or {}
        ).items()
    }

    by_code: dict[str, dict] = {}
    for key, count in process_code_distribution.items():
        process, code = key.split(":", 1) if ":" in key else ("", key)
        if process == "BBT":
            continue
        row = by_code.setdefault(code, {"count": 0, "process_breakdown": {}})
        row["count"] += count
        if process:
            row["process_breakdown"][process] = row["process_breakdown"].get(process, 0) + count

    final_by_code: dict[str, dict] = {}
    for code, row in by_code.items():
        final_by_code[code] = {
            "count": int(row["count"]),
            "percent_of_total_units": round((row["count"] / total_units * 100), 6) if total_units else 0.0,
            "process_breakdown": dict(sorted(row["process_breakdown"].items())),
        }
    for code, count in existing_bbt_codes.items():
        row = final_by_code.setdefault(
            code,
            {"count": 0, "percent_of_total_units": 0.0, "process_breakdown": {}},
        )
        row["count"] += count
        row["process_breakdown"]["BBT"] = row["process_breakdown"].get("BBT", 0) + count
        row["percent_of_total_units"] = round((row["count"] / total_units * 100), 6) if total_units else 0.0

    process_unique_by_code = {
        code: {
            "count": int(row["count"]),
            "percent_of_total_units": round((row["count"] / total_units * 100), 6) if total_units else 0.0,
            "process_breakdown": dict(sorted(row["process_breakdown"].items())),
        }
        for code, row in sorted(by_code.items())
    }
    final_by_code = dict(sorted(final_by_code.items()))
    ok_unit_rows = {
        str(row.get("strip_id", "")): int(row.get("good_units", 0) or 0)
        for row in summary.get("per_strip_rows", [])
        if int(row.get("good_units", 0) or 0) > 0
    }
    return {
        "model_name": model_name,
        "lot_id": summary.get("lot_id", ""),
        "total_units": total_units,
        "ok_units": ok_units,
        "ng_units": ng_units,
        "ok_unit_aggregation": {
            "ok_units": ok_units,
            "ok_unit_percent": round((ok_units / total_units * 100), 6) if total_units else 0.0,
            "ok_strip_count": int(summary.get("good_strips", 0) or 0),
            "scrap_strip_count": int(summary.get("bad_strips", summary.get("scrap_strips", 0)) or 0),
            "per_strip_ok_units": ok_unit_rows,
        },
        "existing_bbt_ot14_units": int(existing_bbt_codes.get("OT14", summary.get("bbt_units", 0)) or 0),
        "dummy_9999_sm03_units": dummy_sm03_units,
        "real_sm03_units": real_sm03_units,
        "xout_over_170_units": xout_170_units,
        "process_unique_defects_by_code": process_unique_by_code,
        "final_defects_by_code_including_bbt": final_by_code,
        "ski_defect_units": int(summary.get("ski_defect_units_for_report", ng_units) or 0),
        "final_vs_ski_delta": sum(int(item.get("count", 0) or 0) for item in final_by_code.values()) - int(summary.get("ski_defect_units_for_report", ng_units) or 0),
    }


def calculate_yield_summary_by_process(
    vrs_paths: list[Path],
    fvi_paths: list[Path],
    ivs_paths: list[Path] | None,
    ivs_json_paths: list[Path] | None,
    strip_json_path: Path | None,
    units_per_strip: int,
    xout_limit: int,
    skipdata_path: Path | None = None,
    ski_path: Path | None = None,
    filters: list[str] | None = None,
    ivs_dummy_scrap_strips: set[str] | None = None,
) -> dict:
    json_lot_id, strip_json_ids = maybe_load_strip_id_json(strip_json_path)
    ivs_dummy_scrap_strips = ivs_dummy_scrap_strips or set()
    bbt_sources = {}
    bbt_map = load_skipdata_folder(skipdata_path, bbt_sources)
    bbt_code_map = load_skipdata_code_folder(skipdata_path)
    ski_map = load_ski_file(ski_path) if ski_path and ski_path.is_file() else {}
    ivs_origins = {}
    from core.report_trace import file_version
    watched_paths=list(vrs_paths)+list(fvi_paths)+list(ivs_paths or [])+list(ivs_json_paths or [])+([ski_path] if ski_path else [])
    input_versions={str(p):file_version(p) for p in watched_paths}
    ivs_records_by_strip = load_ivs_records_by_strip(ivs_json_paths or [], json_lot_id, ivs_origins)
    from core.its_validation import load_history,validate_strip,source_metadata
    its_history=load_history(ivs_json_paths or [])
    retired_ivs={}
    for strip,epoch in its_history.items():
        retired_ivs[strip]=[r for r in ivs_records_by_strip.get(strip,[]) if (r.machine,r.trial)!=(epoch['machine'],epoch['trial'])]
        ivs_records_by_strip[strip]=[r for r in ivs_records_by_strip.get(strip,[]) if (r.machine,r.trial)==(epoch['machine'],epoch['trial'])]
    if its_history:
        ivs_dummy_scrap_strips={strip for strip,items in ivs_records_by_strip.items() if any(r.is_dummy_9999 for r in items)}
    process_priority = ["IVS", "VRS", "FVI"]
    process_inputs = {
        "VRS": vrs_paths,
        "FVI": fvi_paths,
        "IVS": ivs_paths or [],
    }
    from core.report_trace import ReportTrace
    report_trace = ReportTrace(process_inputs, ivs_json_paths or [], skipdata_path, ski_path)
    report_trace.set_bbt_sources(bbt_sources)
    process_blocks: dict[str, dict[str, GatherStrip]] = {}
    process_replaced: dict[str, int] = {}
    process_selected_sources = {}
    for process, paths in process_inputs.items():
        if filters and process not in filters:
            continue
        selected = {}
        blocks, replaced = load_latest_process_blocks(paths, json_lot_id, bbt_map, ski_map, selected, its_history,
            ski_path.stat().st_mtime if ski_path and ski_path.is_file() else 0) if paths else ({}, 0)
        process_selected_sources[process] = selected
        process_blocks[process] = blocks
        process_replaced[process] = replaced
    gather_strip_ids = set().union(*(set(blocks) for blocks in process_blocks.values()))
    dummy_9999_validation = classify_ivs_dummy_9999_strips(
        ivs_dummy_scrap_strips,
        ski_map,
        units_per_strip,
        gather_strip_ids,
    )
    ivs_dummy_scrap_strips = set(dummy_9999_validation["applied_strips"])
    observed_strip_ids = gather_strip_ids | ivs_dummy_scrap_strips
    population_ids = verified_population_ids(strip_json_path, units_per_strip)
    expected_strip_ids = observed_strip_ids | population_ids
    lot_id = lot_id_from_blocks(
        [block for blocks in process_blocks.values() for block in blocks.values()],
        json_lot_id,
    )
    validation = build_strip_json_validation(strip_json_ids, observed_strip_ids, lot_id)

    total_units = production_units(len(expected_strip_ids), units_per_strip)
    defective_units = 0
    bbt_units = 0
    bbt_strip_count = 0
    bbt_good_strip_units = 0
    bbt_good_strip_count = 0
    bbt_scrap_strip_units = 0
    bbt_scrap_strip_count = 0
    added_units = 0
    good_strip_defective_units = 0
    good_strips = 0
    bad_strips = 0
    scrap_strip_ids: list[str] = []
    missing_by_process: dict[str, list[str]] = {process: [] for process in process_blocks}
    only_by_process: dict[str, list[str]] = {process: [] for process in process_blocks}
    code_to_coords: dict[str, set[tuple[str, int, int]]] = defaultdict(set)
    process_code_to_coords: dict[str, set[tuple[str, int, int]]] = defaultdict(set)
    process_unique_added_coords: dict[str, set[tuple[str, int, int]]] = defaultdict(set)
    xout_distribution: Counter[int] = Counter()
    gather_coords_by_strip: dict[str, set[tuple[int, int]]] = {}
    ski_gather_xout_mismatch_rows: list[dict] = []
    per_strip_rows: list[dict] = []
    ivs_170_code_adjustments: list[dict] = []
    full_unit_without_xout_over_limit_rows: list[dict] = []
    xout_consistency_rows: list[dict] = []
    report_ski_total_units = 0
    its_audits=[]

    for strip_id in sorted(expected_strip_ids):
        bbt_coords = set(bbt_map.get(strip_id, set()))
        present_processes = [process for process, blocks in process_blocks.items() if strip_id in blocks]
        for process, blocks in process_blocks.items():
            if strip_id not in blocks:
                missing_by_process[process].append(strip_id)
        if len(present_processes) == 1:
            only_by_process[present_processes[0]].append(strip_id)

        by_process_code: dict[str, dict[str, set[tuple[int, int]]]] = {}
        xml_x_count=infer_x_count_for_strip(units_per_strip,[bbt_coords,set(ski_map.get(strip_id,set())),
            set().union(*(gather_line_coords(blocks[strip_id].defect_lines) for blocks in process_blocks.values() if strip_id in blocks))])
        merged_by_code: dict[str, set[tuple[int, int]]] = defaultdict(set)
        process_coord_counts: dict[str, int] = {}
        for process, blocks in process_blocks.items():
            block = blocks.get(strip_id)
            by_code = coords_by_code(block.defect_lines) if block else {}
            if process=='IVS' and strip_id in its_history:
                for record in ivs_records_by_strip.get(strip_id,[]):
                    coord=unit_idx_to_xy(record.unit_idx,xml_x_count)
                    if record.is_rate_defect and coord:by_code.setdefault(record.ivs_1,set()).add(coord)
            if strip_id in its_history:by_code.pop('9998',None)
            by_process_code[process] = by_code
            merge_code_coords(merged_by_code, by_code)
            process_coords = set().union(*by_code.values()) if by_code else set()
            process_coord_counts[process] = len(process_coords)

        gather_coords = set().union(*merged_by_code.values()) if merged_by_code else set()
        gather_coords_by_strip[strip_id] = gather_coords
        added_coords = gather_coords - bbt_coords
        final_coords = bbt_coords | gather_coords
        if bbt_coords:
            bbt_strip_count += 1

        ski_coords_for_strip = set(ski_map.get(strip_id, set())) if ski_map else set()
        x_count_for_strip = infer_x_count_for_strip(
            units_per_strip,
            [bbt_coords, gather_coords, ski_coords_for_strip],
        )
        if strip_id in its_history:
            epoch=its_history[strip_id]
            raw={stage:coords_by_code(blocks[strip_id].defect_lines) for stage,blocks in process_blocks.items() if strip_id in blocks}
            stamps={stage:blocks[strip_id].source_mtime for stage,blocks in process_blocks.items() if strip_id in blocks}
            xml_mapping_valid=True
            for record in ivs_records_by_strip.get(strip_id,[]):
                if record.is_dummy:continue
                coord=unit_idx_to_xy(record.unit_idx,x_count_for_strip)
                if coord is None:xml_mapping_valid=False;continue
                raw.setdefault('IVS',{}).setdefault(record.ivs_1,set()).add(coord)
            pending=set(raw.get('IVS',{}).get('9998',()))
            reviewed=not pending or ('VRS' in stamps and stamps['VRS']>=stamps.get('IVS',epoch['epoch_start'])
                and not pending.intersection(raw.get('VRS',{}).get('9998',())))
            source_present='IVS' in raw or not ivs_records_by_strip.get(strip_id)
            if strip_id in ivs_dummy_scrap_strips:
                raw['IVS']={'SM03_DUMMY9999':full_unit_coords(x_count_for_strip,units_per_strip//x_count_for_strip)}
            audit=validate_strip(raw,bbt_coords,ski_coords_for_strip,history_verified=reviewed and source_present and xml_mapping_valid,
                ski_scope_verified=bool(ski_path and ski_path.is_file()),inspection_times=stamps,
                ski_time=ski_path.stat().st_mtime if ski_path and ski_path.is_file() else None,
                retired_coords={c for r in retired_ivs.get(strip_id,[]) if r.is_rate_defect and (c:=unit_idx_to_xy(r.unit_idx,x_count_for_strip))},epoch_start=epoch['epoch_start'])
            audit.update(strip_id=strip_id,machine=epoch['machine'],trial=epoch['trial'],epoch_start=epoch['epoch_start'],clock_basis='source_file_mtime')
            its_audits.append(audit)
        is_ivs_dummy_scrap = strip_id in ivs_dummy_scrap_strips
        if is_ivs_dummy_scrap:
            full_coords = full_unit_coords(x_count_for_strip, units_per_strip // x_count_for_strip) if x_count_for_strip else set()
            by_process_code.setdefault("IVS", {})
            by_process_code["IVS"] = {"SM03": full_coords}
            merged_by_code = defaultdict(set)
            for by_code in by_process_code.values():
                merge_code_coords(merged_by_code, by_code)
            gather_coords = set().union(*merged_by_code.values()) if merged_by_code else set()
            gather_coords_by_strip[strip_id] = gather_coords
            added_coords = gather_coords - bbt_coords
            final_coords = bbt_coords | gather_coords
            process_coord_counts["IVS"] = len(full_coords)

        assigned_by_process = assign_coords_by_process_priority(by_process_code, bbt_coords, process_priority)
        assigned_added_coords = set().union(
            *(set().union(*by_code.values()) for by_code in assigned_by_process.values() if by_code)
        ) if assigned_by_process else set()

        ski_defects = min(len(ski_map.get(strip_id, set())), units_per_strip) if ski_map else min(len(final_coords), units_per_strip)
        effective_final_xout = units_per_strip if is_ivs_dummy_scrap else len(final_coords)
        if (
            ski_coords_for_strip
            and len(ski_coords_for_strip) > effective_final_xout
            and len(ski_coords_for_strip) <= units_per_strip
            and final_coords.issubset(ski_coords_for_strip)
            and process_coord_counts
        ):
            effective_final_xout = len(ski_coords_for_strip)
        if ski_coords_for_strip:
            effective_final_xout = max(effective_final_xout, len(ski_coords_for_strip | bbt_coords))
        is_ski_full_scrap_by_limit = bool(ski_map and ski_defects >= units_per_strip and effective_final_xout > xout_limit)
        is_scrap = effective_final_xout >= units_per_strip or is_ski_full_scrap_by_limit
        unique_bbt_plus_added_count = len(bbt_coords | assigned_added_coords)
        if not is_scrap:
            reasons: list[str] = []
            if unique_bbt_plus_added_count > xout_limit:
                reasons.append("over_xout_limit")
            if ski_map and strip_id in ski_map and unique_bbt_plus_added_count != ski_defects:
                reasons.append("ski_count_mismatch")
            if reasons:
                xout_consistency_rows.append(
                    {
                        "strip_id": strip_id,
                        "bbt_count": len(bbt_coords),
                        "assigned_added_count": len(assigned_added_coords),
                        "bbt_plus_added_count": unique_bbt_plus_added_count,
                        "ski_xout_count": ski_defects,
                        "xout_limit": xout_limit,
                        "reasons": reasons,
                    }
                )
        if effective_final_xout >= units_per_strip and ski_defects <= xout_limit:
            full_unit_without_xout_over_limit_rows.append(
                {
                    "strip_id": strip_id,
                    "final_xout": effective_final_xout,
                    "ski_xout_count": ski_defects,
                    "bbt_count": len(bbt_coords),
                    "gather_count": len(gather_coords),
                    "ivs_dummy_9999_scrap": is_ivs_dummy_scrap,
                }
            )
        strip_defects = units_per_strip if is_scrap else ski_defects
        if ski_map and not is_scrap:
            strip_defects = min(units_per_strip, ski_defects + len(bbt_coords - ski_coords_for_strip))
        defective_units += strip_defects
        bbt_units += len(bbt_coords)
        xout_distribution[effective_final_xout] += 1
        report_target_coords = set(ski_coords_for_strip) if ski_coords_for_strip else set(final_coords)
        if is_scrap and len(report_target_coords) < units_per_strip:
            report_target_coords = set(final_coords)
        if len(report_target_coords) > units_per_strip:
            report_target_coords = set(sorted(report_target_coords, key=lambda item: (item[1], item[0]))[:units_per_strip])
        report_ski_total_units += len(report_target_coords)
        # Previous-process RPT defects belong to the selected Strip regardless
        # of final SKI membership; do not silently drop them or count twice.
        report_target_coords.update(bbt_coords)
        if report_target_coords:
            refs = sorted({(r.unit_idx,r.ivs_1,r.container,r.face,r.source_xml,ivs_origins.get(id(r),''),r.code_main,r.code_sub)
                           for r in ivs_records_by_strip.get(strip_id,[])})
            report_trace.strips[strip_id] = {'x_count':x_count_for_strip,'units_per_strip':units_per_strip,'is_scrap':is_scrap,
                'ski_coords':[list(c) for c in sorted(ski_coords_for_strip)],'bbt_coords':[list(c) for c in sorted(bbt_coords)],
                'processes':{p:{**summarize_gather_block_by_process(p,blocks.get(strip_id)),
                               'selected_source':process_selected_sources.get(p,{}).get(strip_id,'')}
                             for p,blocks in process_blocks.items()},
                'ivs_records':[list(ref) for ref in refs]}
        report_remaining = set(report_target_coords)
        for coord in sorted(bbt_coords & report_remaining, key=lambda item: (item[1], item[0])):
            x, y = coord
            report_trace.add(strip_id,'OT14','BBT',(x,y),'bbt')
        report_remaining -= bbt_coords
        if is_ivs_dummy_scrap:
            for coord in sorted(report_remaining, key=lambda item: (item[1], item[0])):
                x, y = coord
                report_trace.add(strip_id,'SM03_DUMMY9999','IVS',(x,y),'dummy9999')
            report_remaining.clear()
        recovered_ivs_coords = set()
        if is_scrap and not is_ivs_dummy_scrap:
            non170_assigned = set().union(
                *(
                    coords
                    for by_code in assigned_by_process.values()
                    for code, coords in by_code.items()
                    if code != "170"
                )
            ) if assigned_by_process else set()
            has_170_coords = any(by_code.get("170") for by_code in assigned_by_process.values())
            if has_170_coords and len(bbt_coords | non170_assigned) <= xout_limit:
                candidate_records = sorted(
                    [
                        record
                        for record in ivs_records_by_strip.get(strip_id, [])
                        if record.is_rate_defect and record.ivs_1 not in {"170", "9999"}
                    ],
                    key=lambda record: int(record.unit_idx) if str(record.unit_idx).isdigit() else -1,
                    reverse=True,
                )
                for record in candidate_records:
                    coord = unit_idx_to_xy(record.unit_idx, x_count_for_strip)
                    if not coord or coord in bbt_coords:
                        continue
                    removed_from = ""
                    for process in process_priority:
                        if coord in assigned_by_process.get(process, {}).get("170", set()):
                            assigned_by_process[process]["170"].discard(coord)
                            removed_from = process
                            break
                    if not removed_from:
                        continue
                    assigned_by_process["IVS"][record.ivs_1].add(coord)
                    recovered_ivs_coords.add((record.ivs_1,coord))
                    report_trace.strips[strip_id].setdefault('recovered_ivs',{})[f'{coord[0]},{coord[1]}'] = [
                        record.unit_idx,record.ivs_1,record.container,record.face,record.source_xml,
                        ivs_origins.get(id(record),''),record.code_main,record.code_sub]
                    ivs_170_code_adjustments.append(
                        {
                            "strip_id": strip_id,
                            "coord": f"X:{coord[0]:03d} Y:{coord[1]:03d}",
                            "code": record.ivs_1,
                            "unit_idx": record.unit_idx,
                            "removed_170_process": removed_from,
                        }
                    )
                    break
        counted_non170_added_coords: set[tuple[int, int]] = set()
        for process, by_code in assigned_by_process.items():
            for code, coords in by_code.items():
                if code == "170":
                    continue
                for x, y in sorted(coords):
                    code_to_coords[code].add((strip_id, x, y))
                    process_unique_added_coords[process].add((strip_id, x, y))
                    process_code_to_coords[f"{process}:{code}"].add((strip_id, x, y))
                    if (x, y) in report_remaining:
                        reason = 'ivs_recovery' if process == 'IVS' and (code,(x,y)) in recovered_ivs_coords else 'gather'
                        if code == 'UNKNOWN' and any(parse_xy_from_gather_line(line)==(x,y) and
                            (len(parts := line.split(','))<6 or not parts[5].strip())
                            for line in process_blocks.get(process,{}).get(strip_id,GatherStrip('',strip_id)).defect_lines):
                            reason = 'missing_code'
                        report_trace.add(strip_id,code,process,(x,y),reason)
                        report_remaining.discard((x, y))
                counted_non170_added_coords.update(coords)
        added_units += len(counted_non170_added_coords)
        if is_scrap:
            bad_strips += 1
            scrap_strip_ids.append(strip_id)
            if bbt_coords:
                bbt_scrap_strip_count += 1
                bbt_scrap_strip_units += len(bbt_coords)
            counted_170_coords: set[tuple[int, int]] = set()
            for process in process_priority:
                by_code = by_process_code.get(process, {})
                coords_170 = set(by_code.get("170", set()))
                if ski_map and strip_id in ski_map:
                    coords_170 &= set(ski_map.get(strip_id, set()))
                coords_170 -= counted_170_coords
                for x, y in sorted(coords_170):
                    code_to_coords["170"].add((strip_id, x, y))
                    process_unique_added_coords[process].add((strip_id, x, y))
                    process_code_to_coords[f"{process}:170"].add((strip_id, x, y))
                counted_170_coords.update(coords_170)
            added_units += len(counted_170_coords)
        if report_remaining:
            fallback_code = "170" if is_scrap else "UNKNOWN"
            fallback_process = "XOUT" if is_scrap else "UNASSIGNED"
            for x, y in sorted(report_remaining, key=lambda item: (item[1], item[0])):
                report_trace.add(strip_id,fallback_code,fallback_process,(x,y),'xout_fill' if is_scrap else 'ski_unassigned')
        if not is_scrap:
            good_strips += 1
            good_strip_defective_units += ski_defects
            if bbt_coords:
                bbt_good_strip_count += 1
                bbt_good_strip_units += len(bbt_coords)
        if ski_map and strip_id in ski_map and effective_final_xout != ski_defects and not is_ski_full_scrap_by_limit:
            ski_gather_xout_mismatch_rows.append(
                {
                    "strip_id": strip_id,
                    "gather_xout": effective_final_xout,
                    "ski_xout": ski_defects,
                    "bbt_count": len(bbt_coords),
                    "added_count": len(added_coords),
                    "ivs_dummy_9999_scrap": is_ivs_dummy_scrap,
                    "processes": {
                        process: summarize_gather_block_by_process(process, process_blocks[process].get(strip_id))
                        for process in process_priority
                    },
                }
            )
        per_strip_rows.append(
            {
                "strip_id": strip_id,
                "final_xout": effective_final_xout,
                "ski_xout_count": ski_defects,
                "ski_status": "허용수초과 스트립" if ski_defects == units_per_strip else "검증",
                "bbt_count": len(bbt_coords),
                "added_count": len(added_coords),
                "assigned_added_count": len(assigned_added_coords),
                "vrs_count": process_coord_counts.get("VRS", 0),
                "fvi_count": process_coord_counts.get("FVI", 0),
                "ivs_count": process_coord_counts.get("IVS", 0),
                "good_units": max(units_per_strip - strip_defects, 0),
                "ivs_dummy_9999_scrap": is_ivs_dummy_scrap,
                "status": "양폐 strip" if is_scrap else "양품 strip",
            }
        )

    table_bbt_units = sum(int(row.get("bbt_count", 0)) for row in per_strip_rows)
    if any(file_version(path)!=version for path,version in input_versions.items()):
        for audit in its_audits:
            audit.update(status='HISTORY_UNVERIFIED',auto_pass=False,input_changed_during_calculation=True)
    table_bbt_strip_count = sum(1 for row in per_strip_rows if int(row.get("bbt_count", 0)) > 0)
    table_strip_ids = {str(row.get("strip_id", "")) for row in per_strip_rows}
    existing_bbt_code_coords: dict[str, set[tuple[str, int, int]]] = defaultdict(set)
    existing_bbt_process_code_coords: dict[str, set[tuple[str, int, int]]] = defaultdict(set)
    for strip_id in table_strip_ids:
        bbt_coords_for_strip = set(bbt_map.get(strip_id, set()))
        if not bbt_coords_for_strip:
            bbt_coords_for_strip = set().union(*bbt_code_map.get(strip_id, {}).values()) if bbt_code_map.get(strip_id) else set()
        for x, y in bbt_coords_for_strip:
            existing_bbt_code_coords["OT14"].add((strip_id, x, y))
            existing_bbt_process_code_coords["BBT:OT14"].add((strip_id, x, y))
    existing_bbt_code_distribution = {
        code: len(coords)
        for code, coords in sorted(existing_bbt_code_coords.items())
    }
    existing_bbt_process_code_distribution = {
        code: len(coords)
        for code, coords in sorted(existing_bbt_process_code_coords.items())
    }
    existing_bbt_defect_summary = {
        "source": "per_strip_rows.bbt_count",
        "unit_count": table_bbt_units,
        "strip_count": table_bbt_strip_count,
        "code_distribution": existing_bbt_code_distribution,
        "process_code_distribution": existing_bbt_process_code_distribution,
        "strip_counts": {
            str(row.get("strip_id", "")): int(row.get("bbt_count", 0))
            for row in per_strip_rows
            if int(row.get("bbt_count", 0)) > 0
        },
    }
    code_distribution = {code: len(coords) for code, coords in sorted(code_to_coords.items())}
    process_code_distribution = {code: len(coords) for code, coords in sorted(process_code_to_coords.items())}
    report_code_distribution, report_process_code_distribution = report_trace.distributions()
    report_process_defect_distribution = merge_process_code_distribution(report_process_code_distribution)
    process_unique_added_distribution = {
        process: len(coords)
        for process, coords in sorted(process_unique_added_coords.items())
    }
    merged_process_defect_distribution = merge_process_code_distribution(process_code_distribution)
    split_added_distribution = split_170_added_distribution(process_code_distribution)
    dummy_9999_sm03_units = sum(
        max(units_per_strip - len(bbt_map.get(strip_id, set())), 0)
        for strip_id in ivs_dummy_scrap_strips
    )
    return {
        "mode": "process",
        "lot_id": lot_id,
        "total_strips": len(expected_strip_ids),
        "units_per_strip": units_per_strip,
        "total_units": total_units,
        "good_units": max(total_units - defective_units, 0),
        "defective_units": defective_units,
        "unit_yield_percent": round(((total_units - defective_units) / total_units * 100), 4) if total_units else 0.0,
        "good_strips": good_strips,
        "bad_strips": bad_strips,
        "scrap_strip_ids": sorted(scrap_strip_ids),
        "scrap_units": bad_strips * units_per_strip,
        "xout_limit": xout_limit,
        "bbt_units": bbt_units,
        "bbt_strip_count": bbt_strip_count,
        "table_bbt_units": table_bbt_units,
        "table_bbt_strip_count": table_bbt_strip_count,
        "existing_bbt_defect_summary": existing_bbt_defect_summary,
        "existing_bbt_code_distribution": existing_bbt_code_distribution,
        "existing_bbt_process_code_distribution": existing_bbt_process_code_distribution,
        "bbt_good_strip_units": bbt_good_strip_units,
        "bbt_good_strip_count": bbt_good_strip_count,
        "bbt_scrap_strip_units": bbt_scrap_strip_units,
        "bbt_scrap_strip_count": bbt_scrap_strip_count,
        "added_units": added_units,
        "good_strip_defective_units": good_strip_defective_units,
        "scrap_strips": bad_strips,
        "ivs_dummy_9999_scrap_strips": sorted(ivs_dummy_scrap_strips),
        "ivs_dummy_9999_scrap_strip_count": len(ivs_dummy_scrap_strips),
        "ivs_dummy_9999_scrap_units": len(ivs_dummy_scrap_strips) * units_per_strip,
        "dummy_9999_sm03_units": dummy_9999_sm03_units,
        "ivs_dummy_9999_validation": dummy_9999_validation,
        "ivs_170_code_adjustments": ivs_170_code_adjustments,
        "ivs_170_code_adjustment_count": len(ivs_170_code_adjustments),
        "full_unit_without_xout_over_limit_rows": full_unit_without_xout_over_limit_rows,
        "full_unit_without_xout_over_limit_count": len(full_unit_without_xout_over_limit_rows),
        "xout_consistency_rows": xout_consistency_rows,
        "xout_consistency_mismatch_count": len(xout_consistency_rows),
        "process_strip_counts": {process: len(blocks) for process, blocks in process_blocks.items()},
        "process_replaced_counts": process_replaced,
        "only_by_process": only_by_process,
        "missing_by_process": missing_by_process,
        "vrs_strip_count": len(process_blocks["VRS"]),
        "fvi_strip_count": len(process_blocks["FVI"]),
        "ivs_strip_count": len(process_blocks["IVS"]),
        "vrs_replaced_count": process_replaced["VRS"],
        "fvi_replaced_count": process_replaced["FVI"],
        "ivs_replaced_count": process_replaced["IVS"],
        "vrs_only": only_by_process["VRS"],
        "fvi_only": only_by_process["FVI"],
        "ivs_only": only_by_process["IVS"],
        "missing_vrs": missing_by_process["VRS"],
        "missing_fvi": missing_by_process["FVI"],
        "missing_ivs": missing_by_process["IVS"],
        "strip_json_validation": validation,
        "xout_distribution": dict(sorted(xout_distribution.items())),
        "code_distribution": code_distribution,
        "process_code_distribution": process_code_distribution,
        "report_code_distribution": report_code_distribution,
        "report_trace": report_trace.snapshot(),
        "its_validation": {'enabled':bool(its_history),'strips':its_audits,'auto_pass':bool(its_audits) and all(a['auto_pass'] for a in its_audits) and set(expected_strip_ids).issubset(its_history),
            'untracked_strips':sorted(set(expected_strip_ids)-set(its_history)) if its_history else []},
        "report_process_code_distribution": report_process_code_distribution,
        "report_process_defect_distribution": report_process_defect_distribution,
        "ski_defect_units_for_report": report_ski_total_units,
        "report_defect_units_total": sum(report_code_distribution.values()),
        "report_vs_ski_delta": sum(report_code_distribution.values()) - report_ski_total_units,
        "process_unique_added_distribution": process_unique_added_distribution,
        "merged_process_defect_distribution": merged_process_defect_distribution,
        "split_170_added_distribution": split_added_distribution,
        "per_strip_rows": per_strip_rows,
        "ski_validation": build_ski_xout_validation(ski_map, expected_strip_ids, units_per_strip, lot_id),
        "ski_gather_xout_mismatch_rows": ski_gather_xout_mismatch_rows,
        "bbt_full_unit_summary": build_bbt_full_unit_summary(bbt_map, units_per_strip, lot_id),
        "bbt_validation": build_bbt_validation(bbt_map, expected_strip_ids, gather_coords_by_strip, ski_map, units_per_strip, xout_limit),
    }


def build_yield_report_aggregation(summary: dict, model_name: str = "") -> dict:
    total_units = int(summary.get("total_units", 0) or 0)
    ok_units = int(summary.get("good_units", 0) or 0)
    ng_units = int(summary.get("defective_units", 0) or 0)
    process_code_distribution = {
        str(key): int(value)
        for key, value in (summary.get("report_process_code_distribution") or summary.get("process_code_distribution") or {}).items()
    }
    dummy_sm03_units = int((summary.get("report_code_distribution") or {}).get("SM03_DUMMY9999", summary.get("dummy_9999_sm03_units", 0)) or 0)
    real_sm03_units = int((summary.get("report_code_distribution") or {}).get("SM03", 0) or 0)
    xout_170_units = int((summary.get("report_code_distribution") or {}).get("170", 0) or 0)
    existing_bbt_codes = {
        str(key): int(value)
        for key, value in (
            {"OT14": (summary.get("report_process_code_distribution") or {}).get("BBT:OT14", 0)}
            if summary.get("report_code_distribution")
            else summary.get("existing_bbt_code_distribution") or {}
        ).items()
    }

    by_code: dict[str, dict] = {}
    for key, count in process_code_distribution.items():
        process, code = key.split(":", 1) if ":" in key else ("", key)
        if process == "BBT":
            continue
        row = by_code.setdefault(code, {"count": 0, "process_breakdown": {}})
        row["count"] += count
        if process:
            row["process_breakdown"][process] = row["process_breakdown"].get(process, 0) + count

    final_by_code: dict[str, dict] = {}
    for code, row in by_code.items():
        final_by_code[code] = {
            "count": int(row["count"]),
            "percent_of_total_units": round((row["count"] / total_units * 100), 6) if total_units else 0.0,
            "process_breakdown": dict(sorted(row["process_breakdown"].items())),
        }
    for code, count in existing_bbt_codes.items():
        row = final_by_code.setdefault(
            code,
            {"count": 0, "percent_of_total_units": 0.0, "process_breakdown": {}},
        )
        row["count"] += count
        row["process_breakdown"]["BBT"] = row["process_breakdown"].get("BBT", 0) + count
        row["percent_of_total_units"] = round((row["count"] / total_units * 100), 6) if total_units else 0.0

    process_unique_by_code = {
        code: {
            "count": int(row["count"]),
            "percent_of_total_units": round((row["count"] / total_units * 100), 6) if total_units else 0.0,
            "process_breakdown": dict(sorted(row["process_breakdown"].items())),
        }
        for code, row in sorted(by_code.items())
    }
    final_by_code = dict(sorted(final_by_code.items()))
    ok_unit_rows = {
        str(row.get("strip_id", "")): int(row.get("good_units", 0) or 0)
        for row in summary.get("per_strip_rows", [])
        if int(row.get("good_units", 0) or 0) > 0
    }
    return {
        "model_name": model_name,
        "lot_id": summary.get("lot_id", ""),
        "total_units": total_units,
        "ok_units": ok_units,
        "ng_units": ng_units,
        "ok_unit_aggregation": {
            "ok_units": ok_units,
            "ok_unit_percent": round((ok_units / total_units * 100), 6) if total_units else 0.0,
            "ok_strip_count": int(summary.get("good_strips", 0) or 0),
            "scrap_strip_count": int(summary.get("bad_strips", summary.get("scrap_strips", 0)) or 0),
            "per_strip_ok_units": ok_unit_rows,
        },
        "existing_bbt_ot14_units": int(existing_bbt_codes.get("OT14", summary.get("bbt_units", 0)) or 0),
        "dummy_9999_sm03_units": dummy_sm03_units,
        "real_sm03_units": real_sm03_units,
        "xout_over_170_units": xout_170_units,
        "process_unique_defects_by_code": process_unique_by_code,
        "final_defects_by_code_including_bbt": final_by_code,
        "ski_defect_units": int(summary.get("ski_defect_units_for_report", ng_units) or 0),
        "final_vs_ski_delta": sum(int(item.get("count", 0) or 0) for item in final_by_code.values()) - int(summary.get("ski_defect_units_for_report", ng_units) or 0),
    }
