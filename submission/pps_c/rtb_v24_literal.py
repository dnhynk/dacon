"""Literal notice-versus-registration comparisons for v24; opt-in CPU rules."""
import re
from . import switches

METHOD_FIELD = re.compile(r'(?:계\s*약|입\s*찰)\s*(?:방\s*법|방\s*식)\s*[:：|]+\s*([\s\S]{0,100})')
METHODS = ('일반경쟁', '제한경쟁', '지명경쟁')
# A slash/conjoined abbreviated list is not a single current method.
NO_LICENCE_REQUIREMENT = re.compile(r'(?:등록|신고|허가|보유)(?:하지|하지않|되지|되지않).{0,12}(?:않|필요없)|(?:등록|신고|허가|보유).{0,12}(?:불필요|필요없)|미등록.{0,25}(?:가능|참가)|등록하지않아도')
AMBIG_METHOD = re.compile(r'(일반|제한|지명)\s*[/·ㆍ,]\s*(일반|제한|지명)|제한일반|일반제한')
QUAL_PRED = re.compile(r'등\s*록|허\s*가|신\s*고|보\s*유|지\s*정\s*된')
OR = re.compile(r'또\s*는|이\s*거\s*나|혹\s*은|중\s*어\s*느\s*하\s*나')
BARE_CODE = re.compile(r'(?:업|용역|사업자|기관|연수원|대학|협력단|학원|전문회사|사무소|제작사|제작자)\s*(?:\([^()]{0,50}\))?\s*[\[(]\s*(\d{4})\s*[\])]')


def flat(text):
    return re.sub(r'[^가-힣a-zA-Z0-9]', '', text)


def method_body(b):
    """An explicit body field names a different competitive procedure.

    Read each field against meta. A matching title/another field does not
    erase the disagreement. No title token is used to trigger the rule.
    Private-contract and negotiation-versus-competition cases stay out.
    """
    from . import judge as J
    if b.meta.method not in METHODS:
        return None
    for ln in b.notice.notice_lines():
        if ln.sec in ('NOTE', 'DOCS'):
            continue
        for m in METHOD_FIELD.finditer(ln.text):
            val = m.group(1)
            compact = re.sub(r'\s', '', val)
            if AMBIG_METHOD.search(compact) or re.search(r'경우|전환|유찰|수의|견적|아님|아니|않', compact):
                continue
            said = J.stated_methods(val)
            if len(said) == 1 and said <= set(METHODS) and b.meta.method not in said:
                return ln
    return None


def pure_meta_alternatives(value):
    groups = re.findall(r'\[([^\[\]]+)\]', str(value or ''))
    if not groups:
        return None
    out = {}
    for group in groups:
        codes = re.findall(r'\((\d{4})\)', group)
        if len(codes) != 1:
            return None  # compound AND branches require a different comparator
        out[codes[0]] = re.sub(r'\(\d{4}\)', '', group).strip()
    return out


def licence_or(b):
    """A closed licence qualification selects a different set of alternatives.

    Both sides state a licence. No absence inference from a failed text
    search alone: an affirmative registration/possession clause is required.
    Whole-notice name/code checks suppress unparsed alternatives and wraps.
    """
    from . import judge as J
    if b.meta.license_flag != 'Y':
        return None
    names = pure_meta_alternatives(b.meta.license)
    if not names:
        return None
    registered = set(names)
    nt = '\n'.join(x.text for x in b.notice.notice_lines())
    nf = flat(nt)
    seen = set()
    for ln in b.notice.notice_lines():
        if not J.qual_section(ln, b.notice):
            continue
        text = J.clause_text(b.notice, ln)
        if text in seen:
            continue
        seen.add(text)
        if not QUAL_PRED.search(text) or NO_LICENCE_REQUIREMENT.search(re.sub(r'\s', '', text)) or J.LICENSE_GUIDE.search(text) \
                or J.JV_PARTNER3.search(text) or J.PARTNER_WORK.search(text) or J.alternative_item(b.notice, ln):
            continue
        codes = J.wide_license_codes(text) | set(J.CODE_LABELLED.findall(text)) | set(BARE_CODE.findall(text))
        if not codes or not codes & registered:
            continue
        # The explicit codes must describe alternatives, not cumulative roles.
        if len(codes) > 1:
            positions = sorted((re.search(r'(?<!\d)' + c + r'(?!\d)', text).span() for c in codes))
            connectors = [text[a[1]:z[0]] for a,z in zip(positions,positions[1:])]
            if not all(OR.search(x) for x in connectors) and not re.search(r'중\s*어느\s*하나', text[positions[-1][1]:]):
                continue
        # A code-free registered name elsewhere completes a partially printed
        # list. Use literal full names and explicit subtype names, not a lookup.
        omitted = registered - codes
        missing = set()
        for code in omitted:
            name = names[code]
            aliases = [flat(name)] + [flat(x) for x in re.findall(r'\(([^()]+)\)', name)]
            if re.search(r'(?<!\d)' + code + r'(?!\d)', re.sub(r'\s', '', nt)) \
                    or any(len(alias) >= 4 and alias in nf for alias in aliases):
                continue
            missing.add(code)
        # Missing alternatives need a completed affirmative clause; a lone
        # "업종코드: NNNN" table fragment does not close the eligibility list.
        complete = bool(re.search(r'(?:업체|기관|사업자|자)(?:이어야|여야|입니다|임|만|로서|로|$)|등\s*록\s*을?\s*필', text.strip()))
        if complete and (codes - registered or missing):
            return ln
    return None


def extra(b):
    for name, rule in (('RTB_V24_METHOD_BODY', method_body), ('RTB_V24_LICENCE_OR', licence_or)):
        if getattr(switches, name, False):
            hit = rule(b)
            if hit is not None:
                return hit
    return None
