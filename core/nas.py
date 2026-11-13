"""NAS/SSH connection helpers and remote-path utilities."""
from __future__ import annotations
from core.i18n import _

import os
import re
import stat
import xml.etree.ElementTree as ET
from typing import Iterable, Optional

import paramiko

from config import NAS_HOST, NAS_PORT, NAS_USER, NAS_PASSWORD, NAS_ROOT, MACHINES, IMAGE_EXTENSIONS
from core.models import BundleInfo, TrialContext
from core.cancellation import check_cancelled


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def connect() -> tuple[paramiko.SSHClient, paramiko.SFTPClient]:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(NAS_HOST, port=NAS_PORT, username=NAS_USER, password=NAS_PASSWORD,
                    timeout=10, banner_timeout=10, auth_timeout=10, channel_timeout=15)
        ssh.get_transport().set_keepalive(15)
        sftp = ssh.open_sftp()
        sftp.get_channel().settimeout(30)
        from core.nas_cache import ScanSftp
        return ssh, ScanSftp(sftp)
    except Exception:
        ssh.close()
        raise


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def normalize_remote_path(path: str) -> str:
    value = path.strip().strip('"').strip("'").replace("\\", "/")
    value = re.sub(r"^//[^/]+", "", value)
    value = re.sub(r"^/volume\d+/", "/", value)
    if not value.startswith("/"):
        value = "/" + value
    return re.sub(r"/+", "/", value).rstrip("/")


def path_exists(sftp: paramiko.SFTPClient, path: str) -> bool:
    try:
        sftp.stat(path)
        return True
    except (FileNotFoundError, OSError):
        return False


def iter_dirs(sftp: paramiko.SFTPClient, path: str) -> Iterable[str]:
    try:
        for attr in sftp.listdir_attr(path):
            if stat.S_ISDIR(attr.st_mode):
                yield attr.filename
    except FileNotFoundError:
        return


def iter_xml_files_recursive(root_path: str, sftp: paramiko.SFTPClient) -> Iterable[str]:
    try:
        entries = sorted(sftp.listdir_attr(root_path), key=lambda attr: attr.filename.lower())
    except FileNotFoundError:
        return
    for attr in entries:
        path = f"{root_path}/{attr.filename}"
        if stat.S_ISDIR(attr.st_mode):
            yield from iter_xml_files_recursive(path, sftp)
        elif attr.filename.lower().endswith(".xml"):
            yield path


# ---------------------------------------------------------------------------
# Model / Lot / Trial listing
# ---------------------------------------------------------------------------

def list_models(sftp: paramiko.SFTPClient) -> list[str]:
    models: set[str] = set()
    for machine in MACHINES:
        machine_path = f"{NAS_ROOT}/{machine}"
        if not path_exists(sftp, machine_path):
            alt = f"{NAS_ROOT}/{machine.lower()}"
            if path_exists(sftp, alt):
                machine_path = alt
            else:
                continue
        models.update(iter_dirs(sftp, machine_path))
    return sorted(models)


def list_lots(model: str, sftp: paramiko.SFTPClient) -> list[str]:
    lots: set[str] = set()
    for machine in MACHINES:
        machine_path = f"{NAS_ROOT}/{machine}"
        if not path_exists(sftp, machine_path):
            alt = f"{NAS_ROOT}/{machine.lower()}"
            if path_exists(sftp, alt):
                machine_path = alt
            else:
                continue
        model_path = f"{machine_path}/{model}"
        if path_exists(sftp, model_path):
            lots.update(iter_dirs(sftp, model_path))
    return sorted(lots)


def folder_index(name: str) -> Optional[int]:
    if name.isdigit():
        return int(name)
    match = re.search(r"(\d+)$", name)
    return int(match.group(1)) if match else None


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def face_from_def_side(def_side: str) -> str:
    value = def_side.strip().upper()
    if value in {"B", "BC", "BACK", "BOTTOM"}:
        return "BC"
    return "FC"


def image_candidates(base_without_ext: str) -> list[str]:
    paths: list[str] = []
    for suffix in ("_Sub2", "", "_Sub1"):
        for ext in IMAGE_EXTENSIONS:
            paths.append(f"{base_without_ext}{suffix}{ext}")
    return paths


def resolve_three_images(sftp: paramiko.SFTPClient, base_without_ext: str, should_stop=None) -> list[str]:
    if getattr(sftp, '_dmt_scan_session', False) is True:
        return [sftp.resolve_image(base_without_ext+suffix+'.bmp') for suffix in ('_Sub2','','_Sub1')]
    resolved: list[str] = []
    for suffix in ("_Sub2", "", "_Sub1"):
        found = ""
        for ext in IMAGE_EXTENSIONS:
            check_cancelled(should_stop)
            candidate = f"{base_without_ext}{suffix}{ext}"
            if path_exists(sftp, candidate):
                found = candidate
                break
        resolved.append(found or f"{base_without_ext}{suffix}.bmp")
    return resolved


# ---------------------------------------------------------------------------
# XML / Bundle parsing
# ---------------------------------------------------------------------------

def decode_xml(data: bytes) -> ET.Element:
    # Let XML declarations/BOMs select UTF-8/UTF-16 before legacy encodings.
    try:
        return ET.fromstring(data)
    except Exception:
        pass
    last_err: Exception | None = None
    for enc in ("cp949", "euc-kr", "utf-8", "utf-8-sig", "utf-16"):
        try:
            return ET.fromstring(data.decode(enc))
        except Exception as exc:
            last_err = exc
    raise last_err or ValueError("XML decode failed")


def read_xml_root(xml_path, sftp):
    import time
    if getattr(sftp, '_dmt_scan_session', False) is True:
        data=sftp.read_xml(xml_path);started=time.monotonic()
        try:return decode_xml(data)
        finally:sftp.metrics['xml_parse_seconds']+=time.monotonic()-started
    with sftp.open(xml_path,'rb') as file:return decode_xml(file.read())


def read_barcode_from_xml(xml_path: str, sftp: paramiko.SFTPClient) -> Optional[str]:
    try:
        root = read_xml_root(xml_path,sftp)
    except Exception:
        return None
    for elem in root.iter():
        barcode = elem.attrib.get("barcode")
        if barcode:
            return barcode.strip()
    return None


def fc_name_variants(strip_folder: str) -> set[str]:
    base = strip_folder.strip().lower()
    names = {"fc.xml"}
    if base:
        names.add(f"{base}_fc.xml")
        names.add(f"s{base}_fc.xml")
        try:
            idx = int(base)
            names.add(f"{idx:04d}_fc.xml")
            names.add(f"s{idx:04d}_fc.xml")
        except ValueError:
            pass
    return names


def find_fc_file(strip_path: str, strip_folder: str, sftp: paramiko.SFTPClient, depth: int = 0) -> Optional[str]:
    try:
        entries = sftp.listdir_attr(strip_path)
    except FileNotFoundError:
        return None
    targets = fc_name_variants(strip_folder)
    for attr in entries:
        if stat.S_ISDIR(attr.st_mode):
            continue
        name = attr.filename.lower()
        if name in targets or name.endswith("_fc.xml"):
            return f"{strip_path}/{attr.filename}"
    if depth >= 2:
        return None
    for attr in entries:
        if stat.S_ISDIR(attr.st_mode):
            result = find_fc_file(f"{strip_path}/{attr.filename}", strip_folder, sftp, depth + 1)
            if result:
                return result
    return None


def parse_bundle_ini(ini_path: str, sftp: paramiko.SFTPClient) -> Optional[BundleInfo]:
    try:
        with sftp.open(ini_path, "rb") as fh:
            data = fh.read()
    except FileNotFoundError:
        return None

    for enc in ("utf-8", "cp949", "euc-kr"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            text = data.decode("cp949", errors="ignore")

    indexes: set[int] = set()
    viamc = ""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip().lower()
        val = val.strip().rstrip(",")
        if key == "index" and val:
            for token in val.split(","):
                token = token.strip()
                if token.isdigit():
                    indexes.add(int(token))
        elif key == "viamcname":
            viamc = val
    return BundleInfo(name=os.path.basename(ini_path), indexes=indexes, viamc_name=viamc)


def load_valid_bundle_indexes(trial_path: str, sftp: paramiko.SFTPClient) -> set[int]:
    indexes: set[int] = set()
    try:
        names = sftp.listdir(trial_path)
    except FileNotFoundError:
        return indexes
    for name in names:
        if name.lower().startswith("bundle") and name.lower().endswith(".ini"):
            info = parse_bundle_ini(f"{trial_path}/{name}", sftp)
            if info and info.viamc_name.strip():
                indexes.update(info.indexes)
    return indexes


def load_bundle_infos(trial_path: str, sftp: paramiko.SFTPClient) -> list[BundleInfo]:
    bundles: list[BundleInfo] = []
    try:
        names = sftp.listdir(trial_path)
    except FileNotFoundError:
        return bundles
    for name in names:
        if name.lower().startswith("bundle") and name.lower().endswith(".ini"):
            info = parse_bundle_ini(f"{trial_path}/{name}", sftp)
            if info:
                bundles.append(info)
    return bundles


def parse_trial_context(trial_path: str) -> TrialContext:
    normalized = normalize_remote_path(trial_path)
    parts = normalized.strip("/").split("/")
    try:
        data_idx = parts.index("Data")
        machine, model, lot, trial = parts[data_idx + 1 : data_idx + 5]
    except (ValueError, IndexError) as exc:
        raise ValueError(
            _("Trial 경로는 /Pixel/ResultData/Data/{설비}/{모델}/{LOT}/{Trial} 형식이어야 합니다.")
        ) from exc
    return TrialContext(machine=machine, model=model, lot=lot, trial=trial, path=normalized)


def list_lot_trials(model: str, lot: str, sftp: paramiko.SFTPClient, should_stop=None) -> list[TrialContext]:
    trials: list[TrialContext] = []
    for machine in MACHINES:
        check_cancelled(should_stop)
        machine_path = f"{NAS_ROOT}/{machine}"
        if not path_exists(sftp, machine_path):
            alt = f"{NAS_ROOT}/{machine.lower()}"
            if path_exists(sftp, alt):
                machine_path = alt
            else:
                continue
        lot_path = f"{machine_path}/{model}/{lot}"
        if not path_exists(sftp, lot_path):
            continue
        for trial in iter_dirs(sftp, lot_path):
            check_cancelled(should_stop)
            trials.append(
                TrialContext(machine=machine, model=model, lot=lot, trial=trial, path=f"{lot_path}/{trial}")
            )
    return trials
