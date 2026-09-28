"""Bidder-location restrictions in further shapes (switch REGION_CLAUSE_FORMS2), read after REGION_CLAUSE_FORMS finds nothing.

Attachments: an attachment line (과업지시서, 제안요청서, 규격서 …) that states the participation qualification and
names where the bidder's office must be: "입찰참가자격: X에 본점을 둔 업체", "입찰참가자는 X 관내에 본사가 소재하여야 함",
"| 참가자격 | X 소재 업체 |", "입찰참가 자격 요건 : X 관내 업체", "제안사 자격 : X에 본사가 있는 기업", "본점 소재지가 X인
업체만 제안서를 제출할 수 있음". For v7 an attachment's 시·도 count only when the 공고문 states no bidder-location restriction
of its own: the 입찰공고 sets the qualification, and an attachment cannot widen it (the rule region_restriction applies to
attachment 시·도).

공고문 clauses outside evaluation and document lists: a participation limit to firms that prove their
location ("X 소재 업체임을 증명하는 법인등기부등본을 제출한 업체에 한함"), a location field of the qualification section
("주사무소 소재지 : X", "소재지 : X"; a street address is one office, not a restriction), a restriction label whose value is a
region ("| 참가제한 | X 소재 업체 |", "제한사항 | 지역(X)"), an office clause with any firm noun in the qualification section
("X에 본사가 있는 기업"), a limit to "X 업체" ("X 업체만 참가 가능", "입찰참가 자격 : X 업체"), an exclusion of firms outside X
("X 외의 업체는 본 입찰에 참가할 수 없습니다"), invalidity for firms not located in X ("X에 소재하지 않는 업체의 입찰서는
무효"), a bid addressed to X's firms ("본 입찰은 X 지역업체 대상 입찰임") and a labelled list of who may bid ("투찰 가능 업체:
X 소재 업체").
"""
import re
from . import judge, region_clause_forms as rf, rtd_region_clause as rc

FIRM = rf.FIRM
_REGION = r'(?:\[(?:등록)?지역:[^\]\n]{1,60}\]|\[수요기관\([^\]\n]{1,40}\]|' + rf._SIDO + r'|' + rf._N + r')'
REGIONS = _REGION + r'(?:\s*(?:[,，·ㆍ/]|및|또는|와|과)?\s*' + _REGION + r')*'

# ---- attachments (a value runs to the next table cell; an anonymised token "[지역:r1|단위=기초|광역=경기도]" is one piece of it)
QUAL_CTX = re.compile(rf.QUAL_WORD.pattern + r'|참\s*여\s*자\s*격|자\s*격\s*요\s*건|제\s*안\s*(?:사|자|업\s*체)?\s*자\s*격'
                      r'|' + FIRM + r'\s*만[^.。]{0,20}?(?:제\s*출|입\s*찰|참\s*가|참\s*여|응\s*찰|투\s*찰)\s*(?:할|이)\s*(?:수\s*있|가\s*능)')
LABELLED_LOCATED = re.compile(r'(?:참\s*가|참\s*여|입\s*찰\s*참\s*가)\s*자\s*격\s*(?:요\s*건\s*)?[:：|｜]\s*(?:\[[^\]\n]{1,60}\]|[^|｜\n\[])*?'
                              r'(?:소\s*재\s*(?:하\s*는\s*|한\s*)?|관\s*내\s*(?:소\s*재\s*)?|지\s*역\s*(?:내\s*)?)' + FIRM)
AFTER_AWARD = re.compile(r'(?:낙\s*찰|선\s*정|계\s*약\s*체\s*결)\s*(?:된\s*후|후|이\s*후)|지\s*역\s*제\s*한[^.。]{0,8}(?:없|않)')


# "X에 본사가 있는 기업": the office verb with any firm noun, 기업 included.
HELD_ANY = re.compile(r'(?:본\s*점|본\s*사|주\s*된\s*(?:영\s*업\s*소|사\s*무\s*소)|주\s*사\s*무\s*소|사\s*업\s*장(?!\s*소))[^.。]{0,40}?(?:이|가|을|를)?\s*'
                      r'(?:있\s*는|소\s*재\s*한|소\s*재\s*하\s*는|둔|두\s*고\s*있\s*는)\s*(?:업\s*체|기\s*업|사\s*업\s*자|법\s*인|자)(?=$|[^가-힣]|[은는이가을를의에로만])')


def _held_office(text):
    return bool(HELD_ANY.search(text) or rc.OFFICE.search(text) and (judge.BID_LOCATION.search(text) or rc.MUST.search(text)
                                                                     or rc.BIDDER_HELD.search(text) or rc.OFFICE_HELD.search(text)))


def _usable(text):
    return not (rc.FACILITY.search(text) or AFTER_AWARD.search(text) or rf.excluded(text))


def attach_clauses(b):
    for ln in b.notice.lines:
        if ln.doc_type == '공고문' or not ln.text.strip():
            continue
        text = rc.own_office_clause(b, ln)
        if not (ln.sec == 'QUAL' or QUAL_CTX.search(text)):
            continue
        if not (_held_office(text) or LABELLED_LOCATED.search(text)) or not _usable(text):
            continue
        g = rf.geography(text)
        if g['sido'] or g['basic'] or judge.orderer_level(text):
            yield ln, g, text


def notice_states_region(b):
    """The 공고문 itself states a bidder-location restriction (the qualification the 입찰공고 sets)."""
    lines, _, _ = judge.region_restriction(b)
    if any(ln.doc_type == '공고문' for ln in lines):
        return True
    return any(ln.doc_type == '공고문' for ln, _, _ in rc.clauses(b)) or any(ln.doc_type == '공고문' for ln, _, _ in rf.clauses(b))


# ---- 공고문 forms
PROOF = re.compile(r'(?:소\s*재|본\s*점|본\s*사|주\s*된\s*영\s*업\s*소|사\s*업\s*장)[^.。]{0,30}?' + FIRM + r'\s*임\s*을\s*(?:증\s*명|입\s*증|확\s*인|증\s*빙)'
                   r'[^.。]{0,50}?' + FIRM + r'\s*(?:에\s*한|만|(?:으\s*)?로\s*(?:제\s*한|한\s*정))')
HEAD_FIELD = re.compile(r'^\W*(?:[가-하]\s*[.)]\s*|\d{1,2}\s*[.)]\s*|[①-⑳]\s*)?(?:(?:본\s*점|본\s*사|주\s*사\s*무\s*소|주\s*된\s*(?:영\s*업\s*소|사\s*무\s*소))'
                        r'\s*(?:의\s*)?)?소\s*재\s*지\s*[:：]\s*(?P<val>(?:\[[^\]\n]{1,60}\]|[^|｜\n\[]){1,80})')
FIRM_ONLY = re.compile(REGIONS + r'\s*(?:지\s*역\s*)?(?:내\s*)?(?:업\s*체|기\s*업|사\s*업\s*자|법\s*인)\s*(?:만|에\s*한|(?:으\s*)?로\s*(?:제\s*한|한\s*정))')
LABEL_FIRM = re.compile(r'(?:입\s*찰\s*)?참\s*(?:가|여)\s*자\s*격\s*(?:요\s*건\s*)?[:：|｜]\s*' + REGIONS + r'\s*(?:지\s*역\s*)?(?:업\s*체|기\s*업|사\s*업\s*자|법\s*인)\s*[.。|｜]?\s*$')
OUTSIDE_FIRM = re.compile(REGIONS + r'\s*(?:지\s*역\s*)?(?:외|이\s*외|밖)\s*(?:의|에\s*소\s*재\s*한|에\s*있\s*는)?\s*' + FIRM + r'[^.。]{0,40}?'
                          r'(?:참\s*가\s*할\s*수\s*없|참\s*여\s*할\s*수\s*없|입\s*찰\s*할\s*수\s*없|무\s*효|불\s*가|제\s*외|허\s*용\s*(?:하\s*지\s*않|되\s*지\s*않))')
NOT_LOCATED = re.compile(r'(?:에|내\s*에|관\s*내\s*에)\s*(?:본\s*점\s*(?:이|을)?\s*)?(?:소\s*재\s*하\s*지|두\s*지|있\s*지)\s*(?:않|아\s*니\s*하)\s*(?:는|은|한)\s*' + FIRM
                         + r'[^.。]{0,40}?(?:무\s*효|참\s*가\s*할\s*수\s*없|참\s*여\s*할\s*수\s*없|불\s*가|제\s*외|자\s*격\s*이\s*없)')
TARGET = re.compile(REGIONS + r'\s*(?:지\s*역\s*)?(?:소\s*재\s*)?(?:업\s*체|기\s*업|사\s*업\s*자)\s*(?:를|을)?\s*대\s*상\s*(?:으\s*로\s*(?:하\s*는|한)\s*)?'
                    r'(?:입\s*찰|제\s*한|공\s*고|경\s*쟁)')
# A participation-restriction label whose value is a region ("| 참가제한 | X 소재 업체 |", "제한사항 | 지역(X)").
RESTRICT_LABEL = re.compile(r'(?:(?:입\s*찰\s*)?참\s*(?:가|여)\s*제\s*한|제\s*한\s*(?:사\s*항|조\s*건|내\s*용))\s*[:：|｜]\s*(?:지\s*역\s*[(（]\s*)?'
                            r'(?P<val>(?:\[[^\]\n]{1,60}\]|[^|｜\n\[()（）]){1,80})')
# A street address ("[상세주소]", 번지, 로·길 numbers) names one office, not a region bidders must be in.
ADDRESS = re.compile(r'\[상세주소\]|번\s*지|\d+\s*(?:로|길)(?![가-힣])|\(\s*우\s*\d')
ABLE_LABEL = re.compile(r'(?:투\s*찰|입\s*찰|참\s*가|참\s*여|제\s*안)\s*(?:가\s*능|허\s*용)\s*(?:업\s*체|자|대\s*상|지\s*역)\s*[:：|｜]\s*(?P<val>(?:\[[^\]\n]{1,60}\]|[^|｜\n\[]){1,80})')


def _value(text, pattern):
    for m in pattern.finditer(text):
        g = rf._region_value(m.group('val'))
        if g is not None:
            return g
    return None


def notice_clauses(b):
    for ln in b.notice.lines:
        if ln.doc_type != '공고문' or ln.sec in ('EVAL', 'DOCS') or not ln.text.strip():
            continue
        text = rc.own_office_clause(b, ln)
        g = None
        if HEAD_FIELD.search(ln.text) and ln.sec == 'QUAL' and not ADDRESS.search(ln.text):
            g = _value(ln.text, HEAD_FIELD)
        if g is None and RESTRICT_LABEL.search(ln.text):
            g = _value(ln.text, RESTRICT_LABEL)
        if g is None and HELD_ANY.search(text) and judge.qual_section(ln, b.notice) and _usable(text):
            g = rf.geography(text)
            if not (g['sido'] or g['basic'] or judge.orderer_level(text)):
                g = None
        if g is None and ABLE_LABEL.search(text):
            g = _value(text, ABLE_LABEL)
        if g is None and (PROOF.search(text) or FIRM_ONLY.search(text) or LABEL_FIRM.search(text) or OUTSIDE_FIRM.search(text)
                          or NOT_LOCATED.search(text) or TARGET.search(text)) and _usable(text):
            g = rf.geography(text)
            if not (g['sido'] or g['basic'] or judge.orderer_level(text)):
                g = None
        if g is not None:
            yield ln, g, text


def _clauses(b):
    yield from ((ln, g, t, False) for ln, g, t in notice_clauses(b))
    yield from ((ln, g, t, True) for ln, g, t in attach_clauses(b))


def v5(b, hit):
    if hit is not None or b.meta.P is None or b.meta.P < b.meta.T_hi:
        return hit
    return next((ln for ln, _, _, _ in _clauses(b)), None)


def v6(b, hit):
    if hit is not None or b.meta.P is None or b.meta.P >= b.meta.T_lo or b.meta.local_private:
        return hit
    return next((ln for ln, g, t, _ in _clauses(b) if g['basic'] or judge.orderer_level(t) == 'basic'), None)


def v7(b, hit):
    if hit is not None or b.meta.P is None or b.meta.P >= b.meta.T_lo or b.meta.local_private:
        return hit
    stated = None
    for ln, g, _, attach in _clauses(b):
        if len(g['sido']) < 2 or judge.switches.V7_SITE_SPAN and judge.x7_site_spans(b, g['sido']):
            continue
        if attach:
            stated = notice_states_region(b) if stated is None else stated
            if stated:
                continue
        return ln
    return None
