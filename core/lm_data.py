"""LM Data cleanup utilities (SMK/PMK file management)."""
from __future__ import annotations
from core.i18n import _

import json
import shutil
from pathlib import Path

from core.utils import read_text_lines


def load_lm_target_strip_ids(json_path: Path) -> tuple[str, list[str]]:
    payload = json.loads(json_path.read_text(encoding="utf-8-sig"))
    lot_id = str(payload.get("lot_id", "")).strip().upper()
    strip_ids_payload = payload.get("strip_ids", {})
    targets: set[str] = set()
    if isinstance(strip_ids_payload, dict):
        for key in ("ski_only_strips", "bbt_only_strips"):
            values = strip_ids_payload.get(key, [])
            if isinstance(values, list):
                targets.update(str(value).strip().upper() for value in values if str(value).strip())
    else:
        for key in ("ski_only_strips", "bbt_only_strips"):
            values = payload.get(key, [])
            if isinstance(values, list):
                targets.update(str(value).strip().upper() for value in values if str(value).strip())
    return lot_id, sorted(targets)


def unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(1, 10000):
        candidate = path.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise ValueError(f"대상 파일명을 만들 수 없습니다: {path}")


def preview_lm_data_cleanup(json_path: Path, smk_folder: Path, pmk_folder: Path) -> dict:
    lot_id, target_strip_ids = load_lm_target_strip_ids(json_path)
    target_set = set(target_strip_ids)
    smk_files = [path for path in smk_folder.glob("*.smk") if path.stem.upper() in target_set]
    pmk_files = list(pmk_folder.glob("*_Master.pmk")) or list(pmk_folder.glob("*.pmk"))
    pmk_updates: list[dict] = []
    for pmk_file in pmk_files:
        remove_count = sum(1 for line in read_text_lines(pmk_file) if line.strip().upper() in target_set)
        if remove_count:
            pmk_updates.append({"path": str(pmk_file), "remove_count": remove_count})
    return {
        "lot_id": lot_id,
        "target_strip_count": len(target_strip_ids),
        "target_strip_ids": target_strip_ids,
        "smk_move_count": len(smk_files),
        "smk_files": [str(path) for path in smk_files],
        "pmk_update_count": len(pmk_updates),
        "pmk_updates": pmk_updates,
        "missing_smk_count": max(len(target_strip_ids) - len(smk_files), 0),
    }


def execute_lm_data_cleanup(json_path: Path, smk_folder: Path, pmk_folder: Path) -> dict:
    preview = preview_lm_data_cleanup(json_path, smk_folder, pmk_folder)
    target_set = set(preview["target_strip_ids"])
    smk_bk = smk_folder / "BK"
    pmk_bk = pmk_folder / "BK"
    smk_bk.mkdir(exist_ok=True)
    pmk_bk.mkdir(exist_ok=True)

    moved_smk: list[dict] = []
    for source_text in preview["smk_files"]:
        source = Path(source_text)
        destination = unique_destination(smk_bk / source.name)
        shutil.move(str(source), str(destination))
        moved_smk.append({"from": str(source), "to": str(destination)})

    updated_pmk: list[dict] = []
    for update in preview["pmk_updates"]:
        pmk_file = Path(str(update["path"]))
        lines = read_text_lines(pmk_file)
        kept_lines = [line for line in lines if line.strip().upper() not in target_set]
        removed_count = len(lines) - len(kept_lines)
        if not removed_count:
            continue
        backup = unique_destination(pmk_bk / f"{pmk_file.name}.bak")
        shutil.copy2(pmk_file, backup)
        pmk_file.write_text("\n".join(kept_lines) + ("\n" if kept_lines else ""), encoding="utf-8")
        updated_pmk.append({"path": str(pmk_file), "backup": str(backup), "removed_count": removed_count})

    preview["moved_smk"] = moved_smk
    preview["updated_pmk"] = updated_pmk
    return preview
