"""Excel export logic for yield reports."""
from __future__ import annotations

import os
from datetime import date
from collections import defaultdict
from pathlib import Path
from core.i18n import _


def _week_of_month(d: date) -> int:
    """Return the week number (1-based) of the month for a given date."""
    first_day = d.replace(day=1)
    return (d.day + first_day.weekday()) // 7 + 1


def _autofit_columns(ws) -> None:
    """Approximate column auto-fit based on max cell content length."""
    col_widths: dict[int, int] = defaultdict(int)
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is not None:
                col_widths[cell.column] = max(col_widths[cell.column], len(str(cell.value)) + 2)
    for col_idx, width in col_widths.items():
        col_letter = ws.cell(row=1, column=col_idx).column_letter
        ws.column_dimensions[col_letter].width = min(max(width, 8), 40)


def export_report_to_2xlsx(
    report: dict,
    template_path: str,
    output_path: str,
) -> dict:
    """
    Export 1차 성적서 to Excel.

    template_path가 있으면 기존 2.xlsx 포맷 사용, 없으면 새 워크북으로 생성.

    Expected report keys (from build_yield_report_aggregation):
      model_name, lot_id, total_units, ok_units, ng_units,
      ok_unit_aggregation.ok_unit_percent,
      final_defects_by_code_including_bbt: {code: {count, ...}}
    """
    try:
        from openpyxl import load_workbook, Workbook
        from openpyxl.styles import Alignment, PatternFill, Font
        from openpyxl.utils import get_column_letter
    except ImportError:
        return {"success": False, "error": _("openpyxl 라이브러리가 필요합니다. 'pip install openpyxl'을 실행하세요.")}

    use_template = bool(template_path and os.path.exists(template_path))
    if template_path and not use_template:
        return {"success": False, "error": _("템플릿 파일이 존재하지 않습니다.")}
    if use_template and Path(template_path).resolve() == Path(output_path).resolve():
        return {"success": False, "error": _("템플릿 원본에 덮어쓸 수 없습니다. 다른 저장 위치를 선택하세요.")}

    try:
        today = date.today()

        # ── 공통: 기본 지표 ──────────────────────────────────────────────────
        total_units = int(report.get("total_units", 0) or 0)
        ok_units    = int(report.get("ok_units", 0) or 0)
        ng_units    = int(report.get("ng_units", 0) or 0)
        ok_pct      = round(report.get("ok_unit_aggregation", {}).get("ok_unit_percent", 0.0), 2)
        model_name  = report.get("model_name", "")
        lot_id      = report.get("lot_id", "")

        # ── 공통: 불량 코드 정렬 ─────────────────────────────────────────────
        raw_defects: dict[str, int] = {
            code: int(item.get("count", 0) or 0)
            for code, item in report.get("final_defects_by_code_including_bbt", {}).items()
        }
        if "170" in raw_defects:
            raw_defects["OT06"] = raw_defects.pop("170") + raw_defects.get("OT06", 0)
        defects = {code: cnt for code, cnt in raw_defects.items() if cnt > 0}

        PRIORITY_CODES = ["SM03_DUMMY9999", "UNKNOWN"]
        priority_defects: list[tuple[str, int]] = [
            (code, defects.pop(code)) for code in PRIORITY_CODES if code in defects
        ]
        normal_defects: list[tuple[str, int]] = sorted(defects.items())
        ordered_defects: list[tuple[str, int]] = priority_defects + normal_defects

        DEFECT_START_COL = 10  # J 열 (1-based)

        if use_template:
            # ── 템플릿 모드: 기존 2.xlsx 로드 ────────────────────────────────
            wb = load_workbook(template_path)
            ws = wb.active
            from copy import copy
            template_styles = {row: copy(ws.cell(row, DEFECT_START_COL)._style) for row in (1, 2, 3)}
            template_widths = {str(ws.cell(1, col).value).strip().upper(): ws.column_dimensions[get_column_letter(col)].width for col in range(DEFECT_START_COL, ws.max_column+1)}

            ws["A3"] = f"{today.month}/{today.strftime('%d/%Y')}"
            ws["B3"] = _week_of_month(today)
            ws["C3"] = None
            ws["D3"] = model_name
            ws["E3"] = lot_id
            ws["F3"] = total_units
            ws["G3"] = ok_units
            ws["H3"] = ng_units
            ws["I3"] = round(ok_pct / 100, 4)
            ws["I3"].number_format = "0.00%"

            header_to_col: dict[str, int] = {}
            for col_idx in range(1, ws.max_column + 1):
                val = ws.cell(row=1, column=col_idx).value
                if isinstance(val, str) and val.strip():
                    header_to_col[val.strip().upper()] = col_idx

            defect_code_set = {code.upper() for code, _cnt in ordered_defects}
            existing_defect_cols: dict[str, int] = {
                hdr: col for hdr, col in header_to_col.items()
                if col >= DEFECT_START_COL
            }

            for code, cnt in ordered_defects:
                col = existing_defect_cols.get(code.upper())
                if col:
                    ws.cell(row=3, column=col).value = cnt

            cols_to_delete = sorted(
                [col for hdr, col in existing_defect_cols.items() if hdr not in defect_code_set],
                reverse=True,
            )
            for col in cols_to_delete:
                ws.delete_cols(col)

            header_to_col_after: dict[str, int] = {}
            for col_idx in range(1, ws.max_column + 1):
                val = ws.cell(row=1, column=col_idx).value
                if isinstance(val, str) and val.strip():
                    header_to_col_after[val.strip().upper()] = col_idx

            insert_at = DEFECT_START_COL
            for code, cnt in ordered_defects:
                if code.upper() in header_to_col_after:
                    insert_at = header_to_col_after[code.upper()] + 1
                    continue
                ws.insert_cols(insert_at)
                for row, style in template_styles.items():
                    ws.cell(row, insert_at)._style = copy(style)
                ws.cell(row=1, column=insert_at).value = code
                ws.cell(row=3, column=insert_at).value = cnt
                for k in list(header_to_col_after.keys()):
                    if header_to_col_after[k] >= insert_at:
                        header_to_col_after[k] += 1
                header_to_col_after[code.upper()] = insert_at
                insert_at += 1

            for code, col in header_to_col_after.items():
                if col >= DEFECT_START_COL:
                    ws.column_dimensions[get_column_letter(col)].width = template_widths.get(code, 14)

        else:
            # ── 템플릿 없음: 새 워크북 생성 ──────────────────────────────────
            wb = Workbook()
            ws = wb.active
            ws.title = "성적서"

            bold = Font(bold=True)

            # Row 1: 헤더
            fixed_headers = ["날짜", "주차", "", "모델", "LOT", "총수량", "양품", "불량", "수율"]
            for i, h in enumerate(fixed_headers, 1):
                cell = ws.cell(row=1, column=i, value=h)
                cell.font = bold
            for j, (code, _cnt) in enumerate(ordered_defects):
                cell = ws.cell(row=1, column=DEFECT_START_COL + j, value=code)
                cell.font = bold

            # Row 3: 데이터
            ws["A3"] = f"{today.month}/{today.strftime('%d/%Y')}"
            ws["B3"] = _week_of_month(today)
            ws["C3"] = None
            ws["D3"] = model_name
            ws["E3"] = lot_id
            ws["F3"] = total_units
            ws["G3"] = ok_units
            ws["H3"] = ng_units
            ws["I3"] = round(ok_pct / 100, 4)
            ws["I3"].number_format = "0.00%"

            for j, (code, cnt) in enumerate(ordered_defects):
                ws.cell(row=3, column=DEFECT_START_COL + j, value=cnt)

            header_to_col_after: dict[str, int] = {
                code.upper(): DEFECT_START_COL + j
                for j, (code, _cnt) in enumerate(ordered_defects)
            }

        # ── 공통: 검증 수식 및 하이라이트 ──────────────────────────────────
        defect_col_letters: list[str] = []
        for code, _cnt in ordered_defects:
            col = header_to_col_after.get(code.upper())
            if col:
                defect_col_letters.append(get_column_letter(col))

        first_defect_letter = defect_col_letters[0] if defect_col_letters else "J"
        last_defect_letter  = defect_col_letters[-1] if defect_col_letters else "J"

        ws["H6"] = (
            f'=IF(SUM({first_defect_letter}3:{last_defect_letter}3)=H3,'
            f'"✓ 일치 ("&SUM({first_defect_letter}3:{last_defect_letter}3)&")",'
            f'"⚠ 불일치: 합계="&SUM({first_defect_letter}3:{last_defect_letter}3)&" / NG="&H3)'
        )

        high_rate: list[str] = []
        for code, cnt in ordered_defects:
            if total_units > 0 and cnt / total_units >= 0.03:
                pct = round(cnt / total_units * 100, 2)
                high_rate.append(f"{code} {pct}%")
        ws["H7"] = ", ".join(high_rate) if high_rate else _("없음")

        if not use_template:
            _autofit_columns(ws)
        wb.save(output_path)

        return {
            "success": True,
            "ng_qty": ng_units,
            "sum_defects": sum(cnt for _code, cnt in ordered_defects),
            "high_rate_codes": high_rate,
            "used_template": use_template,
        }

    except Exception as e:
        import traceback
        return {"success": False, "error": f"{str(e)}\n{traceback.format_exc()}"}


def export_yield_to_excel(summary: dict, template_path: str, output_path: str) -> dict:
    """
    Legacy export: maps defect codes from summary._merged_defect_totals
    to template columns by header name, deleting unused defect columns.
    """
    if not os.path.exists(template_path):
        return {"success": False, "error": _("템플릿 파일이 존재하지 않습니다.")}

    try:
        from openpyxl import load_workbook
    except ImportError:
        return {"success": False, "error": _("openpyxl 라이브러리가 필요합니다. 'pip install openpyxl'을 실행하세요.")}

    try:
        wb = load_workbook(template_path)
        ws = wb.active

        header_map: dict[str, int] = {}
        for col_idx in range(1, ws.max_column + 1):
            cell_value = ws.cell(row=1, column=col_idx).value
            if cell_value and isinstance(cell_value, str):
                header_map[cell_value.strip().upper()] = col_idx

        inspection_qty = summary.get("total_units", 0)
        accept_qty     = summary.get("good_units", 0)
        reject_qty     = summary.get("defective_units", 0)
        accept_rate    = round(summary.get("unit_yield_percent", 0.0), 2)

        all_defects: dict[str, int] = defaultdict(int)
        for code, count in summary.get("existing_bbt_code_distribution", {}).items():
            all_defects[code.strip().upper()] += count
        for code, count in summary.get("_merged_defect_totals", {}).items():
            all_defects[code.strip().upper()] += count

        reserved_headers = {"INSPECTION QTY", "ACCEPT QTY", "REJECT QTY", "ACCEPT RATE", "모델명", "LOT 번호"}

        def write_if_exists(header_name: str, value):
            col_idx = header_map.get(header_name.strip().upper())
            if col_idx:
                ws.cell(row=3, column=col_idx).value = value
            return col_idx

        write_if_exists("INSPECTION QTY", inspection_qty)
        write_if_exists("ACCEPT QTY",     accept_qty)
        write_if_exists("REJECT QTY",     reject_qty)
        write_if_exists("ACCEPT RATE",    accept_rate)

        used_col_indices: set[int] = set()
        for k in ["INSPECTION QTY", "ACCEPT QTY", "REJECT QTY", "ACCEPT RATE"]:
            idx = header_map.get(k)
            if idx:
                used_col_indices.add(idx)

        defect_col_candidates = [
            idx for h, idx in header_map.items() if h not in reserved_headers
        ]

        inserted_defects: dict[str, int] = {}
        unmapped_defects: list[str] = []

        for code, count in all_defects.items():
            if not count:
                continue
            col_idx = header_map.get(code)
            if col_idx:
                ws.cell(row=3, column=col_idx).value = count
                inserted_defects[code] = count
                used_col_indices.add(col_idx)
            else:
                available = [c for c in defect_col_candidates if c not in used_col_indices]
                if available:
                    target = available[0]
                    ws.cell(row=1, column=target).value = code
                    ws.cell(row=3, column=target).value = count
                    inserted_defects[code] = count
                    used_col_indices.add(target)
                    header_map[code] = target
                else:
                    unmapped_defects.append(code)

        cols_to_delete = sorted(
            [c for c in defect_col_candidates if c not in used_col_indices],
            reverse=True,
        )
        for col_idx in cols_to_delete:
            ws.delete_cols(col_idx)

        wb.save(output_path)

        return {
            "success": True,
            "ng_qty": reject_qty,
            "sum_inserted": sum(inserted_defects.values()),
            "unmapped": unmapped_defects,
            "inserted_defects": inserted_defects,
        }

    except Exception as e:
        import traceback
        return {"success": False, "error": f"{str(e)}\n{traceback.format_exc()}"}
