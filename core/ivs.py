"""IVS XML parsing and summary building."""
from __future__ import annotations
from core.i18n import _

import json
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Optional

import paramiko

from core.models import DefectRecord, TrialContext
from core.cancellation import check_cancelled
from core.yield_rates import single_defect_rate
from core.nas import decode_xml, face_from_def_side, resolve_three_images, normalize_remote_path


def parse_float(value: str | None) -> float:
    try:
        return float(value or 0)
    except ValueError:
        return 0.0


def find_ivs_files(strip_path: str, sftp: paramiko.SFTPClient) -> list[str]:
    import stat
    try:
        attrs = sftp.listdir_attr(strip_path)
    except FileNotFoundError:
        return []
    return [
        f"{strip_path}/{attr.filename}"
        for attr in attrs
        if not stat.S_ISDIR(attr.st_mode) and "_ivs.xml" in attr.filename.lower()
    ]


def parse_ivs_xml(
    xml_path: str,
    sftp: paramiko.SFTPClient,
    ctx: TrialContext,
    strip_folder: str,
    progress: Optional[Callable[[str], None]] = None,
    should_stop=None,
    on_strip=None,
) -> list[DefectRecord]:
    check_cancelled(should_stop)
    records: list[DefectRecord] = []
    try:
        with sftp.open(xml_path, "rb") as fh:
            root = decode_xml(fh.read())
    except Exception as exc:
        check_cancelled(should_stop)
        if progress:
            progress(f"XML 파싱 실패: {xml_path} ({exc})")
        return records

    barcode = "UNKNOWN"
    for elem in root.iter():
        value = elem.attrib.get("barcode")
        if value and value.strip():
            barcode = value.strip().upper()
            break

    if on_strip is not None:
        from core.ivs_population import xml_grid_units
        on_strip(barcode, xml_grid_units(root))

    for parent in root.iter():
        check_cancelled(should_stop)
        parent_tag = parent.tag.split("}")[-1]
        for p_elem in list(parent):
            tag = p_elem.tag.split("}")[-1].upper()
            if not tag.startswith("P"):
                continue
            ivs_1 = p_elem.attrib.get("ivs_1", "").strip()
            if not ivs_1 or ivs_1 == "0":
                continue

            unit_idx = p_elem.attrib.get("unit_idx", "0").strip()
            container = "Dummy" if parent_tag.lower() == "dummy" or unit_idx == "-1" else "Unit"
            face = face_from_def_side(p_elem.attrib.get("def_side", "F"))
            img_merge_idx = p_elem.attrib.get("img_merge_idx", "0").strip() or "0"
            if container == "Dummy":
                base_img_path = f"{ctx.path}/{strip_folder}/Dummy/{face}/p{img_merge_idx}"
            else:
                base_img_path = f"{ctx.path}/{strip_folder}/Unit/U{unit_idx}/{face}/p{img_merge_idx}"

            records.append(
                DefectRecord(
                    strip_id=barcode,
                    machine=ctx.machine,
                    model=ctx.model,
                    lot=ctx.lot,
                    trial=ctx.trial,
                    strip_folder=strip_folder,
                    container=container,
                    face=face,
                    unit_idx=unit_idx,
                    img_merge_idx=img_merge_idx,
                    ivs_1=ivs_1,
                    code_main=p_elem.attrib.get("code_main", ""),
                    code_sub=p_elem.attrib.get("code_sub", ""),
                    def_side=p_elem.attrib.get("def_side", ""),
                    strip_pt_x=parse_float(p_elem.attrib.get("strip_pt_x")),
                    strip_pt_y=parse_float(p_elem.attrib.get("strip_pt_y")),
                    img_pt_x=parse_float(p_elem.attrib.get("img_pt_x")),
                    img_pt_y=parse_float(p_elem.attrib.get("img_pt_y")),
                    source_xml=xml_path,
                    images=resolve_three_images(sftp, base_img_path, should_stop),
                )
            )
    return records


def build_summary(records: list[DefectRecord], total_units=None) -> dict:
    normal_records = [r for r in records if r.is_rate_defect]
    rework_records = [r for r in records if r.is_rework]
    dummy_records = [r for r in records if r.is_dummy and not r.is_rework]
    dummy_9999_records = [r for r in records if r.is_dummy_9999]

    total_defect_units = len({r.unit_key() for r in normal_records})
    by_type_units: dict[str, set[str]] = defaultdict(set)
    for record in normal_records:
        by_type_units[record.ivs_1].add(record.unit_key())

    by_type = []
    for code, unit_keys in sorted(by_type_units.items(), key=lambda item: (-len(item[1]), item[0])):
        count = len(unit_keys)
        by_type.append(
            {
                "ivs_1": code,
                "defect_units": count,
                "rate_percent": round((count / total_defect_units * 100), 4) if total_defect_units else 0.0,
                "single_defect_rate_percent": single_defect_rate(count, total_units),
            }
        )

    return {
        "total_records": len(records),
        "total_units": total_units,
        "ivs_yield_percent": round(100-total_defect_units/total_units*100,6) if total_units and total_defect_units<=total_units else None,
        "total_defect_units_excluding_rework_dummy": total_defect_units,
        "rework_9998_count": len(rework_records),
        "rework_9998_units": len({r.unit_key() for r in rework_records}),
        "dummy_records": len(dummy_records),
        "dummy_9999_count": len(dummy_9999_records),
        "by_defect_type": by_type,
    }


def records_to_json(model: str, lot: str, records: list[DefectRecord], population=None) -> dict:
    payload = {
        "schema": "dmt-studio-ivs-v1",
        "lot_id": lot,
        "model": model,
        "total_strips": len({record.strip_id for record in records}),
        "summary": build_summary(records, population.get('total_units') if population else None),
        "records": [asdict(record) for record in records],
        "strips": {},
    }
    if population is not None:
        payload['population']=population
    strip_map: dict[str, list[DefectRecord]] = defaultdict(list)
    for record in records:
        strip_map[record.strip_id].append(record)
    for strip_id, strip_records in strip_map.items():
        units: dict[str, dict[str, list[dict]]] = {}
        for record in strip_records:
            unit_key = "Dummy" if record.is_dummy else record.unit_idx
            units.setdefault(unit_key, {}).setdefault(record.face, []).append(asdict(record))
        payload["strips"][strip_id] = {"defect_count": len(strip_records), "units": units}
    return payload


def load_records_from_payload(payload: dict) -> list[DefectRecord]:
    if payload.get("schema") == "dmt-studio-ivs-v1" and isinstance(payload.get("records"), list):
        return [DefectRecord(**item) for item in payload["records"]]

    # 기존 ivs_strip_extract.py JSON 호환 로더
    records: list[DefectRecord] = []
    lot = str(payload.get("lot_id", ""))
    for strip_id, strip_data in payload.get("strips", {}).items():
        for unit_idx, faces in strip_data.get("units", {}).items():
            for face, defect_list in faces.items():
                for item in defect_list:
                    images = item.get("images", [])
                    machine, model, trial, strip_folder = "", str(payload.get("model", "")), "", ""
                    if images:
                        parts = normalize_remote_path(images[1] if len(images) > 1 else images[0]).strip("/").split("/")
                        try:
                            data_idx = parts.index("Data")
                            machine, model, lot, trial, strip_folder = parts[data_idx + 1 : data_idx + 6]
                        except (ValueError, IndexError):
                            pass
                    records.append(
                        DefectRecord(
                            strip_id=str(strip_id),
                            machine=machine,
                            model=model,
                            lot=lot,
                            trial=trial,
                            strip_folder=strip_folder,
                            container="Dummy" if str(unit_idx) == "Dummy" else "Unit",
                            face=str(face),
                            unit_idx="-1" if str(unit_idx) == "Dummy" else str(unit_idx),
                            img_merge_idx=str(item.get("img_merge_idx", "0")),
                            ivs_1=str(item.get("ivs_1", item.get("code_main", ""))),
                            code_main=str(item.get("code_main", "")),
                            code_sub=str(item.get("code_sub", "")),
                            def_side=str(face),
                            strip_pt_x=parse_float(str(item.get("strip_pt_x", 0))),
                            strip_pt_y=parse_float(str(item.get("strip_pt_y", 0))),
                            img_pt_x=parse_float(str(item.get("img_pt_x", 0))),
                            img_pt_y=parse_float(str(item.get("img_pt_y", 0))),
                            source_xml="",
                            images=images,
                        )
                    )
    return records


def load_ivs_dummy_scrap_strips(json_paths: list[Path], lot_id: str = "") -> set[str]:
    scrap_strips: set[str] = set()
    lot_key = lot_id.strip().upper()
    for json_path in json_paths:
        payload = json.loads(json_path.read_text(encoding="utf-8-sig"))
        for record in load_records_from_payload(payload):
            strip_id = record.strip_id.strip().upper()
            if not strip_id:
                continue
            if lot_key and not strip_id.startswith(lot_key):
                continue
            if record.is_dummy_9999:
                scrap_strips.add(strip_id)
    return scrap_strips


def load_ivs_records_by_strip(json_paths: list[Path], lot_id: str = "") -> dict[str, list[DefectRecord]]:
    records_by_strip: dict[str, list[DefectRecord]] = defaultdict(list)
    lot_key = lot_id.strip().upper()
    for json_path in json_paths:
        payload = json.loads(json_path.read_text(encoding="utf-8-sig"))
        for record in load_records_from_payload(payload):
            strip_id = record.strip_id.strip().upper()
            if not strip_id:
                continue
            if lot_key and not strip_id.startswith(lot_key):
                continue
            records_by_strip[strip_id].append(record)
    return records_by_strip
