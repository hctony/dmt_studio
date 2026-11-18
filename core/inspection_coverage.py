"""Absent blocks are distinct from explicit EOS-only (zero defect) blocks."""
LABELS={'ZERO_DEFECT_BLOCK':'검사 완료 블록 · 불량 0개','INSPECTED_BLOCK':'검사 결과 블록 있음',
        'NO_BLOCK':'결과 블록 없음 · 검사 여부 확인 필요'}
def build(expected, blocks):
    return [{'strip_id':strip,
             'processes':{stage:('ZERO_DEFECT_BLOCK' if not stages[strip].defect_lines else 'INSPECTED_BLOCK')
                          if strip in stages else 'NO_BLOCK'
                          for stage,stages in blocks.items()},
             'no_result_block':not any(strip in stages for stages in blocks.values())}
            for strip in sorted(expected)]
