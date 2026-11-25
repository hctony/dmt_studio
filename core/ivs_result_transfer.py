"""Verify a live IVS result before moving its unchanged bytes to a configured VRS station."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from tempfile import NamedTemporaryFile

from config import DATA_DIR
from core.collection import iter_blocks
from core.gather import parse_xy_from_gather_line
from core.i18n import _
from core.inspection_history import decode_original, original, search


CONFIG_NAME = 'network-gathering-config.json'
DEFAULTS = {'IVS': ('108', r'bdm_kinsus\BDM_BACKUP'),
            'VRS': ('121-130', r'VRSData\ResultData\BDM_KINSUS\BDM_Backup')}


def configured_roots(model):
    from workers.gather_workers import GatheringWorker
    from ui.settings_helper import setting_text

    if not re.fullmatch(r'[A-Za-z0-9_&-][A-Za-z0-9_.&-]*', model):
        raise ValueError(_('모델명을 확인하세요.'))
    if os.environ.get('DMT_REMOTE_SESSION') == '1':
        manifest = Path(os.environ['DMT_WORK_ROOTS_FILE']).with_name(CONFIG_NAME)
        if not manifest.is_file():
            raise ValueError(_('A PC의 게더링 호기 설정을 먼저 불러오세요.'))
        settings = json.loads(manifest.read_text(encoding='utf-8'))
    else:
        manifest = DATA_DIR / CONFIG_NAME
        settings = json.loads(manifest.read_text(encoding='utf-8')) if manifest.is_file() else {
            stage: [setting_text(f'gathering/{stage}/ip', ip), setting_text(f'gathering/{stage}/path', base)]
            for stage, (ip, base) in DEFAULTS.items()
        }
    roots = {}
    for stage in DEFAULTS:
        if stage not in settings:
            raise ValueError(_('A PC의 게더링 호기 설정을 먼저 불러오세요.'))
        ip, base = settings[stage]
        if stage == 'IVS' and ip.strip() == '108' and base.split('\\')[0].lower() == 'bdm_kinsus':
            ip = '101-110'
        worker = GatheringWorker(stage, ip, base, model, '', 0, 0, 0, '170', set(), set())
        paths = worker.build_paths()
        if any(not path.is_absolute() or '..' in path.parts for path in paths):
            raise ValueError(_('게더링 호기 경로를 확인하세요.'))
        roots[stage] = paths
    return roots


def _source_root(path, ivs_roots):
    for root in ivs_roots:
        if path.is_relative_to(root):
            return root
        # Older IVS profiles point at BDM_BACKUP while the machine can write
        # completed TXT files directly to the same configured bdm_kinsus share.
        share_name = root.drive.rsplit('\\', 1)[-1] if root.parent == Path(root.anchor) else root.parent.name
        if root.name.casefold() == 'bdm_backup' and share_name.casefold() == 'bdm_kinsus' and path.parent == root.parent:
            return root.parent
    return None


def _station_locations(source, roots):
    yield source
    for root in roots:
        if _source_root(source, [root]) is None:
            continue
        alternate = root / source.name
        if root.name.casefold() == 'bdm_backup' and source.parent == root:
            alternate = root.parent / source.name
        elif source.parent == root and root.name.casefold() != 'bdm_backup':
            alternate = root / 'BDM_BACKUP' / source.name
        if alternate != source and _source_root(alternate, roots) is not None:
            yield alternate


def source_candidates(values, ivs_roots):
    return list(dict.fromkeys(path for value in values if value
                              and (path := Path(value)).suffix.lower() == '.txt'
                              and '..' not in path.parts
                              and _source_root(path, ivs_roots) is not None))


def snapshot_hashes(paths):
    from core.collection import cache_root
    objects = cache_root() / 'collector' / 'objects'
    return frozenset(path.stem for value in paths if (path := Path(value)).parent == objects
                     and path.suffix.lower() == '.txt' and re.fullmatch(r'[0-9a-f]{64}', path.stem))


def _remote_allowed(path):
    if os.environ.get('DMT_REMOTE_SESSION') == '1':
        from remote_paths import allowed_path
        if not allowed_path(path):
            raise PermissionError(_('A PC에서 허용한 데이터 폴더를 선택하세요.'))


def vrs_result_root(configured):
    parent_name = configured.parent.name or configured.parent.drive.rsplit('\\', 1)[-1]
    if configured.name.casefold() == 'bdm_backup' and parent_name.casefold() == 'bdm_kinsus':
        return configured.parent
    return configured


def resolve_vrs_result_folder(station, roots):
    configured = next((root for root in roots['VRS'] if station in (root, vrs_result_root(root))), None)
    if configured is None:
        raise PermissionError(_('게더링 호기 경로를 확인하세요.'))
    folder = vrs_result_root(configured)
    _remote_allowed(folder)
    actual = folder.resolve(strict=True)
    is_junction = getattr(folder, 'is_junction', lambda: False)
    if not actual.is_dir() or folder.is_symlink() or is_junction():
        raise NotADirectoryError(str(folder))
    return folder, actual


def publish_without_overwrite(temporary, target):
    if os.name == 'nt':
        temporary.rename(target)
    else:
        os.link(temporary, target)


def _fingerprint(path):
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino


def _digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


@dataclass(frozen=True)
class Transfer:
    source: Path
    destination: Path
    resolved_source: Path | None
    resolved_destination: Path
    version: tuple
    digest: str
    strips: tuple[str, ...]
    archive_id: str | None = None
    archive_mtime_ns: int = 0


def _load_source(source, roots, lot, strip_id, hashes):
    for candidate in _station_locations(source, roots['IVS']):
        try:
            actual_source = candidate.resolve(strict=True)
        except FileNotFoundError:
            continue
        _remote_allowed(candidate)
        ivs_root = _source_root(candidate, roots['IVS'])
        if not actual_source.is_relative_to(ivs_root.resolve(strict=True)) or candidate.is_symlink() or not actual_source.is_file():
            raise PermissionError(_('게더링 호기 경로를 확인하세요.'))
        stamp = _fingerprint(actual_source)
        body = actual_source.read_bytes()
        if _fingerprint(actual_source) != stamp:
            raise ValueError(_('IVS 원본 TXT가 변경되었습니다. 다시 확인하세요.'))
        digest = hashlib.sha256(body).hexdigest()
        if candidate == source or not hashes or digest in hashes:
            return actual_source, stamp, body, None, 0
    if hashes:
        matches = [item for item in search(lot=lot, strip=strip_id, mode='IVS')
                   if Path(item['source_path']) == source and item['hash'] in hashes]
        if matches and len({item['hash'] for item in matches}) == 1:
            item = matches[0]
            row, body, _block = original(item['id'])
            return None, (), body, item['id'], row['source_mtime_ns']
    raise FileNotFoundError(_('IVS 설비 원본과 검증된 수집 보관본을 찾지 못했습니다. IVS를 다시 수집하세요.')
                            + '\n' + str(source))


def inspect_transfer(source, station, roots, lot, strip_id, coords, hashes=()):
    if source not in source_candidates([source], roots['IVS']):
        raise PermissionError(_('게더링 호기 경로를 확인하세요.'))
    _remote_allowed(source)
    station, actual_station = resolve_vrs_result_folder(station, roots)
    # Use resolved paths for the write and reject changes to the named roots before execution.
    target = actual_station / source.name
    if os.path.lexists(target):
        raise FileExistsError(_('VRS 폴더에 같은 이름의 TXT가 있습니다.') + '\n' + str(target))
    actual_source, stamp, body, archive_id, archive_mtime_ns = _load_source(source, roots, lot, strip_id, hashes)
    blocks = list(iter_blocks(decode_original(body).splitlines()))
    matches = [block for block in blocks if (block['lot'], block['strip']) == (lot, strip_id)]
    if not matches or not coords.intersection(parse_xy_from_gather_line(line) for line in matches[-1]['lines']):
        raise ValueError(_('이 파일에서 선택한 Strip의 미반영 IVS 좌표를 확인할 수 없습니다.'))
    return Transfer(source, station / source.name, actual_source, target, stamp, hashlib.sha256(body).hexdigest(),
                    tuple(sorted({block['strip'] for block in blocks})), archive_id, archive_mtime_ns)


def move_transfer(plan, roots, lot, strip_id, coords, hashes=()):
    if inspect_transfer(plan.source, plan.destination.parent, roots, lot, strip_id, coords, hashes) != plan:
        raise ValueError(_('IVS 원본 TXT가 변경되었습니다. 다시 확인하세요.'))
    source, target = plan.resolved_source, plan.resolved_destination
    temporary = None
    published = False
    published_version = None
    try:
        with NamedTemporaryFile(dir=target.parent, prefix='.dmt-ivs-', suffix='.tmp', delete=False) as stage:
            temporary = Path(stage.name)
            if source is None:
                _row, body, _block = original(plan.archive_id)
                stage.write(body)
            else:
                with source.open('rb') as stream:
                    shutil.copyfileobj(stream, stage)
            stage.flush()
            os.fsync(stage.fileno())
        if source is None:
            os.utime(temporary, ns=(plan.archive_mtime_ns, plan.archive_mtime_ns))
        else:
            shutil.copystat(source, temporary)
        if (source is not None and _fingerprint(source) != plan.version) or _digest(temporary) != plan.digest:
            raise ValueError(_('IVS 원본 TXT가 변경되었습니다. 다시 확인하세요.'))
        publish_without_overwrite(temporary, target)
        published = True
        published_version = _fingerprint(target)
        if source is not None:
            if _fingerprint(source) != plan.version:
                raise ValueError(_('IVS 원본 TXT가 변경되었습니다. 다시 확인하세요.'))
            source.unlink()
    except Exception as error:
        if published:
            try:
                if published_version is None or _fingerprint(target) != published_version:
                    raise OSError('Destination changed after publication')
                target.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                raise OSError(_('VRS 폴더에 복사본이 남았을 수 있습니다. 원본과 대상 경로를 확인하세요.')
                              + '\n' + str(source) + '\n' + str(target)) from error
        raise
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return plan.destination
