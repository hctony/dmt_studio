"""Read model dimensions from the A PC's inspection repository."""
from pathlib import Path
import re


def handler_geometry(path):
    body = path.read_bytes()
    if body.startswith((b'\xff\xfe', b'\xfe\xff')):
        text = body.decode('utf-16')
    else:
        text = body.decode('utf-8-sig', errors='replace')
    values = {}
    for key in ('UnitRowNum', 'UnitColNum'):
        matches = {int(value) for value in re.findall(
            r'^\s*' + key + r'\s*=\s*(\d+)\s*(?:[;#].*)?$', text, re.MULTILINE | re.IGNORECASE)}
        if len(matches) != 1 or not 0 < next(iter(matches)) <= 1024:
            raise ValueError(f'{path.name}: {key}')
        values[key] = matches.pop()
    x, y = values['UnitRowNum'], values['UnitColNum']
    return {'x_count': x, 'y_count': y, 'units_per_strip': x * y}


def lookup(model, repository=None):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{1,63}', model):
        raise ValueError('INVALID_MODEL_NAME')
    if repository is None:
        from config import NAS_HOST
        repository = Path(rf'\\{NAS_HOST}\Pixel\PxRepository')
    folder = repository / model
    versions = [version for version in folder.iterdir()
                if re.fullmatch(r'\d+(?:\.\d+)*', version.name)]
    for version in sorted(versions, key=lambda item: tuple(map(int, item.name.split('.'))), reverse=True):
        source = version / 'Line' / 'Top' / 'Top_handler.txt'
        if source.is_file():
            return handler_geometry(source), str(source)
    raise FileNotFoundError('Top_handler.txt')
