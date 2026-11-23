"""Restrict remote file inputs to administrator-approved roots and private session data."""
import json
import os
import re
from pathlib import Path, PureWindowsPath


WORK_FILES = {'vrs_txt_in', 'fvi_txt_in', 'ivs_txt_in', 'txt_in', 'ivs_json_in',
              'strip_json_in', 'exclude_json_in', 'ski_in', 'template_in'}
WORK_FOLDERS = {'skipdata_in'}


def configured_work_roots(data_dir, gathering, fields):
    from core.utils import BBT_LOOKUP_ROOT, SKI_LOOKUP_ROOT
    roots = {root for root in (BBT_LOOKUP_ROOT, SKI_LOOKUP_ROOT) if Path(root).is_absolute()}
    for value in gathering:
        path = PureWindowsPath(value)
        if path.drive.startswith('\\\\') and not path.drive.endswith('$') and not protected_relative(value):
            roots.add(path.anchor)
    for name, value in fields:
        if name not in WORK_FILES | WORK_FOLDERS:
            continue
        for part in re.split(r'[;\r\n]+', value):
            part = part.strip().strip('"')
            path = Path(part)
            if not path.is_absolute() or protected_relative(part) or '..' in path.parts:
                continue
            root = path.parent if name in WORK_FILES else path
            if root == Path(root.anchor) and not root.drive.startswith('\\\\'):
                continue
            if root.is_relative_to(data_dir) or data_dir.is_relative_to(root):
                continue
            roots.add(str(root))
    return sorted(roots, key=str.casefold)


def allowed_path(value):
    requested = Path(value)
    path = requested.resolve()
    if protected_relative(str(path)):
        return False
    roots = json.loads(os.environ.get('DMT_ALLOWED_ROOTS', '[]'))
    manifest = os.environ.get('DMT_WORK_ROOTS_FILE')
    if manifest:
        try:
            auto = json.loads(Path(manifest).read_text(encoding='utf-8'))
            if isinstance(auto, list):
                roots.extend(value for value in auto if isinstance(value, str))
        except (OSError, ValueError):
            pass
    roots.append(os.environ['DMT_DATA_DIR'])
    roots = sorted(map(Path, roots), key=lambda root: not requested.is_relative_to(root))
    for root in roots:
        if root.drive.startswith('\\\\') and root.drive.lower() not in {requested.drive.lower(), path.drive.lower()}:
            continue
        try:
            if path.is_relative_to(root.resolve()):
                return True
        except OSError:
            continue
    return False


def validate_input(controller, widget, value):
    names = [key.rsplit('/', 1)[-1] for key, candidate in controller.input_widgets() if candidate is widget]
    if not names or not any(any(word in name.lower() for word in ('path', 'txt', 'json', 'ski', 'skipdata', 'smk', 'pmk', 'folder', 'dir')) for name in names):
        return
    # NAS paths are SFTP paths interpreted relative to the configured NAS root;
    # local Windows paths must stay within the explicitly shared source roots.
    for part in re.split(r'[;\r\n]+', value):
        part = part.strip().strip('"')
        if not part:
            continue
        if '..' in re.split(r'[\\/]', part):
            raise ValueError('상위 경로 이동은 허용되지 않습니다. / 不允許使用上層路徑。')
        if protected_relative(part):
            raise ValueError('보호된 파일입니다. / 此為受保護檔案。')
        if any(name == 'trial_path_in' for name in names) and part.startswith('/'):
            from config import NAS_ROOT
            if part == NAS_ROOT or part.startswith(NAS_ROOT.rstrip('/') + '/'):
                continue
            raise ValueError('설정된 NAS 루트 밖입니다. / 此路徑位於設定的 NAS 根目錄之外。')
        # Gathering path templates are UNC share suffixes, not local paths.
        if names == ['path_in'] and not re.match(r'^[A-Za-z]:', part) and not part.startswith(('\\\\', '/')):
            continue
        if not allowed_path(part):
            raise ValueError('A PC에서 허용한 데이터 폴더를 선택하세요. / 請選擇 A 電腦允許的資料夾。')


def protected_relative(value):
    return any(part.lower() in {'.env', '.git', '.ssh', '.aws', 'settings.ini', 'network-server.json',
                               'network-work-roots.json', 'network-gathering-config.json', 'client-connection.json'}
               for part in re.split(r'[\\/]', value))
