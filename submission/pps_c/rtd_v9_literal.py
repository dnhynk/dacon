"""Literal v9 specifications missed by reader selection or line wrapping.

Only adds evidence of the supplied goods; no identifiers, labels, edit signatures
or other violation decisions are inspected.
"""
import re
from .dedicated import model_name as st

_LABEL = re.compile(
    r'(?:제\s*품\s*)?(?:모\s*델\s*명?|제\s*조\s*(?:사|원|업\s*체)|'
    r'제\s*작\s*사|브\s*랜\s*드|메\s*이\s*커|'
    r'Model(?:\s+(?:Name|No\.?))?|Manufacturer|Maker|Brand)', re.I)
_FIELD = re.compile(
    r'(?<![A-Za-z가-힣])(?:C\s*\.?\s*P\s*\.?\s*U|G\s*\.?\s*P\s*\.?\s*U|'
    r'Processor|Chipset|Graphics|Main\s*Board|Motherboard|'
    r'프\s*로\s*세\s*서|칩\s*셋|그\s*래\s*픽\s*카\s*드|메\s*인\s*보\s*드)'
    r'\s*[:：|]\s*(.+)', re.I)
_INLINE = re.compile(_LABEL.pattern + r'\s*[:：|]\s*(.+)', re.I)
_ONLY_LABEL = re.compile(r'^[\s•○◦\-]*' + _LABEL.pattern + r'\s*[:：|]?\s*$', re.I)
# Model code starts with at least two Latin letters, has a digit, and is not a
# unit, material, standard, request number, telephone number or anonymous field.
_CODE = re.compile(r'(?<![A-Za-z0-9])(?=[A-Za-z0-9‐‑–-]*\d)'
                   r'[A-Za-z]{2,}[A-Za-z0-9]*(?:[-‐‑–][A-Za-z0-9]+)*(?![A-Za-z0-9])')
_WRAP = re.compile(r'(?P<prefix>[A-Za-z]{2,}[A-Za-z0-9]*[-‐‑–])\s*$')
_NEXT_CODE = re.compile(r'^\s*(?P<tail>\d[A-Za-z0-9-]{2,})(?=$|[\s)）,，;；|])')
_NOT = re.compile(
    r'기\s*존|기\s*설\s*치|보\s*유\s*장\s*비|유\s*지\s*보\s*수|유\s*지\s*관\s*리|'
    r'수\s*리|점\s*검\s*대\s*상|호\s*환|연\s*동\s*대\s*상|적\s*용\s*대\s*상|'
    r'운\s*영\s*환\s*경|개\s*발\s*환\s*경|예\s*시|예를\s*들|참\s*고\s*용|'
    r'확\s*약\s*서|증\s*명\s*서|인\s*증\s*서|발\s*급|작\s*성|기\s*재|기\s*입|'
    r'입\s*력|모\s*델\s*명\s*기\s*록|빈\s*칸|해\s*당\s*없|'
    r'브\s*랜\s*드\s*(?:홍\s*보|마\s*케\s*팅|가\s*치|개\s*발)|'
    r'신\s*용\s*평\s*가|회\s*사\s*채|재\s*질|재\s*료|표\s*준\s*규\s*격|'
    r'입\s*찰\s*공\s*고|공\s*고\s*번\s*호|문\s*서\s*번\s*호|'
    r'요\s*구\s*사\s*항\s*(?:번\s*호|ID)|고\s*유\s*번\s*호|분\s*류\s*번\s*호')
_STD = re.compile(r'^(?:GC|GCD|KGS|EPSG|KCR|CODE|AES|VAC|VDC|R)\d', re.I)
_DEMAND = re.compile(
    r'^\s*(?:제\s*품|컨\s*트\s*롤|제\s*어\s*기|장\s*비|부\s*품|모\s*터|'
    r'펌\s*프|카\s*메\s*라|서\s*버|센\s*서)?\s*(?:으\s*로|로|을|를)?\s*'
    r'(?:하\s*며|할\s*것|한\s*다|납\s*품|설\s*치|장\s*착|탑\s*재|공\s*급)')
_DOCS = ('규격서', '과업지시서', '제안요청서', '공고문')


def _maker(text):
    # Reuse the maintained vocabulary, with word boundaries (애플리케이션 is
    # not Apple; KSB is not the maker KSB).
    from . import judge
    for pattern in (judge.V9_CODE_BRAND, st.MAKER_LIST_RE):
        for m in pattern.finditer(text):
            before, after = text[:m.start()], text[m.end():]
            if before and re.search(r'[A-Za-z가-힣0-9]$', before):
                continue
            if after and re.match(r'[A-Za-z가-힣]', after):
                continue
            if m.group() == 'HP' and (re.search(r'\d\s*[/]?\s*$', before) or after.startswith('-')):
                continue
            yield m


def _code(text):
    text = st.mask_standards(text)
    return any(len(m.group()) >= 4 and not st.token_is_standard(m.group())
               and not _STD.match(m.group()) for m in _CODE.finditer(text))


def _scope(b, ln):
    same = [x for x in b.notice.lines[max(0, ln.i-6):ln.i+1]
            if x.doc == ln.doc and x.text.strip()]
    before = same[-3:]
    heading = b.notice.lines[ln.head].text if ln.head >= 0 else ''
    return heading + '\n' + '\n'.join(x.text for x in before)


def find(b, *, wrapped=False, fields=False):
    if b.meta.work != '물품':
        return None
    previous = None
    for ln in b.notice.lines:
        text = ln.text.strip()
        if not text:
            continue
        prev = previous
        previous = ln
        if ln.doc_type not in _DOCS or ln.sec in ('EVAL','DOCS') or len(text) > 220:
            continue
        if _NOT.search(text) or _NOT.search(_scope(b, ln)):
            continue
        if wrapped:
            m = _WRAP.search(text)
            if m and ln.i+1 < len(b.notice.lines):
                nxt = b.notice.lines[ln.i+1]
                tail = _NEXT_CODE.match(nxt.text) if nxt.doc == ln.doc else None
                # An explicit model label, or a product-name parenthesis, binds
                # the code to the goods. A bare split code is insufficient.
                prefix = text[:m.start()]
                bound = bool(_LABEL.search(prefix) or re.search(r'[가-힣A-Za-z][^()\n]{0,35}[(（]\s*$',prefix))
                code = m.group('prefix') + tail.group('tail') if tail else ''
                if tail and bound and _code(code) and not _NOT.search(nxt.text):
                    return ln, 'wrapped_model'
        if not fields:
            continue
        inline = _INLINE.search(text)
        component = _FIELD.search(text)
        value = inline.group(1) if inline else component.group(1) if component else None
        reason = 'inline_label' if inline else 'component_maker'
        if value is None and prev and prev.doc == ln.doc and ln.i-prev.i <= 3 and _ONLY_LABEL.fullmatch(prev.text.strip()):
            if not _NOT.search(_scope(b, prev)):
                value, reason = text, 'vertical_label'
        if value is not None:
            if not _NOT.search(value) and (any(_maker(value)) or (not component and _code(value))):
                return ln, reason
        # Literal prescribed maker, e.g. "Danfoss 컨트롤로 하며".
        if ln.doc_type != '공고문':
            for maker in _maker(text):
                if _DEMAND.match(text[maker.end():]):
                    return ln, 'maker_demand'
    return None


def verdict(b):
    from . import switches
    hit = find(b, wrapped=switches.RTD_V9_WRAPPED_MODEL, fields=switches.RTD_V9_SPEC_FIELDS)
    return hit[0] if hit else None
