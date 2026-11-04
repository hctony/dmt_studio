"""Persist the report template independently of the installed application."""
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from config import DATA_DIR, APP_SETTINGS


def validate_template(path):
    from openpyxl import load_workbook
    book = load_workbook(path, read_only=True)
    try:
        sheet = book.active
        if not all(sheet[cell].value for cell in ('A1', 'B1', 'D1', 'E1', 'F1', 'G1', 'H1', 'I1')):
            raise ValueError('2.xlsx 성적서 형식의 템플릿을 선택하세요. / 請選擇 2.xlsx 報表格式的範本。')
    finally:
        book.close()


def remember_template(source):
    source = Path(source)
    validate_template(source)
    destination = DATA_DIR / 'templates' / 'report.xlsx'
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != destination.resolve():
        if destination.exists():
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            shutil.copy2(destination, destination.with_name(f'report-backup-{stamp}.xlsx'))
        temp = destination.with_suffix('.tmp')
        shutil.copy2(source, temp)
        temp.replace(destination)
    APP_SETTINGS.setValue('yield/report_template', str(destination.resolve()))
    return str(destination)


def resolve_template():
    private = DATA_DIR / 'templates' / 'report.xlsx'
    if private.is_file():
        validate_template(private)
        return str(private)
    base = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent
    saved = str(APP_SETTINGS.value('yield/report_template', '') or '')
    candidates = ([Path(saved)] if saved else []) + [DATA_DIR/'2.xlsx', Path(sys.argv[0]).resolve().parent/'2.xlsx', Path.cwd()/'2.xlsx', base/'resources/templates/report.xlsx']
    for path in candidates:
        if path.is_file():
            return remember_template(path)
    raise FileNotFoundError('저장된 성적서 템플릿이 없습니다. 템플릿 변경에서 지정하세요. / 找不到儲存的報表範本，請於變更範本中指定。')
