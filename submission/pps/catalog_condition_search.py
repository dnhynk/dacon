"""Turn supplied designation predicates into questions, never purchase facts.

The whole note remains available, including unsupported clauses. Atomic questions
ask for both sides of a condition and its scope; a search hit cannot satisfy the
predicate. The cap is shared fairly across candidate products and every omitted
question is reported. No notice answer, review example or label enters planning.
"""
from __future__ import annotations

from collections import deque
import re

from .catalog_condition_facts import _LABELS
from .catalog_predicates import compile_note


# These are retrieval vocabulary/role contrasts, not additional legal rules.
# In particular takeoff weight and maximum altitude are NOT self weight and
# operating altitude. Finding their context can explain an unresolved fact.
_QUESTIONS = {
    'cpu_architecture': 'CPU 프로세서 Processor 아키텍처와 구조, x86 또는 ARM 등 실제 납품 사양',
    'cpu_count': '서버 한 대에 장착할 CPU 프로세서 개수와 소켓 수, 코어 수 및 서버 수량과의 구별',
    'cpu_base_ghz': 'CPU Clock 기본 주파수 GHz MHz, 최대 터보 부스트 주파수와의 구별',
    'fixed_wing': '기체의 고정익 또는 회전익 구조와 프로펠러 사양',
    'military_use': '기체의 실제 군사용 또는 다른 사용 목적과 적용 대상',
    'hydrogen_drone': '기체의 수소 연료전지, 배터리 또는 혼합 동력 방식',
    'self_weight_kg': '기체 자체중량과 kg g 단위, 이륙중량 및 부속품 중량과의 구별',
    'operating_altitude_m': '기체 운용 상승고도와 m 단위, 최대 비행고도 및 이륙고도와의 구별',
    'product_subtype': '제품의 세부유형, 원재료와 제조방식 및 구매 품목별 규격',
    'delivery_province': '실제 납품장소의 광역 시도, 업체 본점 소재지 및 입찰 참가 지역과의 구별',
    'annual_exception_percent': '해당 지역의 연간 예측량 대비 이번 예외 물량의 비율과 계산 범위',
    'quota_exception_applied': '연간 예측량 예외의 실제 적용 또는 미적용, 단순 예외 가능 안내와의 구별',
    'statutory_heritage_repair': '실제 사업의 국가유산 수리 용도와 공사 범위 및 적용 법률',
    'public_agency_promotion': '제작 영상의 홍보 또는 교육 등 사용 목적과 각 산출물의 범위',
    'commissioning_public_agency_identified': '제작 영상에 포함할 발주 공공기관의 명칭, 로고, 캐릭터 등 식별정보와 적용 영상의 범위',
}


def _note_parts(note, limit=240):
    """Bound search strings without dropping an unsupported clause.

    These slices are search suggestions only; the full original note and the
    compiler's all-or-nothing result remain in the plan.
    """
    for start in range(0, len(note), limit):
        yield note[start:start + limit]


def query_plan(candidates, *, max_queries=32):
    if type(max_queries) is not int or max_queries < 1:
        raise ValueError('A positive condition query cap is required')
    rows = {}
    for row in candidates:
        if (not isinstance(row, dict) or not isinstance(row.get('code'), str)
                or (row['code'] and not re.fullmatch(r'[0-9]{10}', row['code']))
                or not isinstance(row.get('name'), str) or not row['name'].strip()
                or not isinstance(row.get('condition'), str)):
            raise ValueError('Condition search requires named supplied catalog rows')
        # A duplicate row cannot gain extra rank weight. Conflicting static rows
        # must not be collapsed by choosing one note.
        key = (row['code'], row['name'], row['condition'])
        rows[key] = row
    products, pending = [], []
    for (code, name, note), row in sorted(rows.items()):
        if not note.strip():
            continue
        program = compile_note(note)
        requests = []
        if program is not None:
            for field in program['required_fields']:
                # Every field understood by the predicate compiler has a source
                # label; an unknown future field fails instead of vanishing.
                labels = _LABELS[field]
                vocabulary = _QUESTIONS.get(field, ', '.join(labels) + '의 해당 여부, 값, 단위와 적용 범위')
                requests.append({'field': field,
                    'query': f'구매 후보 {name}: {vocabulary}. 필수, 선택, 대체 허용 및 예외 조건을 함께 확인'})
        if program is None or not program['required_fields']:
            requests.extend({'field': None, 'query': f'구매 후보 {name}의 실제 규격과 용도 및 조건: {part}'}
                            for part in _note_parts(note))
        products.append({'code': code, 'name': name, 'note': note,
            'program': program, 'questions': requests, 'queried_fields': [],
            'unsearched_fields': [], 'query_plan_complete': False})
        pending.append(deque((len(products) - 1, i) for i in range(len(requests))))
    queries, selected = [], set()
    # Each product gets a turn before a long compound condition gets another.
    # Canonical catalog order makes duplicate/permuted discovery reproducible.
    while any(pending):
        for queue in pending:
            if not queue:
                continue
            product_index, index = queue.popleft()
            question = products[product_index]['questions'][index]['query']
            if question not in queries and len(queries) >= max_queries:
                continue
            if question not in queries:
                queries.append(question)
            selected.add((product_index, index))
    for pi, product in enumerate(products):
        for qi, question in enumerate(product['questions']):
            question['searched'] = (pi, qi) in selected
            if question['field']:
                product['queried_fields' if question['searched'] else 'unsearched_fields'].append(question['field'])
        product['query_plan_complete'] = all(q['searched'] for q in product['questions'])
    return {'version': 'catalog_condition_questions_v1', 'products': products,
        'queries': queries, 'max_queries': max_queries,
        'omitted_questions': sum(not q['searched'] for p in products for q in p['questions']),
        'query_plan_complete': all(p['query_plan_complete'] for p in products),
        'condition_truth_certified': False, 'purchase_identity_certified': False,
        'absence_verified': False}
