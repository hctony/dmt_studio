"""One code mapping and ordering for Excel export and its preview."""
from collections import Counter


def excel_code(code):
    return 'OT06' if str(code).strip() in {'170', '1111'} else str(code)


def ordered_report_codes(report):
    counts = Counter()
    for code, row in report.get('final_defects_by_code_including_bbt', {}).items():
        counts[excel_code(code)] += int(row.get('count', 0) or 0)
    counts={code:value for code,value in counts.items() if value>0}
    result=[(code,counts.pop(code)) for code in ('SM03_DUMMY9999','UNKNOWN') if code in counts]
    return result+sorted(counts.items())


def followup_code(code):
    normalized = excel_code(code).strip().upper()
    return bool(normalized) and normalized not in {'OT06', 'OT14', 'UNKNOWN', '9998'}


def high_rate_report_codes(report):
    total_units = int(report.get('total_units', 0) or 0)
    return [(code, count) for code, count in ordered_report_codes(report)
            if total_units > 0 and count * 100 >= total_units * 3 and followup_code(code)]
