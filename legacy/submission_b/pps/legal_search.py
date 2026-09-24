"""Issue-directed, bounded retrieval from supplied law, never a legal verdict.

The curated dependency plans select statutory units, not notice IDs, labels or
model predictions. They are intentionally distinct from the default v2 input.
All named plan dependencies are measured; a covered plan is not a closed legal
proof, and unresolved external references remain explicit.
"""
from __future__ import annotations

import hashlib

from .law_units import Unit, render_unit, select_unit


EXTRA_ALIASES = {
    '국가계약법': '국가를 당사자로 하는 계약에 관한 법률.txt',
    '지방계약법': '지방자치단체를 당사자로 하는 계약에 관한 법률.txt',
}
_SUBJECT = (Unit('판로지원법', '제2조', number='2'), Unit('판로지원법 시행령', '제2조'))

# Obligation and its immediate exception/implementation are atomic. Definitions
# are a separate declared group so an insufficient budget is visible, not a
# reason to silently drop a proviso from the obligation.
PLANS = {
    'direct_production': (
        ('obligation_and_verification', (Unit('판로지원법', '제9조'),
                                         Unit('판로지원법 시행령', '제10조'))),
        ('public_agency_definition', _SUBJECT)),
    'sme_competition': (
        ('competition_and_exceptions', (Unit('판로지원법', '제7조', (1,)),
                                        Unit('판로지원법 시행령', '제7조', (1, 2)))),
        ('public_agency_definition', _SUBJECT)),
    'sme_priority': (
        ('priority_and_exceptions', (Unit('판로지원법', '제4조', (2,)),
                                    Unit('판로지원법 시행령', '제2조의2', (1,)),
                                    Unit('판로지원법 시행령', '제2조의3'))),
        ('public_agency_definition', _SUBJECT)),
    'public_agency': (('public_agency_definition', _SUBJECT),),
    'local_contract_delegation': (
        ('local_scope_and_delegation', (Unit('지방계약법', '제2조'), Unit('지방계약법', '제8조'))),
        ('public_agency_definition', _SUBJECT)),
}
UNEXPANDED = {
    'direct_production': ('국가계약법 제7조·시행령 제26조 및 지방계약법 제9조·시행령 제25조의 수의계약 요건',
                          '판로지원법 제11조·제33조 및 직접생산 확인기준·시행규칙',
                          '경쟁제품 고시 품목·특이사항 및 실제 구매대상'),
    'sme_competition': ('판로지원법 시행령 제8조 및 다른 법령의 우선구매·수의계약 요건',
                        '중소기업자 정의·확인 및 경쟁제품 고시 품목·특이사항'),
    'sme_priority': ('중소기업·소기업·소상공인 및 간주단체의 정의·확인',
                     '국가계약법 제4조의 고시금액 및 추정가격 정의',
                     '다른 법령의 우선구매·수의·지명계약 및 별도 고시 예외',
                     '판로지원법 제6조의 경쟁제품 지정·특이사항'),
    'public_agency': (),
    'local_contract_delegation': ('대행계약의 구체적 범위·계약 당사자·채택 절차',),
}
_SUBJECT_OUTSIDE = ('공공기관 정의에서 인용한 개별 법령 및 실제 기관의 해당 여부',)
_NOTE = '[배포 법령의 쟁점별 발췌; 원문·예외 확인용이며 위반판정 아님]\n[외부 참조·실제 적용조건은 미확정; 의존 단위 생략 가능]'


def search_legal_dependencies(topic, laws, aliases, *, max_chars=3600,
                              tokenizer=None, max_source_tokens=None):
    if topic not in PLANS:
        raise ValueError('Unknown legal dependency topic')
    if type(max_chars) is not int or max_chars < 0:
        raise ValueError('max_chars must be a nonnegative integer')
    if max_source_tokens is not None and (type(max_source_tokens) is not int
            or max_source_tokens < 0 or tokenizer is None):
        raise ValueError('Source-token cap requires a tokenizer and nonnegative integer')
    cache = {}

    def observe(unit):
        if unit not in cache:
            source = laws.get(unit.alias, '')
            found = select_unit(source, unit)
            found['file'] = aliases.get(unit.alias, unit.alias)
            found['source_sha256'] = hashlib.sha256(source.encode('utf8')).hexdigest() if source else None
            found['rendered'] = render_unit(source, found)
            cache[unit] = found
        return cache[unit]

    def cost(units):
        # Count the exact original source once, including whitespace and notes.
        by_alias = {}
        for unit in units:
            by_alias.setdefault(unit.alias, []).extend(observe(unit)['spans'])
        n = 0
        for alias, spans in sorted(by_alias.items()):
            merged = []
            for lo, hi in sorted(spans):
                if merged and lo <= merged[-1][1]:
                    merged[-1][1] = max(hi, merged[-1][1])
                else:
                    merged.append([lo, hi])
            for lo, hi in merged:
                n += len(tokenizer.encode(laws[alias][lo:hi], add_special_tokens=False))
        return n

    def text_for(units):
        return _NOTE + ''.join('\n\n' + observe(unit)['rendered'] for unit in units)

    selected_units, groups = [], []
    for key, units in PLANS[topic]:
        sources = [observe(u) for u in units]
        candidate = list(dict.fromkeys([*selected_units, *units]))
        candidate_text = text_for(candidate)
        missing = any(s['status'] != 'observed_unit' for s in sources)
        candidate_tokens = cost(candidate) if tokenizer is not None and not missing else None
        fits = (not missing and len(candidate_text) <= max_chars
                and (max_source_tokens is None or candidate_tokens <= max_source_tokens))
        status = ('selected' if fits else 'source_or_structure_missing' if missing
                  else 'source_token_budget' if max_source_tokens is not None
                  and candidate_tokens > max_source_tokens else 'character_budget')
        groups.append({'group': key, 'status': status,
                       'units': [{k: v for k, v in s.items() if k != 'rendered'} for s in sources],
                       'candidate_chars': len(candidate_text), 'candidate_source_tokens': candidate_tokens})
        if fits:
            selected_units = candidate
    text = text_for(selected_units) if len(_NOTE) <= max_chars else ''
    return {'version': 'legal_dependency_search_v1', 'topic': topic, 'text': text,
            'max_chars': max_chars, 'used_chars': len(text), 'max_source_tokens': max_source_tokens,
            'source_tokens': cost(selected_units) if tokenizer is not None else None,
            'rendered_tokens': len(tokenizer.encode(text, add_special_tokens=False)) if tokenizer is not None else None,
            'groups': groups, 'specified_dependency_units_covered': all(g['status'] == 'selected' for g in groups),
            'unexpanded_references': list(UNEXPANDED[topic] + _SUBJECT_OUTSIDE),
            'legal_basis_complete': False, 'legal_applicability_determined': False,
            'source_kind': 'supplied_law_only', 'source_structure_repaired': False}
