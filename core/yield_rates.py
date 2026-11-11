"""Shared whole-production denominator and per-code defect rate."""
def production_units(strip_count, units_per_strip):
    return int(strip_count) * int(units_per_strip)


def single_defect_rate(defect_units, total_units):
    if not total_units or defect_units < 0 or defect_units > total_units:
        return None
    return round(defect_units / total_units * 100, 6)


def verified_population_ids(strip_json_path, units_per_strip):
    """Only a completed, full-LOT NAS population changes legacy denominators."""
    if not strip_json_path:
        return set()
    import json
    payload=json.loads(strip_json_path.read_text(encoding='utf-8-sig'))
    if payload.get('schema')!='dmt-studio-strip-population-v1' or payload.get('scope')!='lot' or payload.get('complete') is not True:
        return set()
    known_units=int(payload.get('units_per_strip') or 0)
    if not known_units:
        return set()
    if known_units!=units_per_strip:
        raise ValueError('IVS와 수율 계산의 Strip당 Unit 수가 다릅니다. 모델 설정과 Gathering X×Y를 확인하세요. / IVS 與良率計算的每片 Unit 數不同，請確認機種設定及 Gathering 的 X×Y。')
    ids=set(payload.get('strip_ids',[]))
    lot=str(payload.get('lot_id','')).upper()
    if not ids or not lot or any(not isinstance(value,str) or not value.upper().startswith(lot) for value in ids):
        raise ValueError('NAS 전체 Strip 목록의 LOT가 일치하지 않습니다. / NAS 完整 Strip 清單的 LOT 不一致。')
    return ids
