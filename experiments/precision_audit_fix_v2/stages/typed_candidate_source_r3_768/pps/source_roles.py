"""Bounded source role observations for the typed prompt; never item labels."""
from .regions import region_facts
from .model_constraints import facts as model_facts
from .other_checks import sw_check


def observations(rec, items):
    groups = []
    if 7 in items:
        regional = region_facts(rec)
        office = [{'kind': 'bidder_location', 'role': x['role'], 'locations':
                   [{k: loc[k] for k in ('raw', 'unit', 'province')} for loc in x['locations']],
                   'operative': x['operative'], 'evidence': x['evidence']}
                  for x in regional['office_requirements']]
        worksite = [{'kind': 'performance_location', 'provinces': x['provinces'],
                    'possible_multi_province': x['possible_multi_province'], 'evidence': x['evidence']}
                   for x in regional['places_of_performance']]
        groups.extend([office, worksite])
    if 9 in items:
        models = model_facts(rec)
        groups.extend([
            [{'kind': 'model_field_candidate', 'role': x['role'], 'evidence': x['evidence']} for x in models['model_field_candidates']],
            [{'kind': 'equivalence_scope_requires_review', 'evidence': x['evidence']} for x in models['alternative_clauses']],
            [{'kind': 'existing_or_renewal_context', 'evidence': x} for x in models['existing_or_renewal_clauses']]])
    if 20 in items:
        sw = sw_check(rec)['facts']
        groups.extend([
            [{'kind': 'actual_SW_work_candidate', 'evidence': {**x['evidence'], 'text': x['evidence']['quote']}} for x in sw['actual_work']],
            [{'kind': 'SW_floor_disclosure', 'evidence': {**x, 'text': x['quote']}} for x in sw['floor_disclosure']]])
    # Per-family bounds keep numerous model/boilerplate mentions from consuming
    # every source reservation. Round-robin families, never labels or IDs.
    queues = [g[:6] for g in groups]
    selected = []
    while any(queues) and len(selected) < 18:
        for queue in queues:
            if queue and len(selected) < 18:
                selected.append(queue.pop(0))
    return selected


def ranges(observed):
    return [(x['evidence']['doc_index'], x['evidence']['start'], x['evidence']['end'])
            for x in observed if x['evidence']['end'] > x['evidence']['start']]


def packet(observed, spans, rec, items):
    kinds = ({'bidder_location', 'performance_location'} if 7 in items else set())
    if 9 in items: kinds |= {'model_field_candidate', 'equivalence_scope_requires_review', 'existing_or_renewal_context'}
    if 20 in items: kinds |= {'actual_SW_work_candidate', 'SW_floor_disclosure'}
    result, omitted = [], 0
    for observation in observed:
        if observation['kind'] not in kinds: continue
        ev = observation['evidence']
        covered = [(max(s.start, ev['start']), min(s.end, ev['end']), n) for n, s in enumerate(spans,1)
                   if s.doc_index == ev['doc_index'] and s.start < ev['end'] and ev['start'] < s.end]
        covered.sort()
        cursor, shown = ev['start'], bool(covered)
        original = rec['docs'][ev['doc_index']]['text']
        for start, end, _ in covered:
            if start > cursor and original[cursor:start].strip(): shown = False
            cursor = max(cursor, end)
        if original[cursor:ev['end']].strip(): shown = False
        if not shown:
            omitted += 1
            continue
        result.append({k:v for k,v in observation.items() if k != 'evidence'} | {'S': [n for _,_,n in covered]})
    return {'observations': result, 'omitted_observations': omitted,
            'instruction': '원문 역할 검색 보조정보다. 모델명 자체는 신규납품 제한이 아니며, 대체 허용은 적용되는 품목 범위를 확인한다. 본점과 수행 장소를 합치지 않는다. 관측 없음은 법정 예외 부재 증명이 아니다.'}
