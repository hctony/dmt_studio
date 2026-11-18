"""Only explicit whole-strip Dummy codes can scrap the non-BBT units."""
WHOLE_STRIP_CODES = frozenset('AI01 AI02 AU00 AU02 DR00 DR01 DR02 DR05 DR07 RT00 RT02 RT07 RT08 SM03'.split())
ALARM_LABELS={'DUMMY_9999_ALARM':'Dummy 9999 감지 — 자동 전량 판정하지 않음',
              'DUMMY_CODE_CONFLICT':'Dummy 전량 코드 충돌 — XML 확인 필요'}


class ScrapStrips(set):
    def __init__(self, codes):
        super().__init__(codes)
        self.codes = codes


def evaluate(records_by_strip):
    codes = {}; alarms = []
    for strip, records in records_by_strip.items():
        dummy = [r for r in records if r.is_dummy]
        found = sorted({r.ivs_1.upper() for r in dummy if r.ivs_1.upper() in WHOLE_STRIP_CODES})
        if any(r.is_dummy_9999 for r in dummy):
            alarms.append({'strip_id': strip, 'reason': 'DUMMY_9999_ALARM', 'codes': ['9999']})
        if len(found) == 1:
            codes[strip] = found[0]
        elif len(found) > 1:
            alarms.append({'strip_id': strip, 'reason': 'DUMMY_CODE_CONFLICT', 'codes': found})
    return codes, alarms
