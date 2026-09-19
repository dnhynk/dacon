"""Typed, source-addressed notice/attachment/registration comparisons.

Only this record is read. A missing or matching field is never a whole-item
negative. Amount bases and document conflicts are retained before comparison;
neither metadata flags nor a province projection alone prove a mismatch.
"""
from __future__ import annotations

import re
from decimal import Decimal

from .data import clean_evidence
from .temporal import contract_fields, industry_fields, region_clauses, region_set, REGION_RE
from .amounts import NUMBER as _NUMBER, FIELD_WON as _WON, positive_number as _number, won_value


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
_FIELD_ALIAS = re.compile(r'(?P<first>'+_FIELD.pattern+r')[ \t]*(?P<open>[(（])[ \t]*'
                          r'(?P<second>'+_FIELD.pattern+r')[ \t]*(?P<close>[)）])')
# An explicitly named new duty owns its predicates. Continuations such as
# "위 금액은 ..." stay with the amount, including exemptions and unit prices.
_OTHER_AMOUNT_DUTY = re.compile(
    r'^[ \t]*(?:(?:[※○◦●□■◇◆◎▶▷•①-⑳➀-➉-]|[가-하\d]{1,3}[.)])[ \t]*)?(?:[|][ \t]*)?'
    r'(?:'+'|'.join(r'[ \t]*'.join(map(re.escape, key)) for key in (
        '입찰보증금','계약보증금','계약방법','입찰방법','입찰방식','사업기간','과업기간','계약기간',
        '용역기간','납품기한','납품장소','인도조건','공동계약','공동수급','하도급','사업부서',
        '사업담당공무원','입찰참가자격','제안서제출'))+r')(?=[\s:：|은는이가])', re.M)
_UNIT = re.compile(r'(?:단\s*위\s*[:：]?\s*|[（(]\s*)(조|억|백만|천|만)?\s*원\s*[)）]?')
_FACT_END = re.compile(r'\n\s*(?:[가-하]|\d{1,2})[.)]\s*|\n\s*\n')
_PARTIAL = re.compile(r'금차|차년도|차분|연차별|연도별|월별|품목별|단가|월액|연간\s*단가|'
    r'(?:연간|평균)\s*(?=(?:사업|계약|용역)?\s*(?:예산|금액)|추정\s*가격)|'
    r'(?:제\s*)?\d+\s*(?:차(?:년도|분)?|단계)\s*(?=(?:사업|계약|용역)?\s*(?:예산|금액)|추정\s*가격)|'
    r'원\s*[/／]\s*(?:년|월|일|개|대|시간)')
_AMOUNT_WITHDRAWN = re.compile(r'(?:금액|예산(?:액)?|사업비)(?:은|는|을|를)?\s*(?:삭제|철회|폐기|취소|정정)')
_CONDITIONAL = re.compile(r'예시|작성\s*예|가정|경우(?:에)?만|경우에\s*한|(?:이하|이상|미만|초과)\s*(?:인|일|의|경우|사업|용역|물품|대상)|예산\s*범위')
_NEGATED = re.compile(r'아닌|아님|아니(?:다|라|며|고|었|함|한다)|아닙니다|않|미적용|요구하지|적용하지|제한\s*없|불허|불가')
_VALUE_PREFIX = re.compile(r'[\s:：|=￦₩\\]*(?:(?:은|는|일금|금|총|전체)\s*)?'
                           r'(?:[（(][^()（）\r\n]{0,45}[)）]\s*)?[\s:：|=￦₩\\]*(?:금\s*)?')
_MONEY_START = re.compile(r'\s*(?:일금|금)?\s*[-−+]?\s*(?:\d|[일이삼사오육칠팔구영공조억만천백십]'
                          r'(?:\s*[일이삼사오육칠팔구영공조억만천백십])*\s*원)')
_UNIT_ANNOTATION = re.compile(r'\s*(?:조|억|백만|천|만)?\s*원\s*(?=[,，/／)）]|$)')
_VAT_EXPENSES = r'(?:\s*(?:및|[·ㆍ,])\s*(?:대행수수료|수수료|이윤|제경비|보험료|운송비|설치비))*'
_VAT_NO = re.compile(r'(?:부가(?:가치)?세|vat)'+_VAT_EXPENSES+r'\s*(?:는\s*)?(?:미포함|불포함|별도|제외)', re.I)
_VAT_YES = re.compile(r'(?:부가(?:가치)?세|vat)'+_VAT_EXPENSES+r'\s*(?:는\s*)?포함', re.I)
_VAT_EXEMPT = re.compile(r'(?:부가(?:가치)?세|vat)?\s*(?:는\s*)?(?:면세|비과세)', re.I)
# Explicit key/value typography supplies an ownership boundary even when the
# caption was not anticipated. Restrict caption syntax so colons in document
# tokens, prose quotations and URLs cannot invent a new field.
_EXPLICIT_FIELD = re.compile(
    r'(?:^|(?<=[|;；]))[ \t]*(?:(?:[※○◦●□■◇◆◎▶▷•①-⑳➀-➉-]|[가-하\d]{1,3}[.)])[ \t]*)?'
    r'(?:[|][ \t]*)?(?P<caption>[가-힣A-Za-z][가-힣A-Za-z0-9·ㆍ_/()\- \t]{0,48}?)[ \t]*[:：|]', re.M)
_AMOUNT_CONTINUATION_CAPTION = re.compile(
    r'^(?:(?:위|상기|해당|본|이|그)(?:의)?(?:금액|가격|예산|사업비)|'
    r'(?:비고|주|주석|유의사항|주의사항|참고사항|참고|예시|작성예|작성예시|가정|조건|'
    r'적용조건|산출조건|금액조건|금액기준|예산조건|산출기준|산출내역|단위|'
    r'부가세|부가가치세|세금|vat)$)', re.I)


def _independent_field_caption(caption):
    name = _compact(caption)
    return bool(name and not _AMOUNT_CONTINUATION_CAPTION.search(name)
                and not _CONDITIONAL.search(name) and not _NEGATED.search(name))


def _next_amount_boundary(text, start, end):
    candidates = {m.start() for m in _OTHER_AMOUNT_DUTY.finditer(text, start, end)}
    candidates.update(m.start() for m in _EXPLICIT_FIELD.finditer(text, start, end)
                      if _independent_field_caption(m['caption']))
    # Caption syntax inside the current value's parentheses/quotation remains
    # part of that value. An unclosed group is uncertainty, not permission to
    # discard its later exception or negation.
    pairs = {'(':')','（':'）','[':']','【':'】','「':'」','『':'』','“':'”','‘':'’','"':'"',"'":"'"}
    stack, cursor = [], start
    for position in sorted(candidates):
        for ch in text[cursor:position]:
            if stack and ch == stack[-1]:
                stack.pop()
            elif ch in pairs:
                stack.append(pairs[ch])
        cursor = position
        if not stack:
            return position
    return end


def _compact(s):
    return re.sub(r'\s+', '', str(s))


def _literal_prefix(prefix):
    if not _VALUE_PREFIX.fullmatch(prefix):
        return False
    # A currency literal in parentheses is a value, not an arbitrary qualifier
    # that can be skipped to borrow a later amount.
    return not any(_MONEY_START.match(m[1]) and '원' in m[1] and not _UNIT_ANNOTATION.match(m[1])
                   for m in re.finditer(r'[(（]([^()（）]*)[)）]', prefix))


def _amount_value_view(area):
    """Unwrap a literal value in place; preserve source offsets and ambiguity.

    A VAT/role annotation before a value remains an annotation. A numeric
    wrapper must close with matching delimiters; broken layout is never repaired
    by trusting a partial match inside it. This view is not an evidence quote.
    """
    for opening in re.finditer(r'[(（]', area):
        at = opening.start()
        inner = area[at+1:].lstrip()
        while inner.startswith(('(', '（')):
            inner = inner[1:].lstrip()
        if not (_literal_prefix(area[:at].strip()) and _MONEY_START.match(inner)
                and not _UNIT_ANNOTATION.match(inner)):
            continue
        stack, closing = [], None
        pairs = {'(': ')', '（': '）'}
        for pos in range(at, len(area)):
            ch = area[pos]
            if ch in pairs:
                stack.append(pairs[ch])
            elif ch in ')）':
                if not stack or stack.pop() != ch:
                    return area, True
                if not stack:
                    closing = pos
                    break
        if closing is None:
            return area, True
        # Two unlabelled scalar values cannot be made unambiguous by treating
        # the first one as a parenthetical comment (or by dropping the second).
        tail = area[closing+1:]
        if any(_literal_prefix(tail[:m.start()].strip()) for m in _WON.finditer(tail)):
            return area, True
        view = area[:at]+' '+area[at+1:closing]+' '+area[closing+1:]
        return _amount_value_view(view)
    return area, False


def _inline_currency_value(line):
    for money in _WON.finditer(line):
        # A parenthesized table unit is not the row's numeric value. A spelled
        # amount such as (오천만원), or a literal 천원 after a colon, remains one.
        if (line[:money.start()].rstrip().endswith(('(', '（'))
                and _UNIT_ANNOTATION.fullmatch(money[0].strip())):
            continue
        return True
    return False


def _amount_parts(text):
    """Yield a field and its own value area; never borrow the next field's VAT."""
    matches = list(_FIELD.finditer(text))
    aliases = {}
    for alias in _FIELD_ALIAS.finditer(text):
        if {'(':')','（':'）'}[alias['open']] == alias['close'] and '\n' not in alias[0]:
            for field in ('first','second'):
                aliases[alias.start(field)] = alias
    for i, match in enumerate(matches):
        alias = aliases.get(match.start())
        value_start = alias.end() if alias else match.end()
        line_start = text.rfind('\n', 0, match.start()) + 1
        line_end = text.find('\n', match.end())
        line_end = len(text) if line_end < 0 else line_end
        next_field = next((m.start() for m in matches[i+1:] if m.start() >= value_start),len(text))
        end = min(len(text), value_start + 230, next_field)
        heading = _FACT_END.search(text, value_start, end)
        if heading:
            end = heading.start()
        end = _next_amount_boundary(text, value_start, end)
        # Same-row table headers have no values beside the individual labels.
        # Align explicit pipe columns instead of pairing a label with a later column.
        line = text[line_start:line_end]
        if '|' in line and not _inline_currency_value(line) and line.count('|') >= 2:
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
        area = text[value_start:end]
        previous_on_line = i and matches[i-1].end() > line_start
        header = text[line_start:value_start] if alias else match.group() if previous_on_line else text[line_start:match.end()]
        # A damaged compound label cannot promote its interior fragment into
        # an independent assignment while its closing parenthesis is missing.
        if alias is None and previous_on_line and re.fullmatch(r'[ \t]*[(（][ \t]*',text[matches[i-1].end():match.start()]):
            fragment = text[matches[i-1].start():end]
            if fragment.count('(')>fragment.count(')') or fragment.count('（')>fragment.count('）'):
                area = ''
        yield match, area, line_start if alias or not previous_on_line else match.start(), end, header, False


_COMPARISON_SUBJECTS = {
    'region': re.compile(r'지역|본점|본사|영업소|소재지|주소지'),
    'competition_method': re.compile(r'계약(?:방법|방식)|입찰(?:방법|방식)|일반경쟁|제한경쟁|지명경쟁|수의계약'),
}
_DUTY_PREFIX = re.compile(
    r'^(?:(?:본|이|해당|금번)(?:공고|입찰|계약|사업|용역|구매)(?:은|는|의|에서는)?(?:의)?)?'
    r'(?:공동수급|공동도급|하도급|지사투찰|입찰보증금|계약보증금|제안서제출|납품기한|대금지급)')
_ITEM_PREFIX = re.compile(r'^[ \t]*(?:(?:[※○◦●□■◇◆◎▶▷•①-⑳➀-➉-]|[가-하\d]{1,3}[.)])[ \t]*)?')
_FIELD_REFERENCE = re.compile(r'아래|다음|상기|위(?:조건|요건|제한)|해당(?:조건|요건|제한)|그(?:조건|요건|제한)')


def _independent_duty_sentence(sentence, field):
    if field not in _COMPARISON_SUBJECTS:
        return False
    plain = _compact(_ITEM_PREFIX.sub('', sentence.strip()))
    return not (_COMPARISON_SUBJECTS[field].search(plain) or _FIELD_REFERENCE.search(plain)
            or _CONDITIONAL.search(plain) or not _DUTY_PREFIX.match(plain)
            or not re.search(r'(?:[.。]|다|니다|함|불가|불허|없음|않음|아님)$', plain))


def _independent_previous_duty(text, previous_start, line_start, field):
    """Discard only a completed, explicitly different duty's local polarity."""
    if not _independent_duty_sentence(text[previous_start:line_start], field):
        return False
    # Reuse the original-source quotation/example ownership guard, not its
    # governing-law interpretation. A local duty cannot exit an outer example.
    from .law_declarations import _reference_reason
    return _reference_reason(text, line_start) is None


def _scope_context(text, lo, hi, *, amount=False, field=None):
    """Preserve a governing prefix instead of treating a quoted field as active."""
    line_start = text.rfind('\n', 0, lo) + 1
    lo = line_start
    if lo:
        prev_end = lo - 1
        prev_start = text.rfind('\n', 0, prev_end) + 1
        previous = text[prev_start:prev_end]
        explicit = _EXPLICIT_FIELD.match(previous) if amount else None
        other_field = bool(explicit and _independent_field_caption(explicit['caption']))
        if ((_CONDITIONAL.search(previous) or _NEGATED.search(previous))
                and not (amount and (_OTHER_AMOUNT_DUTY.match(previous) or other_field))
                and not (not amount and _independent_previous_duty(text, prev_start, lo, field))):
            lo = prev_start
    return lo, hi, text[lo:hi]


_REGION_CONTINUATION = re.compile(
    r'^[ \t]*(?:(?:로|으로)[ \t]*)?(?:제한하지|한정하지|제한되는|한정되는|이어야|여야|이여야|'
    r'(?:다만[ \t]*)?(?:위|상기|해당|이|그)[ \t]*(?:지역[ \t]*제한|소재지[ \t]*요건|조건|요건|제한))')
_OTHER_PRODUCT_PREDICATE = re.compile(
    r'^(?:(?:\[[^\]\r\n]+\]|[가-힣]{2,12})(?:에서|이|가)(?:제시|요구|정)하는)?'
    r'(?:납품)?(?:물품|제품)(?:의|은|는|을|과|도|번호)')


def _region_local_view(view):
    """Separate explicit other duties in the semantic view, keeping source intact."""
    def parenthesis(match):
        if {'(': ')', '（': '）'}[match[1]] != match[3]:
            return match[0]
        parts = re.split(r'[,;；]|및', match[2])
        if parts and all(_independent_duty_sentence(part, 'region') for part in parts):
            return ' '
        return match[0]
    view = re.sub(r'([(（])([^()（）\r\n]+)([)）])', parenthesis, view)
    # "... 소재한 업체로서 납품 물품의 규격은 ..." adds a product
    # requirement. Its performance threshold does not qualify the office's
    # location. References, examples and repeated office subjects remain bound.
    for join in re.finditer(r'(?:업체|자)\s*로서\s*', view):
        following = _compact(view[join.end():])
        if (_OTHER_PRODUCT_PREDICATE.match(following)
                and not _COMPARISON_SUBJECTS['region'].search(following)
                and not _FIELD_REFERENCE.search(following)
                and not re.search(r'예시|작성예|가정', following)):
            return view[:join.end()]
    return view


def _region_predicate_context(text, start, end, lo):
    """Keep the office predicate after '업체', including wrapped withdrawal."""
    from .assertions import assertion_scope
    def sentence_end(at):
        line_end = text.find('\n', at)
        line_end = len(text) if line_end < 0 else line_end
        stop = re.search(r'[.。;；](?=\s|$)', text[at:line_end])
        return at + stop.end() if stop else line_end

    def related_start(at):
        gap = re.match(r'[ \t]*(?:\r?\n[ \t]*)?', text[at:])
        following = at + gap.end()
        return following if following < len(text) and _REGION_CONTINUATION.match(text[following:]) else None

    hi = sentence_end(end)
    for _ in range(4):
        following = related_start(hi)
        if following is None:
            break
        hi = sentence_end(following)
    incomplete = related_start(hi) is not None
    # An independent subject after a connective owns its own polarity; the
    # evidence interval remains the unchanged original text, never this view.
    view = _region_local_view(assertion_scope(text, start, end, 'region', bounds=(lo, hi)))
    return hi, view, incomplete


def amount_facts(rec):
    facts = []
    for di, doc in enumerate(rec.get('docs', [])):
        text = doc['text']
        unreadable_assignments = set()
        for match, area, lo, hi, header, table in _amount_parts(text):
            label = _compact(match.group())
            values = []
            value_tails = []
            bounded_values = {}
            value_area, wrapper_error = _amount_value_view(area)
            owned_literal = False
            for money in _WON.finditer(value_area):
                prefix = value_area[:money.start()].strip()
                if prefix.endswith(('-', '−')):
                    continue
                if wrapper_error or not _literal_prefix(prefix):
                    continue
                # A plain field value may have a Korean spelled-out duplicate.
                # Legal thresholds or calculations are not literal field assignments.
                if _CONDITIONAL.search(prefix) or re.search(r'%|산정|계산|곱한|제\s*\d+\s*조', prefix):
                    continue
                owned_literal = True
                value = won_value(money.group())
                if value is not None:
                    values.append(value)
                    tail = value_area[money.end():]
                    bound = re.match(r'\s*(미만|이하|이상|초과|내외|정도|한도)', tail)
                    if bound:
                        bounded_values[value] = bound[1]
                    first, *remaining = tail.splitlines() or ['']
                    first = re.split(r'[|;；]|(?:입찰|투찰|견적|계약)\s*(?:금액|가격)\s*(?:[:：]|은|는)',
                                     first, maxsplit=1)[0]
                    # Only an immediately adjacent VAT qualifier can continue
                    # onto the next line; a bidding instruction is another fact.
                    if remaining and re.match(r'^\s*[※*(（]*\s*(?:부가(?:가치)?세|vat)', remaining[0], re.I):
                        first += ' ' + remaining[0]
                    value_tails.append(money.group() + ' ' + first)
            if not values and not wrapper_error:
                unit = _UNIT.search(header)
                numeric = re.fullmatch(r'\s*[:：=|]?\s*(' + _NUMBER + r')\s*', area)
                if unit and numeric:
                    multiplier = won_value('1' + (unit[1] or '') + '원')
                    value = _number(numeric[1])
                    if multiplier is not None and value is not None:
                        values.append(value * multiplier)
            if not values:
                # Generic references are not assignments. An actual numeric
                # currency literal, however, cannot disappear into metadata
                # merely because its syntax or duplicate failed validation.
                literal_start = any(_literal_prefix(value_area[:m.start()].strip())
                                    and '원' in value_area[m.start():]
                                    for m in _MONEY_START.finditer(value_area))
                _, _, assertion = _scope_context(text, lo, hi, amount=True)
                context = header + ' ' + area
                if ((wrapper_error or owned_literal or literal_start)
                        and not (_PARTIAL.search(context) or _CONDITIONAL.search(assertion)
                                 or _NEGATED.search(assertion) or _AMOUNT_WITHDRAWN.search(context)
                                 or re.search(r'(?:원|[)）])\s*(?:미만|이하|이상|초과|내외|정도|한도)', area))
                        and table != 'multi_row'):
                    unreadable_assignments.add(match.start())
                continue
            field = AMOUNT_FIELDS[label]
            lo, hi, scope_context = _scope_context(text, lo, hi, amount=True)
            context = header + ' ' + area
            scope = ('partial' if _PARTIAL.search(context) else
                     'bounded' if bounded_values else
                     'conditional' if (_CONDITIONAL.search(scope_context) or _NEGATED.search(scope_context)
                                       or _AMOUNT_WITHDRAWN.search(context)) else
                     'table_row_unresolved' if table == 'multi_row' else 'whole')
            vat_context = header + ' ' + (' '.join(value_tails) if value_tails else area)
            vat_no, vat_yes = bool(_VAT_NO.search(vat_context)), bool(_VAT_YES.search(vat_context))
            vat_exempt = bool(_VAT_EXEMPT.search(vat_context))
            basis = ('tax_exempt' if vat_exempt and not vat_no and not vat_yes else
                     'unknown' if field == 'estimated_price' and vat_yes else
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
                              'value_relation': bounded_values.get(value, 'exact'),
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
            hi = _next_amount_boundary(text, anchor.end(), hi)
            lo, hi, context = _scope_context(text, anchor.start(), hi, amount=True)
            label = _compact(anchor.group())
            facts.append({'field': AMOUNT_FIELDS[label], 'label': label, 'value': None,
                          'basis': 'unknown', 'scope': 'unparsed', 'doc_index': di,
                          'doc_type': doc['type'], 'start': lo, 'end': hi,
                          'anchor_start': anchor.start(), 'table_column': False})
            if anchor.start() in unreadable_assignments:
                facts[-1]['literal_error'] = 'unreadable_monetary_assignment'
            else:
                from .amount_usage import nonassignment
                use = nonassignment(text, anchor.start(), anchor.end())
                if use is not None:
                    facts[-1].update(scope='nonassignment', amount_use=use['kind'],
                        start=use['start'], end=use['end'])
    # An explicit tender amount can be a component of the wider project budget.
    # Keep both observations; compare registration to the actual tender scope.
    tender_docs = {f['doc_index'] for f in facts if f['label'] == '입찰대상금액' and f['scope'] == 'whole'}
    for fact in facts:
        if fact['field'] == 'project_total':
            fact['scope'] = 'project_total'
        elif (fact['doc_index'] in tender_docs and fact['field'] == 'budget'
              and fact['label'] != '입찰대상금액'
              and (fact['scope'] == 'whole' or fact.get('literal_error'))):
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
            context_incomplete = False
            if key == 'industry':
                # The source extractor already bound the registration predicate
                # to this code. Expanding again can borrow an adjacent SME OR
                # or an unrelated debarment negation.
                context = item['predicate_scope']
            else:
                lo, hi, context = _scope_context(text, lo, hi, field=key)
                if key == 'region':
                    hi, context, context_incomplete = _region_predicate_context(
                        text, item['start'], item['end'], lo)
            scope = 'conditional' if _CONDITIONAL.search(context) or _NEGATED.search(context) else 'whole'
            if item.get('assertion_scope_unresolved') or context_incomplete:
                scope = 'assertion_unresolved'
            if key == 'competition_method':
                # Competing method names can express a correction or a choice.
                methods = set(re.findall(r'일반\s*경쟁|제한\s*경쟁|지명\s*경쟁|수의\s*계약', context))
                if len(methods) > 1 and item['value'] != '수의계약':
                    scope = 'method_relation_unresolved'
            facts.append({'field': key, 'value': item['value'], 'doc_index': di,
                          'doc_type': rec['docs'][di]['type'], 'start': lo, 'end': hi,
                          'scope': scope,
                          **({'source_context_truncated': True} if context_incomplete else {}),
                          'basic_level': item.get('basic_level', False),
                          **({'anonymous_region_scope_unresolved':True,
                              'unresolved_region_tokens':item['unresolved_region_tokens']}
                             if item.get('anonymous_region_scope_unresolved') else {}),
                          'alternative': item.get('alternative', False)})
    return facts


def _explicit_province_branches(text):
    """Read an explicit OR place expression in the same bidder-office predicate.

    A province may be narrowed by an original district token. Its exact district
    identity remains unknown, but a different named province is still outside a
    registration that permits only the first province.
    """
    place = '(?:' + REGION_RE.pattern + r')(?:\s*\[지역:[^\]\r\n]+\])?'
    expression = '(?P<places>' + place + r'(?:\s*또는\s*' + place + ')+)'
    pattern = expression + r'\s*(?:지역)?\s*(?:에|내에?)\s*(?:둔|있는|소재한|소재하고)[^\r\n]{0,25}?업체'
    matches = list(re.finditer(pattern, text))
    if len(matches) != 1:
        return None
    branches = []
    for branch in re.split(r'\s*또는\s*', matches[0]['places']):
        names, _ = region_set(branch)
        if len(names) != 1:
            return None  # An inconsistent district/parent token is unresolved.
        branches.append(next(iter(names)))
    return set(branches)


def _unit_price_estimate_fact(rec, fact):
    """Limit a unit/total distinction to the v24 metadata comparison."""
    if fact.get('field') != 'estimated_price' or fact.get('value') is None:
        return False
    text = rec['docs'][fact['doc_index']]['text']
    if not re.search(r'단가\s*계약[^\n]{0,100}기초\s*금액[^\n]{0,80}투찰', text):
        return False
    source = text[fact['start']:fact['end']]
    return bool(re.search(r'기\s*초\s*금\s*액', source)
                and re.search(r'추\s*정\s*가\s*격', source))


def compare(rec):
    """Return all extracted observations and only comparable field conclusions."""
    facts = _source_facts(rec)
    meta = rec.get('meta', {})
    comparisons = []
    for field, meta_key in META_FIELDS.items():
        relevant = [i for i, f in enumerate(facts) if f['field'] == field]
        eligible = [i for i in relevant if facts[i]['scope'] == 'whole' and facts[i]['value'] is not None]
        unit_price_indices = []
        raw_meta = meta.get(meta_key)
        outside_provinces = []
        normalized, status = None, 'unresolved'
        if field in {'budget', 'estimated_price'}:
            basis = 'including_vat' if field == 'budget' else 'excluding_vat'
            eligible = [i for i in eligible if facts[i]['basis'] == basis]
            if field == 'estimated_price':
                unit_price_indices = [i for i in eligible if _unit_price_estimate_fact(rec, facts[i])]
                eligible = [i for i in eligible if i not in unit_price_indices]
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
            if basic or any(facts[i].get('basic_level') or facts[i].get('anonymous_region_scope_unresolved') for i in eligible):
                status = 'hierarchy_unresolved'
            if normalized is not None and not basic and eligible and len(values) == 1:
                branches = [_explicit_province_branches(rec['docs'][facts[i]['doc_index']]['text'][facts[i]['start']:facts[i]['end']])
                            for i in eligible]
                if (all(branches) and all(b == branches[0] for b in branches)
                        and branches[0] - set(normalized)):
                    outside_provinces = sorted(branches[0] - set(normalized))
                    status = 'different'
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
                            'comparable_fact_indices': eligible,
                            **({'noncomparable_unit_price_fact_indices': unit_price_indices}
                               if unit_price_indices else {}),
                            **({'outside_registered_provinces': outside_provinces} if outside_provinces else {})})
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
        field = comparison['field']
        flag_key = {'region': '지역제한여부', 'industry': '업종제한여부'}.get(field)
        registered_flag = packet.get('flags', {}).get(flag_key) if flag_key else None
        # A registration flag and its value/list are different operands.  In
        # particular, null is not N.  Surface a source-complete N-versus-duty
        # relation for the model to inspect, without turning the flag alone
        # into a deterministic positive.
        operative_restriction = bool(comparison['fact_indices']) and all(
            packet['facts'][index].get('scope') == 'whole'
            for index in comparison['fact_indices'])
        flag_relation = ('source_restriction_vs_registered_N_candidate'
                         if all_shown and operative_restriction
                         and _compact(str(registered_flag)).upper() == 'N'
                         else 'none')
        compact.append({'field': comparison['meta_key'],
                        'registered_value': comparison['metadata'],
                        **({'registered_flag_field': flag_key,
                            'registered_flag': registered_flag,
                            'flag_relation': flag_relation} if flag_key else {}),
                        'comparison': state, 'observed': observations,
                        'omitted_observations': max(0, len(comparison['fact_indices']) - 6)})
    return {'fields': compact, 'instruction':
            '같은 의미·범위·부가세 기준의 값만 대조한다. 기초금액≠배정예산, 추정가격≠부가세포함예산, '
            '낙찰방법≠경쟁방식이다. 지역/업종 플래그 N만으로 원문 자격과의 불일치를 확정하지 않는다. '
            '다만 N과 원문의 의무 참가자격이 같은 제한 개념인지 확인하고, null·미입력과 N을 구별한다. '
            '예산·계약방법·지역·업종 네 축을 모두 확인하며 한 축의 일치 뒤에 검토를 끝내지 않는다. '
            '같음은 해당 필드만의 관측이며 v24 전체 정상이 아니다. 미추출·생략은 불일치도 일치도 아니다. '
            '첨부와 공고가 충돌하면 양쪽 원문과 적용범위를 확인한다. e에는 직접 관련된 S번호를 쓴다.'}


def positive_decision(rec, packet):
    for comparison in packet['comparisons']:
        allowed = comparison['field'] in {'budget', 'competition_method', 'industry'}
        allowed |= comparison['field'] == 'region' and bool(comparison.get('outside_registered_provinces'))
        if comparison['status'] != 'different' or not allowed:
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


def _comparison_claim_clauses(text):
    """Separate model assertions without splitting parenthetical field values."""
    pairs = {'(': ')', '（': '）', '[': ']', '【': '】'}
    stack, start, result = [], 0, []
    for index, char in enumerate(text):
        if char in pairs:
            stack.append(pairs[char])
        elif char in pairs.values():
            if not stack or stack.pop() != char:
                return None
        elif not stack and char in ',;；。\n.':
            if char in ',.' and index and index+1 < len(text) and text[index-1].isdigit() and text[index+1].isdigit():
                continue
            if text[start:index].strip():
                result.append(text[start:index].strip())
            start = index+1
    if stack:
        return None
    if text[start:].strip():
        result.append(text[start:].strip())
    return result


_MATCHED_OTHER_CLAIM = re.compile(
    r'(?:지역(?:제한|범위)?|업종(?:제한)?|면허(?:업종)?|계약방법|낙찰방법|'
    r'사업예산|배정예산|기초금액)'
    r'\((?:메타|등록정보)(?P<meta>[^()]+)/(?:본문|공고문)(?P<body>[^()]+)\)(?:일치|동일|같음)')


def reject_bounded_amount_witness(rec, row, response, packet):
    """Reject one positively identified bad proof, not certify item24 absence.

    This guard is deliberately narrower than missing evidence: the model must
    claim an estimated-price mismatch and cite an original statutory price
    range instead of an assigned price. Independent source positives still run
    afterwards. Other asserted fields and unresolved price evidence abstain.
    """
    from .response_contract import loads
    quote = row.get('e24')
    if row.get('v24') not in (1,'1') or not isinstance(quote,str) or not quote.strip():
        return None
    claim = loads(response['text']).get('facts',{}).get('본문과메타의동일필드차이')
    if not isinstance(claim,str):
        return None
    clauses = _comparison_claim_clauses(claim)
    if clauses is None:
        return None
    matching = []
    for clause in clauses:
        relation = _MATCHED_OTHER_CLAIM.fullmatch(_compact(clause))
        if relation and relation['meta'] == relation['body']:
            matching.append(clause)
    compact = _compact(' '.join(c for c in clauses if c not in matching))
    if ('추정가격' not in compact or not re.search(r'메타|등록정보',compact)
            or not re.search(r'상이|다르|불일치|차이',compact)):
        return None
    if re.search(r'불일치(?:가|는)?없|다르지|상이하지|차이(?:가|는)?없|불일치하지',compact):
        return None
    if re.search(r'예산|기초금액|사업금액|계약|지역|업종|면허|일시|마감|품명|규격|수량|수요기관|낙찰|공동|업무|방식|조달',compact):
        return None
    price = next(c for c in packet['comparisons'] if c['field']=='estimated_price')
    if price['status'] not in ('same','no_comparable_document_value'):
        return None
    if not re.search(r'적격심사|세부심사기준|평가기준|별표',quote):
        return None
    occurrences=[]
    for di,doc in enumerate(rec['docs']):
        for match in re.finditer(re.escape(quote),doc['text']):
            observed=[f for f in packet['facts'] if f['doc_index']==di
                      and f['start']<match.end() and match.start()<f['end']]
            if not observed or any(f['field']!='estimated_price' or f['scope']!='bounded'
                    or f.get('value_relation') not in ('미만','이하','이상','초과')
                    or f['start']<match.start() or f['end']>match.end() for f in observed):
                return None
            occurrences.append({'doc_index':di,'start':match.start(),'end':match.end(),
                                'bounded_price_facts':observed})
    if not occurrences:
        return None
    return {'item':24,'value':0,'evidence':'','semantic_value':None,'absence_verified':False,
        'source':'comparison_witness_validation','reason':'model_compared_statutory_bound_as_literal_price',
        'model_claim':claim,'matched_other_claims_not_mismatches':matching,
        'rejected_witness':quote,'occurrences':occurrences}


_DIFFERENCE_ASSERTION = re.compile(r'상이|다르|불일치|차이|불일치확인|서로(?:다른|상이)')
_DENIED_DIFFERENCE = re.compile(
    r'불일치(?:가|는|이)?없|불일치하지|다르지|상이하지|차이(?:가|는|이)?없|차이가나지')


def _claim_amounts(text):
    values = set()
    for match in _WON.finditer(text):
        value = won_value(match[0])
        if value is not None and value == value.to_integral_value():
            values.add(int(value))
    return values


_SHORT_WON = re.compile(r'(?<![\d,])(?P<number>\d+(?:\.\d+)?)\s*(?P<unit>조|억|만)(?:\s*원)?')


def _claim_amount_sequence(text):
    """Return claimed amounts, retaining repetitions needed to prove equality."""
    values, occupied = [], []
    for match in _WON.finditer(text):
        value = won_value(match[0])
        if value is not None and value == value.to_integral_value():
            values.append(int(value))
            occupied.append((match.start(), match.end()))
    multipliers = {'조': 10**12, '억': 10**8, '만': 10**4}
    for match in _SHORT_WON.finditer(text):
        if any(start < match.end() and match.start() < end for start, end in occupied):
            continue
        value = Decimal(match['number']) * multipliers[match['unit']]
        if value == value.to_integral_value():
            values.append(int(value))
    return values


def _comparison_by_field(packet, field):
    return next(x for x in packet['comparisons'] if x['field'] == field)


def _asserts_difference(clause):
    compact = _compact(clause)
    if _DENIED_DIFFERENCE.search(compact):
        return False
    return bool(_DIFFERENCE_ASSERTION.search(compact)
                or (re.search(r'본문|공고문', compact) and re.search(r'메타|등록정보', compact)
                    and re.search(r'반면|그러나|하지만|하나|인데', compact)))


def _invalid_region_flag_comparison(clause, packet):
    """A yes/no registration flag is not the registered region value."""
    compact = _compact(clause)
    region = _comparison_by_field(packet, 'region')
    flag = _compact(str(packet.get('flags', {}).get('지역제한여부') or '')).upper()
    flag_claim = ('지역제한여부N' in compact or '지역제한N' in compact
                  or bool(re.search(r'(?:메타|등록정보)(?:에는|는|:|=)?N(?:[),;/]|이나|이나본문|본문)', compact)))
    return (region['status'] != 'different' and flag == 'N' and bool(region['fact_indices'])
            and '지역' in compact and flag_claim and _asserts_difference(clause)
            and re.search(r'본문|공고문', compact) and re.search(r'메타|등록정보', compact))


def _invalid_industry_flag_comparison(clause, packet):
    """The industry yes/no flag is not the registered industry value/list."""
    compact = _compact(clause)
    industry = _comparison_by_field(packet, 'industry')
    flag = _compact(str(packet.get('flags', {}).get('업종제한여부') or '')).upper()
    flag_claim = ('업종제한여부N' in compact or '업종제한N' in compact
                  or bool(re.search(r'(?:메타|등록정보)(?:에는|는|:|=)?N(?:[),;/]|이나|이나본문|본문)', compact)))
    return (industry['status'] != 'different' and flag == 'N' and bool(industry['fact_indices'])
            and re.search(r'업종|면허', compact) and flag_claim and _asserts_difference(clause)
            and re.search(r'본문|공고문', compact) and re.search(r'메타|등록정보', compact))


def _invalid_missing_metadata_comparison(clause, packet):
    """A missing registered value is uncertainty, not a conflicting value."""
    compact = _compact(clause)
    if not (_asserts_difference(clause) and re.search(r'메타|등록정보', compact)
            and re.search(r'미입력|누락|없음|null|none', compact, re.I)):
        return False
    fields = []
    if re.search(r'예산|사업비|배정예산|기초금액', compact):
        fields.append('budget')
    if '추정가격' in compact:
        fields.append('estimated_price')
    if re.search(r'계약방법|경쟁방식', compact):
        fields.append('competition_method')
    if re.search(r'지역|소재지|본점', compact):
        fields.append('region')
    if re.search(r'업종|면허', compact):
        fields.append('industry')
    return bool(fields) and all(
        _comparison_by_field(packet, field)['metadata'] is None
        and _comparison_by_field(packet, field)['status'] != 'different'
        for field in fields)


def _invalid_equal_amount_comparison(clause, packet):
    """Equal or one-won-rounded literals cannot prove an amount mismatch."""
    compact = _compact(clause)
    if not (_asserts_difference(clause)
            and re.search(r'예산|사업비|배정예산|기초금액|추정가격', compact)):
        return False
    fields = ['estimated_price'] if ('추정가격' in compact
              and not re.search(r'예산|사업비|배정예산|기초금액', compact)) else ['budget']
    if any(_comparison_by_field(packet, field)['status'] == 'different' for field in fields):
        return False
    amounts = _claim_amount_sequence(clause)
    return len(amounts) >= 2 and max(amounts) - min(amounts) <= 1


def _invalid_tax_basis_budget_comparison(clause, packet):
    """A tax-exempt price and the portal's mechanical 10% gross are not peers."""
    compact = _compact(clause)
    if not (_asserts_difference(clause)
            and re.search(r'예산|사업비|배정예산|소요예산', compact)):
        return False
    budget = _comparison_by_field(packet, 'budget')
    price = _comparison_by_field(packet, 'estimated_price')
    if budget['status'] == 'different':
        return False
    try:
        registered_budget = int(Decimal(str(budget['metadata']).replace(',', '')))
        registered_price = int(Decimal(str(price['metadata']).replace(',', '')))
    except Exception:
        return False
    if registered_budget * 10 != registered_price * 11:
        return False
    exempt = {int(Decimal(packet['facts'][i]['value']))
              for i in budget['fact_indices']
              if packet['facts'][i].get('basis') == 'tax_exempt'
              and packet['facts'][i].get('scope') == 'whole'
              and packet['facts'][i].get('value') is not None}
    claimed = _claim_amounts(clause)
    return registered_price in exempt and {registered_budget, registered_price} <= claimed


def _invalid_contract_award_or_interdocument_comparison(clause, packet):
    """Award methods and source-to-source conflicts are not metadata contract values."""
    compact = _compact(clause)
    comparison = _comparison_by_field(packet, 'competition_method')
    if (comparison['status'] == 'different' or not _asserts_difference(clause)
            or not re.search(r'계약방법|경쟁방식|일반경쟁|제한경쟁|지명경쟁|수의계약', compact)):
        return False
    contract_mode = re.search(r'일반경쟁|제한경쟁|지명경쟁|수의계약', compact)
    award_mode = re.search(r'협상|낙찰방법|적격심사|최저가', compact)
    if contract_mode and award_mode:
        return True
    sources = {name for name in ('본문', '공고문', '제안요청서', '과업지시서', '첨부') if name in compact}
    return len(sources) >= 2 and not re.search(r'메타|등록정보', compact)


def _invalid_unresolved_industry_scope(clause, packet):
    """A vague list/combination assertion cannot resolve an AND/OR industry scope."""
    compact = _compact(clause)
    industry = _comparison_by_field(packet, 'industry')
    codes = set(re.findall(r'(?<!\d)\d{4}(?!\d)', compact))
    return (industry['status'] == 'and_or_scope_unresolved'
            and _asserts_difference(clause) and bool(re.search(r'업종|면허', compact))
            and len(codes) < 2)


def _invalid_project_total_comparison(clause, packet):
    """A whole-program total is not the current component tender budget."""
    if not _asserts_difference(clause):
        return False
    budget = _comparison_by_field(packet, 'budget')
    if budget['status'] != 'same' or not re.search(r'예산|사업비|입찰대상금액', clause):
        return False
    try:
        metadata = int(Decimal(str(budget['metadata']).replace(',', '')))
    except Exception:
        return False
    facts = [packet['facts'][i] for i in budget['fact_indices']]
    project = {int(Decimal(f['value'])) for f in facts
               if f.get('scope') == 'project_total' and f.get('value') is not None}
    whole = {int(Decimal(f['value'])) for f in facts
             if f.get('scope') == 'whole' and f.get('value') is not None}
    amounts = _claim_amounts(clause)
    return metadata in whole and metadata in amounts and bool((project - {metadata}) & amounts)


def _invalid_bounded_price_comparison(clause, packet):
    """A statutory band selects a rule; it does not assign the notice price."""
    if '추정가격' not in _compact(clause) or not _asserts_difference(clause):
        return False
    price = _comparison_by_field(packet, 'estimated_price')
    if price['status'] not in {'same', 'no_comparable_document_value'}:
        return False
    bounded = {int(Decimal(f['value'])) for f in packet['facts']
               if f.get('field') == 'estimated_price' and f.get('scope') == 'bounded'
               and f.get('value') is not None
               and f.get('value_relation') in {'미만', '이하', '이상', '초과'}}
    return bool(bounded & _claim_amounts(clause))


def reject_unsupported_comparison_claim(rec, row, response, packet):
    """Reject a positive supported only by a typed non-comparable relation.

    This is a proof validator, not a negative v24 classifier.  Any comparison
    that the source extractor independently marks ``different`` is preserved.
    Otherwise every asserted difference clause must be demonstrably one of the
    three relations above; unknown or additional mismatch claims abstain.
    """
    legacy = reject_bounded_amount_witness(rec, row, response, packet)
    if legacy is not None:
        return legacy
    quote = row.get('e24')
    if row.get('v24') not in (1, '1'):
        return None
    # A missing model citation is not itself an absence proof.  It also must
    # not bypass validation when the structured claim is independently shown
    # to compare non-equivalent operands.  Unknown claims still abstain below.
    quote = quote if isinstance(quote, str) else ''
    if any(x['status'] == 'different' for x in packet['comparisons']):
        return None
    from .response_contract import loads
    claim = loads(response['text']).get('facts', {}).get('본문과메타의동일필드차이')
    if not isinstance(claim, str):
        return None
    clauses = _comparison_claim_clauses(claim)
    if not clauses:
        return None
    invalid = []
    for clause in clauses:
        reason = None
        if _invalid_region_flag_comparison(clause, packet):
            reason = 'model_compared_region_flag_as_registered_region'
        elif _invalid_industry_flag_comparison(clause, packet):
            reason = 'model_compared_industry_flag_as_registered_industry'
        elif _invalid_missing_metadata_comparison(clause, packet):
            reason = 'model_treated_missing_metadata_as_conflicting_value'
        elif _invalid_project_total_comparison(clause, packet):
            reason = 'model_compared_project_total_as_tender_budget'
        elif _invalid_bounded_price_comparison(clause, packet):
            reason = 'model_compared_statutory_bound_as_literal_price'
        elif _invalid_equal_amount_comparison(clause, packet):
            reason = 'model_asserted_difference_between_equal_amounts'
        elif _invalid_tax_basis_budget_comparison(clause, packet):
            reason = 'model_compared_tax_exempt_price_to_portal_gross_budget'
        elif _invalid_contract_award_or_interdocument_comparison(clause, packet):
            reason = 'model_compared_contract_method_to_nonmetadata_method'
        elif _invalid_unresolved_industry_scope(clause, packet):
            reason = 'model_asserted_unresolved_industry_scope_as_difference'
        if reason:
            invalid.append({'reason': reason, 'clause': clause})
        elif _asserts_difference(clause):
            return None
    if not invalid:
        return None
    reasons = sorted({x['reason'] for x in invalid})
    return {'item': 24, 'value': 0, 'evidence': '', 'semantic_value': None,
        'absence_verified': False, 'source': 'comparison_relation_validation',
        'reason': reasons[0] if len(reasons) == 1 else 'model_used_only_noncomparable_relations',
        'invalid_relations': invalid, 'model_claim': claim, 'rejected_witness': quote,
        'source_differences_preserved': True}
