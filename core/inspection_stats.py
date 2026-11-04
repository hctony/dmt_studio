"""Inspection statistics core logic."""
import os
import re
from collections import defaultdict
from pathlib import Path
from datetime import datetime
from typing import Iterator

from core.utils import read_text_lines

# Filename pattern: {model}_{lot}_{workerID}[_yyyy-mm-dd ...].txt
# model/lot may contain hyphens; date portion is optional.
# e.g. 7P05-N29_4489825260321750_048_2026-05-21 102254.txt
#      7M70-012_RX1263041000-_042_154425.txt   (no date → use file mtime)
#
# (?=[_. ]|$) lookahead ensures emp_id ends at a field separator (_, ., space)
# or end-of-string — never mid-date. This prevents "2026" being captured from
# "...lot_2026-05-26..." because after "2026" the next char is "-", not in [_. ].
FILE_PATTERN = re.compile(
    r"^(?P<model>[^_]+)_(?P<lot>[^_]+)_(?P<emp_id>[A-Za-z0-9]+)(?=[_. ]|$)"
    r"(?:_(?P<date>\d{4}-\d{2}-\d{2}))?",
    re.IGNORECASE,
)

# Matches YYYY-MM-DD anywhere in a directory path
_PATH_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
# Matches YYYYMMDD (2000-2099 only, to avoid false positives in IP addresses etc.)
_PATH_DATE8_RE = re.compile(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])([0-2]\d|3[01])(?!\d)")

# Models counted as BGA unit (not strip).
# Hyphen is required so that e.g. "7N02A-xxx" (strip) is not misclassified.
UNIT_MODEL_PREFIXES = ("7N02-", "7A66-")


def _is_unit_product(model: str) -> bool:
    """Return True if model series is 7N02 or 7A66 (BGA unit product)."""
    upper = model.upper()
    return any(upper.startswith(p) for p in UNIT_MODEL_PREFIXES)


def _max_consecutive_days(date_strs: set) -> int:
    if not date_strs:
        return 0
    dates = sorted([datetime.strptime(ds, "%Y-%m-%d").date() for ds in date_strs])
    max_streak = 1
    current_streak = 1
    for i in range(1, len(dates)):
        delta = dates[i] - dates[i - 1]
        if delta.days == 1:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        elif delta.days > 1:
            current_streak = 1
    return max_streak


def parse_strip_blocks(lines: list) -> Iterator:
    """
    Parse TXT file content into inspection blocks.

    File format (repeating):
        {defect_count},{lot},{id}                <- block header (3 fields)
        {lot},{id},{x},{y},{face},{code}          <- defect record (6 fields, 0+)
        {lot},{id},EOS                           <- block terminator

    strip product: id = strip_id (pure digits)
    unit product:  id = {lot}_{barcode} (contains underscore / space)

    Yields (lot: str, block_id: str, defects: list[(x, y, face, code)])
    """
    cur_lot = None
    cur_id = None
    cur_defects: list = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]

        if len(parts) == 3:
            if parts[2].upper() == "EOS":
                if cur_id is not None:
                    yield cur_lot, cur_id, cur_defects
                cur_lot = cur_id = None
                cur_defects = []
            else:
                # New block header — flush any unclosed previous block
                if cur_id is not None:
                    yield cur_lot, cur_id, cur_defects
                lot, block_id = parts[1], parts[2]
                if lot and block_id:
                    cur_lot, cur_id, cur_defects = lot, block_id, []
                else:
                    cur_lot = cur_id = None
                    cur_defects = []

        elif len(parts) == 6 and cur_id is not None:
            # Defect record: lot, id, x, y, face, code
            cur_defects.append((parts[2], parts[3], parts[4], parts[5].upper()))

    # Flush block without trailing EOS
    if cur_id is not None:
        yield cur_lot, cur_id, cur_defects


def _date_from_path(path_str: str) -> str | None:
    """
    Extract the most specific YYYY-MM-DD date found in a directory path.
    Tries YYYY-MM-DD first, then YYYYMMDD (2000-2099 only).
    Returns None if no valid date is found.
    """
    m = _PATH_DATE_RE.search(path_str)
    if m:
        return m.group(1)
    m = _PATH_DATE8_RE.search(path_str)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def _iter_matched_files(dirs: list, start_str: str, end_str: str) -> Iterator:
    """
    Recursively walk directories, yield (emp_id, file_date, model, lot, Path).

    Date resolution priority (for files without YYYY-MM-DD in the filename):
      1. Date embedded in filename   e.g. …_2026-05-28 102254.txt
      2. Date found in directory path  e.g. …\\2026-05-28\\file.txt
      3. File modification time (mtime) — last resort; unreliable on network shares
    """
    for root_dir in dirs:
        root = Path(root_dir)
        if not root.exists() or not root.is_dir():
            continue
        for path_str, _dirs, files in os.walk(str(root)):
            # Cache dir-level date so we don't recompute per file
            dir_date = _date_from_path(path_str)

            for f in files:
                if not f.lower().endswith((".txt", ".csv")):
                    continue
                m = FILE_PATTERN.match(f)
                if not m:
                    continue
                emp_id = m.group("emp_id")
                model = m.group("model")
                lot = m.group("lot") or ""
                if not emp_id or not model:
                    continue
                filepath = Path(path_str) / f

                # ── date resolution ──────────────────────────────────────────
                # Priority: filename date → directory path date → no filter
                # mtime is intentionally NOT used for filtering: on network shares
                # it reflects copy/access time, not the actual inspection date.
                file_date = m.group("date") or dir_date
                if file_date:
                    if not (start_str <= file_date <= end_str):
                        continue  # reliable date found but outside range → skip
                else:
                    # No reliable date source → include unconditionally.
                    # Record the range-start as a placeholder so consecutive-day
                    # logic doesn't crash (it won't skew stats meaningfully).
                    file_date = start_str

                yield emp_id, file_date, model, lot, filepath


def _process_dir(
    dirs: list,
    start_str: str,
    end_str: str,
    emp_stats: dict,
    strip_key: str,
    unit_key: str,
    detail_key: str,
    collect_defects: bool,
) -> None:
    """
    Shared processing loop for a single process (IVS or VRS).
    Increments raw block counts — no deduplication.
    Appends one record **per file** (not per block) to emp[detail_key]
    so the detail dialog shows a meaningful summary row per file.
    """
    for emp_id, file_date, model, lot_filename, filepath in _iter_matched_files(dirs, start_str, end_str):
        try:
            lines = read_text_lines(filepath)
        except OSError:
            continue
        emp = emp_stats[emp_id]
        emp["dates"].add(file_date)
        is_unit = _is_unit_product(model)

        # ── per-file accumulators ────────────────────────────────────────────
        file_strip_count = 0
        file_unit_count = 0
        file_defect_codes: list = []

        for lot, block_id, defects in parse_strip_blocks(lines):
            if is_unit:
                emp[unit_key] += 1
                file_unit_count += 1
            else:
                emp[strip_key] += 1
                file_strip_count += 1
            for x, y, face, code in defects:
                if "170" not in code:
                    file_defect_codes.append(code)
                    if collect_defects:
                        emp["defects"].add((lot, block_id, x, y, face, code))

        # ── one detail row per file (skip files with 0 parsed blocks) ────────
        if file_strip_count + file_unit_count == 0:
            continue
        from collections import Counter
        code_summary = ", ".join(
            f"{c}×{n}" if n > 1 else c
            for c, n in sorted(Counter(file_defect_codes).items())
        )
        emp[detail_key].append({
            "file":          str(filepath),
            "date":          file_date,
            "model":         model,
            "lot":           lot_filename,
            "type":          "unit" if is_unit else "strip",
            "strip_count":   file_strip_count,
            "unit_count":    file_unit_count,
            "defect_count":  len(file_defect_codes),
            "defect_summary": code_summary,
        })


def gather_inspection_stats(
    ivs_dirs: list,
    vrs_dirs: list,
    start_date: datetime,
    end_date: datetime,
) -> list:
    """
    Recursively scan IVS and VRS directories, aggregate per-employee counts.

    Both IVS and VRS use raw block counting (no deduplication).
    Strip products (default) and unit products (7N02/7A66 models) are tracked separately.
    Defects (excluding code 170 variants) are collected from both processes.

    Returns list of dicts sorted by (total_strips + total_units) descending:
      [{"emp_id", "total_strips", "total_units",
        "ivs_strips", "ivs_units", "vrs_strips", "vrs_units",
        "consecutive_days", "defect_count"}, ...]
    """
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    emp_stats: dict = defaultdict(lambda: {
        "ivs_strip_count": 0,
        "ivs_unit_count": 0,
        "vrs_strip_count": 0,
        "vrs_unit_count": 0,
        "dates": set(),
        "defects": set(),       # (lot, block_id, x, y, face, code)
        "ivs_records": [],      # per-block detail for drill-down
        "vrs_records": [],
    })

    _process_dir(ivs_dirs, start_str, end_str, emp_stats,
                 "ivs_strip_count", "ivs_unit_count", "ivs_records", collect_defects=True)
    _process_dir(vrs_dirs, start_str, end_str, emp_stats,
                 "vrs_strip_count", "vrs_unit_count", "vrs_records", collect_defects=True)

    results = []
    for emp_id, stats in emp_stats.items():
        ivs_s = stats["ivs_strip_count"]
        ivs_u = stats["ivs_unit_count"]
        vrs_s = stats["vrs_strip_count"]
        vrs_u = stats["vrs_unit_count"]
        total_s = ivs_s + vrs_s
        total_u = ivs_u + vrs_u
        if total_s + total_u == 0:
            continue
        results.append({
            "emp_id": emp_id,
            "total_strips": total_s,
            "total_units": total_u,
            "ivs_strips": ivs_s,
            "ivs_units": ivs_u,
            "vrs_strips": vrs_s,
            "vrs_units": vrs_u,
            "consecutive_days": _max_consecutive_days(stats["dates"]),
            "defect_count": len(stats["defects"]),
            "ivs_records": stats["ivs_records"],
            "vrs_records": stats["vrs_records"],
        })

    results.sort(key=lambda x: x["total_strips"] + x["total_units"], reverse=True)
    return results
