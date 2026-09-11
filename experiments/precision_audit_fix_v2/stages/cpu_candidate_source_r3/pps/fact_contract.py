"""Typed, source-bound facts; free prose is never a promotion interface."""
from __future__ import annotations
import re

PRODUCT = '실제구매대상_경쟁제품_고시조건'
SOFTWARE = '실제SW사업_하한제도기재'
COMPARISON = '본문과메타의동일필드차이'
MODEL = '특정제품의_신규납품_대체허용'
STATES = {PRODUCT: ('competition', 'general', 'unknown', 'conflict'),
          SOFTWARE: ('sw', 'non_sw', 'unknown', 'conflict'),
          COMPARISON: ('different', 'same', 'unknown', 'conflict'),
          MODEL: ('new_specific', 'existing', 'equivalent', 'generic', 'unknown', 'conflict')}
FIELDS = ('budget', 'estimated_price', 'competition_method', 'region', 'industry', 'unknown')


def schema(name, max_evidence=None):
    ref = {'type': 'integer', 'minimum': 0} if max_evidence is None else {
        'type': 'integer', 'enum': list(range(max_evidence + 1))}
    props = {'state': {'type': 'string', 'enum': list(STATES[name])},
             'scope': {'type': 'string', 'enum': ['whole', 'component', 'unknown']},
             'e': ref, 'quote': {'type': 'string', 'maxLength': 180}}
    if name == COMPARISON:
        props['field'] = {'type': 'string', 'enum': list(FIELDS)}
    return {'type': 'object', 'additionalProperties': False,
            'required': list(props), 'properties': props}


def validated(value, name, rec, spans):
    keys = {'state', 'scope', 'e', 'quote'} | ({'field'} if name == COMPARISON else set())
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError('Typed fact has invalid keys')
    if value['state'] not in STATES[name] or value['scope'] not in {'whole', 'component', 'unknown'}:
        raise ValueError('Typed fact has an invalid state or scope')
    if name == COMPARISON and value['field'] not in FIELDS:
        raise ValueError('Typed comparison has an invalid field')
    ref, quote = value['e'], value['quote']
    if type(ref) is not int or not 0 <= ref <= len(spans) or not isinstance(quote, str) or len(quote) > 180:
        raise ValueError('Typed fact has an invalid source reference')
    unknown = value['state'] in {'unknown', 'conflict'}
    if not ref:
        if not unknown or quote:
            raise ValueError('A resolved fact requires an exact source quote')
        return {**value, 'source': None}
    selected = spans[ref - 1]
    if not quote.strip() or quote not in selected.text:
        raise ValueError('Typed fact quote is not in its selected source span')
    if rec is not None:
        raw = rec['docs'][selected.doc_index]['text'][selected.start:selected.end]
        if quote not in raw:
            raise ValueError('Typed fact quote is not in the original document')
    # Anchor against the original range, even if a caller supplied a normalized
    # Span.text. Offsets always address the actual document.
    offset = raw.index(quote) if rec is not None else selected.text.index(quote)
    return {**value, 'source': {'doc_index': selected.doc_index,
                              'start': selected.start + offset, 'end': selected.start + offset + len(quote),
                              'text': quote}}


def validate_facts(facts, names, rec, spans, *, require_typed=False):
    if not isinstance(facts, dict) or set(facts) != set(names):
        raise ValueError('Invalid fact fields')
    for name, value in facts.items():
        if name in STATES and (require_typed or isinstance(value, dict)):
            validated(value, name, rec, spans)
        elif not isinstance(value, str) or not 1 <= len(value) <= 220:
            raise ValueError('Invalid descriptive fact')


def general_purchase(value, rec, spans):
    # Old response prose can still be replayed as a model judgment, but cannot
    # be converted into a categorical product fact by substring matching.
    if not isinstance(value, dict):
        return None
    try:
        claim = validated(value, PRODUCT, rec, spans)
    except ValueError:
        return None
    if claim['state'] != 'general' or claim['scope'] != 'whole' or not claim['source']:
        return None
    text = claim['quote']
    # A possession requirement or catalog citation is not purchase identity.
    if re.search(r'직접\s*생산\s*확인|입찰\s*참가\s*자격|확인서|참고용|예시', text):
        return None
    if not re.search(r'용역|과업|구매|구입|납품|연구|조사|제작|운영|운송|경비', text):
        return None
    return claim


def validate_judgments(facts, items, values, rec, spans):
    chosen = dict(zip(items, values))
    if chosen.get(9) == 1:
        claim = validated(facts[MODEL], MODEL, rec, spans)
        from .model_constraints import supports_new_specific_quote
        if (claim['state'] != 'new_specific' or claim['scope'] not in {'whole', 'component'}
                or not claim['source'] or not supports_new_specific_quote(rec, claim['source'])):
            raise ValueError('Positive v9 lacks a source-bound new specific product requirement')
    if chosen.get(20) == 1:
        claim = validated(facts[SOFTWARE], SOFTWARE, rec, spans)
        if claim['state'] != 'sw' or claim['scope'] not in {'whole', 'component'}:
            raise ValueError('Positive v20 contradicts unresolved or non-SW applicability')
        from .other_checks import supports_sw_quote
        if not supports_sw_quote(rec, claim['source']):
            raise ValueError('Positive v20 cites a subject/tool rather than an actual software obligation')
    if chosen.get(10) == 1:
        claim = validated(facts[PRODUCT], PRODUCT, rec, spans)
        if (claim['state'] != 'competition' or claim['scope'] not in {'whole', 'component'}
                or re.search(r'직접\s*생산\s*확인|입찰\s*참가\s*자격', claim['quote'])):
            raise ValueError('Positive v10 lacks an identified competitive purchase')
    if chosen.get(24) == 1:
        claim = validated(facts[COMPARISON], COMPARISON, rec, spans)
        if claim['state'] != 'different' or claim['field'] == 'unknown' or claim['scope'] != 'whole':
            raise ValueError('Positive v24 lacks a same-scope field difference')
        if rec is not None:
            from .comparison import compare
            packet = compare(rec)
            compared = next(x for x in packet['comparisons'] if x['field'] == claim['field'])
            if compared['status'] in {'same', 'rounding_unresolved', 'metadata_missing_or_unparsed', 'hierarchy_unresolved', 'and_or_scope_unresolved'}:
                raise ValueError('Positive v24 contradicts the supplied comparison scope or values')
            source = claim['source']
            matches = [packet['facts'][i] for i in compared['comparable_fact_indices']
                       if packet['facts'][i]['doc_index'] == source['doc_index']
                       and packet['facts'][i]['start'] < source['end'] and source['start'] < packet['facts'][i]['end']]
            if not matches:
                raise ValueError('Positive v24 quote does not support the claimed field and whole-project value')


def instructions(names):
    active = [name for name in names if name in STATES]
    if not active:
        return ''
    lines = ['다음 facts 값은 자유서술 대신 지정된 객체로 쓴다. state와 원문 근거를 분리한다.']
    for name in active:
        fields = '{state,scope,e,quote,field}' if name == COMPARISON else '{state,scope,e,quote}'
        lines.append(name + ': ' + fields + '; state=' + '/'.join(STATES[name]) + '.')
    lines.append('scope=whole/component/unknown. e는 해당 사실의 원문 S번호, quote는 그 S구간 안의 연속 원문 180자 이하다. '
                 '확정 근거가 없으면 state=unknown,scope=unknown,e=0,quote="". '
                 '제품 근거는 실제 과업이며 요구한 증명서 품목이 아니다. SW 근거는 실제 납품·개발·운영 의무이며 주제·도구가 아니다.')
    if COMPARISON in active:
        lines.append('field=budget/estimated_price/competition_method/region/industry/unknown. 등록목록과 본문을 같은 의미로 대조한다. '
                     'N 플래그나 null만으로 state=different라고 쓰지 않는다.')
    if MODEL in active:
        lines.append('신규납품의 특정 제품 요구만 new_specific이다. 기존 장비/라이선스 갱신=existing, '
                     '해당 본체의 대체가 허용되면 equivalent, 숫자 성능/장비 종류만 있으면 generic이다. '
                     '실제 모델 원문을 인용한다. 동등 허용의 대상이 불명확하면 unknown이다.')
    return '\n'.join(lines) + '\n'
