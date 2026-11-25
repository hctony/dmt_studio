"""Generate an operator-999 VRS result from the evidence in a yield calculation."""
from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

from core.gather import build_gather_output, gather_line_coords, parse_gather_code, parse_xy_from_gather_line
from core.i18n import _
from core.ivs_result_transfer import configured_roots, publish_without_overwrite, resolve_vrs_result_folder
from core.models import GatherStrip
from core.report_review import sources_changed


COMMON_CODES = {'170', '1111', '111', 'OT14', 'OT06', '9999'}
PROCESS_PRIORITY = {'IVS': 0, 'FVI': 1, 'VRS': 2}


@dataclass(frozen=True)
class Correction:
    body: bytes
    strips: int
    corrected: int
    retained: int
    common: int
    missing: tuple[tuple[str, int, int], ...]


@dataclass(frozen=True)
class CorrectionPlan:
    correction: Correction
    destination: Path
    resolved_folder: Path


def _defects(process, lines, lot, strip):
    for index, line in enumerate(lines):
        fields = line.split(',')
        if len(fields) < 6 or fields[0].strip().upper() != lot or fields[1].strip().upper() != strip:
            continue
        coord = parse_xy_from_gather_line(line)
        face = fields[4].strip().upper()
        code = parse_gather_code(line).upper()
        if not coord or min(coord) < 1 or face not in {'T', 'B'}:
            continue
        common = code in COMMON_CODES
        if common or re.fullmatch(r'[A-Z]{2}[0-9]{2}', code):
            yield coord, (not common, PROCESS_PRIORITY[process], index), face, code


def build_correction(summary, rows):
    lot = summary['lot_id'].strip().upper()
    if not re.fullmatch(r'[A-Z0-9][A-Z0-9-]*', lot):
        raise ValueError(_('수율 계산 LOT를 확인하세요.'))
    trace = summary.get('report_trace', {})
    reviewed = {(item['strip_id'], item['process']) for item in summary.get('whole_strip_review', [])}
    output = []
    missing = set()
    corrected = retained = common_count = 0
    for row in rows:
        strip = row['strip_id']
        if not strip.startswith(lot) or strip == lot or not re.fullmatch(r'[A-Z0-9-]+', strip):
            raise ValueError(_('불일치 Strip의 LOT가 수율 계산과 다릅니다.'))
        info = trace.get('strips', {}).get(strip, {})
        ski = {tuple(coord) for coord in info.get('ski_coords', [])}
        bbt = {tuple(coord) for coord in info.get('bbt_coords', [])}
        targets = {tuple(coord) for field in ('txt_only', 'ski_only') for coord in row.get(field, [])}
        best = {}
        previous_vrs = {}
        for process, data in info.get('processes', {}).items():
            if process not in PROCESS_PRIORITY:
                continue
            lines = data.get('defect_lines', [])
            if (strip, process) in reviewed:
                targets.update(gather_line_coords(lines) - ski - bbt)
            for coord, rank, face, code in _defects(process, lines, lot, strip):
                result = rank, face, code
                if coord not in best or rank > best[coord][0]:
                    best[coord] = result
                if process == 'VRS' and (coord not in previous_vrs or rank > previous_vrs[coord][0]):
                    previous_vrs[coord] = result
        targets -= bbt
        missing.update((strip, *coord) for coord in targets - best.keys())
        corrections = {coord: best[coord] for coord in targets & best.keys()}
        if not corrections:
            continue
        # The VRS reader treats the new block as the latest complete Strip.
        # Carry forward its previously matched VRS coordinates to preserve them.
        preserved = {coord: defect for coord, defect in previous_vrs.items() if coord in ski - bbt - targets}
        defects = preserved | corrections
        lines = [f'{lot},{strip},{x},{y},{face},{code}'
                 for (x, y), (_, face, code) in sorted(defects.items(), key=lambda item: (item[0][1], item[0][0]))]
        output.append(GatherStrip(lot, strip, lines))
        corrected += len(corrections)
        retained += len(preserved)
        common_count += sum(code in COMMON_CODES for _, _, code in corrections.values())
    body = build_gather_output(output, lot) + f'\n{lot},EOL\n' if output else ''
    return Correction(body.replace('\n', '\r\n').encode('ascii'), len(output), corrected, retained, common_count,
                      tuple(sorted(missing)))


def _destination(model):
    roots = configured_roots(model)
    if not roots['VRS']:
        raise ValueError(_('A PC의 VRS 1호기 설정을 확인하세요.'))
    return resolve_vrs_result_folder(roots['VRS'][0], roots)


def _require_current(summary):
    if not summary.get('report_trace', {}).get('ski_path'):
        raise ValueError(_('최종 SKI 파일을 선택하고 수율을 다시 계산하세요.'))
    if summary.get('ivs_transfer_requires_recalculation') or summary.get('vrs_correction_output') or sources_changed(summary):
        raise ValueError(_('최신 SKI와 공정 TXT로 수율을 다시 계산하세요.'))


def prepare_correction(model, summary, rows):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.&-]*', model):
        raise ValueError(_('모델명을 확인하세요.'))
    _require_current(summary)
    correction = build_correction(summary, rows)
    if not correction.body:
        raise ValueError(_('원본 코드와 검사면을 확인할 수 있는 불일치 좌표가 없습니다.'))
    folder, resolved = _destination(model)
    now = datetime.now().strftime('%H%M%S')
    filename = f"{model}_{summary['lot_id'].strip().upper()}_999_{now}.txt"
    return CorrectionPlan(correction, folder / filename, resolved)


def save_correction(model, summary, plan):
    _require_current(summary)
    folder, resolved = _destination(model)
    if folder != plan.destination.parent or resolved != plan.resolved_folder:
        raise ValueError(_('VRS 1호기 설정이 바뀌었습니다. 저장 위치를 다시 확인하세요.'))
    target = resolved / plan.destination.name
    temporary = None
    try:
        with NamedTemporaryFile(dir=resolved, prefix='.dmt-vrs-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(plan.correction.body)
            stream.flush()
            os.fsync(stream.fileno())
        publish_without_overwrite(temporary, target)
    except FileExistsError:
        raise FileExistsError(_('VRS 폴더에 같은 이름의 TXT가 있습니다.') + '\n' + str(target)) from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return plan.destination
