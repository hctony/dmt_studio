"""Restrict remote file inputs to administrator-approved roots and private session data."""
import json
import os
import re
from pathlib import Path


def allowed_path(value):
    path = Path(value).resolve()
    if any(part.lower() in {'.env', '.git', '.ssh', '.aws', 'network-server.json'} for part in path.parts):
        return False
    roots = json.loads(os.environ.get('DMT_ALLOWED_ROOTS', '[]'))
    roots.append(os.environ['DMT_DATA_DIR'])
    return any(path.is_relative_to(Path(root).resolve()) for root in roots)


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
    return any(part.lower() in {'.env', '.git', '.ssh', '.aws', 'settings.ini', 'network-server.json'} for part in re.split(r'[\\/]', value))
