"""Shared utility functions (no Qt/NAS dependencies)."""
from __future__ import annotations
from core.i18n import _

import json
import re
from pathlib import Path

from config import LOT_ID_RE


def read_text_lines(path: Path) -> list[str]:
    for enc in ("cp949", "utf-8-sig", "utf-8"):
        try:
            return path.read_text(encoding=enc).splitlines()
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="cp949", errors="ignore").splitlines()


def sanitize_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "defect"


def to_json_plain(value):
    if isinstance(value, dict):
        return {str(key): to_json_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_json_plain(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def split_path_list(text: str) -> list[Path]:
    return [Path(part.strip().strip('"').strip("'")) for part in re.split(r";|\r?\n", text) if part.strip()]


def parse_excluded_strip_ids(json_path: str) -> set[str]:
    if not json_path.strip():
        return set()
    path = Path(json_path)
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        strip_ids = payload.get("strip_ids", [])
        if not isinstance(strip_ids, list):
            raise ValueError(_("StripID 제외 JSON에는 strip_ids 배열이 필요합니다."))
        return {str(item).strip().upper() for item in strip_ids if str(item).strip()}

    strip_ids: set[str] = set()
    for line in read_text_lines(path):
        text = line.strip()
        if not text:
            continue
        for token in re.split(r"[,;\s]+", text):
            token = token.strip().strip('"').strip("'").upper()
            if token:
                strip_ids.add(token)
    return strip_ids


def extract_lot_id_from_text(text: str) -> str:
    match = LOT_ID_RE.search(text.upper())
    return match.group(0) if match else ""


def infer_lot_id_from_file(path: Path) -> str:
    if path.suffix.lower() == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            lot_id = str(payload.get("lot_id", "")).strip().upper()
            if lot_id:
                return lot_id
        except Exception:
            pass
    lot_id = extract_lot_id_from_text(" ".join([path.name, *[part.name for part in path.parents[:4]]]))
    if lot_id:
        return lot_id
    if path.is_file() and path.suffix.lower() in {".txt", ".ski", ".pmk"}:
        try:
            for line in read_text_lines(path)[:200]:
                lot_id = extract_lot_id_from_text(line)
                if lot_id:
                    return lot_id
        except Exception:
            return ""
    return ""


def infer_lot_id_from_values(*values: str) -> str:
    for value in values:
        for part in [item.strip().strip('"').strip("'") for item in str(value).split(";") if item.strip()]:
            path = Path(part)
            if path.exists():
                lot_id = infer_lot_id_from_file(path)
            else:
                lot_id = extract_lot_id_from_text(part)
            if lot_id:
                return lot_id
    return ""


def lot_from_strip_id(strip_id: str) -> str:
    text = strip_id.strip().upper()
    if "-" not in text:
        return text
    return text.rsplit("-", 1)[0] + "-"


def first_path_text(value: str) -> str:
    return next((part.strip() for part in re.split(r"[;|]", value) if part.strip()), "")


def infer_x_count_for_strip(units_per_strip: int, coord_sources: list[set[tuple[int, int]]]) -> int:
    max_x = max((x for coords in coord_sources for x, _ in coords), default=0)
    max_y = max((y for coords in coord_sources for _, y in coords), default=0)
    if max_x and max_y and max_x * max_y == units_per_strip:
        return max_x
    if max_x and units_per_strip % max_x == 0:
        return max_x
    if max_y and units_per_strip % max_y == 0:
        return units_per_strip // max_y
    if units_per_strip % 4 == 0:
        return units_per_strip // 4
    return units_per_strip


def unit_idx_to_xy(unit_idx: str, x_count: int) -> tuple[int, int] | None:
    try:
        idx = int(str(unit_idx).strip())
    except ValueError:
        return None
    if idx < 0 or x_count <= 0:
        return None
    return (idx % x_count) + 1, (idx // x_count) + 1


def full_unit_coords(x_count: int, y_count: int) -> set[tuple[int, int]]:
    return {(x, y) for y in range(1, y_count + 1) for x in range(1, x_count + 1)}


def make_lines_for_coords(lot: str, strip_id: str, coords: set[tuple[int, int]], code: str, tb: str = "T") -> list[str]:
    return [f"{lot},{strip_id},{x},{y},{tb},{code}" for x, y in sorted(coords, key=lambda item: (item[1], item[0]))]


def is_full_unit_coords(coords: set[tuple[int, int]], x_count: int, y_count: int) -> bool:
    return bool(coords) and len(coords) == x_count * y_count

def auto_find_bbt_skipdata_path(lot: str) -> str:
    """Finds the BBT/Skipdata folder in D:\\KINSUS DATA\\Backup by matching the LOT number against .rpt files."""
    if not lot:
        return ""
    from pathlib import Path
    base_path = Path(r"D:\KINSUS DATA\Backup")
    if not base_path.exists():
        return ""
        
    lot_cleaned = lot.strip()
    try:
        for sub_dir in base_path.iterdir():
            if not sub_dir.is_dir():
                continue
            for rpt_path in sub_dir.glob("*.rpt"):
                if lot_cleaned.upper() in rpt_path.name.upper():
                    return str(sub_dir)
    except OSError:
        pass
    return ""

def auto_find_ski_file_path(lot: str) -> str:
    """Finds the .ski file in D:\\SkipData matching the LOT number."""
    if not lot:
        return ""
    from pathlib import Path
    base_path = Path(r"D:\SkipData")
    if not base_path.exists() or not base_path.is_dir():
        return ""
        
    lot_cleaned = lot.strip()
    try:
        for p in base_path.rglob("*.ski"):
            if lot_cleaned.upper() in p.name.upper():
                return str(p)
    except OSError:
        pass
    return ""
