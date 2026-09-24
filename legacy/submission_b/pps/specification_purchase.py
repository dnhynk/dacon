"""Positive-only v9 witnesses: a named purchase requirement and its scope.

An explicit model field needs a procurement specification parent; prose needs
an operative supply/use obligation. Reference equipment, examples and linked
alternatives withhold the witness. Unknown names/roles remain model decisions.
No prompt, candidate inventory, or model judgment is changed by this extractor.
"""
from __future__ import annotations

import re

from .data import clean_evidence

_FIELD = re.compile(
    r'(?P<label>(?:제조사\s*[·/ㆍ]\s*)?모델(?:명|번호)?|서버모델|기종명|'
    r'제조사|제작사|브랜드|(?<![A-Za-z])model)\s*[:：]\s*(?P<value>[^\n|]{2,110})', re.I)
_NUMBER = re.compile(r'모델번호\s+(?P<value>[A-Za-z][A-Za-z0-9._/-]*\d[A-Za-z0-9._/-]*)')
_POST_MODEL = re.compile(r'(?P<value>[A-Za-z][A-Za-z0-9._/-]*\d[A-Za-z0-9._/-]*)\s+모델')
_NAME = re.compile(r'(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9._/-]*\d[A-Za-z0-9._/-]*')
_STANDARD = re.compile(r'^(?:ISO|IEC|KS|MIL|IEEE|USB|DDR|IP|HDMI|RJ|RS|CAT|G2B)[-._]?\d', re.I)
_GENERIC = re.compile(r'^(?:없음|무관|제한\s*없|미지정|자유|상관없|해당\s*없|동일|국산|외산|'
    r'국내|국외|중소기업|정품|제품|업체|기재|작성|확인|제출|선택|제안|Linux|Ubuntu|Windows|OS|CPU|GPU)\b', re.I)
_SPEC_PARENT = re.compile(r'구\s*매|납\s*품|규\s*격|사\s*양|품\s*명|물품명|'
    r'차\s*종|적용범위|commodity\s+description|performance\s+and\s+specification', re.I)
_OBLIGATION = re.compile(r'(?:납품|공급|구매|구입|설치|사용|적용|제안)[^\n.!?]{0,18}'
    r'(?:하여야|해야|한다|할\s*것|필수|한정)|(?:으로|로)\s*(?:한정|제한)|제품에\s*한함')
_OBJECT = re.compile(r'납품\s*(?:물품|제품|장비)|구매\s*(?:대상|물품|장비)|'
    r'입찰참가자|계약상대자|공급\s*(?:물품|제품)|규격|사양|장비|콘솔|차량|서버')
_REFERENCE = re.compile(r'예시|참고\s*(?:모델|기종|제품|용|자료)|추천\s*모델|'
    r'(?:기존|기\s*보유|현재\s*보유|사용\s*중인)[^\n.!?]{0,45}(?:장비|기종|모델|시스템)|'
    r'적용\s*(?:기종|모델)|호환\s*(?:대상|기종)|(?:소모품|부품|유지보수)\s*(?:적용|대상)|'
    r'매뉴얼|설명서\s*(?:예시|참조)')
_EQUIVALENCE = re.compile(r'동\s*(?:등|급)(?:\s*(?:또는|혹은))?(?:\s*이상)?|\bequivalent\b', re.I)
_DENIAL = re.compile(r'불가|불허|금지|인정하지|허용하지|허용\s*안|할\s*수\s*없|제외|미허용')
_ALTERNATIVE = re.compile(r'또는|혹은|\bor\b|허용|가능|인정|납품|제안|대체|적합|제조사.{0,15}무관', re.I)
_ALL_SCOPE = re.compile(r'(?:본|첨부|별첨|붙임|상기)?\s*(?:규격서|사양서|시방서|구매\s*규격)'
    r'[^\n.!?]{0,65}(?:동\s*등|동\s*급)|'
    r'(?:명시|제시|공고)된?\s*규격(?:\s*\([^)]*\))?[^\n.!?]{0,45}(?:동\s*등|동\s*급)|'
    r'(?:모든|전체|전\s*품목|각\s*품목)[^\n.!?]{0,40}(?:동\s*등|동\s*급)')
_IDENTITY = re.compile(r'모델|기종|제조사|제품|물품|장비|규격|사양|품목')
_PRODUCT_SCOPE = re.compile(r'(?:본|해당|상기|위|납품|구매)\s*(?:제품|물품|장비|모델|규격)|'
    r'동\s*(?:등|급)(?:\s*이상)?\s*(?:제품|물품|장비|모델|기종)')
_NEW_FIELD = re.compile(r'^\s*[-○●•]?\s*[가-힣A-Za-z][가-힣A-Za-z /]{0,22}\s*[:：]')
_FORM_START = re.compile(r'^(?:.*(?:구매|제품|입찰|소모품).{0,12}규\s*격\s*서|'
    r'구매규격\s*및|제품명칭\s|품\s*명\s*[:：]|기존\s*장비|보유\s*장비)')
_MAINTENANCE = re.compile(r'정기\s*수리|수리\s*내역|정비\s*내역|유지\s*보수|보수\s*대상')


def _block(lines, index, size):
    starts = [i for i, (_, _, line) in enumerate(lines) if _FORM_START.search(line.strip())]
    first = max((i for i in starts if i <= index), default=0)
    last = min((i for i in starts if i > index), default=len(lines))
    return lines[first][0], lines[last][0] if last < len(lines) else size


def _compatibility_table(text):
    # A collective note explicitly binds the named rows to already-used
    # equipment. Generic compatibility language elsewhere is insufficient.
    return bool(re.search(r'제조사[^\n.]{0,15}명시[^\n.]{0,60}물품[\s\S]{0,180}'
                          r'(?:기\s*사용|기존|보유)[\s\S]{0,70}호환', text))


def _lines(text):
    at = 0
    for raw in text.splitlines(keepends=True):
        line = raw.rstrip('\r\n')
        if line.strip():
            yield at, at + len(line), line
        at += len(raw)


def _named(value, label):
    value = value.strip(' ()[]「」『』“”"')
    if (_GENERIC.search(value)
            or re.search(r'제출|확인|증명|미지정|무관|하여야|없습니다|제한하지', value)):
        return False
    if any(not _STANDARD.match(m[0]) for m in _NAME.finditer(value)):
        return True
    if re.fullmatch(r'[A-Z][A-Za-z]+(?:[ -]+[A-Z][A-Za-z]+){1,3}\s+\d[\w.-]*', value):
        return True
    # Labelled manufacturer/model names may contain no digits. Require a
    # literal name-sized field, never arbitrary explanatory Korean prose.
    if re.search(r'제조사|제작사|브랜드', label):
        return bool(re.fullmatch(r'[가-힣A-Za-z][가-힣A-Za-z &().-]{1,45}', value))
    return bool(re.fullmatch(r'[A-Z][A-Za-z]+(?:[ -]+[A-Z][A-Za-z]+){1,4}', value))


def _allows(text):
    """Do not mistake a prohibition containing 'equivalent' for permission."""
    # A separate parenthetical ban on used/non-genuine goods has a different
    # object from the equivalence permission. Retain parenthetical denials of
    # equivalents themselves, and every restriction outside the parentheses.
    text = re.sub(r'\([^()]*(?:재생품|중고품|비정품)[^()]*\)',
                  lambda m: m[0] if _EQUIVALENCE.search(m[0]) else '', text)
    for match in _EQUIVALENCE.finditer(text):
        tail = text[match.end():match.end()+85]
        if _DENIAL.search(tail):
            continue
        if _ALTERNATIVE.search(text) or re.match(r'\s*(?:품|제품|모델|기종|장비|물품|이상)', tail):
            return True
    return False


def _common_specification_review(record):
    """A notice's common comparison procedure links its attached specs."""
    for doc in record['docs']:
        if doc['type'] != '공고문':
            continue
        text = doc['text']
        for match in re.finditer(r'규격\s*검토\s*확인서', text):
            block = text[match.start():match.end()+600]
            boundary = re.search(r'\n\s*\d+(?:\.\d+)*[.)]\s', block)
            if boundary:
                block = block[:boundary.start()]
            if (re.search(r'동등\s*이상\s*규격.{0,30}(?:판단|판정)', block)
                    and re.search(r'수요부서|수요기관|발주부서', block)
                    and re.search(r'(?:판단|판정)(?:합니다|한다|함)', block)
                    and not re.search(r'(?:동등품|대체품|동등\s*이상\s*규격)'
                                      r'[^\n.]{0,40}(?:불가|불허|허용하지|인정하지)', block)):
                return True
    return False


def _named_supplier_gate(text):
    """A named manufacturer's mandatory supplier gate limits alternatives."""
    for _, _, line in _lines(text):
        match = re.search(r'(?:공급\s*업체|입찰\s*참가자|납품\s*업체)(?:는|가)\s*'
                          r'([가-힣A-Za-z][가-힣A-Za-z0-9&._-]{1,40})(?:\([사社]\))?\s*'
                          r'(?:국내\s*)?(?:공식|지정|독점)\s*(?:공급\s*업체|대리점)'
                          r'(?:여야|이어야)', line)
        if (match and _named(match[1], '제조사')
                and not re.search(r'제조사|제작사|생산자', match[1])
                and not _REFERENCE.search(line)
                and not re.search(r'가정|인용|필요.{0,5}없|(?:요건|조건).{0,15}(?:삭제|철회)', line)):
            return True
    return False


def named_purchase_check(record):
    """Return one literal positive witness, or abstain; never return v9=0."""
    docs = record['docs']
    # An explicit whole-specification allowance in the notice bridges to its
    # attachments. A different product's local alternative cannot do this.
    blanket = any(_ALL_SCOPE.search(line) and _allows(line)
                  for doc in docs if doc['type'] in {'공고문', '제안요청서'}
                  for _, _, line in _lines(doc['text']))
    blanket = blanket or _common_specification_review(record)
    supplier_gates = {di for di, doc in enumerate(docs) if _named_supplier_gate(doc['text'])}
    if blanket and not supplier_gates:
        return None
    for di, doc in enumerate(docs):
        if blanket and di not in supplier_gates:
            continue
        text = doc['text']
        lines = list(_lines(text))
        for index, (lo, hi, line) in enumerate(lines):
            matches = [(m, m['label'], True) for m in _FIELD.finditer(line)]
            matches += [(m, '모델번호', False) for m in _NUMBER.finditer(line)]
            matches += [(m, '모델', False) for m in _POST_MODEL.finditer(line)]
            for match, label, labelled in matches:
                value = match['value'].split(')')[0].strip()
                if not _named(value, label):
                    continue
                # The current requirement and its parent must agree on role.
                # A blank line alone does not end a heading's equipment role.
                block_lo, block_hi = _block(lines, index, len(text))
                parent_lo = max(block_lo, lo-550)
                parent = text[parent_lo:lo]
                local_parent = '\n'.join(x[2] for x in lines[max(0,index-2):index])
                context = local_parent + '\n' + line
                if (_REFERENCE.search(context)
                        or re.match(r'(?:기존|보유)\s*장비', text[block_lo:block_hi])):
                    continue
                if (re.search(r'모델(?:에|과|와)[^\n.!?]{0,25}(?:사용|호환|연결|적용)', line)
                        or _MAINTENANCE.search(text[:300]) or _compatibility_table(text)):
                    continue
                if re.search(r'(?:모델|기종|제조사)[^\n.!?]{0,20}(?:지정하지|제한하지|무관)', line):
                    continue
                prose = bool(_OBJECT.search(line) and _OBLIGATION.search(line))
                field = (labelled and doc['type'] != '공고문' and bool(_SPEC_PARENT.search(parent + line))
                         and not re.search(r'모델\s*(?:수립|개발)|브랜드\s*개발', line))
                if not prose and not field:
                    continue
                if _allows(line) and di not in supplier_gates:
                    continue
                # A directly continuing alternative belongs to this field.
                # A separately labelled component (e.g. memory capacity) does
                # not lend its equivalence to the named server model.
                allowed = False
                for _, _, following in lines[index+1:index+3]:
                    if _NEW_FIELD.match(following):
                        break
                    if _allows(following) and _IDENTITY.search(following):
                        allowed = True
                if allowed and di not in supplier_gates:
                    continue
                # Whole-spec permissions apply within this attachment; a
                # second explicit product/form heading bounds their scope.
                if di not in supplier_gates and any(_ALL_SCOPE.search(other) and _allows(other)
                       for a, _, other in lines if block_lo <= a < block_hi):
                    continue
                if di not in supplier_gates and any(_PRODUCT_SCOPE.search(other) and _allows(other)
                       and not _NEW_FIELD.match(other)
                       for a, _, other in lines if block_lo <= a < block_hi and a != lo):
                    continue
                if hi-lo > 500:
                    continue  # Preserve the full requirement, never clip its exception.
                source = (di, lo, hi)
                quote = clean_evidence(line, record, source=source)
                if not quote or match.end() > len(quote):
                    continue
                return {'item': 9, 'value': 1, 'evidence': quote,
                    'source': 'named_purchase_requirement',
                    'reason': 'named_identity_bound_to_purchase_requirement_without_linked_alternative',
                    'identity': value, 'label': label,
                    'identity_source': {'doc_index': di, 'start': lo+match.start('value'),
                                        'end': lo+match.end('value')},
                    'requirement_source': {'doc_index': di, 'start': lo, 'end': hi},
                    'role': 'operative_supply_obligation' if prose else 'procurement_specification_field',
                    'parent_source': {'doc_index': di, 'start': parent_lo, 'end': lo},
                    'legal_exemption_inferred': False}
    return None
