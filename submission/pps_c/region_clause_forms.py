"""Bidder-location restrictions in further shapes (switch REGION_CLAUSE_FORMS).

A restriction of who may bid by where the bidder is, written as a labelled field ("지역제한: 경기도", "| 참가지역 | 부산광역시 |",
"입찰참가자격 제한: 지역(대구광역시)", "제한경쟁(지역제한 - 경기도)"), as a qualification item "X 소재 업체" / "X 소재 업체에
한함", as an exclusion of bidders outside X ("X 외 지역 업체는 입찰에 참가할 수 없음"), with a seat, residence or registration
predicate ("소재지가 X인 업체", "X에 주소를 둔 자", "X에 사업자등록이 되어 있는 업체", "입찰참가자는 X 내에 소재하여야"),
naming the orderer's own 시·군·구 ("당해 시 관내") or naming 시·군 without the anonymised token ("여주, 양평 지역 업체"). Read in
the 공고문 outside evaluation and document lists, and in an attachment line that states the participation qualification.
"""
import re
from . import judge, regions, rtd_region_clause as rc

SEP = r'\s*(?:[:：|｜]|[-–]\s|\s(?=\S))\s*'
# A value runs to the next table cell; an anonymised token ("[지역:r1|단위=기초|광역=경기도]") is one piece of it.
VALUE = r'(?P<val>(?:\[[^\]\n]{1,60}\]|[^|｜\n\[]){1,90})'
LABEL = re.compile(r'(?:입\s*찰\s*)?참\s*가\s*(?:가\s*능\s*|자\s*격\s*)?지\s*역|지\s*역\s*제\s*한|제\s*한\s*지\s*역|소\s*재\s*지\s*(?:제\s*한|요\s*건)')
LABEL_FIELD = re.compile(r'(?P<label>' + LABEL.pattern + r')(?:\s*여\s*부)?' + SEP + VALUE)
PAREN_FIELD = re.compile(r'(?:제\s*한\s*[:：]?\s*지\s*역|지\s*역\s*제\s*한)\s*[(（]\s*(?:[-–:：]\s*)?(?P<val>(?:\[[^\]\n]{1,60}\]|[^)）\n\[]){1,70})[)）]')
NONE_VALUE = re.compile(r'^\W*(?:없\s*음|없\s*습|해\s*당\s*(?:사\s*항\s*)?없|미\s*적\s*용|미\s*해\s*당|무\b|무\s*$|N\b|전\s*국|제\s*한\s*(?:없|하\s*지)|비\s*대\s*상|-\s*$)')
FIRM = r'(?:업\s*체|기\s*업|사\s*업\s*자|법\s*인|자|개\s*인)'
# "X 소재 업체에 한함", "X 소재 업체로 참가자격을 제한", "X 소재 업체만"; "X 지역 업체에 한함"; "동남권(부산, 울산, 경남) 업체에 한함".
LIMITED = re.compile(r'(?:소\s*재|지\s*역|권|[)）])\s*' + FIRM + r'\s*(?:[(（][^)）]{0,20}[)）]\s*)?'
                     r'(?:에\s*한|만|(?:으\s*)?로\s*(?:제\s*한|한\s*정|(?:입\s*찰\s*)?참\s*가\s*자\s*격\s*을\s*제\s*한))')
# A qualification item that is only "X 소재 업체" (a list entry of the qualification section).
LOCATED_ITEM = re.compile(r'소\s*재\s*(?:하\s*는\s*|한\s*)?' + FIRM + r'\s*(?:[(（][^)）]{0,20}[)）])?\s*[.。]?\s*$')
OUTSIDE = re.compile(r'(?:(?:외|이\s*외|밖)(?:의)?|(?<![가-힣])타)\s*(?:지\s*역|시\s*[·ㆍ]?\s*도|시\s*[·ㆍ]?\s*군)[^.。]{0,25}?' + FIRM + r'[^.。]{0,40}?'
                     r'(?:참\s*가\s*할\s*수\s*없|참\s*여\s*할\s*수\s*없|무\s*효|불\s*가|제\s*외|허\s*용\s*하\s*지\s*않|허\s*용\s*되\s*지\s*않)')
RESIDENT = re.compile(r'주\s*소\s*(?:지\s*)?를\s*(?:두\s*고\s*있\s*는|둔)\s*' + FIRM +
                      r'|사\s*업\s*자\s*등\s*록\s*(?:이\s*)?(?:되\s*어\s*있\s*는|된|을\s*한|한)\s*' + FIRM +
                      r'|소\s*재\s*지\s*를[^.。]{1,40}?(?:으\s*)?로\s*등\s*록\s*(?:한|된)\s*' + FIRM +
                      r'|소\s*재\s*지\s*(?:가|는)\s*[^.。]{1,60}?(?:인|이\s*어\s*야\s*하\s*는|에\s*있\s*는)\s*' + FIRM +
                      r'|(?:입\s*찰\s*참\s*가\s*자|참\s*가\s*자|참\s*가\s*업\s*체|입\s*찰\s*자)\s*(?:는|은)[^.。]{1,50}?(?:에|내\s*에|안\s*에)\s*소\s*재\s*(?:하\s*여\s*야|해\s*야)')
OWN_UNIT = re.compile(r'(?:당\s*해|본|우\s*리|해\s*당)\s*(?:시|군|구)\s*(?:[(（][^)）]{0,4}[)）]\s*)?(?:관\s*내|내|에\s*(?:본\s*점|주\s*소|주\s*된|사\s*업\s*장|소\s*재))')
# 시·군 names the anonymiser left raw count only where they cannot be ordinary words (예산, 강화, 진도, 부여 …): after a 시·도
# name ("경기도 이천인 업체"), as a list of such names ("여주, 양평 지역 업체", "화성, 오산"), or right before 지역 업체 / 소재 /
# 관내 ("여주 소재 업체").
_N = r'(?:' + '|'.join(sorted(judge.X7_BASIC_UNITS, key=lambda x: -len(x))) + r')(?:시|군)?'
_SIDO = '|'.join(sorted({a for names in regions.SIDO.values() for a in names}, key=lambda x: -len(x)))
BARE_BASIC = re.compile(r'(?<![가-힣])(?:' + _SIDO + r')\s+' + _N + r'(?=\s*(?:$|[,，·ㆍ/)）]|인\s|인$|에\s|에$|소\s*재|관\s*내|지\s*역|내\b))'
                        r'|(?<![가-힣])' + _N + r'(?:\s*(?:[,，·ㆍ/]|및|또는)\s*' + _N + r')+(?=\s*(?:지\s*역|소\s*재|관\s*내|$))'
                        r'|(?<![가-힣])' + _N + r'\s*(?:지\s*역\s*(?:업\s*체|사\s*업\s*자)|소\s*재\s*(?:업\s*체|사\s*업\s*자|하\s*는|한)|관\s*내\s*(?:업\s*체|사\s*업\s*자|에))')
BARE_VALUE = re.compile(r'^\W*' + _N + r'(?:\s*(?:[,，·ㆍ/]|및|또는)\s*' + _N + r')*\W*$')
LOCATION_WORD = re.compile(r'소\s*재|관\s*내|지\s*역\s*업\s*체|지\s*역\s*제\s*한|참\s*가\s*(?:가\s*능\s*)?지\s*역|본\s*점|본\s*사|주\s*된\s*영\s*업\s*소'
                           r'|주\s*사\s*무\s*소|사\s*업\s*장|소\s*재\s*지')
QUAL_LABEL = re.compile(r'(?:입\s*찰\s*)?참\s*가\s*자\s*격\s*[:：|]')
QUAL_WORD = re.compile(r'참\s*가\s*자\s*격|입\s*찰\s*참\s*가|참\s*여\s*(?:업\s*체|자\s*격|가\s*능)|참\s*가\s*할\s*수|참\s*가\s*가\s*능|한\s*정|한\s*함|제\s*한')
# A place of delivery, work, an event or a contact is not where the bidder must be.
NOT_BIDDER = re.compile(r'납\s*품|납\s*기|장\s*소\s*[:：/|]|설\s*치\s*장\s*소|수\s*행\s*장\s*소|이\s*행\s*장\s*소|현\s*장|행\s*사|개\s*최|배\s*송|문\s*의|연\s*락|주\s*소\s*[:：]'
                        r'|제\s*출\s*(?:장\s*소|처)|접\s*수\s*(?:장\s*소|처)|우\s*편|방\s*문|사\s*업\s*(?:대\s*상\s*)?지|대\s*상\s*지|과\s*업\s*(?:대\s*상|지\s*역|장\s*소)')


def geography(text):
    geo = rc.geography(text)
    if BARE_BASIC.search(text):
        geo['basic'].add(('bare-basic', ''))
    if OWN_UNIT.search(text):
        geo['basic'].add(('own-unit', ''))
    return geo


def excluded(text):
    return bool(rc.FACILITY.search(text) or rc.JOINT_MEMBER.search(text) or rc.POST_AWARD.search(text)
                or judge.REGION_NONE.search(text) or judge.JV_PARTNER3.search(text) or judge.X7_OFFICE_DUTY.search(text)
                or judge.X7_BONUS.search(text) or NOT_BIDDER.search(text) or judge.X7_WORK_SITE.search(text))


def _region_value(v):
    v = v.strip()
    if not v or NONE_VALUE.search(v) or excluded(v):
        return None
    g = geography(v)
    if BARE_VALUE.search(v):
        g['basic'].add(('bare-basic', ''))
    return g if (g['sido'] or g['basic'] or judge.orderer_level(v) or OWN_UNIT.search(v)) else None


def field(text):
    """A labelled region field judged on its own value: the other cells of a table row do not decide it."""
    for m in list(LABEL_FIELD.finditer(text)) + list(PAREN_FIELD.finditer(text)):
        g = _region_value(m.group('val'))
        if g is not None:
            return g
    return None


def own_clause(b, ln):
    return rc.own_office_clause(b, ln)


def clauses(b):
    for ln in b.notice.lines:
        attach = ln.doc_type != '공고문'
        if not attach and ln.sec in ('EVAL', 'DOCS'):
            continue
        text = own_clause(b, ln)
        if attach and not (ln.sec == 'QUAL' or QUAL_WORD.search(text)):
            continue
        if LABEL.search(text) or PAREN_FIELD.search(text):
            g = field(ln.text) or field(text)
            if g is not None:
                yield ln, g, text
                continue
        item = LOCATED_ITEM.search(text) and (judge.qual_section(ln, b.notice) or QUAL_LABEL.search(text))
        if not (LIMITED.search(text) or OUTSIDE.search(text) or RESIDENT.search(text) or OWN_UNIT.search(text) or item
                or BARE_BASIC.search(text) and (LOCATION_WORD.search(text) or QUAL_WORD.search(text))):
            continue
        if excluded(text):
            continue
        g = geography(text)
        if g['sido'] or g['basic'] or judge.orderer_level(text):
            yield ln, g, text


def v5(b, hit):
    if hit is not None or b.meta.P is None or b.meta.P < b.meta.T_hi:
        return hit
    return next((ln for ln, _, _ in clauses(b)), None)


def v6(b, hit):
    if hit is not None or b.meta.P is None or b.meta.P >= b.meta.T_lo or b.meta.local_private:
        return hit
    return next((ln for ln, g, t in clauses(b) if g['basic'] or judge.orderer_level(t) == 'basic'), None)


def v7(b, hit):
    if hit is not None or b.meta.P is None or b.meta.P >= b.meta.T_lo or b.meta.local_private:
        return hit
    return next((ln for ln, g, _ in clauses(b) if len(g['sido']) >= 2
                 and not (judge.switches.V7_SITE_SPAN and judge.x7_site_spans(b, g['sido']))), None)
