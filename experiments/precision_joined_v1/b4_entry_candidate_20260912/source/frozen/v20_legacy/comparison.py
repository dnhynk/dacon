"""Typed, source-addressed notice/attachment/registration comparisons.

Only this record is read. A missing or matching field is never a whole-item
negative. Amount bases and document conflicts are retained before comparison;
neither metadata flags nor a province projection alone prove a mismatch.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from .data import clean_evidence
from .temporal import contract_fields, industry_fields, region_clauses, region_set


AMOUNT_FIELDS = {
    '배정예산금액': 'budget', '배정예산': 'budget', '사업예산': 'budget',
    '사업금액': 'budget', '소요예산': 'budget', '예산금액': 'budget', '예산액': 'budget',
    '입찰대상금액': 'budget', '총사업비': 'project_total',
    '기초금액': 'base_price', '입찰추정가격': 'estimated_price', '추정가격': 'estimated_price',
}
META_FIELDS = {'budget': '배정예산금액', 'estimated_price': '입찰추정가격',
               'competition_method': '계약방법', 'region': '제한지역코드목록',
               'industry': '면허업종제한목록'}
_FIELD = re.compile('|'.join(r'\s*'.join(map(re.escape, s))
                            for s in sorted(AMOUNT_FIELDS, key=len, reverse=True)))
_NUMBER = r'(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)'
_WON = re.compile(r'(?<![\d.,])' + _NUMBER + r'(?:\s*[조억만천백십]\s*(?:' + _NUMBER + r')?)*\s*원')
_UNIT = re.compile(r'(?:단\s*위\s*[:：]?\s*|[（(]\s*)(조|억|백만|천|만)?\s*원\s*[)）]?')
_FACT_END = re.compile(r'\n\s*(?:[가-하]|\d{1,2})[.)]\s*|\n\s*\n')
_PARTIAL = re.compile(r'금차|차년도|차분|연차별|연도별|월별|품목별|단가|월액|연간\s*단가|원\s*[/／]\s*(?:년|월|일|개|대|시간)')
_CONDITIONAL = re.compile(r'예시|작성\s*예|가정|경우(?:에)?만|경우에\s*한|(?:이하|이상|미만|초과)\s*(?:인|일|의|경우|사업|용역|물품|대상)|예산\s*범위')
_NEGATED = re.compile(r'아닌|아니라|아니함|아닙니다|아니한다|않|미적용|요구하지|적용하지|제한\s*없|불허|불가')
_VALUE_PREFIX = re.compile(r'[\s:：|=￦₩\\]*(?:(?:은|는|일금|금|총)\s*)?'
                           r'(?:[（(][^()（）\r\n]{0,45}[)）]\s*)?[\s:：|=￦₩\\]*(?:금\s*)?')
_VAT_NO = re.compile(r'(?:부가(?:가치)?세|vat)\s*(?:는\s*)?(?:미포함|불포함|별도|제외)', re.I)
_VAT_YES = re.compile(r'(?:부가(?:가치)?세|vat)\s*(?:는\s*)?포함', re.I)
_UNITS = {'조': Decimal(10**12), '억': Decimal(10**8), '만': Decimal(10**4),
          '천': Decimal(1000), '백': Decimal(100), '십': Decimal(10)}


def _compact(s):
    return re.sub(r'\s+', '', str(s))


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip()
    if not re.fullmatch(_NUMBER, text):
        return None
    try:
        n = Decimal(text.replace(',', ''))
        return n if n.is_finite() and n > 0 else None
    except InvalidOperation:
        return None


def won_value(text):
    """Parse Arabic numerals with Korean place units, using exact arithmetic.

    2천3백만원 is (2*1000 + 3*100)*10000, not 2000 + 3000000.
    No VAT conversion or unit inference is performed here.
    """
    value = _compact(text)
    if not value.endswith('원') or not _WON.fullmatch(value):
        return None
    total = group = Decimal(0)
    pending = None
    last_large, last_small = Decimal('Infinity'), Decimal('Infinity')
    for token in re.findall(_NUMBER + r'|[조억만천백십]', value[:-1]):
        if token not in _UNITS:
            if pending is not None:
                return None
            pending = _number(token)
            if pending is None:
                return None
            continue
        scale = _UNITS[token]
        if scale >= 10000:
            if scale >= last_large:
                return None
            coefficient = group + (pending if pending is not None else 0)
            if coefficient <= 0:
                return None
            total += coefficient * scale
            group, pending, last_large, last_small = Decimal(0), None, scale, Decimal('Infinity')
        else:
            if pending is None or scale >= last_small:
                return None
            group += pending * scale
            pending, last_small = None, scale
    result = total + group + (pending if pending is not None else 0)
    return result if result > 0 else None


def _amount_parts(text):
    """Yield a field and its own value area; never borrow the next field's VAT."""
    matches = list(_FIELD.finditer(text))
    for i, match in enumerate(matches):
        line_start = text.rfind('\n', 0, match.start()) + 1
        line_end = text.find('\n', match.end())
        line_end = len(text) if line_end < 0 else line_end
        end = min(len(text), match.end() + 230,
                  matches[i+1].start() if i+1 < len(matches) else len(text))
        heading = _FACT_END.search(text, match.end(), end)
        if heading:
            end = heading.start()
        # Same-row table headers have no values beside the individual labels.
        # Align explicit pipe columns instead of pairing a label with a later column.
        line = text[line_start:line_end]
        if '|' in line and not _WON.search(line) and line.count('|') >= 2:
            header_cells = list(re.finditer(r'[^|]+', line))
            column = next((j for j, c in enumerate(header_cells)
                           if line_start+c.start() <= match.start() < line_start+c.end()), None)
            if column is not None and column+1 < len(header_cells) and len(list(_FIELD.finditer(line))) == 1:
                value_cell = header_cells[column+1]
                if re.fullmatch(r'\s*' + _NUMBER + r'\s*', value_cell.group()):
                    yield match, value_cell.group(), line_start, line_end, header_cells[column].group(), 'key_value'
                    continue
            global_start = text.rfind('\n', 0, max(0, line_start-1)) + 1
            global_line = text[global_start:line_start].strip()
            global_unit = global_line if re.match(r'^[\s※*(（]*단\s*위\s*[:：]', global_line) and _UNIT.search(global_line) else ''
            rows = list(re.finditer(r'[^\r\n]+', text[line_end:line_end+1500]))
            parsed_rows = []
            for row_match in rows:
                row = row_match.group()
                if re.fullmatch(r'[\s|:\-]+', row):
                    continue
                cells = list(re.finditer(r'[^|]+', row))
                if column is not None and len(cells) == len(header_cells) and '|' in row:
                    c = cells[column]
                    start = line_end + row_match.start() + c.start()
                    stop = line_end + row_match.start() + c.end()
                    parsed_rows.append((text[start:stop], line_end+row_match.end()))
                else:
                    break
            for area, stop in parsed_rows:
                yield match, area, global_start if global_unit else line_start, stop, header_cells[column].group()+' '+global_unit, ('multi_row' if len(parsed_rows)>1 else 'column')
            continue
        area = text[match.end():end]
        previous_on_line = i and matches[i-1].end() > line_start
        header = match.group() if previous_on_line else text[line_start:match.end()]
        yield match, area, match.start() if previous_on_line else line_start, end, header, False


def _scope_context(text, lo, hi):
    """Preserve a governing prefix instead of treating a quoted field as active."""
    line_start = text.rfind('\n', 0, lo) + 1
    lo = line_start
    if lo:
        prev_end = lo - 1
        prev_start = text.rfind('\n', 0, prev_end) + 1
        previous = text[prev_start:prev_end]
        if _CONDITIONAL.search(previous) or _NEGATED.search(previous):
            lo = prev_start
    return lo, hi, text[lo:hi]


def amount_facts(rec):
    facts = []
    for di, doc in enumerate(rec.get('docs', [])):
        text = doc['text']
        for match, area, lo, hi, header, table in _amount_parts(text):
            label = _compact(match.group())
            values = []
            value_tails = []
            for money in _WON.finditer(area):
                prefix = area[:money.start()].strip()
                if prefix.endswith(('-', '−')):
                    continue
                if not _VALUE_PREFIX.fullmatch(prefix):
                    continue
                # A plain field value may have a Korean spelled-out duplicate.
                # Legal thresholds or calculations are not literal field assignments.
                if _CONDITIONAL.search(prefix) or re.search(r'%|산정|계산|곱한|제\s*\d+\s*조', prefix):
                    continue
                value = won_value(money.group())
                if value is not None:
                    values.append(value)
                    tail = area[money.end():]
                    first, *remaining = tail.splitlines() or ['']
                    first = re.split(r'[|;；]|(?:입찰|투찰|견적|계약)\s*(?:금액|가격)\s*(?:[:：]|은|는)',
                                     first, maxsplit=1)[0]
                    # Only an immediately adjacent VAT qualifier can continue
                    # onto the next line; a bidding instruction is another fact.
                    if remaining and re.match(r'^\s*[※*(（]*\s*(?:부가(?:가치)?세|vat)', remaining[0], re.I):
                        first += ' ' + remaining[0]
                    value_tails.append(first)
            if not values:
                unit = _UNIT.search(header)
                numeric = re.fullmatch(r'\s*[:：=|]?\s*(' + _NUMBER + r')\s*', area)
                if unit and numeric:
                    multiplier = won_value('1' + (unit[1] or '') + '원')
                    value = _number(numeric[1])
                    if multiplier is not None and value is not None:
                        values.append(value * multiplier)
            if not values:
                continue
            field = AMOUNT_FIELDS[label]
            lo, hi, scope_context = _scope_context(text, lo, hi)
            context = header + ' ' + area
            scope = ('partial' if _PARTIAL.search(context) else
                     'conditional' if _CONDITIONAL.search(scope_context) or _NEGATED.search(scope_context) else
                     'table_row_unresolved' if table == 'multi_row' else 'whole')
            vat_context = header + ' ' + (' '.join(value_tails) if value_tails else area)
            vat_no, vat_yes = bool(_VAT_NO.search(vat_context)), bool(_VAT_YES.search(vat_context))
            basis = ('unknown' if field == 'estimated_price' and vat_yes else
                     'excluding_vat' if field == 'estimated_price' else
                     'including_vat' if vat_yes and not vat_no else
                     'excluding_vat' if vat_no and not vat_yes else 'unknown')
            # The exact registration field label itself identifies the same budget
            # concept even without a redundant VAT parenthesis.
            if label == '배정예산금액' and not vat_no and not vat_yes:
                basis = 'including_vat'
            while lo < hi and text[lo].isspace():
                lo += 1
            while hi > lo and text[hi-1].isspace():
                hi -= 1
            for value in sorted(set(values)):
                facts.append({'field': field, 'label': label, 'value': str(value),
                              'basis': basis, 'scope': scope, 'doc_index': di,
                              'doc_type': doc['type'], 'start': lo, 'end': hi,
                              'anchor_start': match.start(), 'table_column': bool(table)})
        observed = {f['anchor_start'] for f in facts if f['doc_index'] == di}
        # Parse failure is an observation, not permission to erase this source
        # before comparing a better-parsed occurrence in another document.
        anchors = list(_FIELD.finditer(text))
        for i, anchor in enumerate(anchors):
            if anchor.start() in observed:
                continue
            hi = min(len(text), anchor.end()+230,
                     anchors[i+1].start() if i+1 < len(anchors) else len(text))
            stop = _FACT_END.search(text, anchor.end(), hi)
            if stop:
                hi = stop.start()
            lo, hi, context = _scope_context(text, anchor.start(), hi)
            label = _compact(anchor.group())
            facts.append({'field': AMOUNT_FIELDS[label], 'label': label, 'value': None,
                          'basis': 'unknown', 'scope': 'unparsed', 'doc_index': di,
                          'doc_type': doc['type'], 'start': lo, 'end': hi,
                          'anchor_start': anchor.start(), 'table_column': False})
    # An explicit tender amount can be a component of the wider project budget.
    # Keep both observations; compare registration to the actual tender scope.
    tender_docs = {f['doc_index'] for f in facts if f['label'] == '입찰대상금액' and f['scope'] == 'whole'}
    for fact in facts:
        if fact['field'] == 'project_total':
            fact['scope'] = 'project_total'
        elif (fact['doc_index'] in tender_docs and fact['field'] == 'budget'
              and fact['label'] != '입찰대상금액' and fact['scope'] == 'whole'):
            fact['scope'] = 'project_total'
    return facts


def _source_facts(rec):
    facts = amount_facts(rec)
    # These functions remain source extractors; using attachments does not give
    # them priority over a notice or turn a template into the active clause.
    for key, extractor in [('competition_method', contract_fields),
                           ('region', region_clauses), ('industry', industry_fields)]:
        for item in extractor(rec, doc_types=None):
            di, lo, hi = item['doc_index'], item['start'], item['end']
            text = rec['docs'][di]['text']
            lo, hi, context = _scope_context(text, lo, hi)
            scope = 'conditional' if _CONDITIONAL.search(context) or _NEGATED.search(context) else 'whole'
            if key == 'competition_method':
                # Competing method names can express a correction or a choice.
                methods = set(re.findall(r'일반\s*경쟁|제한\s*경쟁|지명\s*경쟁|수의\s*계약', context))
                if len(methods) > 1 and item['value'] != '수의계약':
                    scope = 'method_relation_unresolved'
            facts.append({'field': key, 'value': item['value'], 'doc_index': di,
                          'doc_type': rec['docs'][di]['type'], 'start': lo, 'end': hi,
                          'scope': scope,
                          'basic_level': item.get('basic_level', False),
                          'alternative': item.get('alternative', False)})
    return facts


def compare(rec):
    """Return all extracted observations and only comparable field conclusions."""
    facts = _source_facts(rec)
    meta = rec.get('meta', {})
    comparisons = []
    for field, meta_key in META_FIELDS.items():
        relevant = [i for i, f in enumerate(facts) if f['field'] == field]
        eligible = [i for i in relevant if facts[i]['scope'] == 'whole' and facts[i]['value'] is not None]
        raw_meta = meta.get(meta_key)
        normalized, status = None, 'unresolved'
        if field in {'budget', 'estimated_price'}:
            basis = 'including_vat' if field == 'budget' else 'excluding_vat'
            eligible = [i for i in eligible if facts[i]['basis'] == basis]
            normalized = _number(raw_meta)
            values = {Decimal(facts[i]['value']) for i in eligible}
            if any(facts[i]['scope'] == 'whole' and facts[i]['basis'] == 'unknown' for i in relevant):
                status = 'basis_unresolved'
            if any(facts[i]['scope'] == 'table_row_unresolved' for i in relevant):
                status = 'row_scope_unresolved'
            if any(facts[i]['scope'] == 'unparsed' for i in relevant):
                status = 'extraction_unresolved'
        elif field == 'competition_method':
            normalized = _compact(raw_meta)
            if normalized not in {'일반경쟁', '제한경쟁', '지명경쟁', '수의계약'}:
                normalized = None
            values = {facts[i]['value'] for i in eligible}
        elif field == 'region':
            names, basic = region_set(str(raw_meta))
            normalized = tuple(sorted(names)) if names else None
            values = {tuple(facts[i]['value']) for i in eligible}
            if basic or any(facts[i].get('basic_level') for i in eligible):
                status = 'hierarchy_unresolved'
        else:
            codes = set(re.findall(r'(?<!\d)\d{4}(?!\d)', str(raw_meta)))
            normalized = next(iter(codes)) if len(codes) == 1 else None
            values = {facts[i]['value'] for i in eligible}
            if len(codes) > 1 or len(values) > 1 or any(facts[i]['alternative'] for i in eligible):
                status = 'and_or_scope_unresolved'
        if status == 'unresolved':
            if normalized is None:
                status = 'metadata_missing_or_unparsed'
            elif not eligible:
                status = 'no_comparable_document_value'
            elif len(values) > 1:
                status = 'documents_conflict'
            elif values:
                value = next(iter(values))
                if isinstance(normalized, Decimal):
                    delta = abs(value - normalized)
                    status = 'same' if delta == 0 else 'rounding_unresolved' if delta <= 1 else 'different'
                else:
                    status = 'same' if value == normalized else 'different'
        # A province projection is useful to inspect, but it does not preserve
        # districts or registration semantics. Never promote it to a rule label.
        comparisons.append({'field': field, 'meta_key': meta_key, 'metadata': raw_meta,
                            'status': status, 'fact_indices': relevant,
                            'comparable_fact_indices': eligible})
    return {'facts': facts, 'comparisons': comparisons,
            'flags': {k: meta.get(k) for k in ('지역제한여부', '업종제한여부')},
            'note': 'Field matches never certify v24=0; flags alone are not value-to-value differences.'}


def priority_ranges(packet):
    """Round-robin fields and documents, with both sides of conflicts retained."""
    by_field = {}
    for fact in packet['facts']:
        by_field.setdefault(fact['field'], []).append((fact['doc_index'], fact['start'], fact['end']))
    result = []
    while any(by_field.values()):
        for ranges in by_field.values():
            if ranges:
                entry = ranges.pop(0)
                if entry not in result:
                    result.append(entry)
    return result


def prompt_packet(packet, spans, *, rec):
    """Only draw a comparison conclusion when every relevant source is visible."""
    compact = []
    for comparison in packet['comparisons']:
        observations, all_shown = [], True
        for index in comparison['fact_indices']:
            fact = packet['facts'][index]
            refs = [n for n, span in enumerate(spans, 1)
                    if span.doc_index == fact['doc_index'] and span.start < fact['end'] and span.end > fact['start']]
            covered = sorted((max(span.start, fact['start']), min(span.end, fact['end']))
                             for span in spans if span.doc_index == fact['doc_index']
                             and span.start < fact['end'] and span.end > fact['start'])
            # Adjacent whitespace is checked by retrieval; source coordinates
            # still distinguish an absent comparison from a matched value.
            text = rec['docs'][fact['doc_index']]['text']
            shown = bool(covered)
            if shown:
                gaps = [(fact['start'], covered[0][0]), (covered[-1][1], fact['end'])]
                gaps += [(b, c) for (_, b), (c, _) in zip(covered, covered[1:])]
                shown = all(b <= a or not text[a:b].strip() for a, b in gaps)
            all_shown &= shown
            if len(observations) < 6:
                observations.append({k: v for k, v in fact.items()
                                     if k in {'value', 'basis', 'scope', 'doc_type', 'basic_level', 'alternative'}}
                                    | {'S': refs if shown else [], 'source_shown': shown})
        state = comparison['status'] if all_shown and len(comparison['fact_indices']) <= 6 else 'source_omitted'
        compact.append({'field': comparison['meta_key'], 'comparison': state,
                        'observed': observations, 'omitted_observations': max(0, len(comparison['fact_indices']) - 6)})
    return {'fields': compact, 'instruction':
            '같은 의미·범위·부가세 기준의 값만 대조한다. 기초금액≠배정예산, 추정가격≠부가세포함예산, '
            '낙찰방법≠경쟁방식이다. 지역/업종 플래그 N만으로 원문 자격과의 불일치를 확정하지 않는다. '
            '같음은 해당 필드만의 관측이며 v24 전체 정상이 아니다. 미추출·생략은 불일치도 일치도 아니다. '
            '첨부와 공고가 충돌하면 양쪽 원문과 적용범위를 확인한다. e에는 직접 관련된 S번호를 쓴다.'}


def positive_decision(rec, packet):
    for comparison in packet['comparisons']:
        if comparison['status'] != 'different' or comparison['field'] not in {'budget', 'competition_method', 'industry'}:
            continue
        for index in comparison['comparable_fact_indices']:
            fact = packet['facts'][index]
            if fact['doc_type'] != '공고문':
                continue  # Attachment scope/version needs the model's full-context review.
            source = (fact['doc_index'], fact['start'], fact['end'])
            text = rec['docs'][source[0]]['text'][source[1]:source[2]]
            if len(text) > 500:
                continue  # Never cut away a value, table header or VAT qualifier.
            evidence = clean_evidence(text, rec, source=source)
            if evidence:
                return {'item': 24, 'value': 1, 'evidence': evidence,
                        'reason': 'same_semantic_field_difference', 'comparison': comparison}
    return None
