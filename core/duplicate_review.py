"""Group duplicate versions by Strip and make only exact, explainable choices."""
from core.gather import (gather_strip_from_payload, gather_strip_to_payload,
                         merge_gather_strips, should_replace_gather_strip)


def build_review_plan(conflicts):
    grouped = {}
    for conflict in conflicts:
        key = conflict['strip_id'].strip().upper()
        entry = grouped.setdefault(key, {'strip_id': key, 'versions': {}, 'bbt_coords': conflict.get('bbt_coords', []), 'ski_coords': conflict.get('ski_coords', [])})
        for name in ('current', 'candidate'):
            payload = conflict[name]
            fingerprint = tuple(sorted(line.strip() for line in payload.get('defect_lines', [])))
            previous = entry['versions'].get(fingerprint)
            if previous is None or should_replace_gather_strip(gather_strip_from_payload(previous), gather_strip_from_payload(payload)):
                entry['versions'][fingerprint] = payload
    result = []
    for entry in grouped.values():
        candidates = list(entry.pop('versions').values())
        candidates = [gather_strip_to_payload(gather_strip_from_payload(p)) for p in candidates]
        original_count = len(candidates)
        if original_count > 1:
            merged = gather_strip_from_payload(candidates[0])
            for candidate in candidates[1:]:
                merged = merge_gather_strips(merged, gather_strip_from_payload(candidate))
            merged_payload = gather_strip_to_payload(merged)
            if set(merged_payload['defect_lines']) not in [set(p['defect_lines']) for p in candidates]:
                candidates.append(merged_payload)
        ski, bbt = set(entry['ski_coords']), set(entry['bbt_coords'])
        exact = [i for i, p in enumerate(candidates[:original_count]) if set(p['coords']) | bbt == ski] if ski else []
        recommended = exact[0] if len(exact) == 1 else None
        reason = 'SKI 좌표 정확 일치' if recommended is not None else '개별 검수 필요'
        if not ski and original_count == 1:
            recommended, reason = 0, '내용 동일 (SKI 없음)'
        entry.update(candidates=candidates, original_count=original_count, recommended=recommended, reason=reason)
        result.append(entry)
    return result
