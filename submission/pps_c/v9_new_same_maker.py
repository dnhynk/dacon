"""Read an explicitly named installed maker plus a same-maker acquisition demand.

No maker vocabulary or model-to-maker inference. A maker is established by an
explicit manufacturer field, or the written maker + model pair of an installed
asset field. Evidence remains the original requiring line.
"""
import re

_OLD = re.compile(
    r'(?:기\s*존|기\s*설\s*치(?:\s*된)?|현\s*(?:재\s*)?(?:운\s*[영용]|사\s*용|설\s*치)(?:\s*중\s*인)?|'
    r'기\s*(?:운\s*[영용]|사\s*용)(?:\s*중\s*인)?|보\s*유)'
    r'[^:：()\n.;]{0,35}?(?:장\s*비|서\s*버|스\s*토\s*리\s*지|시\s*스\s*템|제\s*품|기\s*기|설\s*비|스\s*위\s*치|냉\s*난\s*방\s*기)')
_FIELD = re.compile(r'(?:제\s*조\s*(?:사|원)|브\s*랜\s*드|메\s*이\s*커|Manufacturer|Maker|Brand)\s*[:：=]\s*([^,;|()\n]{1,60})', re.I)
_PAIR = re.compile(r'^\s*([A-Za-z][A-Za-z.&_-]{1,25}|[가-힣]{2,20})\s+'
                   r'(?=[A-Za-z0-9-]*\d)[A-Za-z][A-Za-z0-9-]{2,30}(?=$|[\s,;)）])')
_BAD_NAME = re.compile(r'미\s*상|미\s*기\s*재|없\s*음|동\s*일|미\s*정|제\s*안|기\s*재|예\s*시|참\s*고|'
                       r'모\s*델|장\s*비|서\s*버|제\s*품|시\s*스\s*템|스\s*토\s*리\s*지|제\s*조\s*사|'
                       r'백\s*업|불\s*문|무\s*관|또\s*는|[○◯〇●*\[\]]|^(?:VTL|NAS|SAN|SSD|HDD|CPU|GPU|RAM|PC|SW|HW|Backup|Disk|Gateway)$', re.I)
_SAME_DEMAND = re.compile(
    r'동일(?:한)?(?:제조사|브랜드)(?:의)?(?:장비|제품|기기|설비|서버|스토리지|스위치|냉난방기)?'
    r'(?:으로|로|을|를)?(?:반드시|필히)?(?:신규)?(?:도입|납품|공급)')
_NEGATED = re.compile(r'하지않|하지아니|할필요(?:가)?없|의무(?:가)?없|요구하지|권장|예시|참고용|불필요')
_OLD_REF = re.compile(r'(?:기존|기설치|현운영|현재운영|기운영|보유)[^.;()]{0,35}?(?:와|과)(?:같은|동일)')


def _name(value):
    value = value.strip(' \t\"\'“”‘’')
    return (2 <= len(value) <= 40 and bool(re.search(r'[A-Za-z가-힣]{2}', value))
            and not _BAD_NAME.search(value) and not re.search(r'[/,;|]|\d+\s*(?:대|개|식)', value))


def existing_maker(text):
    """Return a maker literally written in an installed-asset field, else None."""
    for old in _OLD.finditer(text):
        tail = text[old.end():]
        # The maker label belongs immediately to this installed asset, not a
        # new-asset field later in the paragraph or an unrelated product table.
        labelled = re.match(r'\s*[(（:]?\s*', tail)
        field = _FIELD.match(tail, labelled.end()) if labelled else None
        if field and _name(field.group(1)):
            return field.group(1).strip()
        value = re.match(r'\s*[:：]\s*([^()\n;]{1,100})', tail)
        pair = _PAIR.match(value.group(1)) if value else None
        if pair and _name(pair.group(1)):
            return pair.group(1)
    return None


_UNIFORM = re.compile(r'(?:상호|서로|끼리|품목별|제품간|장비간)[^.;]{0,25}동일(?:한)?(?:제조사|브랜드)'
                      r'|(?:모든|전체|각종)[^.;()]{0,20}(?:장비|제품|품목)(?:은|는)?동일(?:한)?(?:제조사|브랜드)'
                      r'|(?:장비|제품|품목|납품품)(?:은|는)?(?:전부|모두)동일(?:한)?(?:제조사|브랜드)')
_OPTIONAL = re.compile(r'(?:도입|납품|공급)(?:을|를|은|는|이|가)?(?:검토|고려|권장|선택|가능|계획)'
                       r'|(?:도입|납품|공급)할수(?:있|도)|(?:도입|납품|공급)(?:의)?여부')


def matches(b, ln):
    if b.meta.work != '물품' or ln.sec in ('EVAL', 'DOCS') or len(ln.text) > 500:
        return False
    # A semicolon-separated existing-asset note must not supply a manufacturer
    # to an unrelated uniformity requirement on the same extracted source line.
    for part in re.split(r'[;；]', ln.text):
        compact = re.sub(r'\s+', '', part)
        demand = _SAME_DEMAND.search(compact)
        if not demand or _NEGATED.search(compact) or _UNIFORM.search(compact) or _OPTIONAL.search(compact):
            continue
        if existing_maker(part):
            return True
        # Adjacent metadata requires an explicit existing-asset back reference.
        if not _OLD_REF.search(compact):
            continue
        previous = next((x for x in reversed(b.notice.lines[:ln.i]) if x.doc == ln.doc and x.text.strip()), None)
        if previous and ln.i-previous.i <= 3 and existing_maker(previous.text):
            return True
    return False


def find(b):
    return next((ln for ln in b.notice.lines if matches(b, ln)), None)
