"""Item judgments from the fact bundle (the item table, REBUILD_BASIS §2; organizer notices).

Each rule returns the evidence line (or True for absence items) when the item is violated, else None.
"""
from __future__ import annotations

import re

from . import amounts, catalog, dates, record, regions, switches
from .v24 import compare as v24p_compare
from .meta import EOK, NOTICE_AMOUNT

ITEMS = [f'v{k}' for k in range(1, 25)]
ABSENCE = ('v10', 'v11', 'v16', 'v18', 'v20')


QUAL_SECTIONS = ('QUAL', 'TOP', 'OVERVIEW')
NOT_QUAL_SECTIONS = ('EVAL', 'DOCS', 'NOTE')
# "입찰방법: 제한경쟁(소기업·소상공인)" style summaries state the procedure, not a qualification clause (talkboard,
# DEV-039: a bidding-method display does not count as the participation restriction).
METHOD_SUMMARY = re.compile(r'^\W*(입\s*찰\s*(방\s*법|방\s*식)|계\s*약\s*(방\s*법|방\s*식)|경\s*쟁\s*(형\s*태|방\s*법)|입\s*찰\s*및\s*계\s*약\s*(방\s*법|방\s*식))\s*[:：|‣]'
                            r'|^\W*\(?\s*(총액|단가)?\s*(입찰)?\s*,?\s*(제한|일반|지명)\s*경쟁\s*(입찰)?\s*[\(（][^)）]*[\)）]\s*,')


def qual_section(ln, notice=None):
    """A participation qualification is what the notice's qualification section (or its summary table) states;
    evaluation criteria, document lists and cautions elsewhere are not (the model confuses them). A notice without a
    detectable qualification section falls back to its 공고문 lines outside evaluation, document and caution sections."""
    if METHOD_SUMMARY.search(ln.text):
        return False
    if ln.sec in QUAL_SECTIONS and (ln.sec == 'QUAL' or ln.doc_type == '공고문'):
        return True
    return notice is not None and not notice.has_qual and ln.doc_type == '공고문' and ln.sec not in NOT_QUAL_SECTIONS


def lines_where(b, fam, section=False, **want):
    out = []
    for ln in b.cands.get(fam, []):
        if METHOD_SUMMARY.search(ln.text):
            continue
        if section and not qual_section(ln, b.notice):
            continue
        r = b.read(fam, ln)
        if all((r.get(k) in v) if isinstance(v, (tuple, set, list)) else r.get(k) == v for k, v in want.items()):
            out.append(ln)
    return out


# ---------------------------------------------------------------- region and performance
# The orderer's own jurisdiction named by its anonymised token ("본점 소재지가 [수요기관(기초자치단체)] 내에 있는 업체") is a
# bidder-location restriction at that jurisdiction's level (dev DEV-071: 기초 → v6, v8).
ORDERER_JURISDICTION = re.compile(r'(본\s*점|주\s*된\s*(영\s*업\s*소|사\s*무\s*소)|사\s*업\s*장|소\s*재\s*지)[^.。]{0,120}'
                                  r'\[수요기관\((?P<level>기초자치단체|광역자치단체)\)[^\]]*\]\s*(관\s*내|내|안|관\s*할)')


# A bidder that must be, or own, a facility located or registered in a named 시·군·구 is restricted by location at that
# level (dev DEV-066: "여주, 양평 관내에 등록된 청소년수련시설로서").
FACILITY_AT = re.compile(r'(소\s*재|등\s*록|위\s*치|설\s*치)\s*(한|된|하고\s*있는|되어\s*있는)[^.。]{0,40}(시\s*설|센\s*터|사\s*업\s*장|공\s*장|창\s*고|차\s*고\s*지|영\s*업\s*장|매\s*장|점\s*포)'
                         r'[^.。]{0,30}(보\s*유|갖\s*춘|로\s*서|인\s*자|이어야|운\s*영\s*하)')


# A location restriction names a place or a locating verb; a line with neither (a 실적 clause) is not one.
LOCATION_CUE = re.compile(r'관\s*내|소\s*재|둔\s*(자|업\s*체|사\s*업\s*자)|두\s*고|위\s*치|지\s*역\s*(제\s*한|업\s*체|에|내)|\[수요기관')


def orderer_level(text):
    m = ORDERER_JURISDICTION.search(text)
    if m is not None:
        return 'basic' if m.group('level') == '기초자치단체' else 'sido'
    if FACILITY_AT.search(text) and regions.mentions(text)['basic']:
        return 'basic'
    return None


# Audit R2-A: "참가지역제한 : 없음" states that there is none (v5–v8).
REGION_NONE = re.compile(r'지\s*역\s*제\s*한\s*(여\s*부)?\s*[:：|]?\s*(없\s*음|없\s*습\s*니\s*다|미\s*적\s*용|해\s*당\s*(사\s*항\s*)?없\s*음|N\b)'
                         r'|지\s*역\s*제\s*한\s*(을\s*)?(두\s*지\s*않|하\s*지\s*않)')
# Audit R3-C: the bidder's location with a firm subject (소재한 / 소재지가 / 에 둔 … 업체·자).
BID_LOCATION = re.compile(r'(소\s*재\s*(지|한|하는|하고)|에\s*(둔|두\s*고))[^.。]{0,60}?(업\s*체|사\s*업\s*자|법\s*인|자)(?![가-힣])')


def region_restriction(b):
    """(restriction lines, 시·도 set, 기초 set) of bidder-location restrictions stated as qualification."""
    lines = [ln for ln in lines_where(b, 'region', section=True, 역할='참가자격', 대상='입찰자 소재지 제한')
             if LOCATION_CUE.search(ln.text) or regions.mentions(ln.text)['sido'] or regions.mentions(ln.text)['basic']]
    known = {ln.i for ln in lines}
    lines = sorted(lines + [ln for ln in b.notice.lines if ln.i not in known and qual_section(ln, b.notice)
                            and orderer_level(ln.text)], key=lambda ln: ln.i)
    if switches.AUDIT_FIXES2:
        lines = [ln for ln in lines if not REGION_NONE.search(ln.text)]
    if switches.AUDIT_FIXES3:
        # Audit R3-C: a 공고문 BID-section clause the model read as the bidder-location restriction is one when it names the
        # bidder's location with a firm subject ("…사업장의 소재지)를 계속 경상남도 또는 대구광역시에 둔 업체").
        known = {ln.i for ln in lines}
        lines = sorted(lines + [ln for ln in lines_where(b, 'region', 역할='참가자격', 대상='입찰자 소재지 제한')
                                if ln.i not in known and ln.doc_type == '공고문' and ln.sec == 'BID' and BID_LOCATION.search(ln.text)],
                       key=lambda ln: ln.i)
    # Audit R2-A: when the 공고문 states its own bidder-location restriction, an attachment's 시·도 do not add to it (the
    # 입찰공고 sets the qualification); an attachment's 기초 units within it still count.
    sido_lines = ([ln for ln in lines if ln.doc_type == '공고문'] or lines) if switches.AUDIT_FIXES2 else lines
    sido, basic = set(), set()
    for ln in lines:
        if switches.AUDIT_FIXES3 and JV_PARTNER3.search(clause_text(b.notice, ln)):
            continue     # audit R3-C: a partner's location in a joint or shared performance ("…허가를 받은 자와 분담이행을 허용")
        # Audit B: a layout line break is no clause break ("…소재지가 경상북도, / 대구광역시인 업체").
        m = regions.mentions(clause_text(b.notice, ln) if switches.AUDIT_FIXES else ln.text)
        if ln in sido_lines:
            sido |= m['sido']
        basic |= m['basic']
    if lines and not sido and not basic and b.meta.region_sido:
        sido = set(b.meta.region_sido)
    # Audit B: a text parse that finds part of a registered multi-시·도 restriction reads the registered one (the bid system
    # enforces it; v5 already relies on it).
    if switches.AUDIT_FIXES and lines and sido and len(b.meta.region_sido) >= 2 and sido <= set(b.meta.region_sido):
        sido = set(b.meta.region_sido)
    return lines, sido, basic


# A sentence whose subject is the bidder and whose predicate requires holding the record ("…실적이 있는 업체", "…실적을
# 보유하여야") is a participation qualification wherever the 공고문 states it, outside evaluation and document lists.
PERF_BIDDER_REQ = re.compile(r'(실\s*적|경\s*험|이\s*력)[^.。]{0,40}(있는|보유한|보유하고\s*있는|갖춘|가진)\s*(업\s*체|자|법인|사업자)(?!\s*명)'
                             r'|(실\s*적|경\s*험)[^.。]{0,30}(있어야|보유하여야|보유해야|갖추어야)|(수행|납품|이행|완료|준공)\s*(한|하였던)\s*(업\s*체|자)(?!\s*명)')
# A requirement predicate of any form ("…실적에 한함", "실적증명서", "증빙", "이상"); a bare header ("… 참여실적") has none.
PERF_PREDICATE = re.compile(r'한\s*함|한\s*정|이\s*상|있\s*어\s*야|보\s*유|제\s*출|소\s*지|증\s*빙|증\s*명|갖\s*춘|있\s*는|수\s*행\s*한|납\s*품\s*한|요\s*구')
PERF_FORMISH = re.compile(r'서\s*식|양\s*식|작\s*성|기\s*재|\|\s*\d|배\s*점|점\s*수|평\s*가|심\s*사|대\s*체|갈\s*음|^\W*[\(（]')
PERF_FORMISH_ATTACH = re.compile(PERF_FORMISH.pattern.replace(r'|^\W*[\(（]', ''))


# A note on how a record is proved or substituted, or on leaving a bidder out of evaluation, is not the record requirement.
PERF_NOTE = re.compile(r'대\s*체|갈\s*음|로\s*만\s*증\s*빙|평\s*가\s*대\s*상\s*(?:자\s*)?(?:에\s*서\s*)?제\s*외')
# Audit R2-A: evaluation text is no participation limit (시행령 제21조 limits vs 제42조 적격심사·정량평가 factors): a 적격심사
# table item ("① 이행실적의 당해 용역규모", "동등이상 용역 :"), a 정량평가 definition ("※ 실적은 …으로 하되"), the 공동이행 sharing
# note ("참가자격에 따른 실적은 수급체 중 1개사만"), an accident exclusion ("인사사고 업체는 … 무사고 운행실적 확인서"), a bare
# "실적" table header ("실적 / 없음"), and an item under a short 심사기준/별표/정량평가 heading.
EVAL_PERF = re.compile(r'이\s*행\s*실\s*적\s*의\s*당\s*해|동\s*등\s*이\s*상\s*용\s*역\s*[:：]|유\s*사\s*용\s*역\s*[:：]|실\s*적\s*은[^.。]{0,40}으\s*로\s*하\s*되'
                       r'|수\s*급\s*체\s*중\s*\d\s*개\s*사\s*만|^\W*[\(（]?\s*정\s*량\s*평\s*가')
EVAL_HEAD = re.compile(r'^\W*(?:\d+\s*[\.\)]|[가-하]\s*[\.\)]|\(\s*\d+\s*\)|[①-⑳])?\s*[\[\(（<【]?\s*(심\s*사\s*기\s*준|평\s*가\s*기\s*준|정\s*량\s*평\s*가'
                       r'|평\s*가\s*항\s*목|배\s*점\s*한\s*도|별\s*표\s*\d|적\s*격\s*심\s*사\s*(세\s*부\s*)?기\s*준)')
ACCIDENT = re.compile(r'사\s*고[^.。]{0,30}(업\s*체|자)\s*(는|은)[^.。]{0,30}(없|불\s*가|제\s*외)|무\s*사\s*고\s*(운\s*행\s*)?실\s*적')
BARE_PERF = re.compile(r'^\W*실\s*적\W*$|실\s*적\s*[/|│]\s*없\s*음|실\s*적\s*\|\s*\|?\s*없\s*음')
# Audit R3-A (same basis): a two-column layout merges the heading "바. 적격심사 시 적용할 이행실적 평가기준은 다음과 같습니다." with
# the other column, so a line opening with 적격심사 … 평가·심사기준 heads evaluation text at any length; the 정량평가 definition
# "실적은 … 으로 하되" spans its sentence.
EVAL_HEAD3 = re.compile(r'^\W*(?:\d+\s*[\.\)]|[가-하]\s*[\.\)]|\(\s*\d+\s*\)|[①-⑳])?\s*[\[\(（<【]?\s*적\s*격\s*심\s*사[^.。]{0,30}?(평\s*가|심\s*사)\s*기\s*준'
                        r'\s*(은|는|[:：\]）\)】>]|$)')
EVAL_PERF3 = re.compile(r'실\s*적\s*은[^.。]*?으\s*로\s*하\s*되')


def evaluation_context(b, ln):
    if EVAL_PERF.search(ln.text) or ACCIDENT.search(ln.text) or BARE_PERF.search(ln.text):
        return True
    if switches.AUDIT_FIXES3 and EVAL_PERF3.search(ln.text):
        return True
    return any(len(x.text.strip()) <= 40 and EVAL_HEAD.search(x.text) or switches.AUDIT_FIXES3 and EVAL_HEAD3.search(x.text)
               for x in b.notice.window(ln.i, 6, 0)[:-1])


# Audit R3-A (국가계약법 시행령 제21조 limits the bidder's own 실적): a note on how a person's 경력 is recognised or proved
# ("* 경력 : … 용역사업 참여실적 기준으로 인정 … 경력증명서로 증빙") is a staffing condition; a sentence citing 판로지원법 시행령
# 제2조의3 as the reason for the procurement method ("…이행 경험 등이 필요한 점을 감안하여 … 제2조의3 … 적용") is no
# qualification; a note under a document-list entry ("마. 사업실적증명서 1부" / "* … 계약실적으로 … 직인 날인") describes that
# document unless it has a bidder predicate; lines after a form-annex heading ("[별지 제10호 서식]") up to the next top-level
# heading are form content.
STAFF_CAREER = re.compile(r'경\s*력\s*(을|이)?\s*(증\s*명|증\s*빙|인\s*정)|경\s*력\s*증\s*명\s*서|^\W*경\s*력\s*[:：]')
FIRM_WORD = re.compile(r'업\s*체|법\s*인|사\s*업\s*자|회\s*사')
EXCEPTION_REASON = re.compile(r'(감\s*안|고\s*려)\s*하\s*여[^.。]{0,150}?(제\s*2\s*조\s*의\s*3|우\s*선\s*조\s*달\s*계\s*약\s*에\s*대\s*한\s*예\s*외)')
NOTE_MARK = re.compile(r'^\s*[*※\-]')
FORM_HEAD = re.compile(r'^\W*(별\s*지|별\s*첨)\s*(제\s*)?\d+\s*(호|-\s*\d+)?\s*(서\s*식|양\s*식)?\W*$|^\W*서\s*식\s*(제\s*)?\d+\s*호?\W*$')
TOP_HEAD = re.compile(r'^\s*(\d{1,2}\s*\.(?!\d)|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]+\s*[\.\)]?|[IVX]{1,4}\s*\.)')


def document_note(b, ln):
    from .families import DOC_LIST
    if not NOTE_MARK.match(ln.text) or PERF_BIDDER_REQ.search(ln.text):
        return False
    for x in reversed(b.notice.window(ln.i, 6, 0)[:-1]):
        t = x.text.strip()
        if t and not NOTE_MARK.match(t):
            return bool(DOC_LIST.search(t))
    return False


def in_form_annex(b, ln):
    if ln.doc_type != '공고문':
        return False
    for x in reversed(b.notice.lines[:ln.i]):
        if x.doc != ln.doc or x.head == x.i and TOP_HEAD.match(x.text):
            return False
        if FORM_HEAD.match(x.text):
            return True
    return False


def not_record_limit(b, ln):
    return bool(STAFF_CAREER.search(ln.text) and not FIRM_WORD.search(ln.text) or EXCEPTION_REASON.search(clause_text(b.notice, ln))
                or document_note(b, ln) or in_form_annex(b, ln))


def perf_lines(b):
    lines = [ln for ln in lines_where(b, 'perf', section=True, 역할='참가자격') if not PERF_NOTE.search(ln.text)]
    if switches.AUDIT_FIXES:
        # Audit A: an attachment's qualification section holds forms and headers ("… 참여실적" over a résumé form); there a
        # record requirement needs the bidder predicate and no form wording, as outside the section.
        formish = PERF_FORMISH_ATTACH if switches.AF_LIST_MARKER else PERF_FORMISH
        lines = [ln for ln in lines if ln.doc_type == '공고문' or not formish.search(ln.text)
                 and (PERF_BIDDER_REQ.search(ln.text) or PERF_PREDICATE.search(ln.text))]
    stated = [ln for ln in lines_where(b, 'perf', 역할='참가자격')
              if ln not in lines and ln.doc_type == '공고문' and ln.sec not in ('EVAL', 'DOCS')
              and PERF_BIDDER_REQ.search(ln.text) and not PERF_FORMISH.search(ln.text)]
    if switches.AUDIT_FIXES3:
        lines, stated = [ln for ln in lines if not not_record_limit(b, ln)], [ln for ln in stated if not not_record_limit(b, ln)]
    out = [ln for ln in lines + stated if not evaluation_context(b, ln)] if switches.AUDIT_FIXES2 else lines + stated
    if switches.PERF_UNREAD:
        known = {ln.i for ln in b.cands.get('perf', [])}
        out += [ln for ln in b.notice.lines if ln.i not in known and ln.doc_type == '공고문' and ln.sec == 'QUAL'
                and qual_section(ln, b.notice) and HELD_RECORD.search(ln.text) and not NOT_RECORD.search(ln.text)
                and not PERF_FORMISH.search(ln.text) and not PERF_NOTE.search(ln.text) and not METHOD_SUMMARY.search(ln.text)
                and not evaluation_context(b, ln) and not not_record_limit(b, ln)]
    return out


# Audit RD (switch PERF_UNREAD): a 공고문 qualification line stating a held record ("…제작 경험이 있는 업체", "…경험 보유",
# "…을 수행한 자") is a record requirement even when the perf family never selected it; registration or submission completion
# and anti-incumbent notes are none.
HELD_RECORD = re.compile(r'(실\s*적|경\s*험|이\s*력)[^.。]{0,40}(있는|보유한|보유하고\s*있는|갖춘|가진)\s*(업\s*체|자|법인|사업자|기관|단체)(?!\s*명)'
                         r'|(실\s*적|경\s*험|이\s*력)[^.。]{0,30}(있어야|보유하여야|보유해야|갖추어야)'
                         r'|(실\s*적|경\s*험)\s*(을\s*)?보\s*유(?!\s*(현\s*황|여\s*부))'
                         r'|(제\s*작|납\s*품|공\s*급|운\s*영|수\s*행)\s*(을|를)?\s*(수\s*행|완\s*료)?\s*(한|하였던)\s*(업\s*체|자)(?!\s*명)')
NOT_RECORD = re.compile(r'등\s*록|투\s*찰|제\s*출|신\s*청|연\s*속\s*수\s*행|참\s*여\s*불\s*가')


# Expert audit X2 (switch X2_RECORD_NOISE; runs/rebuild_c/transfer_20260925/audit/expert/X2/REPORT.md): 국가계약법 시행령
# 제21조 limits the bidder's own 실적. A record clause whose subject is a third party ("숙박업체는 … 실적이 우수하고"), a
# statute's definitional record (비영리민간단체지원법 제2조 공익활동실적) and a 신인도 line (an 적격심사 evaluation item; dev
# DEV-175 = 0) are no participation limit; v2 and v8.
X2_THIRD_PARTY = re.compile(r'^\W*(?:[가-하]\s*[.)]|\d+\s*[.)]|[①-⑳]|[-•◦○●])?\s*(숙\s*박\s*(업\s*체|시\s*설|업\s*소)|운\s*수\s*(업\s*체|회\s*사)'
                            r'|차\s*량\s*(업\s*체|회\s*사)|협\s*력\s*업\s*체|하\s*수\s*급\s*인|하\s*도\s*급\s*(업\s*체|자)|납\s*품\s*업\s*체'
                            r'|제\s*조\s*(사|업\s*체)|공\s*급\s*(사|업\s*체))\s*(는|은|의)\s')
X2_STATUTORY_RECORD = re.compile(r'공\s*익\s*활\s*동\s*실\s*적')


def x2_not_bidder_record(b, ln):
    return bool(X2_THIRD_PARTY.search(ln.text) or X2_THIRD_PARTY.search(clause_text(b.notice, ln))
                or X2_STATUTORY_RECORD.search(ln.text) or CREDIT_RATING.search(ln.text))


# Expert audit X2 §3.3 (switches X2_HELD_RECORD_X, X2_ATTACH_QUAL; X2_V8_ADDS for v8): a held record in firm-level wording
# read on the wrapped clause ("…교육 수행 경력을 보유한 기관 및 대학", "…대리업무를 수행한 소기업"), and a held-record line
# under an attachment's 참가자격 heading that is no staffing table, are record requirements the perf family never selected.
X2_FIRM_NOUN = r'[가-힣]{0,6}?(업\s*체|법\s*인|사\s*업\s*자|기\s*관|단\s*체|대\s*학|소\s*기\s*업|소\s*상\s*공\s*인|중\s*소\s*기\s*업|기\s*업|회\s*사)(?!\s*명)'
X2_HELD_RECORD = re.compile(HELD_RECORD.pattern
                            + r'|(실\s*적|경\s*험|이\s*력|경\s*력)[^.。]{0,40}(있는|보\s*유\s*한|보유하고\s*있는|갖춘|가진)\s*' + X2_FIRM_NOUN
                            + r'|(제\s*작|납\s*품|공\s*급|운\s*영|수\s*행|이\s*행|시\s*공|진\s*행)\s*(을|를)?\s*(수\s*행|완\s*료)?\s*(한|하였던)\s*' + X2_FIRM_NOUN)
X2_STAFF_CTX = re.compile(r'인\s*력|책\s*임\s*자|연\s*구\s*원|강\s*사|PM|팀\s*장|참\s*여\s*자|운\s*영\s*자|요\s*원|종\s*사\s*원|담\s*당\s*자|경\s*력\s*자'
                          r'|기\s*술\s*자|자\s*격\s*자|배\s*치|투\s*입|선\s*임|채\s*용|재\s*직|프\s*로\s*젝\s*트|학\s*위|자\s*격\s*증|전\s*문\s*가')
X2_QUAL_HEAD = re.compile(r'참\s*가\s*자\s*격|응\s*찰\s*자\s*격|입\s*찰\s*자\s*격|신\s*청\s*자\s*격|제\s*안\s*자\s*격|입\s*찰\s*업\s*체\s*자\s*격|자\s*격\s*요\s*건')
X2_STAFF_HEAD = re.compile(r'인\s*력|책\s*임\s*자|연\s*구\s*원|강\s*사|수\s*행\s*조\s*직|전\s*문\s*가|위\s*원|요\s*원|종\s*사\s*원')
X2_HEADING = re.compile(r'^\W*(\d{1,2}\s*[.)]|[ⅠⅡⅢⅣⅤ]+\s*[.)]?|제\s*\d+\s*[조장절])')


def x2_under_qual_heading(b, ln, back=20):
    """The nearest short heading above names 참가자격 and is no staffing table; a staffing or numbered heading ends the search."""
    for x in reversed(b.notice.window(ln.i, back, 0)[:-1]):
        if x.doc != ln.doc:
            return False
        t = ' '.join(x.text.split())
        if not t or len(t) > 60:
            continue
        if X2_QUAL_HEAD.search(t) and not X2_STAFF_HEAD.search(t):
            return True
        if X2_STAFF_HEAD.search(t) or X2_HEADING.search(t):
            return False
    return False


def x2_unread_records(b):
    known = {ln.i for ln in b.cands.get('perf', [])}
    out = []
    for ln in b.notice.lines:
        if ln.i in known or not ln.text.split():
            continue
        if ln.doc_type == '공고문':
            if not (ln.sec == 'QUAL' and qual_section(ln, b.notice)):
                continue
        elif not (switches.X2_ATTACH_QUAL and ln.sec not in ('EVAL', 'DOCS', 'NOTE') and x2_under_qual_heading(b, ln)):
            continue
        clause = clause_text(b.notice, ln)
        t = clause if switches.X2_HELD_RECORD_X else ln.text
        if not (X2_HELD_RECORD if switches.X2_HELD_RECORD_X else HELD_RECORD).search(t) or NOT_RECORD.search(t) \
                or PERF_FORMISH.search(t) or PERF_NOTE.search(t) or METHOD_SUMMARY.search(t) or evaluation_context(b, ln) \
                or not_record_limit(b, ln):
            continue
        if X2_STAFF_CTX.search(clause) or any(X2_STAFF_CTX.search(' '.join(x.text.split())) and len(' '.join(x.text.split())) < 80
                                              for x in b.notice.window(ln.i, 2, 0)[:-1]):
            continue
        if CREDIT_RATING.search(t) or X2_STATUTORY_RECORD.search(t) or X2_THIRD_PARTY.search(t):
            continue
        out.append(ln)
    return out


# Expert audit X2 §2.4 (switch X2_V8_ADDS): with a record and no region line or registration, a qualification line naming the
# bidder's location in a region is v8's restriction.
X2_BIDDER_LOC = re.compile(r'(본\s*점|주\s*된\s*(영\s*업\s*소|사\s*무\s*소)|사\s*업\s*장|영\s*업\s*소|소\s*재\s*지|주\s*소\s*지)[^.。]{0,80}?'
                           r'(소\s*재|위\s*치|둔|두\s*고|두\s*어야|있\s*는|인\s*(업\s*체|자|사업자)|내\s*에|관\s*내|에\s*있)'
                           r'|지\s*역\s*제\s*한\s*(경\s*쟁)?\s*[\(（]|소\s*재\s*(한|하는|하고\s*있는|하고)\s*(업\s*체|자|사\s*업\s*자|[「(])?|지\s*역\s*(업\s*체|소\s*재)')
X2_PLACE = re.compile(r'관\s*내|\[지역|\[수요기관|\[등록지역')


def x2_cpu_region(b):
    for ln in b.notice.lines:
        if not qual_section(ln, b.notice):
            continue
        t = clause_text(b.notice, ln)
        if X2_BIDDER_LOC.search(t) and not REGION_NONE.search(t) and not JV_PARTNER3.search(t):
            m = regions.mentions(t)
            if m['sido'] or m['basic'] or X2_PLACE.search(t):
                return ln
    return None


# Expert audit X2 §4.3 (switch V3_DISJUNCTIVE): 집행기준 제5조① measures 실적 by one past contract; when a clause offers a
# single-record amount or a cumulative one ("단일 1억 이상 또는 누적 2억 이상"), the single amount is the requirement.
X2_CUMUL = re.compile(r'누\s*적|합\s*산|합\s*계|총\s*(액|합|계|실\s*적|금\s*액)')
X2_SINGLE = re.compile(r'단\s*일|1\s*건|건\s*당|한\s*건')
X2_OR = re.compile(r'또\s*는|이\s*나|거\s*나')


def x2_single_amount(t):
    if not (X2_CUMUL.search(t) and X2_SINGLE.search(t) and X2_OR.search(t)):
        return None
    s, c = X2_SINGLE.search(t).start(), X2_CUMUL.search(t).start()
    single = [mo.value for mo in amounts.money(t) if mo.value >= 1e6 and abs(mo.start - s) <= abs(mo.start - c)]
    return max(single) if single else None


def required_amount(b, ln):
    # Audit A: a title band tag "(수의계약·5천만원미만)" states this notice's amount band, not a required record.
    t = TAG.sub(' ', ln.text) if switches.AUDIT_FIXES else ln.text
    single = x2_single_amount(t) if switches.V3_DISJUNCTIVE else None
    if single is not None:
        return single
    base_b = b.meta.B or ((b.meta.P or 0) * 1.1) or None
    vals = []
    for base, mult in amounts.ratios(t):
        if base in ('추정가격', '예정가격'):
            ref = b.meta.P
        else:
            ref = base_b
        if ref:
            vals.append(ref * mult)
    for mo in amounts.money(t):
        if mo.value >= 1e6:
            vals.append(mo.value)
    return max(vals) if vals else None


INST_WORD = re.compile(r'대학|산학\s*협력단|연구\s*(기관|소|원|단체)|공공\s*기관|국공립|협회|조합|재단|비영리|사회적\s*(기업|협동)|협동\s*조합|학교|병원|시설|단체|법인|센터')
# An exclusion names the type it shuts out: the negative attaches to the institution word ("비영리법인은 참가 불가",
# "대학 및 연구기관은 참여할 수 없습니다"); a negative closing a limit ("…으로 제한합니다", "그 외는 참가 불가") is not one.
EXCLUSION = re.compile(r'(' + INST_WORD.pattern + r')[^.,。:]{0,10}?(은|는|도)\s*(입찰\s*|본\s*입찰\s*)?(에\s*)?(참여|참가|응찰)?\s*(불가|제외|할\s*수\s*없|하실\s*수\s*없|대상에서\s*제외|제한(됩|합)니다)')
TOKEN_ONLY = re.compile(r'^\W*(\[[^\]]*\]\W*)+$')


# "X가 아닌 자는 참가할 수 없음", "X 외에는 불가", "X에 한함" limit bidders to X although they read as exclusions.
ONLY_LIMIT = re.compile(r'만\s*(이\s*)?((입찰|견적|제안)(에|서)?\s*)?(참여|참가|응찰|가능|해당|신청)|(이|가)?\s*아닌\s*(자|업체|경우|기관|단체|법인|조합|회원사|사업자|회사|기업)|아니면|해당\s*(하지|되지)\s*않는\s*(자|업체|기관|경우)'
                        r'|(외|이외)(의|에는|에|는)?\s*(자|업체|기관|단체|법인|조합|회원사|사업자|회사|기업)?\s*(는|은)?\s*(참여|참가|입찰|불가)|에\s*한\s*(하며|하여|함|한다|해|정|합니다|하고)|(으로|로)\s*(한정|제한)')


# A clause that admits one more kind of bidder, or sets conditions for it, is not a limit: "2) 비영리법인(정관 목적에 …
# 기재되어 있어야)", "비영리법인일 경우 … 허가를 받은 자", "중소기업협동조합으로서 적격조합확인서를 소지한 자", "…도 참가 가능".
ALT_CLAUSE = re.compile(r'(일|인)\s*경우|협동\s*조합으로서|비영리\s*법인\s*[(（]|도\s*(입찰\s*)?(참가|참여)\s*(가능|할\s*수)|제외(?!한\s*(자|업체))|제\s*2\s*조의\s*3')
ORDERER_TOKEN = re.compile(r'\[수요기관\([^)\]]*\)[^\]]*\]')
# Names of laws, decrees and guidelines ("지방자치단체를 당사자로 하는 계약에 관한 법률", "…공공기관 정보시스템 구축·운영 지침")
# carry institution words without naming a bidder type.
LAW_NAME = re.compile(r'「[^」]{1,60}」|『[^』]{1,60}』|｢[^｣]{1,60}｣|[가-힣·\s]{2,40}(에\s*관한\s*법률|법\s*시행령|법\s*시행규칙|지침|규칙|규정|예규|고시)')


# The eligible list also names commercial bidders ("…연구기관 또는 컨설팅기관(회사)"): general businesses may bid.
COMMERCIAL = r'(?:회\s*사|(?<!회원)(?<!조합원)(?<!협력)업\s*체|(?<![가-힣])사\s*업\s*자|일\s*반\s*기\s*업|(?<![가-힣])기\s*업(?!\s*부\s*설))'
ALT_JOIN = r'(?:또\s*는|이\s*나|거\s*나|및|,|·|ㆍ|/)'
COMMERCIAL_ALT = re.compile(r'(?:' + INST_WORD.pattern + r')[^.。]{0,25}?' + ALT_JOIN + r'\s*(?:[가-힣A-Za-z]{0,8}\s*[\(（])?\s*' + COMMERCIAL + r'(?!\s*명)'
                            + r'|' + COMMERCIAL + r'\s*' + ALT_JOIN + r'[^.。]{0,10}?(?:' + INST_WORD.pattern + r')')
# A type defined by legal registration or designation ("…에 등록된 청소년수련시설", "…지정된 검사기관") is a
# registration requirement (면허·업종·등록), not a limit to institution types, unless the line says only that type may bid.
REGISTERED_TYPE = re.compile(r'(?<!업종으로\s)(?<!업종으로)(?<!자격으로\s)(?<!자격으로)(?<!업종\s)(?<!업종)(?:등\s*록|지\s*정|허\s*가|인\s*가|신\s*고)\s*(?:된|받은|을\s*받은|한)\s*[^,.。]{0,20}?(?:기\s*관|시\s*설|단\s*체|법\s*인|조\s*합|센\s*터|병\s*원|학\s*교)')
# Expert audit X1 (switch V1_REGISTERED_TYPE2; runs/rebuild_c/transfer_20260925/audit/expert/X1/REPORT.md §4.1): a type the
# bidder holds by a statutory 인가·등록·허가·지정·신고 it has obtained ("…'평생교육기관'으로 관할청의 인가를 득하고", "…으로 등록을
# 필한 기관") is a qualification another statute requires (시행령 제12조①2 / 지방 시행령 제13조①1), not a limit to
# institution types.
X1_REGISTERED_TYPE2 = re.compile(r'(기\s*관|시\s*설|단\s*체|법\s*인|조\s*합|센\s*터|병\s*원|학\s*교|사\s*업\s*자)[^.。]{0,14}?(으\s*로|로)\s*[^.。]{0,24}?'
                                 r'(인\s*가|등\s*록|허\s*가|지\s*정|신\s*고)\s*(를|을)?\s*(득\s*하|받\s*[은고아]|필\s*한|취\s*득)')
# Membership limits ("OO협회 회원사", "조합원") stay limits when phrased as shutting out non-members.
MEMBERSHIP = re.compile(r'회\s*원|조\s*합\s*원|가\s*입|소\s*속')


RESERVED_PROFESSION = re.compile(r'(?:회\s*계|감\s*정\s*평\s*가|법\s*무|세\s*무|노\s*무|특\s*허|관\s*세|손\s*해\s*사\s*정)\s*법\s*인')


def institution_limit(text):
    """A limit to institution types names one (대학, 연구기관, 협회, 법인 …, anonymised [기관(유형)] tokens included; the
    [수요기관(…)] token is the orderer, never the bidder) and is neither an exclusion of that type ("비영리법인은 참가
    불가"), a clause admitting or conditioning one more kind of bidder, a list that also admits commercial bidders, nor a
    type defined by legal registration."""
    bidder_text = LAW_NAME.sub(' ', ORDERER_TOKEN.sub(' ', text))
    if TOKEN_ONLY.match(text) or not INST_WORD.search(bidder_text):
        return False
    only = ONLY_LIMIT.search(text)
    # An institution named only inside an anonymised [기관(…)] token needs an explicit limiting phrase.
    if not INST_WORD.search(re.sub(r'\[[^\]]*\]', ' ', bidder_text)) and not only:
        return False
    if ALT_CLAUSE.search(text) and not only:
        return False
    if COMMERCIAL_ALT.search(bidder_text) and not only:
        return False
    if REGISTERED_TYPE.search(bidder_text) and not only:
        return False
    if switches.V1_REGISTERED_TYPE2 and X1_REGISTERED_TYPE2.search(bidder_text) and not only:
        return False
    if EXCLUSION.search(text) and not only and not MEMBERSHIP.search(text):
        return False
    if switches.AUDIT_FIXES2 and EXCLUSION2.search(text) and not only and not MEMBERSHIP.search(text) and not FOR_PROFIT.search(text):
        return False
    return True


# Holdings spread over the whole country or every region (the organizer's "전국 모든 광역단체에 수리센터") exceed any
# single contract's need.
NATIONWIDE = re.compile(r'전국|모든\s*(광역|시\s*[·ㆍ]?\s*도|지역|시\s*[·ㆍ]?\s*군)|각\s*(시\s*[·ㆍ]?\s*도|광역|지역|시\s*[·ㆍ]?\s*군)|(광역시|도)\s*단위')
HOLDING = re.compile(r'센터|지점|지사|영업소|사업장|시설|장비|인력|기술자|정비|수리|A/S|AS|서비스망|지점망')
HOLD_VERB = re.compile(r'보유|있는|갖춘|갖추|두고|둔|설치|운영|구축|확보')
# The holding itself must be required: a centre, branch or network the bidder must have across the country.
NATIONWIDE_HOLDING = re.compile(r'(전국|모든\s*(광역|시\s*[·ㆍ]?\s*도|지역|시\s*[·ㆍ]?\s*군)|각\s*(시\s*[·ㆍ]?\s*도|광역|지역|시\s*[·ㆍ]?\s*군)|(광역시|도)\s*단위)'
                                r'[^.。]{0,20}(센터|지점|지사|영업소|사업장|서비스망|지점망|네트워크|A/S|AS)[^.。]{0,12}(보유|갖춘|갖추|두고|둔|설치|구축|확보|있는\s*(업체|자))')
# "일정 규모 이상의 인력 보유" (item rule, organizer example: 보안인력 50명 이상): a staff count of ten or more.
STAFF_SCALE = re.compile(r'(인력|기술자|인원|직원|근로자|경비원|요원|전문가|강사|상담사|종사자)[^.。]{0,25}?(\d{2,})\s*(명|인)\s*이상'
                         r'|(\d{2,})\s*(명|인)\s*이상[^.。]{0,15}?(인력|기술자|인원|직원|근로자|경비원|요원|전문가|강사|상담사|종사자)')
STAFF_VERB = re.compile(r'보유|확보|고용|상시|갖춘')


# Audit R2-D (item: a limit shuts general businesses out). An alternative that admits firms — joined in the clause (또는·이나)
# or a sibling item under "다음 중 하나" — naming 업체·회사·기업·사업자·중소기업·소상공인·상법·컨설팅·민간 admits general
# bidders; an institution named as excluded ("비영리법인 참여불가") is no limit unless for-profit bidders are the ones excluded;
# a clause that disclaims the limit ("입찰제한을 두는 것은 아님"), a line about the record's buyer ("발주처가 정부 …") and a
# 중소기업기본법 size definition ("상시근로자수 50명 이상 300명 미만") limit nobody.
COMMERCIAL2 = (r'(?:' + COMMERCIAL + r'|중\s*소\s*기\s*업|소\s*상\s*공\s*인|(?<![가-힣])상\s*법|컨\s*설\s*팅'
               r'|민\s*간\s*(?:기\s*업|업\s*체|사\s*업\s*자|부\s*문|기\s*관|회\s*사))'
               r'(?!\s*명)(?![^.。,]{0,4}?(?:은|는|의)?\s*(?:입\s*찰\s*)?(?:참\s*여|참\s*가|투\s*찰|응\s*찰)?\s*(?:불\s*가|제\s*외|할\s*수\s*없))')
COMMERCIAL_ALT2 = re.compile(r'(?:' + INST_WORD.pattern + r')[^.。]{0,25}?' + ALT_JOIN + r'\s*(?:[가-힣A-Za-z]{0,8}\s*[\(（])?\s*' + COMMERCIAL2
                             + r'|' + COMMERCIAL2 + r'\s*' + ALT_JOIN + r'[^.。]{0,10}?(?:' + INST_WORD.pattern + r')')
ONE_OF = re.compile(r'(다\s*음|아\s*래)\s*(각\s*호\s*)?(의\s*)?(중|의)\s*(어\s*느\s*)?(하\s*나|1)|어\s*느\s*하\s*나\s*(에|를)'
                    r'|중\s*(하\s*나|1\s*개)\s*(이\s*상\s*)?(에\s*)?해\s*당|하\s*나\s*이\s*상\s*(에\s*)?해\s*당')
EXCLUSION2 = re.compile(r'(' + INST_WORD.pattern + r')[^.,。:]{0,10}?(은|는|도|의|이|가)?\s*(입\s*찰|투\s*찰|본\s*입\s*찰)?\s*(에\s*)?'
                        r'(참\s*여|참\s*가|응\s*찰|투\s*찰)?\s*(불\s*가|제\s*외|할\s*수\s*없|하실\s*수\s*없|대상에서\s*제외)')
FOR_PROFIT = re.compile(r'(?<!비)영\s*리\s*(를|을)?\s*(목\s*적|추\s*구)|(?<!비)영\s*리\s*(법\s*인|기\s*업|단\s*체)')
DISCLAIM = re.compile(r'제\s*한\s*(을\s*)?(두\s*는|하\s*는)\s*것\s*(은|이)\s*아\s*(님|닙\s*니\s*다|니\s*며|니\s*다)'
                      r'|(자\s*격\s*)?제\s*한\s*(사\s*항\s*)?이\s*아\s*(님|닙\s*니\s*다|니\s*며|니\s*다)')
BUYER_SUBJECT = re.compile(r'발\s*주\s*처\s*(가|이)(?!\s*아\s*닌)')
SIZE_DEFINITION = re.compile(r'중\s*소\s*기\s*업\s*기\s*본\s*법|(중|소)\s*기\s*업\s*[:：]|상\s*시\s*근\s*로\s*자\s*수?[^.。]{0,20}미\s*만')


OPTION_MARK = re.compile(r'^\s*(\(\s*[가-하]\s*\)|[가-하]\s*[\.\)]|\(\s*\d{1,2}\s*\)|\d{1,2}\s*\)|[①-⑳]|\d{1,2}\s*\.(?!\d)|[○●◎▶►▷ㅇ◦•\-❍])')


def option_kind(text):
    m = OPTION_MARK.match(text)
    if not m:
        return None
    mark = re.sub(r'\s', '', m.group(1))
    return 'korean_paren' if mark.startswith('(') and not mark[1].isdigit() else record.marker_kind(mark)


def commercial_option(b, ln):
    if COMMERCIAL_ALT2.search(LAW_NAME.sub(' ', clause_text(b.notice, ln))):
        return True
    kind = option_kind(ln.text)
    near = b.notice.window(ln.i, 10, 10)
    lead = [x for x in near if x.i < ln.i and ONE_OF.search(x.text)]
    if kind is None or not lead:
        return False
    # The options of one list: after its "다음 중 하나" lead-in, up to a section change or a top-level numbered heading.
    for x in near:
        if x.i <= lead[-1].i or x.i == ln.i:
            continue
        k = option_kind(x.text)
        if x.i > ln.i and (x.sec != ln.sec or k == 'num_dot' and kind != 'num_dot'):
            break
        if k == kind and re.search(COMMERCIAL2, LAW_NAME.sub(' ', x.text)):
            return True
    return False


def disclaimed(b, ln):
    return bool(DISCLAIM.search(clause_text(b.notice, ln)))


# Expert audit X1 (switch V1_LEAD_OPTIONS; audit/expert/X1/REPORT.md §4.1): an option list introduced by "아래의 ①~④ 중 하나를
# 갖춘" / "요건 중 하나에 해당" (the lead-in may be the fired line or sit up to two lines after it) with an option of the same
# marker kind naming a commercial bidder — 민간기관, 컨설팅, 업체, or a licensed business by its statutory "…사업자" name
# (직업소개사업자, 직업정보제공사업자) — admits general businesses; ·/ㆍ bullets are a marker kind of their own and another
# marker kind ends the list (cumulative conditions). 시행령 제21조 gives institution type as no ground to restrict.
X1_LICENSED_BUSINESS = (r'(?<![가-힣])[가-힣]{2,10}사업자(?!\s*등\s*록)(?!\s*명)(?!\s*인\s*경\s*우)'
                        r'(?![^.。,]{0,4}?(?:은|는|의)?\s*(?:입\s*찰\s*)?(?:참\s*여|참\s*가|투\s*찰|응\s*찰)?\s*(?:불\s*가|제\s*외|할\s*수\s*없))')
X1_BULLET = re.compile(r'^\s*[·ㆍ•◦○●◎▶►▷ㅇ❍□■\-]')
X1_COMMERCIAL_ALT2 = re.compile(COMMERCIAL_ALT2.pattern + r'|' + X1_LICENSED_BUSINESS)
X1_COMMERCIAL2 = re.compile(COMMERCIAL2 + r'|' + X1_LICENSED_BUSINESS)
X1_ONE_OF = re.compile(ONE_OF.pattern + r'|중\s*(하\s*나|1\s*개)\s*(이\s*상\s*)?(를|을|에)?\s*(갖\s*춘|충\s*족|해\s*당|만\s*족)')


def x1_option_kind(t):
    return 'bullet_x' if X1_BULLET.match(t) else option_kind(t)


def x1_lead_options_commercial(b, ln):
    near = b.notice.window(ln.i, 12, 16)
    leads = [x for x in near if x.i <= ln.i + 2 and X1_ONE_OF.search(x.text)]
    if not leads:
        return False
    lead = leads[-1]
    kind, gap = None, 0
    for x in near:
        if x.i <= lead.i:
            continue
        t = x.text.strip()
        if not t:
            continue
        if TOP_HEAD.match(t):
            break
        k = x1_option_kind(t)
        if k is not None:
            if kind is None:
                kind = k
            elif k != kind:
                break
            gap = 0
        else:
            if kind is None:
                continue
            gap += 1
            if gap > 2:
                break
        if kind is not None and X1_COMMERCIAL2.search(LAW_NAME.sub(' ', t)):
            return True
    return False


# Expert audit X1 (switch V1_INST_LIST; §4.2): the organizer's DEV-058 form. A qualification line (the 공고문's, or an
# attachment's when the 공고문 has no qualification section) whose eligible bidder is an enumeration of two or more institution
# types (대학·산학협력단·연구기관·공공기관·국공립·협회·학회·재단·비영리·교육기관·[기관(…)]) marked eligible ("… 가능", "…만",
# "…에 한", "…으로 한정", "…이며", "…으로서", or led by "수행이 가능한", "갖춘", "보유한"), with no commercial member, no
# admission, consortium, evaluation or exclusion wording and no record-buyer relation, limits bidders to institution types
# whatever the inst family read; 지명경쟁 names its bidders.
X1_INST_ITEM = (r'(?:대\s*학(?:교|원)?|산학\s*협력단|연구\s*(?:기관|소|원)|공공\s*기관|국\s*공립\s*(?:연구)?\s*기관|협회|학회|재단'
                r'|비영리\s*(?:법인|단체|기관|연구기관)|교육\s*기관|\[기관\([^)]*\)\])')
X1_INST_LIST = re.compile(X1_INST_ITEM + r'\s*(?:,|·|ㆍ|및|또는|이나|와|과)\s*(?:[^.。,]{0,30}?\s*)?' + X1_INST_ITEM)
X1_LIST_TAIL = re.compile(r'^\s*(?:\)|\])?\s*(?:등\s*)?(?:가\s*능|만\s*|에\s*한\s*(?:함|하|정)|(?:으\s*로|로)\s*한\s*정|이\s*며|(?:으\s*로|로)\s*서'
                          r'|인\s*(?:자|기관)|이어야|이\s*아닌)')
X1_LIST_HEAD = re.compile(r'(?:가\s*능\s*한|할\s*수\s*있\s*는|갖\s*춘|있\s*는|풍부한|보유한)\s*$')
X1_LIST_NOT = re.compile(r'컨\s*소\s*시\s*엄|공\s*동\s*수\s*급|권\s*장|우\s*대|평\s*가|배\s*점|점\s*수|발\s*주\s*처|발\s*주\s*기\s*관'
                         r'|도\s*(?:입\s*찰\s*)?(?:참\s*가|참\s*여)\s*(?:가\s*능|할\s*수)'
                         r'|개\s*인|사\s*업\s*자|업\s*체|기\s*업|회\s*사|민\s*간|컨\s*설\s*팅|상\s*법|중\s*소\s*기\s*업|소\s*상\s*공\s*인|제\s*외|불\s*가|할\s*수\s*없')
X1_ARTICLE_REF = re.compile(r'제\s*\d+\s*조(?:\s*의\s*\d+)?(?:\s*제\s*\d+\s*항)?(?:\s*제\s*\d+\s*호)?'
                            r'\s*(?:에\s*(?:따른|의한|의하여|따라|의거한?)|의|규정에\s*의하여)?')
X1_LIST_HEAD_NOT = re.compile(r'(?:업\s*체|사\s*업\s*자|기\s*업|개\s*인|회\s*사|민\s*간[가-힣]{0,3})\s*(?:또\s*는|및|이\s*나|,|·|ㆍ)\s*[^.。,]{0,30}$')


def x1_institution_list_line(b, ln):
    if not qual_section(ln, b.notice) or len(ln.text) > 500:
        return False
    t = X1_ARTICLE_REF.sub(' ', LAW_NAME.sub(' ', ORDERER_TOKEN.sub(' ', ln.text)))
    m = X1_INST_LIST.search(t)
    if not m:
        return False
    if X1_LIST_NOT.search(t[m.start():m.end()]) or ALT_CLAUSE.search(ln.text) or EXCLUSION2.search(ln.text) \
            or X1_LIST_NOT.search(t[m.end():m.end() + 40]) or X1_LIST_HEAD_NOT.search(t[:m.start()]):
        return False
    if not (X1_LIST_TAIL.match(t[m.end():]) or X1_LIST_HEAD.search(t[:m.start()])):
        return False
    return buyer_limit(clause_text(b.notice, ln)) != 'specific'


def x1_institution_list(b):
    if b.meta.method == '지명경쟁':
        return None
    return next((ln for ln in b.notice.lines if (ln.doc_type == '공고문' or not b.notice.has_qual) and x1_institution_list_line(b, ln)), None)


def v1(b):
    for ln in b.cands.get('inst', []):
        if not qual_section(ln, b.notice):
            continue
        r = b.read('inst', ln)
        if r.get('역할') != '참가자격':
            continue
        if switches.AUDIT_FIXES2 and (disclaimed(b, ln) or BUYER_SUBJECT.search(ln.text)):
            continue
        kind = r.get('요건')
        if kind == '기관 유형 한정':
            # Audit A: the 입찰공고 states the 입찰참가자격 (국가계약법 시행령 제36조); an attachment's institution list does
            # not limit bidders when the 공고문 has its own qualification section.
            if switches.AUDIT_FIXES and b.notice.has_qual and ln.doc_type != '공고문':
                continue
            # 지명경쟁 names its bidders by definition (지방계약법 시행령 제22조 등); a nomination list is not a limit.
            # A legally reserved profession (회계법인 for 결산, 법무법인 …) is a license unless the line says only it may bid.
            if RESERVED_PROFESSION.search(ln.text) and not ONLY_LIMIT.search(ln.text):
                continue
            if switches.AUDIT_FIXES2 and commercial_option(b, ln):
                continue
            if switches.V1_LEAD_OPTIONS and (X1_COMMERCIAL_ALT2.search(LAW_NAME.sub(' ', clause_text(b.notice, ln)))
                                             or x1_lead_options_commercial(b, ln)):
                continue
            # Expert audit X1: 지방 소액수의 견적 may limit its 견적 invitees (지방 집행기준 제5장 제3절 1.나.6)아)).
            if switches.V1_LOCAL_PRIVATE_INST and b.meta.local_private:
                continue
            if b.meta.method != '지명경쟁' and institution_limit(ln.text):
                return ln
            continue
        if NATIONWIDE_HOLDING.search(ln.text) and not b.meta.local_private:
            return ln
        if STAFF_SCALE.search(ln.text) and STAFF_VERB.search(ln.text) and not re.search(r'실\s*적', ln.text) \
                and not (switches.AUDIT_FIXES2 and SIZE_DEFINITION.search(ln.text)):
            return ln
        if kind in ('시설·장비 보유', '인력 보유'):
            if kind == '시설·장비 보유' and b.meta.local_private:
                continue
            if r.get('필요성') == '과도하거나 과업과 무관':
                return ln
    hit = region_facility(b) if switches.V1_REGION_FACILITY else None
    if hit is None and switches.V1_INST_LIST:
        hit = x1_institution_list(b)
    return hit


# Audit RD (switch V1_REGION_FACILITY): a qualification clause requiring the bidder to hold or run a facility located in a named
# region (talkboard v1: 법령상 근거 없는 시설 보유 조건; organizer DEV-072); the bidder's own office (본점·주된 영업소) is v5–v7's.
REGION_FACILITY = re.compile(r'(소\s*재|위\s*치)\s*(한|된|하는|하고\s*있는|되어\s*있는)\s*[^.。]{0,30}?(시\s*설|센\s*터|사\s*업\s*장|공\s*장|창\s*고|차\s*고\s*지'
                             r'|영\s*업\s*장|매\s*장|점\s*포|연\s*수\s*원|교\s*육\s*장|정\s*비\s*소)[^.。]{0,20}?(보\s*유|갖\s*춘|갖\s*추|운\s*영\s*하|확\s*보)')
BIDDER_OFFICE = re.compile(r'본\s*점|주\s*된\s*(영\s*업\s*소|사\s*무\s*소)|사\s*업\s*자\s*등\s*록|법\s*인\s*등\s*기')
PLACE_TOKEN = re.compile(r'관\s*내|\[지역|\[수요기관')


def region_facility(b):
    if b.meta.local_private:
        return None
    for ln in b.notice.lines:
        if not qual_section(ln, b.notice):
            continue
        t = clause_text(b.notice, ln)
        if not REGION_FACILITY.search(t) or BIDDER_OFFICE.search(t):
            continue
        m = regions.mentions(t)
        if m['sido'] or m['basic'] or PLACE_TOKEN.search(t):
            return ln
    return None


def v2(b):
    P = b.meta.P
    # Audit R3-A: a placeholder 입찰추정가격 below 100만원 (1원) is no estimate; a real 배정예산 stands in (B/1.1).
    if switches.AUDIT_FIXES3 and P is not None and P < 1e6 and b.meta.B and b.meta.B >= 1e6:
        P = b.meta.B / 1.1
    if P is None or P >= NOTICE_AMOUNT or b.meta.local_private:
        return None
    lines = perf_lines(b)
    if switches.X2_RECORD_NOISE:
        lines = [ln for ln in lines if not x2_not_bidder_record(b, ln)]
    if switches.X2_HELD_RECORD_X or switches.X2_ATTACH_QUAL:
        known = {ln.i for ln in lines}
        lines += [ln for ln in x2_unread_records(b) if ln.i not in known]
    return lines[0] if lines else None


# Audit R2-E: the record amount may sit on a wrapped line of the clause or on a following line that is only a parenthetical
# ("…수행한 업체" / "(단일 건 계약금액 5억원 이상)"); the amount is read on the clause and that line.
PAREN_ONLY = re.compile(r'^\s*[\(（][^)）]{0,80}[\)）]\s*$')


class _Clause:
    def __init__(self, text):
        self.text = text


def record_clause(b, ln):
    text = clause_text(b.notice, ln)
    nxt = next((x for x in b.notice.window(ln.i, 0, 4)[1:] if x.text.strip()), None)
    if nxt is not None and PAREN_ONLY.match(nxt.text) and ' '.join(nxt.text.split()) not in ' '.join(text.split()):
        text = text + ' ' + nxt.text.strip()
    return _Clause(text)


# Audit RE (switch V3_EXACT): a 신인도 line is evaluation text, not a participation limit.
CREDIT_RATING = re.compile(r'신\s*인\s*도')


def v3(b):
    B = b.meta.B or ((b.meta.P or 0) * 1.1) or None
    if B is None:
        return None
    for ln in perf_lines(b):
        if switches.V3_EXACT and CREDIT_RATING.search(ln.text):
            continue
        a = required_amount(b, record_clause(b, ln) if switches.AUDIT_FIXES2 else ln)
        if a is not None and (a >= B - 1 if switches.V3_EXACT else a > B + 1):
            return ln
    return None


# v4 is decided by the text: a required record limited to a kind of buyer or customer names that kind with the relation
# (…이 발주한, …에 납품한, …에서 수행한, …학생 대상). A buyer list that also admits private parties (법인, 기업, 민간, 민자,
# 업체) is no limit. The model's reading alone cannot supply a buyer the text does not name.
_END = r'(?=\s|$|[,·ㆍ\(\)\[\]"\'“”‘’/]|에|이|가|의|등|또|및|과|와|으|로|을|를|은|는|만|나|대\s*상|관\s*련)'
BUYER = re.compile(r'(?:국\s*가\s*기\s*관|국\s*가|중\s*앙\s*행\s*정\s*기\s*관|정\s*부\s*투\s*자\s*기\s*관|정\s*부|지\s*방\s*자\s*치\s*단\s*체|자\s*치\s*단\s*체|지\s*자\s*[체제]'
                   r'|관\s*공\s*서|공\s*공\s*기\s*관|공\s*기\s*업|준\s*정\s*부\s*기\s*관|출\s*자\s*[·ㆍ]?\s*출\s*연\s*기\s*관|교\s*육\s*(?:지\s*원\s*)?청|\[수요기관\([^\]]*\)[^\]]*\]'
                   r'|대\s*학\s*교|대\s*학\s*병\s*원|대\s*학|종\s*합\s*병\s*원|병\s*원|학\s*교|유\s*치\s*원|사\s*회\s*복\s*지\s*(?:기\s*관|시\s*설)|교\s*육\s*기\s*관|의\s*료\s*기\s*관'
                   r'|공\s*직\s*유\s*관\s*단\s*체)' + _END)
# Audit A: facility-type buyers the item table's "특정기관 표현 다양" covers (어린이집, 복지시설, 양로원·요양원·경로당,
# 지역아동센터, 보건소) and "병원급".
BUYER_EXTRA = re.compile(r'(?:어\s*린\s*이\s*집|복\s*지\s*시\s*설|양\s*로\s*원|요\s*양\s*원|경\s*로\s*당|지\s*역\s*아\s*동\s*센\s*터|보\s*건\s*소|병\s*원\s*급)' + _END)


# Audit R3-A: 금융기관 is a buyer kind too ("금융기관에 … 컨설팅 유경험 업체"; item 비고 "특정기관 표현 다양").
BUYER_EXTRA3 = re.compile(r'금\s*융\s*기\s*관' + _END)


def buyer_matches(text):
    found = list(BUYER.finditer(text))
    if switches.AUDIT_FIXES:
        found += [m for m in BUYER_EXTRA.finditer(text) if not any(o.start() <= m.start() < o.end() for o in found)]
        found.sort(key=lambda m: m.start())
    if switches.AUDIT_FIXES3:
        found += [m for m in BUYER_EXTRA3.finditer(text) if not any(o.start() <= m.start() < o.end() for o in found)]
        found.sort(key=lambda m: m.start())
    return found
# A past-record relation: an ordering, delivery or performance verb within the clause after the buyer list, or a particle
# attached to the buyer itself ("공공기관에서", "대학에 대한").
BUYER_VERB = re.compile(r'발\s*주|시\s*행\s*한|납\s*품\s*(?:한|하였|실\s*적|이\s*력)|수\s*행\s*(?:한|하였|완\s*료|실\s*적)|완\s*료\s*(?:한|된)|거\s*래\s*(?:한|실\s*적)'
                        r'|계\s*약\s*(?:한|하여|체\s*결\s*한)|의\s*(?:용\s*역|사\s*업|과\s*업)')
BUYER_PARTICLE = re.compile(r'\s*(?:등\s*)?(?:에\s*서|에\s*대\s*한|에\s*게|을\s*대\s*상|대\s*상)(?!\s*(?:인\s*증|인\s*정|지\s*정|허\s*가|등\s*록|승\s*인|발\s*급|고\s*시))')
# The orderer's own token names a past buyer only with an ordering relation ("[수요기관(…)]에서 발주한").
ORDERER_PAST = re.compile(r'\s*(?:에\s*서|이|가|의)?\s*(?:발\s*주|시\s*행\s*한|납\s*품\s*한)')
PRIVATE_BUYER = re.compile(r'민\s*간|민\s*자|일\s*반\s*기\s*업|사\s*기\s*업|(?<!공)기\s*업(?!\s*부\s*설)|(?<!비영리)(?<!출연)(?<!출자)(?<!학교)법\s*인|업\s*체|회\s*사|사\s*립|개\s*인')
PRIVATE_SECTOR = re.compile(r'민\s*간|민\s*자|일\s*반\s*기\s*업|사\s*기\s*업|사\s*립')
ORDERER_OPEN = re.compile(r'민\s*간\s*(?:실\s*적\s*)?(?:도\s*)?(?:포\s*함|인\s*정)|공\s*공\s*(?:기\s*관\s*)?(?:또\s*는|및|·|ㆍ)\s*민\s*간|발\s*주\s*처\s*(?:구\s*분|불\s*문|무\s*관)')
# Audit R2-A: a buyer list that starts with the private sector ("민간기업 및 공공기관 대상") is open too, and operating a facility
# for the buyer ("…기숙사를 위탁 운영한 실적") is a record relation.
ORDERER_OPEN2 = re.compile(ORDERER_OPEN.pattern + r'|민\s*간\s*(?:기\s*업|기\s*관|부\s*문)?\s*(?:및|또\s*는|·|ㆍ|,)\s*공\s*공')
BUYER_VERB2 = re.compile(BUYER_VERB.pattern + r'|(?:위\s*탁\s*)?운\s*영\s*한')
BENEFICIARY = re.compile(r'(?:유\s*치\s*원|초\s*등\s*학\s*교|중\s*학\s*교|고\s*등\s*학\s*교|중\s*[·ㆍ,]?\s*고\s*등?\s*학\s*교|초\s*[·ㆍ,]?\s*중\s*[·ㆍ,]?\s*고)[^\n]{0,35}(?:학\s*생|원\s*생)\s*(?:을\s*)?대\s*상')
LAW_REF = re.compile(r'「[^」]{1,60}」|『[^』]{1,60}』|｢[^｣]{1,60}｣|[가-힣\s]{2,30}에\s*관한\s*법률(?:\s*시행령|\s*시행규칙)?|[가-힣]{2,20}법\s*(?:시행령|시행규칙)')


CERTIFIER = re.compile(r'\s*(?:에\s*서|으\s*로\s*부\s*터|로\s*부\s*터|이|가)?\s*(?:인\s*증|인\s*정|지\s*정|허\s*가|등\s*록|승\s*인|발\s*급|고\s*시)')


def buyer_limit(text):
    """'specific' when the required record is limited to a named kind of buyer or customer, 'open' when the buyer list
    admits private parties, None when the text names no buyer. Law names are not buyers."""
    text = LAW_REF.sub(' ', text)
    if BENEFICIARY.search(text):
        return 'specific'
    for m in buyer_matches(text):
        if m.group(0).startswith('[수요기관') and not ORDERER_PAST.match(text, m.end()):
            continue
        if switches.BUYER_CERTIFIER and CERTIFIER.match(text, m.end()):
            continue          # audit RD: "국가에서 인증받은 …" names the certifier, not the record's buyer
        particle = BUYER_PARTICLE.match(text, m.end())
        verb = (BUYER_VERB2 if switches.AUDIT_FIXES2 else BUYER_VERB).search(text, m.end(), min(len(text), m.end() + 60))
        if particle:
            stop = particle.start()
        elif verb:
            stop = verb.start()
        else:
            continue
        span = text[m.start():stop]
        return 'open' if PRIVATE_BUYER.search(span) or (ORDERER_OPEN2 if switches.AUDIT_FIXES2 else ORDERER_OPEN).search(text) else 'specific'
    return None


# Expert audit X1 (switch V4_PRIVATE_ENUM; audit/expert/X1/REPORT.md §4.4): the vice is refusing private records ("다른 기관 및
# 민간의 실적을 인정하지 않는", 정부 입찰·계약 집행기준 제5조④3; 지방 집행기준 제1장 7.나.5)). A buyer enumeration — from up to
# 40 characters before the first buyer word (cut at a clause boundary) to the first record word after it, else the closing
# parenthesis — that names a private party (민간·민자·기업·법인·회사·사립·개인, or 업체 as a list member) admits private
# records; bidder words after the record word never count.
X1_ENUM_BACK = 40
X1_PRIVATE_IN_ENUM = re.compile(r'민\s*간|민\s*자|일\s*반\s*기\s*업|사\s*기\s*업|(?<!공)기\s*업(?!\s*(?:부\s*설|부(?![가-힣])|부[가-힣]|청|진\s*흥))'
                                r'|(?<!비영리)(?<!출연)(?<!출자)(?<!학교)법\s*인|회\s*사|사\s*립|개\s*인'
                                r'|업\s*체\s*(?=[,·ㆍ)）\]]|등(?![가-힣])|및|또\s*는|이\s*나|대\s*상|에\s*(?:서|게|대))')
X1_REC_WORD = re.compile(r'실\s*적|경\s*험|이\s*력|경\s*력|납\s*품|수\s*행|발\s*주|시\s*행|운\s*영|거\s*래|계\s*약|공\s*급|제\s*작|구\s*축|설\s*치')
X1_CLAUSE_CUT = re.compile(r'[.。;]|(?:으로서|로서|하고|이며|되고|갖추고|필한|등록한|받은)\s')


def x1_buyer_enum(t):
    ms = [m for m in buyer_matches(t) if not m.group(0).startswith('[수요기관') or ORDERER_PAST.match(t, m.end())]
    if not ms:
        return None
    rec = X1_REC_WORD.search(t, ms[0].end())
    if rec:
        end = rec.start()
    else:
        close = re.search(r'[)）.。;]', t[ms[0].end():])
        end = ms[0].end() + close.start() if close else min(len(t), ms[0].end() + 80)
    back = X1_CLAUSE_CUT.split(t[max(0, ms[0].start() - X1_ENUM_BACK):ms[0].start()])[-1]
    return back + t[ms[0].start():end]


def x1_private_in_enum(t):
    span = x1_buyer_enum(t)
    return bool(span and X1_PRIVATE_IN_ENUM.search(span))


# Expert audit X1 (switch V4_TOKEN_BUYER; §4.5): where the buyer kind equals the orderer's type the anonymiser replaced it with the
# orderer's token ("직장 [수요기관(보육시설)] 납품 실적" = 직장어린이집 납품 실적); a facility-kind token directly followed by a
# delivery or operation record names that kind of buyer (the item table's 어린이집 form).
X1_TOKEN_BUYER = re.compile(r'\[수요기관\((?:보육시설|사회복지시설|의료기관|학교|초등학교|중학교|고등학교|대학|교육기관|유치원|병원|어린이집)[^)\]]*\)[^\]]*\]'
                            r'\s*(?:에|에게|을\s*대상으로)?\s*(?:납\s*품|공\s*급|위\s*탁\s*운\s*영|운\s*영)\s*(?:실\s*적|이\s*력|경\s*험)')


def v4(b):
    for ln in perf_lines(b):
        clause = clause_text(b.notice, ln)
        verdict = buyer_limit(clause)
        if verdict == 'specific' and switches.V4_PRIVATE_ENUM and not BENEFICIARY.search(LAW_REF.sub(' ', clause)) \
                and x1_private_in_enum(LAW_REF.sub(' ', clause)):
            verdict = 'open'
        if verdict == 'specific':
            return ln
        # No buyer relation the CPU can parse: the model's reading stands when the clause is a bidder requirement that
        # names a buyer kind (not the orderer's own token).
        bare = LAW_NAME.sub(' ', clause)
        named = any(not m.group(0).startswith('[수요기관') for m in buyer_matches(bare))
        if (verdict is None and named and PERF_BIDDER_REQ.search(clause) and not PRIVATE_SECTOR.search(bare)
                and not (switches.V4_PRIVATE_ENUM and x1_private_in_enum(LAW_REF.sub(' ', clause)))
                and b.read('perf', ln).get('발주처') == '특정 발주기관만'):
            return ln
    if switches.V4_TOKEN_BUYER:
        return next((ln for ln in perf_lines(b) if X1_TOKEN_BUYER.search(clause_text(b.notice, ln))), None)
    return None


# DATASET-FIT (probe only, switches.REG_CONSISTENCY): the organizer's violations appear where the notice text and the 나라장터
# registration disagree (dev region positives 15/20 vs t2500 natural region firings 12/95; dev v17 positives 5/6 not SME-
# registered vs 30/63 natural firings SME-registered). Unlike R1 there is no statute behind skipping agreeing cases.
def region_agrees(b, sido):
    return b.meta.region_flag == 'Y' and bool(sido) and set(b.meta.region_sido or []) == set(sido)


def sme_registered(b):
    c = str(b.meta.clause or '')
    return bool(re.search(r'중\s*기업|중소\s*기업', c)) and not COMPETITION_REGISTERED.search(c)


def v5(b):
    """A bidder-location restriction stated in the qualification section, or registered on 나라장터 (meta 지역제한여부 Y
    is the restriction the bid system enforces), at P ≥ T."""
    lines, sido, _ = region_restriction(b)
    P = b.meta.P
    if P is None or P < b.meta.T_hi:
        return None
    if lines:
        if switches.REG_CONSISTENCY and region_agrees(b, sido):
            return None
        return lines[0]
    if b.meta.region_flag == 'Y':
        return True
    if switches.V5_ANY_SECTION:
        for ln in lines_where(b, 'region', 역할='참가자격', 대상='입찰자 소재지 제한'):
            if ln.doc_type == '공고문' and ln.sec in ('NOTE', 'OTHER', 'BID') and BID_LOCATION.search(ln.text) \
                    and not JV_PARTNER3.search(ln.text):
                return ln
    return None


# Expert audit X7 (runs/rebuild_c/transfer_20260925/audit/expert/X7/REPORT.md): 국가계약법 시행규칙 제25조③ and 지방계약법 시행규칙
# 제25조③ make the 시·도 of the site or delivery place the unit of a bidder-location restriction, and 정부 입찰·계약 집행기준
# 제5조④6 names a 시·군·구 unit as prohibited.
# Switch V6_LABEL_BASIC: a "지역제한: 여주, 양평" label whose every name is a 시·군 restricts at 시·군 level (the anonymiser left the
# raw names; dev DEV-066 = 1). Names are read only under that label, where no common word collides with a 시·군 name.
X7_BASIC_UNITS = frozenset((
    '수원 성남 의정부 안양 부천 광명 평택 동두천 안산 고양 과천 구리 남양주 오산 시흥 군포 의왕 하남 용인 파주 이천 안성 김포 화성 양주 포천 여주 '
    '연천 가평 양평 춘천 원주 강릉 동해 태백 속초 삼척 홍천 횡성 영월 평창 정선 철원 화천 양구 인제 고성 양양 청주 충주 제천 보은 옥천 영동 증평 '
    '진천 괴산 음성 단양 천안 공주 보령 아산 서산 논산 계룡 당진 금산 부여 서천 청양 홍성 예산 태안 전주 군산 익산 정읍 남원 김제 완주 진안 무주 '
    '장수 임실 순창 고창 부안 목포 여수 순천 나주 광양 담양 곡성 구례 고흥 보성 화순 장흥 강진 해남 영암 무안 함평 영광 장성 완도 진도 신안 포항 '
    '경주 김천 안동 구미 영주 영천 상주 문경 경산 의성 청송 영양 영덕 청도 고령 성주 칠곡 예천 봉화 울진 울릉 창원 진주 통영 사천 김해 밀양 거제 '
    '양산 의령 함안 창녕 남해 하동 산청 함양 거창 합천 서귀포 기장 달성 군위 강화 옹진 울주').split())
X7_REGION_LABEL = re.compile(r'지\s*역\s*제\s*한\s*(?:지\s*역)?\s*[:：]\s*([^()\[\]|\n]{1,60})')
X7_NAME_SPLIT = re.compile(r'[,，·ㆍ/]|\s및\s|\s또는\s')
# Switch V6_OFFICE_DUTY: setting up an office in a 시·군 from the start of the work ("… 소재 사무실 설치 필수이며 착수일로부터 업무
# 수행이 가능해야 함") is a performance duty, not the bidder's location.
X7_OFFICE_DUTY = re.compile(r'(사\s*무\s*실|사\s*업\s*장|지\s*점|영\s*업\s*소|연\s*락\s*사\s*무\s*소)[^.。]{0,10}(설\s*치|개\s*설)[^.。]{0,40}'
                            r'(착\s*수|계\s*약\s*(체\s*결\s*)?(후|이\s*후|일\s*로\s*부\s*터)|(과\s*업|사\s*업|용\s*역|수\s*행)\s*기\s*간)')


def x7_label_basic(t):
    m = X7_REGION_LABEL.search(t)
    if not m:
        return False
    names = [re.sub(r'(시|군)$', '', x.strip()) for x in X7_NAME_SPLIT.split(m.group(1)) if x.strip()]
    return bool(names) and all(n in X7_BASIC_UNITS for n in names)


def x7_v6_extra(b):
    """A 시·군-level restriction the qualification-section reading misses: V6_LABEL_BASIC, or V6_ORDERER_ANY (the bidder's 본점
    "[수요기관(기초자치단체)]내", dev DEV-071 = 1, in any section of the 공고문). None when both switches are off."""
    if not (switches.V6_LABEL_BASIC or switches.V6_ORDERER_ANY):
        return None
    for ln in b.notice.lines:
        if ln.doc_type != '공고문':
            continue
        if switches.V6_LABEL_BASIC and x7_label_basic(ln.text):
            return ln
        m = ORDERER_JURISDICTION.search(ln.text) if switches.V6_ORDERER_ANY else None
        if m and m.group('level') == '기초자치단체':
            return ln
    return None


# Switch V7_SITE_SPAN: 시행규칙 제25조③1 (both laws) — when the site spans the adjacent 시·도 ("과업대상: 부산광역시, 울산광역시,
# 경상남도 소재 지상기상관측장소 72개소"), including them is lawful. The work's 시·도 are read from the title and the 과업명·과업대상
# type label lines of every document.
X7_SITE_LABEL = re.compile(r'^\W*(?:[\d.]+\s*[\.\)]?\s*|[가-하]\s*[\.\)]\s*)?(공\s*고\s*명|과\s*업\s*명|사\s*업\s*명|용\s*역\s*명|건\s*명|과\s*업\s*대\s*상'
                           r'|사\s*업\s*대\s*상|대\s*상\s*지\s*(점|역)?|과\s*업\s*(범\s*위|지\s*역|장\s*소)|사\s*업\s*(범\s*위|지\s*역|장\s*소))\s*[:：]')


def x7_site_spans(b, sido):
    if len(sido) < 2:
        return False
    named = set()
    for t in list(b.titles) + [ln.text for ln in b.notice.lines if X7_SITE_LABEL.search(ln.text)]:
        named |= regions.mentions(t)['sido']
    return set(sido) <= named


def v6(b):
    lines, sido, basic = region_restriction(b)
    P = b.meta.P
    if P is None or P >= b.meta.T_lo or b.meta.local_private:
        return None
    # Audit RE (switch V6_META_BASIC): a 기초-level restriction registered on 나라장터 (제한지역코드목록) is the restriction the bid
    # system enforces, with or without a restriction line (dev DEV-066: raw 시·군 names).
    registered = switches.V6_META_BASIC and b.meta.region_flag == 'Y' and b.meta.region_basic
    if not lines:
        return True if registered else x7_v6_extra(b)
    if switches.REG_CONSISTENCY and region_agrees(b, sido):
        return None
    for ln in lines:
        t = clause_text(b.notice, ln) if switches.AUDIT_FIXES else ln.text
        if (regions.mentions(t)['basic'] or orderer_level(t) == 'basic') \
                and not (switches.V6_OFFICE_DUTY and X7_OFFICE_DUTY.search(t)):
            return ln
    # Audit R2-E: a stated bidder-location restriction that names no 기초 unit while 제한지역코드목록 registers 기초 units is
    # judged at 기초 level (the registered restriction is what the bid system enforces; raw 시·군 names escape the tokens).
    if (switches.AUDIT_FIXES2 or switches.V6_REG_BASIC) and b.meta.region_basic:
        return lines[0]
    return lines[0] if registered else x7_v6_extra(b)


def v7_clause_lines(b):
    """Audit RG (switch V7_CLAUSE): clauses stating the bidder's location with a firm subject that name 2+ 시·도 (region groups
    as AUDIT_FIXES2 parses them), in the qualification section or the 공고문 BID section, wrapped lines joined."""
    saved, switches.AUDIT_FIXES2 = switches.AUDIT_FIXES2, True
    try:
        out = []
        for ln in b.notice.lines:
            if not (qual_section(ln, b.notice) or ln.doc_type == '공고문' and ln.sec == 'BID'):
                continue
            cl = clause_text(b.notice, ln)
            if not BID_LOCATION.search(cl) or JV_PARTNER3.search(cl) or REGION_NONE.search(cl):
                continue
            if len(regions.mentions(cl)['sido']) >= 2:
                out.append(ln)
        return out
    finally:
        switches.AUDIT_FIXES2 = saved


def v7(b):
    lines, sido, _ = region_restriction(b)
    P = b.meta.P
    if P is None or P >= b.meta.T_lo or b.meta.local_private:
        return None
    if lines:
        if switches.REG_CONSISTENCY and region_agrees(b, sido):
            return None
        if len(sido) >= 2:
            if switches.V7_SITE_SPAN and x7_site_spans(b, sido):
                return None
            return max(lines, key=lambda ln: len(regions.mentions(ln.text)['sido']))
    if switches.V7_CLAUSE:
        got = v7_clause_lines(b)
        if got and not (switches.V7_SITE_SPAN and x7_site_spans(b, regions.mentions(clause_text(b.notice, got[0]))['sido'])):
            return got[0]
    # Audit RG (switch V7_META_MULTI): a restriction registered on 나라장터 naming 2+ 시·도 below T is the restriction the bid system
    # enforces, with or without a text line (v5 reads meta 지역제한여부 Y the same way; organizer DEV-049's v5 is registration-only).
    if switches.V7_META_MULTI and b.meta.region_flag == 'Y' and len(b.meta.region_sido) >= 2 \
            and not (switches.V7_SITE_SPAN and x7_site_spans(b, b.meta.region_sido)):
        return True
    return None


def v8(b):
    """실적 and bidder-location restrictions together; the location restriction may be the one registered on 나라장터
    (meta 지역제한여부 Y), as for v5."""
    if b.meta.local_private:
        return None
    lines, _, _ = region_restriction(b)
    perf = perf_lines(b)
    if switches.X2_RECORD_NOISE:
        perf = [ln for ln in perf if not x2_not_bidder_record(b, ln)]
    if switches.X2_V8_ADDS:
        known = {ln.i for ln in perf}
        perf += [ln for ln in x2_unread_records(b) if ln.i not in known]
    if perf and (lines or b.meta.region_flag == 'Y'):
        return lines[0] if lines else perf[0]
    if perf and switches.X2_V8_ADDS:
        return x2_cpu_region(b)
    return None


# A designation names the maker or model as the requirement: a 제조사·모델명·상표 label with its value, a series the
# line requires ("…시리즈일 것", "…시리즈 제품"), or a "…일 것" requirement. A product name merely listed in a
# specification table is not, even when the model name contains 시리즈 ("모니터(시리즈 7 …)", dev DEV-148 = 0). "동등 이상" does not cure a
# designation (user-approved literal default; the organizer left it open).
DESIGNATION = re.compile(r'제조\s*(사|원|회사|업체)\s*[:：·/]|모델\s*(명)?\s*[:：·/]|상표|브랜드|메이커'
                         r'|시리즈\s*(?:일\s*것|이어야|로\s*(?:한정|납품|할\s*것)|의?\s*제품)'
                         r'|(제품|것)\s*(이어야|일\s*것|으로\s*할\s*것)|일\s*것|사\s*제품|정품만')


# Audit F: a listing standard ("작물보호제 지침서에 수록된 상표의 사양") or a uniformity rule ("동일 제조사, 동일 규격")
# names no maker to buy from.
LISTED_STANDARD = re.compile(r'(지\s*침\s*서|목\s*록|고\s*시|등\s*재|수\s*록)[^.。]{0,20}(상\s*표|제\s*품|모\s*델)')
SAME_MAKER = re.compile(r'동\s*일\s*(한\s*)?(제\s*조\s*사|규\s*격|형\s*식|제\s*품|모\s*델)')


# Audit R2-D: a designation names the procured item's maker or model. A 모델명/제조사 field designates only with a value that
# is no placeholder, measurement or further label ("모델명 :", "○○○제조회사 :", "모델 : 130cm", "제조사/모델명 :"); a
# 브랜드/상표 word as a promotion object, the orderer's own brand or the bidder's own brand, and a maker described by a
# condition ("직영 서비스센터 운영중인 제조사 제품"), name no maker; a same-maker/same-model rule is read on the clause (it
# spans lines: "동일 제조사 및 동일 모델 / … 시리즈").
LABEL_CUE = re.compile(r'(제\s*조\s*(사|원|회\s*사|업\s*체)|모\s*델\s*(명)?)\s*[:：·/]\s*')
PLACEHOLDER_VALUE = re.compile(r'\s*($|[○◯〇●＊*]{2,}|[oOxX]{2,}(?![A-Za-z0-9])|\(?\s*(기\s*재|작\s*성|기\s*록|입\s*력|명\s*기)'
                               r'|\d+(\.\d+)?\s*(cm|mm|kg|㎝|㎜|㎏|㎡|인\s*치|inch)(?![A-Za-z0-9가-힣]))')
PLACEHOLDER_LABEL = re.compile(r'[○◯〇]{2,}\s*(제\s*조|모\s*델)')
BRAND_NONPRODUCT = re.compile(r'캐\s*릭\s*터|통\s*합\s*브\s*랜\s*드|브\s*랜\s*드\s*(통\s*일|가\s*치|이\s*미\s*지|홍\s*보|마\s*케\s*팅|개\s*발|전\s*략|아\s*이\s*덴\s*티|네\s*이\s*밍|디\s*자\s*인)'
                              r'|업\s*체\s*명\s*과\s*동\s*일|서\s*비\s*스\s*망|자\s*체\s*브\s*랜\s*드')
MAKER_CONDITION = re.compile(r'(운\s*영\s*중\s*인|운\s*영\s*하\s*는|보\s*유\s*한|갖\s*춘|있\s*는|등\s*록\s*된|인\s*증\s*받\s*은|직\s*영)[^.。]{0,10}제\s*조\s*사\s*(의\s*)?제\s*품')


def designates(text):
    labels = list(LABEL_CUE.finditer(text))
    if labels and not PLACEHOLDER_LABEL.search(text) and any(
            not (LABEL_CUE.match(text, m.end()) or PLACEHOLDER_VALUE.match(text[m.end():m.end() + 30])) for m in labels):
        return True
    if BRAND_NONPRODUCT.search(text) or MAKER_CONDITION.search(text):
        return False
    # Any other designation wording (상표·브랜드·메이커, "…시리즈일 것", "…일 것", "…사 제품", "정품만").
    return bool(DESIGNATION.search(LABEL_CUE.sub(' ', text)))


SAME_MAKER_MODEL = re.compile(r'동\s*일\s*(한\s*)?(제\s*조\s*사|모\s*델)')


def v9_designates(b, ln):
    clause = clause_text(b.notice, ln)
    return designates(ln.text) and not LISTED_STANDARD.search(clause) and not SAME_MAKER_MODEL.search(clause)


# Probe (switches.V9_BROAD): talkboard rule for v9 is "규격서 등에 특정 모델명·제조사명 명시 → 성립"; organizer positives are
# bare model codes in specs ("파종기(DPM-8000GMP)", "Agilent ICP-OES 5900"), which DESIGNATION wording misses.
def model_code(text):
    from .families import LATIN_MODEL, SPEC_NOISE
    return any(not SPEC_NOISE.match(m.group(0)) for m in LATIN_MODEL.finditer(text))


def v9_code_designates(b, ln):
    clause, t = clause_text(b.notice, ln), ln.text
    return (model_code(t) and not LISTED_STANDARD.search(clause) and not SAME_MAKER_MODEL.search(clause)
            and not BRAND_NONPRODUCT.search(t) and not MAKER_CONDITION.search(t) and not PLACEHOLDER_LABEL.search(t))


# Probe (switches.V9_NOISE): an operating system or office suite is a platform the purchase runs on, not a maker or model of
# the procured item, and a label with nothing after it ("모델명 :") designates nothing.
V9_PLATFORM = re.compile(r'windows?\s*\d+(\s*(pro|home|enterprise|education))?|윈\s*도\s*우\s*\d*|linux|리\s*눅\s*스|ubuntu|mac\s*os'
                         r'|android|안\s*드\s*로\s*이\s*드|\bios\b|ms\s*office|한\s*컴\s*오\s*피\s*스', re.I)
V9_EMPTY_LABEL = re.compile(r'(제\s*조\s*(사|원|회\s*사|업\s*체)|모\s*델\s*명?)\s*[:：]\s*$')


def v9_text(t):
    return V9_PLATFORM.sub(' ', t) if switches.V9_NOISE else t


def v9_read2(b):
    for ln in lines_where(b, 'model2', 대상=('납품 물품', '과업용 장비·SW'), 방식='지정'):
        t = v9_text(ln.text)
        if V9_EMPTY_LABEL.search(ln.text.strip()) or not (DESIGNATION.search(t) or model_code(t)):
            continue
        if switches.V9_EQUIVALENT_VIOLATION or b.read('model2', ln).get('동등') != '동등 이상 허용':
            return ln
    return None


def v9_lines(b):
    """Every line v9 fires on from the model family's reading, in order."""
    return [ln for ln in lines_where(b, 'model', 성격='구매 대상의 제조사·모델 지정')
             if not (switches.V9_NOISE and V9_EMPTY_LABEL.search(ln.text.strip()))
             and (DESIGNATION.search(v9_text(ln.text)) or switches.V9_BROAD and model_code(v9_text(ln.text)))
             and (switches.V9_EQUIVALENT_VIOLATION or b.read('model', ln).get('동등') != '동등 이상 허용')
             and not (switches.AUDIT_FIXES and (LISTED_STANDARD.search(ln.text) or SAME_MAKER.search(ln.text)))
             and not (switches.AUDIT_FIXES2 and not (v9_designates(b, ln) or switches.V9_BROAD and v9_code_designates(b, ln)))]


# Expert audit X3 (switch V9_X3; runs/rebuild_c/transfer_20260925/audit/expert/X3/REPORT.md): a practitioner marks a v9 line
# only when it fixes the maker or model of what a goods purchase buys (정부 입찰·계약 집행기준 제5조④5). Not a violation:
# C1 a clause-level 동등 이상 reference model; C2 a software licence or subscription object; C3 consumables, spares and
# upgrades of installed equipment; C4 a service contract (the name is the orderer's asset or the contractor's means);
# C5 grades, colours, flights, same-maker rules, empty labels, and platform names without a model code.
X3_EQUIV = re.compile(r'동\s*등\s*(이상|품|제품|규격|사양|급|하거나|또는)|동\s*급|또는\s*동\s*등|동\s*종\s*동\s*급|equivalent|or\s+better'
                      r'|상\s*급\s*사양|상위\s*기종|이\s*상\s*의\s*(성능|사양)을\s*가진', re.I)
X3_SW_NAME = re.compile(r'소프트웨어|운영체제|라이선스|라이센스|사용권|구독')
X3_SW_TITLE = re.compile(r'라이선스|라이센스|licen[cs]e|사용권|구독|subscription', re.I)
X3_SW_LINE = re.compile(r'라이선스|라이센스|licen[cs]e|사용권|구독|copy\b|edition|버전|version|업그레이드', re.I)
X3_LICENCE = re.compile(r'라이선스|라이센스|licen[cs]e|사용권|구독', re.I)
# The 세부품명 names a consumable when it ends with one ("차량용오일필터", "정품토너"); "필터프레스탈수기" is a machine.
X3_CONSUMABLE_NAME = re.compile(r'(토너|카트리지|시약|소모품|부품|필터|예비품)(류)?\s*$')
X3_CONSUMABLE_TITLE = re.compile(r'소모품|토너|시약|부품\s*(구|납)|교체|증설|업그레이드|예비품|보수용|유지\s*보수')
X3_COMPAT = re.compile(r'기존\s*(장비|설비|시스템|장치|제품|설치품|기기)[^.。]{0,40}(호환|동일|연동|증설|교체|사용)|증설|업그레이드\s*(키트|kit)|보수용\s*부품', re.I)
X3_GRADE = re.compile(r'^\s*[:\-]?\s*(SB-?\d|RSB|RSC-?\d|GC\s*\d{3}|STS\s*\d{3}|SS\s*\d{3}|UPHC\s*D\d+|D\d{3,4})\b')
X3_COLOUR = re.compile(r'색\s*상\s*[:：]|Pantone|CMYK|RGB\s*[:：]', re.I)
X3_FLIGHT = re.compile(r'\b[A-Z]{2}\d{3,4}\b.*(→|공항|항공|출발|도착)|(항공|편명).*\b[A-Z]{2}\d{3,4}\b')
X3_PLATFORM = re.compile(r'windows?\s*\d+|윈\s*도\s*우|linux|리\s*눅\s*스|ubuntu|mac\s*os|android|\bios\b|ms\s*office|한\s*컴|powerpoint|adobe|autocad'
                         r'|catia|inventor|\.net\s*framework|asp\.net|mysql|google\s*analytics|\(ga4\)', re.I)
X3_SAME_MAKER = re.compile(r'동\s*일\s*(한\s*)?(제\s*조\s*사|규\s*격|형\s*식|제\s*품|모\s*델|브\s*랜\s*드)|동일한\s*브랜드')
X3_EMPTY_LABEL = re.compile(r'(제\s*조\s*(사|원|회\s*사|업\s*체)|모\s*델\s*명?)\s*[:：]\s*$|○{2,}\s*(제조|모델)|모\s*델\s*[:：]\s*\d+\s*(cm|mm|kg)')
X3_MODEL_CODE = re.compile(r'[A-Z][A-Za-z]*-?\d{3,}')


def x3_drop(b, ln):
    """True when a v9 line is lawful practice or noise under the practitioner standard (C1-C5)."""
    clause = clause_text(b.notice, ln)
    window = ' '.join(x.text for x in b.notice.window(ln.i, 3, 3))
    title = ' '.join(b.titles)
    names = [n for n, _ in b.meta.codes]
    goods = b.meta.work == '물품'
    if X3_EQUIV.search(clause) or X3_EQUIV.search(window):
        return True
    if goods and names and all(X3_SW_NAME.search(n) for n in names) or X3_SW_TITLE.search(title) \
            or X3_SW_LINE.search(ln.text) and X3_LICENCE.search(clause + ' ' + window):
        return True
    if goods and names and all(X3_CONSUMABLE_NAME.search(n) for n in names) or X3_CONSUMABLE_TITLE.search(title) \
            or X3_COMPAT.search(clause) or X3_COMPAT.search(window):
        return True
    if b.meta.work == '용역':
        return True
    t = ln.text
    return bool(X3_GRADE.search(t) or X3_COLOUR.search(t) or X3_FLIGHT.search(t) or X3_SAME_MAKER.search(t)
                or X3_EMPTY_LABEL.search(t.strip()) or X3_PLATFORM.search(t) and not X3_MODEL_CODE.search(X3_PLATFORM.sub(' ', t)))


# Expert audit X3 direction (b) (switch V9_X3B; audit/expert/X3/REPORT.md): a vehicle purchase that names the car model
# ("스타리아 하이브리드 투어러 11인승") or a goods line that names a maker's brand product ("(주)삼성전자 DM501THA", "아이맥",
# "갤럭시 탭 A9") with no 동등 wording designates the maker — 집행기준 제5조④5's own example; the model family never sees a
# model name without a Latin code. Dev has no such notice.
X3B_VEHICLE_CODE = re.compile(r'(승용차|승합차|화물차|버스|밴|트럭|특수차|구급차|소방차|자동차|탑차)\s*$')     # the 세부품명 names a vehicle
X3B_VEHICLE_MODEL = re.compile(r'(?<![가-힣A-Za-z])(스타리아|카니발|쏘렌토|아반떼|그랜저|쏘나타|봉고\s*3?|포터\s*2?|스타렉스|카운티|일렉시티|아이오닉\s*\d?|투싼|싼타페'
                               r'|팰리세이드|셀토스|스포티지|코나|캐스퍼|토레스|렉스턴|니로|레이|모닝|K[3578]|EV[369]|마이티|메가트럭|쏠라티|유니버스|엑시언트)(?![가-힣A-Za-z0-9])')
X3B_BRAND = re.compile(r'삼성전자|엘지전자|LG전자|갤럭시\s*(탭|북|S\d+)|아이패드|맥북|아이맥|LG\s*그램|삼성\s*(노트북|모니터|갤럭시)')


def x3b_line(b):
    if b.meta.work != '물품':
        return None
    vehicle = any(X3B_VEHICLE_CODE.search(name) for name, _ in b.meta.codes)
    for ln in b.notice.lines:
        t = ln.text
        if len(t.strip()) > 220 or not (vehicle and X3B_VEHICLE_MODEL.search(t) or X3B_BRAND.search(t)):
            continue
        if not X3_EQUIV.search(t) and not X3_EQUIV.search(clause_text(b.notice, ln)) and not x3_drop(b, ln):
            return ln                  # the same practitioner standard: a toner or licence purchase names makers lawfully
    return None


# Second stage (switches.V9_STAGE2; audit RA): the v9obj family is asked only on v9_lines, so its question presupposes a read
# designation. Serviced or compatible existing equipment and names that are no maker or model (flights, OS, grades, blanks)
# designate nothing the contract buys (집행기준 제5조④5); a missing reading keeps the line.
V9_STAGE2_DROP = ('기존 장비', '호환 대상', '해당 없음')


def v9(b):
    if switches.V9_READ2:
        return v9_read2(b)
    lines = v9_lines(b)
    if switches.V9_STAGE2:
        lines = [ln for ln in lines if b.read('v9obj', ln).get('대상') not in V9_STAGE2_DROP]
    if switches.V9_X3:
        lines = [ln for ln in lines if not x3_drop(b, ln)]
    if not lines and switches.V9_X3B:
        return x3b_line(b)
    return lines[0] if lines else None


# ---------------------------------------------------------------- competition products and enterprise size
ITEM_START = re.compile(r'^\s*(?:\d{1,2}\s*[\.\)]|\(\s*\d{1,2}\s*\)|[가-하]\s*[\.\)]|[①-⑳]|[○●◎▶►▷ㅇ◦•❍□■\-‣※]|\|)')


SENT_END = re.compile(r'(다|함|음|것|임|요|자|체)\s*[.。)]?\s*$|[.。]\s*$')


def clause_text(notice, ln):
    """The list item a line belongs to: wrapped continuation lines before and after are joined (one document).
    Documents extracted with a blank line after every line wrap a clause across blank lines; there a blank line is
    crossed unless the text before it ends a sentence."""
    doc = [x for x in notice.window(ln.i, 9, 9)]
    k = next(n for n, x in enumerate(doc) if x.i == ln.i)

    def prev_text(n):
        m = n - 1
        while m >= 0 and not doc[m].text.strip():
            m -= 1
        return m, m < n - 1

    def next_text(n):
        m = n + 1
        while m < len(doc) and not doc[m].text.strip():
            m += 1
        return m, m > n + 1

    start, joined = k, 0
    while joined < 4 and not ITEM_START.match(doc[start].text):
        m, gap = prev_text(start)
        if m < 0 or gap and SENT_END.search(doc[m].text):
            break
        start, joined = m, joined + 1
    end, joined = k, 0
    while joined < 4:
        m, gap = next_text(end)
        if m >= len(doc) or ITEM_START.match(doc[m].text) or gap and SENT_END.search(doc[end].text):
            break
        end, joined = m, joined + 1
    return ' '.join(x.text.strip() for x in doc[start:end + 1] if x.text.strip())


def size_class(b, ln):
    """Allowed enterprise class of a restriction clause, read from its wording (law and agency names removed); the
    model's reading only when the wording names no class."""
    from .families import size_normal, size_words
    clause = clause_text(b.notice, ln)
    cls = size_words(size_normal(clause) if switches.AUDIT_FIXES2 else clause)
    if cls:
        return cls
    if switches.AUDIT_FIXES:
        return None      # audit C: a clause naming no size class does not borrow one from the model's reading
    v = b.read('size', ln).get('허용 대상')
    return {'중소기업 전체': 'sme', '소기업·소상공인만': 'small'}.get(v)


# 판로지원법 시행령 제2조의3 ① lets the orderer skip the SME restriction, stated in the notice (②; talkboard: only a written
# exception counts). The clause must say the restriction or the preferential procurement is not applied; a clause that
# only admits one more kind of bidder into the restricted bid ("제2조의3 제1항제2호에 해당하는 비영리법인은 입찰참여가
# 가능", "간주되는 특별법인은 참가 가능", "비영리법인인 경우 확인서는 제외") waives nothing: the organizer marks size
# violations on such notices too.
SIZE_ADMISSION = re.compile(r'(비\s*영\s*리|특\s*별\s*법\s*인|간\s*주\s*되는)[^.。]{0,80}?(참\s*여|참\s*가|입\s*찰)[^.。]{0,16}?(가\s*능|할\s*수)'
                            r'|확\s*인\s*서\s*(가|를)?\s*(없\s*어\s*도|제\s*출\s*하\s*지\s*않\s*아\s*도|소\s*지\s*하\s*지\s*않\s*아\s*도)'
                            r'|비\s*영\s*리[^.。]{0,40}확\s*인\s*서\W{0,3}(는|은)?\s*제\s*외')
SIZE_WAIVER = re.compile(r'(우\s*선\s*조\s*달|제\s*한|경\s*쟁\s*입\s*찰)[^.。]{0,40}?(예\s*외|제\s*외|비\s*대\s*상|적\s*용\s*(하\s*지|을\s*배\s*제|배\s*제|않))'
                         r'|예\s*외\s*(를\s*)?적\s*용|예\s*외\s*(사\s*유|품\s*목|대\s*상|용\s*역|건)|예\s*외\s*의\s*사\s*유|제\s*외\s*(용\s*역|대\s*상|품\s*목)'
                         r'|제\s*한\s*하\s*지\s*(않|아니)|제\s*한\s*을\s*하\s*지|비\s*대\s*상|외\s*의\s*방\s*법\s*으\s*로|소\s*지\s*여\s*부\s*와\s*관\s*계\s*없\s*이'
                         r'|(대\s*기\s*업|중\s*기\s*업)[^.。]{0,12}참\s*(여|가)\s*(가\s*)?(가\s*능|할\s*수)|통\s*합\s*발\s*주|분\s*리\s*발\s*주'
                         r'|판\s*로\s*지\s*원[^.。]{0,40}(예\s*외|적\s*용\s*(하\s*지\s*)?(않|제\s*외|배\s*제))'
                         r'|제\s*2\s*조\s*의?\s*3[^.。]{0,60}?(예\s*외|제\s*외)|운\s*영\s*요\s*령\W{0,3}\s*제\s*44\s*조')
# Citing 제2조의3 of the 판로지원법 decree is an exception only when the clause does not merely admit another bidder.
SIZE_CITE = re.compile(r'(판\s*로\s*지\s*원|구\s*매\s*촉\s*진|우\s*선\s*조\s*달)[^.。]{0,80}?제\s*2\s*조\s*의?\s*3')


def waives_size_limit(b, ln):
    t = clause_text(b.notice, ln)
    if SIZE_ADMISSION.search(t) and not SIZE_WAIVER.search(t):
        return False
    return bool(SIZE_WAIVER.search(t) or SIZE_CITE.search(t))


# 판로지원법 시행령 제2조의3 ①3: goods and services that another law lets the orderer buy by 수의계약 from a designated class
# (국가계약법 시행령 제26조①5가5·7, 지방계약법 시행령 제25조①5다·바: 여성기업, 장애인기업, 사회적기업, 사회적협동조합,
# 자활기업, 마을기업, 청년창업기업) are outside the SME preference. A qualification limited to such a class states that
# basis (dev DEV-172: 여성기업 수의계약, v18 = 0). The certificate rule's name "…및 장애인기업 확인요령" is not such a limit.
DESIGNATED_CLASS = re.compile(r'여\s*성\s*기\s*업|장\s*애\s*인\s*기\s*업|사\s*회\s*적\s*(기\s*업|협\s*동\s*조\s*합)|자\s*활\s*기\s*업|마\s*을\s*기\s*업|청\s*년\s*창\s*업\s*기\s*업')
DESIGNATED_NAMES = re.compile(r'[^\s「」｢｣『』“”‘’<>]*장\s*애\s*인\s*기\s*업\s*(확\s*인\s*요\s*령|활\s*동\s*촉\s*진\s*법)|여\s*성\s*기\s*업\s*지\s*원\s*에\s*관\s*한\s*법\s*률'
                              r'|사\s*회\s*적\s*기\s*업\s*육\s*성\s*법|협\s*동\s*조\s*합\s*기\s*본\s*법')


def designated_class_limit(b):
    for ln in b.notice.lines:
        if ln.doc_type != '공고문' or not qual_section(ln, b.notice):
            continue
        t = DESIGNATED_NAMES.sub(' ', ln.text)
        if DESIGNATED_CLASS.search(t) and not re.search(r'가\s*점|우\s*대|배\s*점|평\s*가', t):
            return True
    return False


# Audit R2-C. (1) A 공고문 qualification clause with the eligibility structure ("…에 따른 소기업 … 으로서 … 확인서를 소지한 업체",
# "입찰참가업체는 … 소기업 …") is a participation restriction whatever the model read (the 입찰공고's 입찰참가자격, 시행령 제36조);
# (2) "‘소기업 또는 소상공인’ 간 우선조달계약 대상" declares the restriction of the named class (판로지원법 시행령 제2조의2);
# (3) a qualification statement filed under a 공고문 BID heading ("…소상공인으로서, …") is a qualification, but a method tag
# ("제한경쟁(소기업)") or an amount-tier row ("추정가격 1억원 미만 | …") is not; (5) notes on how a certificate is checked,
# without a class statement of their own, and (6) SW진흥법 대기업 참여제한 sentences state no 판로지원법 class. Each test reads
# the clause from the line on.
CLASS_STATEMENT = re.compile(r'으\s*로\s*서|로\s*서|인\s*자|인\s*업\s*체|에\s*한\s*(함|하여|정)')
BIDDER_CLASS = re.compile(CLASS_STATEMENT.pattern + r'|확\s*인\s*서')
AMOUNT_TIER = re.compile(r'(억|천\s*만)\s*원?\s*(\([^)]{0,12}\)\s*)?(미\s*만|이\s*상)')
SIZE_WORD_CHEAP = re.compile(r'소\s*기\s*업|소\s*상\s*공\s*인|중\s*[·ㆍ・‧․⸱∙⋅]?\s*소\s*기\s*업|우\s*선\s*조\s*달')
ELIGIBILITY = re.compile(r'에\s*따른[^.。]{0,40}?(소\s*기\s*업|소\s*상\s*공\s*인|중\s*소\s*기\s*업\s*자?|중\s*[·ㆍ・‧․⸱∙⋅]?\s*소\s*기\s*업)[^.。]{0,60}?'
                         r'(으\s*로\s*서|인\s*자|인\s*업\s*체)[^.。]{0,120}?확\s*인\s*서[^.。]{0,15}?(소\s*지|제\s*출|보\s*유)'
                         r'|입\s*찰\s*참\s*가\s*(업\s*체|자)\s*(는|은)[^.。]{0,60}?(소\s*기\s*업|소\s*상\s*공\s*인|중\s*소\s*기\s*업)')
ELIGIBILITY_NOT = re.compile(r'가\s*점|배\s*점|평\s*가|우\s*대|신\s*인\s*도|해\s*당\s*(시|하\s*는\s*경\s*우|되\s*는\s*경\s*우)')
PRIORITY_DECL = re.compile(r'(소\s*기\s*업|소\s*상\s*공\s*인|중\s*소\s*기\s*업\s*자?)[^.。]{0,12}?간\s*(의\s*)?(우\s*선\s*조\s*달\s*(계\s*약)?|제\s*한\s*경\s*쟁\s*입\s*찰)\s*'
                           r'(대\s*상|으\s*로|로|입\s*니\s*다|임|이\s*며)')
VERIFY_NOTE = re.compile(r'확\s*인\s*이\s*(안\s*될|되지\s*않을|되지\s*않는)\s*경\s*우|확\s*인\s*되\s*지\s*않\s*는\s*경\s*우|신\s*청\s*한\s*업\s*체\s*는'
                         r'|기\s*업\s*구\s*분\s*과\s*다\s*른\s*경\s*우|유\s*효\s*기\s*간\s*시\s*작\s*일')
SW_LARGE = re.compile(r'중\s*소\s*소\s*프\s*트\s*웨\s*어\s*사\s*업\s*자|대\s*기\s*업\s*인\s*소\s*프\s*트\s*웨\s*어\s*사\s*업\s*자'
                      r'|소\s*프\s*트\s*웨\s*어\s*진\s*흥\s*법|사\s*업\s*참\s*여\s*지\s*원\s*에\s*관\s*한\s*지\s*침')


def own_clause(b, ln):
    """The clause from this line on: a note line's clause_text may reach back into the clause above it."""
    flat = ' '.join(clause_text(b.notice, ln).split())
    k = flat.find(' '.join(ln.text.split())[:20])
    return flat[k:] if k >= 0 else flat


def sw_only(text):
    """The clause states a class only in SW진흥법 sentences (a flattened block may hold other clauses too)."""
    from .families import size_normal, size_words
    if not SW_LARGE.search(text):
        return False
    rest = ' '.join(s for s in re.split(r'(?<=[.。])\s+|\s+(?=[○●◎▶►▷ㅇ◦•❍□■※\-]\s)', text) if not SW_LARGE.search(s))
    return size_words(size_normal(rest)) is None


def size_lines2(b, lines, positive):
    from .families import size_normal, size_words
    known = {ln.i for ln in lines}
    extra = []
    if positive:
        extra = [ln for ln in lines_where(b, 'size', 역할='참가자격 제한')
                 if ln.i not in known and ln.doc_type == '공고문' and ln.sec == 'BID'
                 and BIDDER_CLASS.search(own_clause(b, ln)) and not AMOUNT_TIER.search(own_clause(b, ln))]
        known |= {ln.i for ln in extra}
    for ln in b.notice.notice_lines():
        if ln.i in known or not SIZE_WORD_CHEAP.search(ln.text):
            continue
        own = own_clause(b, ln)
        if AMOUNT_TIER.search(own):
            continue
        in_qual = qual_section(ln, b.notice)
        eligible = in_qual and ELIGIBILITY.search(own) and not ELIGIBILITY_NOT.search(own)
        declared = PRIORITY_DECL.search(own) and (in_qual or not positive)
        if (eligible or declared) and size_words(size_normal(own)) is not None:
            extra.append(ln)
            known.add(ln.i)
    out = [ln for ln in sorted(lines + extra, key=lambda ln: ln.i) if not sw_only(own_clause(b, ln))]
    kept = [ln for ln in out if not (VERIFY_NOTE.search(own_clause(b, ln)) and not CLASS_STATEMENT.search(own_clause(b, ln)))]
    return kept or out


# Audit R3-B/R3-C (the 입찰공고 states the 입찰참가자격: 국가계약법 시행령 제36조, 지방계약법 시행령 제33조): when the 공고문 has an
# eligibility clause ("…중소기업자 또는 … 소상공인으로서 … 확인서를 소지한 업체"), in its qualification section or read by the model
# as 참가자격 제한 elsewhere, the class comes from the 공고문's clauses and attachment clauses do not override it; attachments
# decide when the 공고문 states none. A note that admits a bidder without the certificate ("※ 비영리법인은 … 확인서가 없어도 입찰
# 참가 가능") states no class.
NOTICE_ELIGIBLE = re.compile(r'(중\s*[·ㆍ・‧․]?\s*소\s*기\s*업\s*자?|소\s*기\s*업\s*자?|소\s*상\s*공\s*인)[^.。]{0,60}?(으\s*로\s*서|로\s*서|인\s*자|인\s*업\s*체)')
NO_CERT_ADMISSION = re.compile(r'확\s*인\s*서\s*(가|를)?\s*(없\s*어\s*도|제\s*출\s*하\s*지\s*않\s*아\s*도|소\s*지\s*하\s*지\s*않\s*아\s*도)')


def notice_size_lines(b, lines, primary):
    from .families import size_normal, strip_law_titles
    primary = [ln for ln in primary if not NO_CERT_ADMISSION.search(ln.text)] or primary
    read = [ln for ln in lines_where(b, 'size', 역할='참가자격 제한') if ln.doc_type == '공고문' and ln not in lines
            and ln.sec not in NOT_QUAL_SECTIONS and not size_condition(ln) and not NO_CERT_ADMISSION.search(ln.text)]
    stated = [ln for ln in primary + read if ln.doc_type == '공고문' and size_class(b, ln) in ('sme', 'small')
              and NOTICE_ELIGIBLE.search(strip_law_titles(size_normal(own_clause(b, ln))))]
    if not stated:
        return lines, primary
    added = [ln for ln in stated if ln not in primary]
    return sorted(lines + [ln for ln in added if ln not in lines], key=lambda ln: ln.i), [ln for ln in primary if ln.doc_type == '공고문'] + added


# Audit RF (switch SIZE_TAG_NOT_QUAL, L-A 3): the 나라장터 method header "(국내입찰/…/제한경쟁_소기업*소상공인)" and an empty
# checkbox "대기업( ), 중소기업( )" state no restriction for the absence items (dev DEV-039: 입찰방법 표시 is no restriction).
SIZE_TAG = re.compile(r'대\s*기\s*업\s*\(\s*\)|국\s*내\s*입\s*찰\s*/|제\s*한\s*경\s*쟁\s*[_(（]\s*(중|소)')


def size_state(b, positive=False):
    """('small' | 'sme' | 'unknown' | None, restriction lines, exception stated).

    positive=True (v13–v15, v17: the restriction's class is the violation) counts only the qualification section;
    otherwise (v11, v16, v18: absence) a restriction stated anywhere in the documents counts as present.
    """
    lines = lines_where(b, 'size', section=positive, 역할='참가자격 제한')
    if switches.SIZE_TAG_NOT_QUAL and not positive:
        lines = [ln for ln in lines if not SIZE_TAG.search(ln.text)]
    if switches.AUDIT_FIXES2:
        lines = size_lines2(b, lines, positive)
    exc = any(waives_size_limit(b, ln) for ln in lines_where(b, 'size', 역할='판로지원 예외 명시'))
    if switches.AUDIT_FIXES and not exc:
        exc = exception_in_notice(b)
    if not lines:
        return None, lines, exc
    # Probe (switches.CERT_ONLY_ABSENT): certificate-validity lines alone are no participation clause (talkboard), so the
    # restriction is absent when no remaining line says who may bid.
    if (switches.CERT_ONLY_ABSENT and not positive
            and all(size_condition(ln) and CERT_WORD.search(ln.text) and not ELIGIBLE.search(ln.text) for ln in lines)):
        return None, [], exc
    # The class comes from the clauses that define who may bid, not from certificate-validity conditions
    # ("발급된 소기업·소상공인확인서가 … 다른 경우 입찰참가자격이 없습니다").
    primary = [ln for ln in lines if not size_condition(ln)] or lines
    if switches.AUDIT_FIXES3 and positive:
        lines, primary = notice_size_lines(b, lines, primary)
    classes = [size_class(b, ln) for ln in primary]
    if 'small' in classes:
        if switches.AUDIT_FIXES and 'sme' in classes and sibling_options(b, primary, classes):
            return 'sme', lines, exc
        return 'small', lines, exc
    if 'sme' in classes:
        return 'sme', lines, exc
    return 'unknown', lines, exc


SIZE_CONDITION = re.compile(r'경\s*우|신청한\s*(사항|업체)|확인되지\s*않|간주되는|특별\s*법인')
# Audit C: an SME eligibility clause with an appended deemed-SME tail ("중·소기업으로서 … 소지한 자 및 … 간주되는 법인") is a
# primary clause, not a condition.
SIZE_ELIGIBLE = re.compile(r'(중\s*[·ㆍ・‧․]?\s*소\s*기\s*업|소\s*기\s*업|소\s*상\s*공\s*인)[^.。]{0,40}?(으로서|로서)')
DEEMED_TAIL = re.compile(r'및[^.。]{0,80}?간\s*주\s*되\s*는\s*(법\s*인|조\s*합)[^.。]*')
# CERT_ONLY_ABSENT: a certificate line that also states who may bid ("…으로서", "…확인서를 소지한 자") is an eligibility clause.
CERT_WORD = re.compile(r'확\s*인\s*서')
ELIGIBLE = re.compile(r'(으로|로)\s*서|(소\s*지\s*한|보\s*유\s*한|발\s*급\s*받\s*은|갖\s*춘|취\s*득\s*한)\s*(자|업\s*체|법\s*인)'
                      r'|(으로|로)\s*(자\s*격\s*을\s*)?제\s*한|인\s*자(?![가-힣])|에\s*한\s*(함|정|하)|만\s*(참|입\s*찰)')


def size_condition(ln):
    t = ln.text
    if switches.AUDIT_FIXES and SIZE_ELIGIBLE.search(t):
        t = DEEMED_TAIL.sub(' ', t)
    return SIZE_CONDITION.search(t)


# Audit C: sibling items of one list ("가) 중·소기업으로서 …", "나) 소상공인으로서 …") are alternatives, and an SME option admits
# every SME: the list restricts to SMEs, not to small firms. A "…의 경우" tier header between the items splits the list; an
# option for 특별법인·비영리·협동조합 admits one more kind of bidder and decides nothing.
LIST_MARK = re.compile(r'^\s*([가-하]\s*[\.\)]|\(\s*\d{1,2}\s*\)|\d{1,2}\s*\)|[①-⑳]|\d{1,2}\s*\.(?!\d)|[○●◎▶►▷ㅇ◦•\-❍])')
TIER_HEAD = re.compile(r'의\s*경\s*우\s*[\]\)］】]?\s*$|[\[【][^\]】]*의\s*경\s*우[^\]】]*[\]】]')
SPECIAL_BIDDER = re.compile(r'특\s*별\s*법\s*인|비\s*영\s*리|협\s*동\s*조\s*합')


def sibling_options(b, primary, classes):
    if b.meta.P is not None and b.meta.P <= 1e5:
        return False
    small = [ln for ln, c in zip(primary, classes) if c == 'small']
    sme = [ln for ln, c in zip(primary, classes) if c == 'sme' and not SPECIAL_BIDDER.search(ln.text)]
    for s in small:
        for m in sme:
            if s.doc != m.doc or abs(s.i - m.i) > 10:
                continue
            ms, mm = LIST_MARK.match(s.text), LIST_MARK.match(m.text)
            if not ms or not mm:
                continue
            a, z = re.sub(r'\s', '', ms.group(1)), re.sub(r'\s', '', mm.group(1))
            kind = record.marker_kind(a)
            if kind != record.marker_kind(z) or (kind == 'bullet') != (a == z):
                continue
            lo, hi = sorted((s.i, m.i))
            if any(TIER_HEAD.search(x.text) for x in b.notice.lines[lo + 1:hi]):
                continue
            return True
    return False


# Audit D: two-column layouts split the stated exception over lines ("운영요령 제44조(중소기업자 우선조달계약에 대한 예외)를" /
# "적용합니다"); it is read on the de-spaced 공고문 as well (item 비고: 판로지원 예외 명시한 경우, 제한 없어도 가능).
EXCEPTION_APPLIED = re.compile(r'운영요령.{0,160}?제44조.{0,60}?적용(합니다|함|한다)|우선조달계약에대한예외\W{0,3}(를|을)적용(합니다|함|한다)')


def exception_in_notice(b):
    flat = re.sub(r'\s', '', ' '.join(ln.text for ln in b.notice.notice_lines()))
    return bool(EXCEPTION_APPLIED.search(flat))
DP_SANCTION = re.compile(r'위반|제재|해지|취소|불이익|부정당|직접생산\s*확인\s*기준|하도급|타사\s*제품')


def dp_required(b, positive=False):
    return lines_where(b, 'dp', section=positive, 역할='참가자격 소지 요구')


def dp_present(b):
    """Absence test for v10: the model read a possession requirement, or the qualification section mentions the
    certificate outside a sanction or document-list line (a deleted requirement leaves only such mentions)."""
    from .families import DOC_LIST
    if dp_required(b):
        return True
    for ln in b.cands.get('dp', []):
        if qual_section(ln, b.notice) and not METHOD_SUMMARY.search(ln.text) and not DP_SANCTION.search(ln.text) \
                and not DOC_LIST.search(ln.text) and ln.sec != 'DOCS':
            return True
    return False


# The notice states that the competition-product rules do not apply (판로지원법 시행령 제7조 exceptions, or an explicit
# exclusion from the direct-production / SME-competition procedure).
COMPETITION_EXCEPTION = re.compile(r'(판로\s*지원|구매\s*촉진).{0,60}시행령\s*[」』｣]?\s*제\s*7\s*조|중소\s*기업자?\s*간\s*경쟁\s*(제품|입찰)?.{0,40}(예외|제외|적용\s*(하지|을\s*배제|배제))'
                                   r'|직접\s*생산\s*확인\s*(품목|대상)?\s*(에서|을)?\s*제외')


def competition_exception(b):
    return any(COMPETITION_EXCEPTION.search(ln.text) for ln in b.notice.lines if ln.doc_type == '공고문')


def certified_as_listed(b):
    """A service whose 직접생산 requirement certifies a listed competition service admitted at P, which the model reads as
    the procured work itself: the purchase is that competition service (v12 is judged by the procured object)."""
    if b.meta.work != '용역':
        return False
    cat = catalog.load()
    return any(b.read('dp', ln).get('인증 품목') == '과업과 같은 종류' and catalog.cites_listed(ln.text, cat, b.meta.P, services_only=True)
               for ln in dp_required(b))


# Audit X4 (switch X4_OBJECT; runs/rebuild_c/transfer_20260925/audit/expert/X4/REPORT.md): what a 조달 practitioner treats as the
# 경쟁제품 purchase. A 급식·간식 basket registered under one food 세부품명 and bought from a distributor is not the designated
# product (판로지원법 제6조① designates products 중소기업자가 직접 생산하는; the basket falls under 시행령 제2조의2); a product whose
# stated specification lies outside the designation's 특이사항 is not designated; a 시행령 제7조 exception may be stated in any
# document of the 입찰공고; a registered 제7조의2 basis makes a small-only bid lawful.
X4_FOOD_LIC = re.compile(r'식품판매업|집단급식소|식품제조|식품접객')
X4_BASKET = re.compile(r'급식|간식|부식|식재료|식자재|공산품')
X4_KW = re.compile(r'(\d{2,3})\s*kW', re.I)
X4_GHZ = re.compile(r'(\d\.\d{1,2})\s*GHz', re.I)
X4_DUAL = re.compile(r'\*\s*2\s*(EA|ea|개)?|[xX×]\s*2(?!\d)|2\s*(EA|ea|개|기|소켓)|Dual|듀얼|프로세서\s*2', re.I)
X4_NITS = re.compile(r'(\d{3,4})\s*(nits?|니트|cd/㎡|cd/m)', re.I)
X4_VWALL = re.compile(r'비디오\s*월|video\s*wall|캐비닛|케비넷|캐비넷', re.I)
X4_EDU = re.compile(r'자동\s*제어|마이크로\s*프로세서|과학\s*교구')
X4_EXC = re.compile(r'(판로\s*지원|구매\s*촉진).{0,60}시행령\s*[」』｣]?\s*제\s*7\s*조(?!의)|제\s*7\s*조\s*제?\s*1\s*항\s*제?\s*[34]\s*호'
                    r'|중소\s*기업자?\s*간\s*경쟁\s*(제품|입찰)?.{0,40}(예외|제외|적용\s*(하지|을\s*배제|배제))'
                    r'|유찰[^.。\n]{0,40}(완화|일반\s*경쟁)|참가\s*자격\s*완화'
                    r'|제\s*2\s*조의\s*3[^.。\n]{0,80}(중소기업\s*이외|허용|예외를\s*적용|적용하여)|중소기업\s*이외\s*기업[^.。\n]{0,20}허용', re.S)
X4_REG72 = re.compile(r'중기간\s*경쟁\s*제품\s*소기업\s*소상공인\s*제한경쟁')


def x4_products(b):
    cat = catalog.load()
    prods = [cat.by_code.get(code) or cat.by_name.get(re.sub(r'\s', '', name)) for name, code in b.meta.codes]
    return prods if prods and all(prods) else []


def x4_all_text(b):
    return '\n'.join(d['text'] for d in b.notice.docs)


def food_basket(b):
    prods = x4_products(b) if b.meta.work == '물품' else []
    if not prods or not all(p.code.startswith('50') for p in prods):
        return False
    head = '\n'.join(d['text'] for d in b.notice.docs if d['type'] == '공고문')[:3000]
    return bool(X4_FOOD_LIC.search(str(b.meta.license or '')) or X4_BASKET.search(head))


def designation_excluded(b):
    prods = x4_products(b) if b.meta.work == '물품' else []
    if not prods:
        return False
    text, out = x4_all_text(b), False
    for p in prods:
        if p.code == '2611170403':        # 전기자동차용충전장치: 50kW 이하에 한함
            out |= any(int(v) > 50 for v in X4_KW.findall(text)) or '급속' in text
        elif p.code == '4321150102':      # 컴퓨터서버: CPU 2개 중 3.2GHz 이하
            out |= any(float(v) > 3.2 for v in X4_GHZ.findall(text)) and bool(X4_DUAL.search(text)) \
                or bool(re.search(r'3\.2\s*GHz\s*초과', text))
        elif p.code == '4511189301':      # 영상정보디스플레이장치: 단독형 600cd 미만, 비디오월 제외
            out |= any(int(v) >= 600 for v, _ in X4_NITS.findall(text)) or bool(X4_VWALL.search(text))
        elif p.code == '6010999901':      # 교육훈련장비: 자동제어·마이크로프로세서·과학교구 실습장비에 한함
            out |= not X4_EDU.search(text)
    return out


def exception_anydoc(b):
    return bool(X4_EXC.search(x4_all_text(b)))


def competitive_service(b, item=None):
    """v10·v11·v13 are judged on service purchases, and on goods only for the items in switches.COMPETITIVE_GOODS. Dev has
    no label on 16 competition-product goods, but C with goods judged would fire on only 2 of them (both v10), so that
    is weak evidence against the literal rule, which has no goods exclusion."""
    if not switches.SW_SERVICE_COMPETITIVE and b.scope.basis.endswith(':sw'):
        return False
    if switches.X4_OBJECT and item in ('v10', 'v11', 'v13') and (food_basket(b) or designation_excluded(b)):
        return False
    if b.meta.work == '물품' and item in switches.COMPETITIVE_GOODS:
        return b.scope.competitive is True
    return b.meta.work == '용역' and (b.scope.competitive is True or certified_as_listed(b))


def v10(b):
    if not competitive_service(b, 'v10') or competition_exception(b) or (b.meta.private and not switches.V10_PRIVATE) \
            or switches.X4_OBJECT and exception_anydoc(b):
        return None
    if b.meta.P is not None and b.meta.P < 1e7:
        return None
    return None if dp_present(b) else True


# Audit C: requiring the SME or small-business confirmation certificate is the SME restriction (중소기업 범위 및 확인에 관한
# 규정); one asked for 가점·평가 or "(해당 시)" is not. Not for v18: an SME certificate leaves the 소기업 limit absent.
SME_CERT_REQ = re.compile(r'(중\s*[·ㆍ・‧․]?\s*소\s*기\s*업|소\s*기\s*업|소\s*상\s*공\s*인)[^.。]{0,24}?확\s*인\s*서')
SME_CERT_OPTIONAL = re.compile(r'해\s*당\s*(시|하는\s*경\s*우|되는\s*경\s*우|자\s*에\s*한)|가\s*점|배\s*점|평\s*가|우\s*대|창\s*업\s*기\s*업')


def sme_certificate_required(b):
    return any(ln.doc_type == '공고문' and ln.sec != 'EVAL' and SME_CERT_REQ.search(ln.text) and not SME_CERT_OPTIONAL.search(ln.text)
               for ln in b.notice.lines)


def v11(b):
    """판로지원법 제7조 requires SME competition in the *bidding* for a competition product (item: "…경쟁제품 입찰 중소
    없음"); a 소액수의 quotation is not a bid, as the organizer's 소액수의 exception for v13 also reflects."""
    if not competitive_service(b, 'v11') or competition_exception(b) or (b.meta.private and not switches.V11_PRIVATE) \
            or switches.X4_OBJECT and exception_anydoc(b):
        return None
    state, _, _ = size_state(b)
    if state is None and switches.V11_ELIGIBLE:
        saved, switches.AUDIT_FIXES2 = switches.AUDIT_FIXES2, True
        try:
            state = 'stated' if size_lines2(b, [], positive=False) else None
        finally:
            switches.AUDIT_FIXES2 = saved
    return True if state is None and not (switches.AUDIT_FIXES and sme_certificate_required(b)) else None


# Expert audit X3 (switch V12_X3; runs/rebuild_c/transfer_20260925/audit/expert/X3/REPORT.md): 판로지원법 제9조① binds the
# 직접생산 check to competition products; a demand that offers another route ("또는 정품 공급증명서", "직접 생산하거나 제조사와
# 계약된 공급 채널") or only verifies a certificate ("확인이 안 되거나") restricts nothing (dev 6/6 positives kept).
V12_ALT = re.compile(r'또는\s*(정품\s*)?(공급\s*(증명서|확약서)|제조사[^.。]{0,20}(공급\s*)?확약서)|직접\s*생산하거나|생산하거나\s*제조사')
V12_VERIFY2 = re.compile(r'확\s*인\s*(이|가)?\s*(안\s*되거나|되지\s*않거나)')
# Expert audit X3 (switch V12_MANUF): a goods purchase outside the competition products that admits manufacturers only
# ("제조업체이어야", "공장등록증 … 필수"; DEV-057 = 1) restricts bidders with no basis among 국가계약법 시행령 제21조① /
# 지방계약법 시행령 제20조①, unless the 조항호 is 특수한설비·기술의 보유(물품제조). Not an evaluation or sanction line, a
# conditional ("…인 경우") or a clause that opens another route (dealers with a supply pledge or certificate).
V12_MANUF_ONLY = re.compile(r'(제\s*조\s*업\s*체|제\s*조\s*업\s*자|생\s*산\s*업\s*체)\s*(에\s*한\s*함|만\s*(참\s*여|참\s*가|입\s*찰)|이\s*어\s*야|일\s*것|로\s*제\s*한)'
                            r'|직\s*접\s*(생\s*산|제\s*조)\s*(하\s*는|한)\s*(업\s*체|자)|공\s*장\s*등\s*록\s*증[^.。]{0,40}(필\s*수|소\s*지|보\s*유)'
                            r'|제\s*조\s*업\s*자\s*로\s*서|공\s*장\s*등\s*록\s*(을\s*)?(한|필\s*한)\s*(업\s*체|자)')
V12_MANUF_NOT = re.compile(r'위\s*반|제\s*재|해\s*지|취\s*소|부\s*정\s*당|하\s*도\s*급|타\s*사\s*제\s*품|납\s*품\s*(시|하\s*여\s*야)|검\s*수|적\s*격\s*심\s*사|평\s*가'
                           r'|(인|일)\s*경\s*우|도\s*[·ㆍ]?\s*소\s*매|공\s*급\s*확\s*약|또\s*는[^.。]{0,40}(공\s*급|판\s*매|대\s*리\s*점|매\s*매|유\s*통)')


def v12_manuf_line(b):
    if b.meta.work != '물품' or '특수한설비' in str(b.meta.clause or '').replace(' ', ''):
        return None
    for ln in b.notice.lines:
        if ln.doc_type != '공고문' or ln.sec != 'QUAL':
            continue
        t = clause_text(b.notice, ln)
        if len(t) < 400 and V12_MANUF_ONLY.search(t) and not V12_MANUF_NOT.search(t) and not V12_ALT.search(t):
            return ln
    return None


# Switch V12_TITLE_OBJECT2 (talkboard: v12 is judged by the procured object; dev DEV-053 수련활동 and DEV-056 연구 = 1 with
# event and internet-development certificates): a title whose object — the last noun group, after the band tag and the
# 용역·위탁·사업 tail — is research, education, a training course, a school trip or lodging is not the certified event,
# exhibition or development service, whatever the model read. Event heads (행사·축제·박람회·전시 …) never qualify.
V12_OTHER_OBJECT = re.compile(r'(연\s*구|조\s*사|교\s*육|연\s*수(\s*과\s*정)?|강\s*좌|아\s*카\s*데\s*미|수\s*련\s*활\s*동|수\s*학\s*여\s*행|체\s*험\s*학\s*습|숙\s*박|여\s*행'
                              r'|캠\s*프)(\s*(위\s*탁\s*)?운\s*영)?\s*$')
V12_EVENT_HEAD = re.compile(r'행\s*사|축\s*제|박\s*람\s*회|전\s*시|페\s*스\s*티\s*벌|대\s*회|공\s*연|시\s*상\s*식|설\s*명\s*회|포\s*럼|컨\s*퍼\s*런\s*스|세\s*미\s*나')
V12_TOKEN = re.compile(r'\[[^\]]*\]')


def v12_other_object(b):
    tagged = [ln.text for ln in [x for x in b.notice.lines if x.doc_type == '공고문'][:60] if catalog.BAND_TAG.search(ln.text)]
    for t in list(b.titles) + tagged:
        m = catalog.BAND_TAG.search(t)
        head = catalog.TITLE_TAIL.sub('', V12_TOKEN.sub(' ', t[:m.start()] if m else t)).strip()[-16:]
        if V12_OTHER_OBJECT.search(head) and not V12_EVENT_HEAD.search(head):
            return True
    return False


def v12(b):
    """v12 is judged by the procured object (talkboard): a certificate for a listed competition product that the model
    reads as the procured work itself shows the purchase is that product, whatever our title families say."""
    if b.scope.competitive is not False:
        return None
    cat = catalog.load()
    lines = []
    for ln in dp_required(b, positive=True):
        clause = clause_text(b.notice, ln)
        # Audit E: the verification, possession and condition wording is read on the clause (a layout break splits "…확인(" /
        # "종합정보망)이 안 될 경우"), and the clause must name the 직접생산확인 certificate.
        t = clause if switches.AUDIT_FIXES else ln.text
        if b.read('dp', ln).get('인증 품목') == '과업과 같은 종류' and catalog.cites_listed(clause, cat, b.meta.P) \
                and not (switches.V12_TITLE_OBJECT and catalog.title_names_other_work(b.titles, clause, cat)) \
                and not (switches.V12_TITLE_OBJECT2 and v12_other_object(b)):
            continue
        if DP_VERIFY.search(t) and not DP_POSSESS.search(t) or DP_CONDITIONAL.search(t):
            continue
        if switches.V12_X3 and (V12_ALT.search(clause) or V12_VERIFY2.search(t)):
            continue
        if switches.AUDIT_FIXES and not DP_CERT.search(clause):
            continue
        if switches.AUDIT_FIXES2 and not DP_CERT.search(evidence_sentence(clause, ln.text)):
            continue
        lines.append(ln)
    if not lines and switches.V12_MANUF:
        ln = v12_manuf_line(b)
        return ln
    return lines[0] if lines else None


# "…직접생산확인증명서가 종합정보망에서 확인이 안 될 경우 입찰참가자격이 없습니다" verifies a certificate required
# elsewhere; on its own it is not the possession requirement v12 judges (dev: both such lines are organizer 0).
DP_VERIFY = re.compile(r'(확\s*인|조\s*회)\s*(\([^)]{0,120}\))?\s*(이|가)?\s*(안\s*될|안\s*되는|되지\s*않을|되지\s*않는|불가할)\s*(경우|시)|확\s*인\s*(이\s*)?가\s*능\s*하\s*여\s*야')
# A requirement conditioned on the purchase being a competition product ("중소기업자간 경쟁제품으로 입찰공고한 경우 … 보유",
# "해당 품목의 경우") demands nothing of a purchase that is not one.
DP_CONDITIONAL = re.compile(r'(경\s*쟁\s*제\s*품|해\s*당\s*(품\s*목|물\s*품|제\s*품)|대\s*상\s*품\s*목)[^.。]{0,24}(인|일|으로\s*입\s*찰\s*공\s*고\s*한|에\s*해\s*당\s*하\s*는)\s*경\s*우|해\s*당\s*(시|하\s*는\s*경\s*우)')
DP_POSSESS = re.compile(r'(소\s*지|보\s*유|발\s*급\s*받|취\s*득)\s*(한|하고|하여야|해야)\s*(자|업\s*체|사\s*업\s*자)?')
# Audit E: v12 is the 판로지원법 제9조 직접생산확인 requirement; "직접 생산·제조업체" or HACCP lines name no such certificate.
DP_CERT = re.compile(r'직\s*접\s*생\s*산\s*(확\s*인|증\s*명)|직\s*생\s*(확\s*인|증\s*명)')
# Audit R2-B: the certificate must be named in the sentence that holds the evidence line, not elsewhere in the joined clause
# ("…제조물품으로 등록" next to a line about the certificate names none; 판로지원법 제9조 확인 is the certificate).
SENTENCE_SPLIT = re.compile(r'(?<=[.。])\s+|\s+(?=[·ㆍ•○◦▶□■※\-]\s)')


def evidence_sentence(clause, line):
    key = ' '.join((line or '').split())[:30]
    for s in SENTENCE_SPLIT.split(clause or ''):
        if key and key in ' '.join(s.split()):
            return s
    return line


def v13(b):
    if not competitive_service(b, 'v13') or b.meta.private or competition_exception(b) \
            or switches.X4_OBJECT and X4_REG72.search(str(b.meta.clause or '')):
        return None
    state, lines, _ = size_state(b, positive=True)
    if state == 'small':
        return next(ln for ln in lines if size_class(b, ln) == 'small')
    return None


# Expert audit X5 (runs/rebuild_c/transfer_20260925/audit/expert/X5/REPORT.md): practitioner exceptions to the 판로지원법 SME
# priority that the organizer applies to v14–v18, each behind its own switch.
# A (X5_WASTE_EXEMPT): a 폐기물 처리 용역 is a categorical 우선조달 exception (운영요령 제44조제3호, 시행령 제2조의3①5); dev DEV-149
# (v16) and DEV-151 (v18), 폐기물 notices with no size clause and no statement, are 0. v16/v18.
X5_WASTE = re.compile(r'폐\s*기\s*물\s*(처\s*리|수\s*거|운\s*반|위\s*탁)|건\s*설\s*폐\s*기\s*물|임\s*목\s*폐\s*기\s*물|의\s*료\s*폐\s*기\s*물|하\s*수\s*찌\s*꺼\s*기'
                      r'|폐\s*기\s*물[^\n]{0,12}용\s*역')
# C (X5_EXC_WORDING): an exception stated in words the size reading misses, in any document — "의무적용 대상이 아닙니다",
# "제2조의3 제1항 제N호 … 예외/적용하지", "우선조달 … 미적용/예외가 적용", "유찰로 인해 … 완화/확대" (시행령 제2조의3 ①, ②). v16/v18.
X5_WAIVER = re.compile(r'(의\s*무\s*)?적\s*용\s*대\s*상\s*이?\s*아\s*[니닙]|제\s*2\s*조\s*의\s*3\s*(제\s*1\s*항\s*)?제?\s*[1345]\s*호[^.。]{0,120}?(예\s*외|적\s*용\s*하\s*지|않)'
                       r'|우\s*선\s*조\s*달[^.。]{0,30}(미\s*적\s*용|적\s*용\s*하\s*지\s*않|대\s*상\s*이?\s*아\s*[니닙]|예\s*외\s*가\s*적\s*용)|유\s*찰\s*로\s*인\s*해[^.。]{0,40}(완\s*화|확\s*대)')
# D (X5_DP_LISTED_ANY): a 직접생산 requirement the model reads as the procured work ("과업과 같은 종류") and that cites a listed
# non-SW 경쟁제품 admitted at P makes the purchase that 경쟁제품 (goods too; certified_as_listed covers services only): SME
# competition is the 판로지원법 제7조 regime and 제4조② takes it out of v14/v15/v17. SW products stay (8111…, 정보시스템).
X5_SW_PRODUCT = re.compile(r'8111\d{6}|정보시스템|소프트웨어')
# E (X5_V17_CLASS): a v17 clause restricting to 소기업·소상공인 with 벤처기업·창업기업 companions is the small class of 국계령
# 제21조①10가 / 지방령 제20조①12가, as is "(소기업,소상공인으로 제한)"; law titles and the 판단기준일 note name no class.
X5_SMALL = re.compile(r'소\s*기\s*업|소\s*상\s*공\s*인')
X5_VENTURE = re.compile(r'벤\s*처\s*기\s*업|창\s*업\s*(기\s*업|자)')
X5_SME = re.compile(r'중\s*소\s*기\s*업\s*자?(?!\s*(제품|기본법|범위|을\s*창업|창업|창\s*업))|중\s*[·ㆍ・‧․]\s*소\s*기\s*업|중\s*기\s*업|중\s*,\s*소\s*기\s*업')
X5_SMALL_PAREN = re.compile(r'\(\s*소\s*기\s*업\s*[,·]\s*소\s*상\s*공\s*인\s*(으\s*로\s*)?제\s*한\s*\)')
X5_LAW_NAMES = re.compile(r'중소기업제품\s*구매촉진\s*및\s*판로지원에\s*관한\s*법률(\s*시행령)?|중소기업기본법(\s*시행령)?|중소기업\s*범위\s*및\s*확인에\s*관한\s*규정'
                          r'|중소기업창업\s*지원법|중소기업을\s*창업하여|중소기업\s*범위|판단기준일은[^)]*\)')
# F (X5_TAG_NOT_POSITIVE): the 나라장터 method tag ("(국내입찰/…/제한경쟁_…)") or a header line as the only evidence is no
# participation clause (talkboard DEV-039); v14/v15/v17 do not fire on it.
X5_TAG = re.compile(r'국\s*내\s*입\s*찰\s*/|제\s*한\s*경\s*쟁\s*[_(（]')
# G (X5_NOT_PROCUREMENT): an operator-pays contract (구내식당·자판기·매장 운영자 선정 paying 임대료·사용료 to the orderer) sells a
# right of use and procures nothing (v16/v18; the rent is paid or is the bid price: 월·연 임대료, 임대보증금); a notice titled
# 수의계약 or 견적 is no competitive bid (dev DEV-144; all five).
X5_LEASE_TITLE = re.compile(r'(운\s*영\s*자|운\s*영\s*업\s*체)\s*선\s*정|임\s*대\s*운\s*영|임\s*대\s*업\s*체|자\s*동\s*판\s*매\s*기|매\s*장\s*운\s*영'
                            r'|식\s*당[^\n]{0,10}위\s*탁\s*운\s*영|카\s*페[^\n]{0,10}위\s*탁\s*운\s*영')
X5_LEASE_PAY = re.compile(r'(임\s*대\s*료|사\s*용\s*료|임\s*차\s*료)[^.。]{0,40}(납\s*부|납\s*입|입\s*금|지\s*급\s*비\s*율)|(월|연|月)\s*임\s*대\s*료|임\s*대\s*보\s*증\s*금')
X5_PRIVATE_TITLE = re.compile(r'수\s*의\s*계\s*약|견\s*적')
# H (X5_INSURANCE_EXEMPT, probe only): an insurance contract can only go to 보험업법 제4조 insurers, so 우선조달 does not reach
# it in substance; the organizer's stance is unknown (no dev case). v16/v18. The title is a label title or the band-tagged
# title line ("…자동차보험 가입(제한경쟁·5천만원미만)").
X5_INSURANCE = re.compile(r'보\s*험\s*(가\s*입|계\s*약|용\s*역|입\s*찰|공\s*고)|단\s*체\s*(상\s*해\s*)?보\s*험|배\s*상\s*책\s*임\s*보\s*험|종\s*합\s*보\s*험'
                          r'|자\s*동\s*차\s*보\s*험|재\s*해\s*보\s*험|안\s*전\s*보\s*험|보\s*험\s*공\s*제|재\s*해\s*보\s*상')
# Adds (X5_METHOD_STATEMENT): a method statement ("입찰방법: 제한경쟁 … 소기업 및 소상공인", "○ 중소기업자 간 제한경쟁 입찰,
# 협상에 의한 계약") names the procedure, not who may bid; when every restriction line is one and the 공고문's qualification
# section names no size class, the notice restricts nobody (talkboard: 입찰방법 표시 is no restriction; dev DEV-039, a method
# statement only, is v18 = 1). A declaration citing 판로지원법 ("…제2조의2 … 제한경쟁입찰로 진행합니다") is a restriction. v16/v18.
X5_METHOD = re.compile(r'(입\s*찰|계\s*약)\s*(방\s*법|방\s*식)|제\s*한\s*경\s*쟁\s*입\s*찰|협\s*상\s*에\s*의\s*한\s*계\s*약')
X5_WHO = re.compile(r'(으로|로)\s*서|확\s*인\s*서|소\s*지|자\s*격|에\s*한\s*(함|정|하)|만\s*(참|입\s*찰)|참\s*가\s*할\s*수|업\s*체|자\s*(는|만|에)'
                    r'|판\s*로|제\s*2\s*조\s*의\s*2|시\s*행\s*령|진\s*행')


# Adds (X5_DECL_BID): the 공고문's own bid-section declaration that the bid is a 판로지원법 제2조의2 restricted competition
# ("…시행령 제2조의2 … 에 따라 중소기업자(소상공인 포함)간 제한경쟁입찰로 진행합니다") restricts who may bid as a qualification
# clause does; v14/v15/v17 read it when the qualification section states no class.
X5_DECL_LAW = re.compile(r'판\s*로\s*지\s*원|제\s*2\s*조\s*의\s*2')


def x5_declared(b, state, lines):
    if state is not None or not switches.X5_DECL_BID:
        return state, lines
    decl = [ln for ln in lines_where(b, 'size', 역할='참가자격 제한') if ln.doc_type == '공고문' and ln.sec == 'BID'
            and PRIORITY_DECL.search(own_clause(b, ln)) and X5_DECL_LAW.search(own_clause(b, ln))
            and not AMOUNT_TIER.search(own_clause(b, ln))]
    classes = [size_class(b, ln) for ln in decl]
    return ('small' if 'small' in classes else 'sme' if 'sme' in classes else None), decl


def method_only(b, lines):
    if not lines or not all(X5_METHOD.search(ln.text) and not X5_WHO.search(ln.text) for ln in lines):
        return False
    return not any(SIZE_WORD_CHEAP.search(ln.text) and not X5_METHOD.search(ln.text) and qual_section(ln, b.notice)
                   for ln in b.notice.lines if ln.doc_type == '공고문')


def x5_titles(b):
    """Label titles and the band-tagged 공고문 lines, cut at the band tag."""
    tagged = [ln.text for ln in [x for x in b.notice.lines if x.doc_type == '공고문'][:60] if catalog.BAND_TAG.search(ln.text)]
    return ' '.join(b.titles[:3] + [t[:catalog.BAND_TAG.search(t).start()] for t in tagged])


def x5_head(b):
    """The notice head the audit read: the first 60 공고문 lines."""
    return '\n'.join(ln.text for ln in [x for x in b.notice.lines if x.doc_type == '공고문'][:60])


def x5_flat(b):
    return re.sub(r'\s+', ' ', '\n'.join(ln.text for ln in b.notice.lines))


def waste_service(b):
    return bool(X5_WASTE.search(' '.join(b.titles[:3])) or X5_WASTE.search(x5_head(b)[:1200]))


def sw_sme_band(b):
    """B (X5_SW_EXEMPT): an SW사업 below 20억 is in the band where 소프트웨어 진흥법 제48조② and the 중소 SW사업자 지침 (별표1)
    admit only 중소 SW사업자 — the SW-law regime, not 판로지원법 (dev DEV-132 SW 유지보수 1.67억: v16 = 0, v20 = 1). All five."""
    return b.sw_project == '소프트웨어 개발·구축·유지관리·운영' and b.meta.P is not None and b.meta.P < 20e8


def dp_listed_object(b):
    cat = catalog.load()
    return any(b.read('dp', ln).get('인증 품목') == '과업과 같은 종류' and not X5_SW_PRODUCT.search(ln.text)
               and catalog.cites_listed(clause_text(b.notice, ln), cat, b.meta.P) for ln in dp_required(b, positive=True))


def small_with_companions(lines):
    flat = re.sub(r'[「」『』｢｣\s]', '', ' '.join(ln.text for ln in lines)).replace('중,소기업', '중·소기업')
    norm = X5_LAW_NAMES.sub(' ', flat)
    return bool(lines) and bool(X5_VENTURE.search(norm) and X5_SMALL.search(norm) and not X5_SME.search(norm)
                                or X5_SMALL_PAREN.search(flat))


def tag_only(lines):
    return bool(lines) and all(ln.sec == 'TOP' or X5_TAG.search(ln.text) for ln in lines)


def x5_positive_off(b, lines):
    """v14/v15/v17: D (listed 경쟁제품 by 직생) and F (tag-only evidence)."""
    return (switches.X5_DP_LISTED_ANY and dp_listed_object(b)) or (switches.X5_TAG_NOT_POSITIVE and tag_only(lines))


def x5_absence_off(b):
    """v16/v18: A (폐기물), C (exception wording), G (operator pays) and H (insurance)."""
    if switches.X5_WASTE_EXEMPT and waste_service(b):
        return True
    if switches.X5_INSURANCE_EXEMPT and X5_INSURANCE.search(x5_titles(b)):
        return True
    if not (switches.X5_EXC_WORDING or switches.X5_NOT_PROCUREMENT):
        return False
    flat = x5_flat(b)
    return bool(switches.X5_EXC_WORDING and X5_WAIVER.search(flat)
                or switches.X5_NOT_PROCUREMENT and X5_LEASE_TITLE.search(' '.join(b.titles[:3]) + ' ' + x5_head(b)[:600])
                and X5_LEASE_PAY.search(flat))


def _general(b):
    if not switches.SIZE_PRIVATE and b.meta.method == '수의계약':
        return False
    if switches.PRICE_FLOOR and b.meta.P is not None and b.meta.P < switches.PRICE_FLOOR:
        return False     # a unit price or placeholder, not the contract's 추정가격: no band can be judged
    if switches.X5_SW_EXEMPT and sw_sme_band(b) or switches.X5_NOT_PROCUREMENT and X5_PRIVATE_TITLE.search(' '.join(b.titles[:3])):
        return False
    return b.scope.competitive is False and b.meta.P is not None and not certified_as_listed(b)


def v14(b):
    if not _general(b) or b.meta.P < NOTICE_AMOUNT:
        return None
    state, lines, _ = size_state(b, positive=True)
    state, lines = x5_declared(b, state, lines)
    if x5_positive_off(b, lines):
        return None
    return lines[0] if state in ('small', 'sme') else None


def v15(b):
    if not _general(b) or not (EOK <= b.meta.P < NOTICE_AMOUNT):
        return None
    state, lines, _ = size_state(b, positive=True)
    state, lines = x5_declared(b, state, lines)
    if x5_positive_off(b, lines):
        return None
    if state == 'small':
        return next(ln for ln in lines if size_class(b, ln) == 'small')
    return None


# 판로지원법 시행령 제2조의3②: an orderer that does not apply the SME priority may enter the reason in the e-procurement
# system instead of the notice, so a notice with no size clause shows a v16/v18 violation only when 나라장터 registers a
# size restriction (조항호내용) that the notice leaves out. Competition-product designations are not such a registration.
SIZE_REGISTERED = re.compile(r'중\s*기업|소\s*기업|소\s*상공인|중소\s*기업자')
COMPETITION_REGISTERED = re.compile(r'중기간\s*경쟁\s*제품|지정\s*[.·]?\s*고시한\s*제품|지정\s*공고한\s*물품')


def size_registered(b):
    c = str(b.meta.clause or '')
    return bool(SIZE_REGISTERED.search(c)) and not COMPETITION_REGISTERED.search(c)


def v16(b):
    if not _general(b) or not (EOK <= b.meta.P < NOTICE_AMOUNT):
        return None
    if switches.SIZE_ABSENCE_NEEDS_REG and not size_registered(b):
        return None
    if x5_absence_off(b):
        return None
    state, lines, exc = size_state(b)
    if switches.X5_METHOD_STATEMENT and method_only(b, lines):
        state = None
    return (True if state is None and not exc and not designated_class_limit(b)
            and not (switches.AUDIT_FIXES and sme_certificate_required(b)) else None)


# 판로지원법 시행령 제2조의2 ①1 단서: below 1억 the restriction may widen to all SMEs when the 소기업·소상공인 bid failed (유찰)
# or three or fewer eligible small firms evidently exist; a re-announcement or a stated 가목·나목 reason is such a case.
REBID_TITLE = re.compile(r'재\s*공고|재\s*입찰|\(\s*재\s*\)')
SME_WIDENING = re.compile(r'유\s*찰\s*(되어|됨에|로\s*인|에\s*따라|되었|된\s*(건|사업|입찰))|제\s*2\s*조의\s*2\s*제\s*1\s*항\s*제\s*1\s*호\s*(가|나)\s*목'
                          r'|3\s*인\s*이하임이\s*명백')


# Audit RF (switch V17_WIDEN_ANYWHERE): the widening is also read in every document line — a report of this bid's failed
# small-firm bid next to a size or qualification word, a citation of 제1호 가목·나목·단서 or "3인 이하임이 명백", or an explicit
# widening to 중기업 under a cited 판로지원 provision; the conditional re-bid boilerplate ("유찰되었을 경우") is none.
PAST_FAIL = re.compile(r'유\s*찰\s*(되\s*어|됨\s*에|로\s*인|되\s*었(?!\s*을\s*(경\s*우|때))|된\s*(건|사\s*업|입\s*찰))|무\s*응\s*찰')
SIZE_CONTEXT = re.compile(r'소\s*기\s*업|소\s*상\s*공\s*인|중\s*소\s*기\s*업|참\s*가\s*자\s*격|제\s*한\s*경\s*쟁')
CLAUSE_1 = re.compile(r'제\s*2\s*조\s*의\s*2\s*제\s*1\s*항\s*제\s*1\s*호\s*((가|나)\s*목|단\s*서)|3\s*인\s*이\s*하\s*임\s*이\s*명\s*백')
EXPLICIT_WIDEN = re.compile(r'중\s*기\s*업\s*(을\s*)?포\s*함|중\s*소\s*기\s*업\s*(자\s*)?(으\s*로|까\s*지)\s*(참\s*가\s*자\s*격\s*을\s*)?확\s*대')
PANRO = re.compile(r'판\s*로|제\s*2\s*조\s*의\s*[23]|우\s*선\s*조\s*달')


def sme_widening_allowed(b):
    if bool(REBID_TITLE.search(' '.join(b.titles[:2]))) or any(
            SME_WIDENING.search(ln.text) for ln in b.notice.lines[:120] if ln.doc_type == '공고문'):
        return True
    if switches.V17_WIDEN_ANYWHERE:
        return any(PAST_FAIL.search(ln.text) and SIZE_CONTEXT.search(ln.text) or CLAUSE_1.search(ln.text)
                   or EXPLICIT_WIDEN.search(ln.text) and PANRO.search(ln.text) for ln in b.notice.lines)
    return False


def v17(b):
    if not _general(b) or b.meta.P >= EOK or sme_widening_allowed(b):
        return None
    if switches.REG_CONSISTENCY and sme_registered(b):
        return None
    state, lines, _ = size_state(b, positive=True)
    state, lines = x5_declared(b, state, lines)
    if x5_positive_off(b, lines) or switches.X5_V17_CLASS and small_with_companions(lines):
        return None
    return lines[0] if state == 'sme' else None


def v18(b):
    if not _general(b) or b.meta.P >= EOK:
        return None
    if switches.SIZE_ABSENCE_NEEDS_REG and not size_registered(b):
        return None
    if x5_absence_off(b):
        return None
    state, lines, exc = size_state(b)
    if switches.X5_METHOD_STATEMENT and method_only(b, lines):
        state = None
    return True if state is None and not exc and not designated_class_limit(b) else None


# ---------------------------------------------------------------- pledges, software, joint contracts, briefings
# 집행기준 제5조의3 ③: the orderer concludes the supply/support agreement with the maker before the notice and the winner
# obtains the pledge after the award; a line reporting that agreement, recommending a document, or asking for partner
# cooperation agreements is not a pledge demanded of bidders, and "제출 가능한 업체" without a time sets no bid-time duty.
ORDERER_AGREEMENT = re.compile(r'(수\s*요\s*기\s*관|발\s*주\s*기\s*관|\[수요기관[^\]]*\])\s*(와|과|은|는|이|가|에서)?.{0,24}(제\s*조|공\s*급|기\s*술\s*지\s*원)[^.]{0,30}협\s*약.{0,12}(체\s*결|되어)')
PLEDGE_NOT_DEMANDED = re.compile(r'권\s*고|권\s*장|상\s*호\s*협\s*력|협\s*력\s*(관\s*계|체\s*계)')
UNTIMED_CAPABILITY = re.compile(r'제\s*출\s*(이\s*)?가\s*능\s*한\s*(업\s*체|자)')
BID_TIME = re.compile(r'입\s*찰|투\s*찰|견\s*적|마\s*감|전\s*일|이\s*전|까\s*지|제\s*안\s*서|적\s*격\s*심\s*사|낙\s*찰\s*자\s*(결\s*정|선\s*정)\s*전')


# The pledge must come from a third party the line names (organizer v19 lines name 제조사·제조회사·공급사·기술지원사); a
# "확약서 1부" list entry or an attached form with no issuer is the bidder's own undertaking as far as the notice says.
THIRD_PARTY = re.compile(r'제\s*조\s*(사|원|회\s*사|업\s*체|자)|공\s*급\s*(사|업\s*체|원)|기\s*술\s*지\s*원\s*사|총\s*판|대\s*리\s*점|본\s*사|원\s*천\s*사|개\s*발\s*사|제\s*작\s*사|판\s*매\s*사')
# Audit F: "본 사업", "개발사업", "제작사업" name no issuer.
THIRD_PARTY_FIX = re.compile(r'제\s*조\s*(사|원|회\s*사|업\s*체|자)|공\s*급\s*(사|업\s*체|원)|기\s*술\s*지\s*원\s*사|총\s*판|대\s*리\s*점|본\s*사(?!\s*업)|원\s*천\s*사'
                             r'|개\s*발\s*사(?!\s*업)|제\s*작\s*사(?!\s*업)|판\s*매\s*사(?!\s*업)')


# 집행기준 제5조의3: the 물품공급·기술지원 확약서 is issued by the maker, supplier or support company, so a sentence that
# demands that document (제출·보유·발급·첨부 …) names its third-party issuer; a document-list entry ("…확약서 1부") or a form
# title does not (dev DEV-051, 096).
SUPPLY_PLEDGE = re.compile(r'(?:물\s*품\s*|정\s*품\s*)?공\s*급\s*[·ㆍ및\s]*(?:기\s*술\s*지\s*원\s*|무\s*상\s*지\s*원\s*)?(?:\(\s*A\s*/\s*S\s*\)\s*)?확\s*약\s*서'
                           r'|기\s*술\s*지\s*원\s*(?:\(\s*A\s*/\s*S\s*\)\s*)?확\s*약\s*서')
PLEDGE_DEMAND = re.compile(r'제\s*출|보\s*유|발\s*급|첨\s*부|구\s*비|소\s*지|받\s*아|받\s*은')
LIST_ENTRY = re.compile(r'\d+\s*부\s*[\.。)）]?\s*$|각\s*\d+\s*부|_\s*\d+\s*부')


def names_issuer(ln):
    t = ln.text
    if (THIRD_PARTY_FIX if switches.AUDIT_FIXES else THIRD_PARTY).search(t):
        return True
    return bool(SUPPLY_PLEDGE.search(t) and PLEDGE_DEMAND.search(t)) and ln.sec != 'DOCS' and not LIST_ENTRY.search(t)


# Audit R2-D: "제출할 수 있는 업체", "제출 가능 업체" and "발급이 가능한 업체" state a capability without a time, as "제출 가능한
# 업체" does, and "…발급받을 수 있음" advises; a pledge demand names the supply/support document (확약서, 공급(자)증명, 공급확인서,
# 공급·기술지원 협약서); a note that the pledge is issued between the maker and the winner or contract party, or a pointer to an
# attached form, demands nothing of bidders.
UNTIMED_CAPABILITY2 = re.compile(UNTIMED_CAPABILITY.pattern + r'|제\s*출\s*할\s*수\s*있\s*는\s*(업\s*체|자)|제\s*출\s*가\s*능\s*(업\s*체|자)'
                                 r'|(발\s*급|증\s*명)\s*(이\s*)?가\s*능\s*한\s*(업\s*체|자)|발\s*급\s*(받\s*을|할)\s*수\s*있\s*는\s*(업\s*체|자)'
                                 r'|발\s*급\s*받\s*을\s*수\s*있\s*(음|습\s*니\s*다|다)')
PLEDGE_DOC = re.compile(r'확\s*약\s*서|공\s*급\s*(자\s*)?증\s*명|공\s*급\s*확\s*인\s*서|기\s*술\s*지\s*원\s*(확\s*약|증\s*명|확\s*인)'
                        r'|(공\s*급|기\s*술\s*지\s*원)[^.。]{0,10}협\s*약\s*서')
ISSUED_BETWEEN = re.compile(r'발\s*급\s*(은|는)[^.。]{0,60}(낙\s*찰\s*자|계\s*약\s*(대\s*상\s*자|상\s*대\s*자))\W{0,3}\s*간')
# A line that only points to an attached form ("…기술지원협약서는 [붙임2] 참고") demands nothing.
REFERENCE_ONLY = re.compile(r'(참\s*고|참\s*조)\s*[.。)）]?\s*$')
# Audit R2-D (집행기준 제5조의3; dev DEV-033 "1-4) 제조회사 공급 증명원 및 A/S 확약서 각 1부"): a named-issuer pledge item under a
# pre-award submission-list heading (제출서류·구비서류·입찰(등록)·견적·제안서 서류) of the 공고문 (which sets what is submitted with
# the bid) is demanded before the award; "계약시 구비서류" is not, nor is a document scored in an evaluation table (an
# evaluation factor, not a demand).
SUBMIT_HEAD = re.compile(r'제\s*출\s*(할\s*)?서\s*류|구\s*비\s*서\s*류|입\s*찰\s*(등\s*록|참\s*가)\s*(신\s*청\s*)?(시\s*)?서\s*류|입\s*찰\s*참\s*가\s*신\s*청\s*서'
                         r'|견\s*적\s*서?\s*(제\s*출\s*)?서\s*류|제\s*안\s*서\s*(제\s*출\s*)?(서\s*류|목\s*차)')
POST_HEAD = re.compile(r'계\s*약\s*(시|체\s*결)|낙\s*찰\s*자|납\s*품\s*시|검\s*수|계\s*약\s*상\s*대\s*자|착\s*수\s*시')


def pre_award_list(b, ln):
    if ln.doc_type != '공고문' or ln.sec == 'EVAL' or POST_HEAD.search(ln.text):
        return False
    for x in reversed(b.notice.window(ln.i, 15, 0)[:-1]):
        t = x.text.strip()
        if not t or len(t) > 60:
            continue
        if SUBMIT_HEAD.search(t) or POST_HEAD.search(t):
            return bool(SUBMIT_HEAD.search(t)) and not POST_HEAD.search(t)
    return False


def pledge_demanded(ln):
    t = ln.text
    if ORDERER_AGREEMENT.search(t) or PLEDGE_NOT_DEMANDED.search(t) or not names_issuer(ln):
        return False
    return not ((UNTIMED_CAPABILITY2 if switches.AUDIT_FIXES2 else UNTIMED_CAPABILITY).search(t) and not BID_TIME.search(t))


# Audit F: a demanding sentence set at a pre-award stage (적격심사·서류심사·제안서 평가·입찰 시, 개찰·낙찰자 결정 전) asks for
# the pledge before the award whatever the model read (집행기준 제5조의3: the pledge is obtained after the award; dev DEV-037).
PRE_AWARD = re.compile(r'(적\s*격\s*심\s*사|서\s*류\s*심\s*사|제\s*안\s*서?|입\s*찰|투\s*찰|개\s*찰\s*(전|이\s*전)|낙\s*찰\s*자?\s*(결\s*정|선\s*정)\s*(전|이\s*전)'
                       r'|낙\s*찰\s*통\s*보\s*(이\s*)?전)[^.。]{0,10}?제\s*출')
POST_AWARD = re.compile(r'계\s*약\s*체\s*결|낙\s*찰\s*(후|이\s*후|된\s*후|자\s*는)|낙\s*찰\s*\(?\s*예\s*정\s*\)?\s*자|계\s*약\s*상\s*대\s*자|납\s*품|검\s*수')


def before_award(text):
    return bool(PRE_AWARD.search(text)) and not POST_AWARD.search(text)


def pledge_document(b, ln):
    clause = clause_text(b.notice, ln)
    return bool(PLEDGE_DOC.search(clause)) and not ISSUED_BETWEEN.search(clause) and not REFERENCE_ONLY.search(ln.text)


# Audit R3-C (집행기준 제5조의3: the supply/support pledge is issued by the maker, supplier or support company): the issuer and
# the pledge document share one sentence (a merged qualification block holds unrelated ones), and the bidder's own
# undertakings — "자체 기술지원확약서", 청렴 서약, 입찰보증금 지급확약, "전자입찰서 제출로 확약서 제출을 갈음" — are no such pledge.
# A document-list entry ("제조회사 공급 증명원 및 A/S 확약서 각 1부", dev DEV-033) needs no demand verb of its own.
OWN_PLEDGE = re.compile(r'(자\s*체|자\s*사)[^.。]{0,10}확\s*약\s*서|청\s*렴[^.。]{0,20}(서\s*약|확\s*약)\s*서?'
                        r'|보\s*증\s*금[^.。]{0,12}확\s*약\s*서?|확\s*약\s*서[^.。]{0,12}갈\s*음')
PLEDGE_SENTENCE = re.compile(r'(?<=[다함음임됨])\s*[.。]\s*|\s+(?=[◦○●◎▶►□■※•]\s*)|\s+(?=[가-하]\s*[\.\)]\s)|\s+(?=\(?\d{1,2}\)\s)')


def pledge_sentence(b, ln):
    issuer = THIRD_PARTY_FIX if switches.AUDIT_FIXES else THIRD_PARTY
    for s in PLEDGE_SENTENCE.split(clause_text(b.notice, ln)):
        s = OWN_PLEDGE.sub(' ', s)
        if PLEDGE_DOC.search(s) and (issuer.search(s) or SUPPLY_PLEDGE.search(s)):
            return True
    return False


# Probe (switches.V19_HOLD_BY_BID): a pledge the bidder must hold or obtain by the bid deadline is timed before the award even
# when it is handed over at contract (항목표 v19 비고 "입찰 전 발급, 계약시 제출 등 표현 다양").
HOLD_BY_BID = re.compile(r'(입\s*찰\s*서?|제\s*안\s*서|견\s*적\s*서|투\s*찰)\s*(제\s*출\s*)?마\s*감\s*(일|시\s*간|일\s*시)?\s*(전\s*일|이\s*전|전)?\s*까\s*지'
                         r'[^.。]{0,40}(보\s*유|발\s*급|구\s*비|확\s*보|취\s*득)')


# Expert audit X6 (switch V19_STAGE; runs/rebuild_c/transfer_20260925/audit/expert/X6/REPORT.md): 정부 입찰·계약 집행기준
# 제5조의3 ③ — the winner obtains the maker's 확약서 after the award. A pledge every bidder must hold or hand in at or before the
# bid is the violation; one demanded of the 적격심사 대상자·낙찰예정자 is the ordinary award procedure (조달청 standard clause),
# an untimed capability clause is not marked (dev 0 four times), and a maker or dealer certificate alone is no pledge (dev
# DEV-152 = 0). Stage classes as the audit's v19_stage.py assigns them; only PRE and PRE_LIST demands fire.
X6_LAWFUL = re.compile(r'발\s*급\s*(은|는)[^.。]{0,60}(낙\s*찰\s*자|계\s*약\s*(대\s*상\s*자|상\s*대\s*자))\W{0,3}\s*간|발\s*급\s*받\s*을\s*수\s*있\s*(음|습|다|도록)'
                       r'|협\s*의\s*(하\s*여|후)\s*입\s*찰|수\s*요\s*기\s*관[^.。]{0,30}협\s*약[^.。]{0,10}체\s*결|발\s*주\s*기\s*관[^.。]{0,30}협\s*약[^.。]{0,10}체\s*결')
X6_PRE_TIME = re.compile(r'(입\s*찰\s*서?|투\s*찰|견\s*적\s*서?|제\s*안\s*서|규\s*격\s*(입\s*찰\s*)?서?)\s*(제\s*출\s*)?(마\s*감\s*)?(일\s*(시)?\s*)?(전\s*일|이\s*전|전\s*까\s*지|까\s*지|시|전|당\s*일)'
                         r'|입\s*찰\s*(참\s*가\s*)?(시|전|전\s*까\s*지)|개\s*찰\s*(시|전|일\s*전|일\s*까\s*지|이\s*전)|(참\s*가|입\s*찰)\s*등\s*록\s*(마\s*감|시|일)|규\s*격\s*심\s*사'
                         r'|사\s*전\s*(제\s*출|구\s*비|확\s*보)|입\s*찰\s*참\s*가\s*시|투\s*찰\s*마\s*감|제\s*안\s*서\s*제\s*출\s*시|사\s*전\s*판\s*정')
X6_PRE_HOLD = re.compile(r'보\s*유|소\s*지|확\s*보\s*하\s*여|구\s*비\s*한|받\s*아\s*야|발\s*급\s*받\s*아')
X6_SUBJECT_BIDDER = re.compile(r'입\s*찰\s*(참\s*가\s*)?(업\s*체|자|예\s*정\s*자)|참\s*가\s*(업\s*체|자)|응\s*찰\s*자|제\s*안\s*(업\s*체|사|자)')
X6_LIST_HEAD_PRE = re.compile(r'제\s*출\s*(할\s*)?서\s*류|구\s*비\s*서\s*류|입\s*찰\s*(등\s*록|참\s*가)\s*(신\s*청\s*)?(시\s*)?서\s*류|견\s*적\s*서?\s*(제\s*출\s*)?서\s*류'
                              r'|제\s*안\s*서\s*(제\s*출\s*)?(서\s*류|목\s*차)|첨\s*부\s*서\s*류')
X6_LIST_HEAD_POST = re.compile(r'계\s*약\s*(시|체\s*결)|낙\s*찰\s*자|납\s*품\s*시|검\s*수|계\s*약\s*상\s*대\s*자|착\s*수\s*시')
X6_LIST_HEAD_POST2 = re.compile(r'계\s*약\s*(이\s*)?전')
X6_DOC_WORD = re.compile(r'서\s*류|제\s*출')
X6_LIST_ENTRY = re.compile(r'\d+\s*부\s*[\.。)）]?\s*$|각\s*\d+\s*부|_\s*\d+\s*부')
X6_PRE_CAP = re.compile(r'(제\s*출|발\s*급|증\s*명)\s*(이\s*)?(할\s*수\s*있\s*는|가\s*능\s*한?|가\s*능\s*(업\s*체|자))|(소\s*지|보\s*유)\s*한\s*(업\s*체|자)|확\s*보\s*하\s*여\s*제\s*출')
X6_QUAL_STAGE = re.compile(r'적\s*격\s*심\s*사|계\s*약\s*이\s*행\s*능\s*력\s*심\s*사|낙\s*찰\s*(자\s*)?(결\s*정|선\s*정|통\s*보)\s*(전|이\s*전|시)|낙\s*찰\s*(예\s*정|대\s*상)\s*자'
                           r'|심\s*사\s*(서\s*류|시)|서\s*류\s*심\s*사')
X6_POST = re.compile(r'계\s*약\s*(체\s*결\s*)?(시|후|이\s*후|전\s*까\s*지|체\s*결\s*전)|낙\s*찰\s*(후|이\s*후|된\s*후|자\s*는|자\s*에\s*한)|계\s*약\s*상\s*대\s*자'
                     r'|납\s*품\s*(시|전|후)|검\s*수|착\s*수\s*(시|전)|계\s*약\s*담\s*당\s*자\s*에\s*게|납\s*품\s*업\s*체\s*(는|가)')
# A line that introduces the document list ("…마감일시까지 아래 서류를 제출") heads it however long it is.
X6_LIST_INTRO = re.compile(r'(아\s*래|다\s*음)\s*(의\s*)?(서\s*류|구\s*비\s*서\s*류|제\s*출\s*서\s*류)')
X6_SENT_END = re.compile(r'[.。다함음임됨)]\s*$')
X6_PLEDGE = re.compile(r'확\s*약|협\s*약')
X6_CAPABILITY = re.compile(r'확\s*보\s*하\s*여\s*제\s*출\s*할\s*수\s*있\s*는|발\s*급\s*이\s*가\s*능\s*한'
                           r'|제\s*출\s*(이\s*)?가\s*능\s*한\s*(업\s*체|자)|제\s*출\s*할\s*수\s*있\s*는\s*(업\s*체|자)')


def x6_pledge_stage(b, ln):
    """'PRE' (at or before the bid, or held by the bidder), 'PRE_LIST' (a bid-document list entry) or another class."""
    doc = [x for x in b.notice.lines if x.doc == ln.doc]
    k = next(n for n, x in enumerate(doc) if x.i == ln.i)
    t = doc[k].text.strip()
    if not X6_SENT_END.search(t) and not X6_LIST_ENTRY.search(t):      # a list entry ("… 각 1부") is complete
        for x in doc[k + 1:k + 3]:
            if x.text.strip():
                t = t + ' ' + x.text.strip()
                break
    if X6_LAWFUL.search(t):
        return 'LAWFUL'
    if X6_PRE_TIME.search(t) or X6_PRE_HOLD.search(t) and X6_SUBJECT_BIDDER.search(t):
        return 'PRE'
    cap, qual = bool(X6_PRE_CAP.search(t)), bool(X6_QUAL_STAGE.search(t))
    if cap and not qual:
        return 'PRE_CAP'
    if qual:
        return 'QUAL_STAGE'
    if X6_POST.search(t):
        return 'POST'
    if X6_LIST_ENTRY.search(doc[k].text):
        for x in reversed(doc[max(0, k - 15):k]):
            s = x.text.strip()
            if s and X6_LIST_INTRO.search(s) and len(s) > 60:
                if X6_PRE_TIME.search(s):
                    return 'PRE_LIST'
                if X6_POST.search(s) or X6_QUAL_STAGE.search(s):
                    return 'POST'
            if not s or len(s) > 60 or X6_LIST_ENTRY.search(s):
                continue                    # a sibling entry ("5) 수의계약 체결 제한 여부 확인서 1부") is no heading
            # A heading names documents or is a short sub-heading ("가. 계약 전"); "…납품 및 검수 완료" heads no list.
            heading = bool(X6_DOC_WORD.search(s)) or len(s) <= 15
            if heading and X6_QUAL_STAGE.search(s):
                return 'QUAL_STAGE'                    # "제출 서류 (적격심사 시 제출)"
            pre = bool(X6_LIST_HEAD_PRE.search(s))
            post = bool(heading and (X6_LIST_HEAD_POST.search(s) or X6_LIST_HEAD_POST2.search(s)))
            if pre or post:
                return 'PRE_LIST' if pre and not post else 'POST'
    return 'OTHER'


def x6_pledge_violation(b, ln):
    """False for a demand at the 적격심사 stage, after the award, under the lawful regime, an untimed capability clause, or
    a document that is no pledge or agreement (maker or dealer certificates); bid-timed, list and unstated demands stand."""
    t = clause_text(b.notice, ln)
    if x6_pledge_stage(b, ln) in ('QUAL_STAGE', 'POST', 'LAWFUL', 'PRE_CAP'):
        return False
    if X6_CAPABILITY.search(t) and not X6_PRE_TIME.search(t):
        return False
    return bool(X6_PLEDGE.search(t))


def v19(b):
    if not switches.V19_PRIVATE and b.meta.private:
        return None
    for ln in b.cands.get('pledge', []):
        r = b.read('pledge', ln)
        timed = (str(r.get('시점', '')).startswith('입찰 전') or (switches.AUDIT_FIXES and before_award(ln.text))
                 or switches.V19_HOLD_BY_BID and bool(HOLD_BY_BID.search(clause_text(b.notice, ln))))
        # Expert audit X6 C19a (switch V19_CPU_TIMED): a demand the CPU stage check places at or before the bid ("투찰 시",
        # "입찰참가업체는 … 보유해야 한다", a bid-document list) is timed although the model read no time (013890, 000155).
        if not timed and switches.V19_CPU_TIMED and x6_pledge_stage(b, ln) in ('PRE', 'PRE_LIST'):
            timed = True
        if (not timed and switches.AUDIT_FIXES2 and switches.V19_LIST_STAGE and r.get('시점', '') in ('불명', '')
                and (ln.doc_type == '공고문' and ln.sec == 'QUAL' or pre_award_list(b, ln))):
            timed = True
        if str(r.get('발급 주체', '')).startswith('제3자') and timed and pledge_demanded(ln) \
                and not ((switches.AUDIT_FIXES2 or switches.V19_PLEDGE_FIXES) and not pledge_document(b, ln)) \
                and not ((switches.AUDIT_FIXES3 or switches.V19_PLEDGE_FIXES) and not pledge_sentence(b, ln)) \
                and not (switches.V19_STAGE and not x6_pledge_violation(b, ln)):
            return ln
    return None


# The 제48조 statement: a line that states whether large (or 중견) SW businesses may bid, or restricts bidding to 중소
# SW businesses under 제48조 / the 중소 SW사업자 지침 (talkboard: SW 신고, 중소기업 확인서 and 상호출자 alone are not it).
SW_STATEMENT = re.compile(r'제\s*48\s*조.{0,80}(참여|참가|입찰|제한|하한|금액)|(대기업|중견\s*기업).{0,40}(참여|참가).{0,20}(제한|불가|가능|허용|없)'
                          r'|중소\s*소프트웨어\s*사업자.{0,30}(만|참여|참가)|참여\s*제한\s*(금액|하한|대상)|사업\s*참여\s*지원에\s*관한\s*지침')
# Audit R3-B: "「소프트웨어 진흥법 시행령」 제41조(중소 소프트웨어사업자의 기준 등) … 해당하는 자" limits bidding to 중소 SW사업자; the
# article number alone is no such statement ("…시행령 제41조에 의해 소프트웨어사업자 … 로 신고된 자" is the SW사업자 신고).
SW_STATEMENT3 = re.compile(SW_STATEMENT.pattern + r'|중소\s*소프트웨어\s*사업자의\s*기준')
CROSS_ONLY = re.compile(r'상호\s*출자')


def sw_statement(b):
    if lines_where(b, 'sw', 표기='대기업 참여제한 여부·근거 기재'):
        return True
    for ln in b.notice.lines:
        t = ln.text
        if (SW_STATEMENT3 if switches.AUDIT_FIXES3 or switches.V20_SCOPE or switches.V20_CONTENT else SW_STATEMENT).search(t) and not (CROSS_ONLY.search(t) and not re.search(r'대기업|중견|중소\s*소프트웨어|하한|금액', t)):
            return True
    return False


# Expert audit X6 C20b (switch V20_ORDERER; audit/expert/X6/REPORT.md): 소프트웨어 진흥법 시행령 제21조 binds 국가기관등;
# 사립대학, 협회, 중앙회, 위원회, 조합, 연합회 and 학회 orderers are outside it. The 공고문's dominant orderer token decides
# (first 5,000 characters; [수요기관(X)] counts 2, [기관(X)] 1).
V20_ORDERER_TOKEN = re.compile(r'\[(수요기관|기관)\(([^)|\]]+)\)')
V20_UNBOUND = ('대학', '협회', '중앙회', '위원회', '조합', '연합회', '학회')


def unbound_orderer(b):
    text = '\n'.join(ln.text for ln in b.notice.lines if ln.doc_type == '공고문')[:5000]
    count = {}
    for m in V20_ORDERER_TOKEN.finditer(text):
        count[m.group(2)] = count.get(m.group(2), 0) + (2 if m.group(1) == '수요기관' else 1)
    return bool(count) and max(count.items(), key=lambda kv: kv[1])[0] in V20_UNBOUND


def v20(b):
    if b.sw_project != '소프트웨어 개발·구축·유지관리·운영':
        return None
    if b.meta.private and not switches.V20_PRIVATE:
        return None
    if switches.V20_ORDERER and unbound_orderer(b):
        return None
    if switches.V20_MIN_ESTIMATE and (b.meta.P or 0) < switches.V20_MIN_ESTIMATE:
        return None
    return None if sw_statement(b) else True


JV_LINE = re.compile(r'지\s*분|출\s*자\s*비\s*율|참\s*여\s*비\s*율|구\s*성\s*비\s*율|참\s*여\s*지\s*분')
PCT = re.compile(r'(\d{1,3}(?:\.\d+)?)\s*(%|퍼센트|％)')
# Audit R2-E: "100분의 5" in a joint-contract line is 5% (the 이해충돌방지법 shareholding boilerplate has no JV context).
FRACTION = re.compile(r'100\s*분\s*의\s*(\d{1,2}(?:\.\d+)?)')


JV_CONTEXT = re.compile(r'공\s*동\s*(수\s*급|도\s*급|이\s*행|계\s*약)|구\s*성\s*원')


def member_minimum(t):
    """Minimum member share (%) a joint-contract line sets, or None."""
    best = None
    for m in PCT.finditer(t):
        v = float(m.group(1))
        if v <= 0 or v >= 50:
            continue
        before, after = t[max(0, m.start() - 24):m.start()], t[m.end():m.end() + 16]
        if re.search(r'대\s*표|주\s*관|주\s*계\s*약|합\s*계|총|전\s*체|±|\+|-', before[-12:]) or re.search(r'차\s*이|초\s*과|이\s*내|이\s*하', after[:8]):
            continue
        floor = re.search(r'^\s*(\)|\s)*이\s*상', after) or re.search(r'최\s*소|하\s*한', before) or re.search(r'^\s*미\s*만.{0,14}(불가|없|제한|안\s*됨|아니)', after)
        if floor:
            best = v if best is None else min(best, v)
    return best


# Audit F: a shareholder's stake ("지분율 5% 이상 주주", "소유구조 현황") is no joint-contract member share (공동계약운용요령).
SHAREHOLDING = re.compile(r'주\s*주|소\s*유\s*구\s*조|주\s*식|지\s*배\s*구\s*조')


def v21(b):
    limit = 10.0 if b.meta.law == '국가' else 5.0
    prev = None
    for ln in b.notice.lines:
        t = ln.text
        if switches.AUDIT_FIXES2 and JV_CONTEXT.search(t):
            t = FRACTION.sub(lambda m: m.group(1) + '%', t)
        context = JV_LINE.search(t) or (prev is not None and prev.doc == ln.doc and JV_LINE.search(prev.text) and ln.sec in ('JV', 'QUAL'))
        prev = ln
        if not context or not PCT.search(t):
            continue
        if switches.AUDIT_FIXES and SHAREHOLDING.search(t):
            continue
        if re.search(r'분담\s*이행', t) and not re.search(r'공동\s*이행', t):
            continue
        low = member_minimum(t)
        if low is not None and low < limit and (JV_CONTEXT.search(t) or re.search(r'최\s*소|지\s*분\s*율|출\s*자\s*비\s*율', t)):
            return ln
    return None


# A bidder's proposal presentation (제안설명회·제안서 발표 in an evaluation, order or screening context) is not the
# orderer's briefing session; "제안설명회" alone is ambiguous in practice and stays a briefing.
# The bidder's own proposal presentation is not the orderer's briefing: it is scored (평정, 최저점), held after the
# proposal is in, conducted by the bidder, or requested by the orderer when needed.
PRESENTATION = re.compile(r'제\s*안\s*(서\s*)?(설\s*명\s*회?|발\s*표).{0,40}(평\s*가|발\s*표|순\s*서|심\s*사)|발\s*표\s*(회|평\s*가|순\s*서|시\s*간)|평\s*가\s*위\s*원|정\s*성\s*평\s*가'
                          r'|제\s*안\s*서?\s*(를\s*)?(제\s*출|접\s*수)\s*(한\s*)?(후|이\s*후|뒤)|제\s*안\s*(업\s*체|사|자)\s*(는|가|은).{0,40}설\s*명\s*회?\s*를?\s*(실\s*시|개\s*최|하\s*여\s*야)'
                          r'|평\s*정|최\s*저\s*점|필\s*요\s*(시|한\s*경\s*우)[^.。]{0,40}설\s*명\s*회?\s*를?\s*요\s*구|기\s*술\s*능\s*력\s*평\s*가\s*를?\s*위\s*하'
                          r'|(심\s*사|평\s*가|선\s*정)\s*(을|를)?\s*거\s*쳐[^.。]{0,20}설\s*명\s*회|설\s*명\s*회\s*참\s*가\s*자\s*격\s*을\s*제\s*한'
                          r'|(평\s*가|심\s*사)\s*결\s*과[^.。]{0,40}설\s*명\s*회|상\s*위\s*\d+\s*개\s*(업\s*체|사)[^.。]{0,30}설\s*명\s*회')
# Who may attend (대표·위임장) is not a duty to attend; resident hearings and reports are contract work, not bid briefings.
ATTENDEE_ONLY = re.compile(r'참\s*(가|석)\s*자\s*(는|의).{0,25}(대\s*표|위\s*임\s*장|대\s*리\s*인|재\s*직)')
NON_BID_BRIEFING = re.compile(r'주\s*민\s*설\s*명\s*회|공\s*청\s*회|보\s*고\s*회|결\s*과\s*설\s*명\s*회')
BID_BRIEFING = re.compile(r'현\s*장\s*설\s*명|과\s*업\s*설\s*명|사\s*업\s*설\s*명|요\s*청\s*서?\s*설\s*명|입\s*찰\s*설\s*명')


# A condition on documents ("구비서류가 누락되면 현장설명·입찰참가 자격이 없다") and a quoted legal provision that merely
# permits the practice ("시행령 제43조제5항에 따라 … 참가하게 할 수 있다") impose no attendance.
# Events the contractor attends while performing ("사업설명회/워크숍 : 담당자 … 주관 참석") are contract duties.
WORK_DUTY = re.compile(r'워\s*크\s*숍|주\s*관\s*(하\s*는\s*)?[^.。]{0,10}참\s*석')
DOC_CONDITION = re.compile(r'서\s*류[^.。]{0,24}(누\s*락|미\s*비|미\s*제\s*출|맞\s*지\s*않)')
PERMISSIVE_LAW = re.compile(r'제\s*\d+\s*조[^.。]*할\s*수\s*있(다|음|습\s*니\s*다)')


# Audit R2-E: the bidder's own presentation (발표자·리허설·참가대상 선정·업체별 요구), an attendee rule for "참가자 전원"
# (대표·위임장·재직) and a quoted invalidity condition ("현장설명에 참가를 요하는 입찰에 있어서 …") impose no attendance at the
# orderer's briefing.
PRESENTATION2 = re.compile(r'발\s*표\s*자|리\s*허\s*설|참\s*가\s*대\s*상\s*(을|를)?\s*선\s*정|(업\s*체|제\s*안\s*사)\s*별\s*로[^.。]{0,20}요\s*구')
ATTENDEE_ALL = re.compile(r'참\s*(가|석)\s*자\s*전\s*원\s*(의|은|는).{0,25}(대\s*표|위\s*임\s*장|대\s*리\s*인|재\s*직)')
QUOTED_INVALIDITY = re.compile(r'(을|를)\s*요\s*하\s*는\s*입\s*찰\s*에\s*있\s*어\s*서')


# The proposers' own session ("제안업체가 5개 이하인 경우: 모든 제안업체 설명회 참가") is the presentation stage unless the
# line names the orderer's briefing.
PROPOSER_SESSION = re.compile(r'제\s*안\s*(참\s*여\s*)?업\s*체[^.。]{0,20}설\s*명\s*회\s*(에\s*)?(참\s*가|참\s*석|실\s*시)')


def bid_briefing(ln):
    t = ln.text
    if PRESENTATION.search(t) or ATTENDEE_ONLY.search(t) or DOC_CONDITION.search(t) or PERMISSIVE_LAW.search(t) or WORK_DUTY.search(t):
        return False
    if switches.AUDIT_FIXES2 and (PRESENTATION2.search(t) or ATTENDEE_ALL.search(t) or QUOTED_INVALIDITY.search(t)):
        return False
    if PROPOSER_SESSION.search(t) and not BID_BRIEFING.search(t):
        return False
    return not (NON_BID_BRIEFING.search(t) and not BID_BRIEFING.search(t))


# Audit RE (switch V22_PRESENTATION): the proposer's presentation (발표, 평가위원, 기술·제안서 평가, 평가회 in the line or the four
# lines above; "제안 내용을 설명", "설명회를 요구") is no orderer's briefing unless the line names one (dev DEV-193, DEV-054 = 0),
# nor is a rule on who registers ("설명회 등록 시 대표자가 등록"; expert audit X6).
PRESENT_CTX = re.compile(r'발\s*표|평\s*가\s*위\s*원|(기\s*술|제\s*안\s*서?)\s*평\s*가|평\s*가\s*회')
PRESENT_LINE = re.compile(r'제\s*안\s*(내\s*용|사\s*항)\s*(을|를)?\s*설\s*명|설\s*명\s*회\s*를?\s*요\s*구'
                          r'|설\s*명\s*회\s*등\s*록\s*시\s*대\s*표\s*자')


def presentation_context(b, ln):
    if BID_BRIEFING.search(ln.text):
        return False
    if PRESENT_LINE.search(ln.text) or PRESENT_CTX.search(ln.text):
        return True
    return any(PRESENT_CTX.search(x.text) for x in b.notice.window(ln.i, 4, 0)[:-1])


def v22(b):
    if not b.meta.negotiation:
        return None
    lines = [ln for ln in lines_where(b, 'brief', 참석='참석해야 입찰·제안 가능') if ln.sec != 'EVAL' and bid_briefing(ln)
             and not (switches.V22_PRESENTATION and presentation_context(b, ln))]
    return lines[0] if lines else None


NO_BRIEF = re.compile(r'생략|미\s*개최|개최\s*(하지\s*)?않|(설명회|설명)\s*(는|은)?\s*[:：]?\s*(없음|없습니다|미개최)|해당\s*없음|미\s*실시|실시\s*하지\s*않|(으로|로)\s*갈음')
# Audit F: a briefing replaced by documents ("제안요청서 및 과업이행요청서로 대체", "과업설명: <과업지시서 및 제안요청서> 참조") is
# not held.
NO_BRIEF_FIX = re.compile(NO_BRIEF.pattern + r'|(으로|로)\s*대\s*체|(과\s*업\s*지\s*시\s*서|제\s*안\s*요\s*청\s*서|과\s*업\s*이\s*행\s*요\s*청\s*서'
                          r'|과\s*업\s*내\s*용\s*서|공\s*고\s*문|규\s*격\s*서)[^.。]{0,40}참\s*조\W{0,3}$')
DEADLINE = re.compile(r'제\s*안\s*서.{0,24}(제\s*출|접\s*수|마\s*감)|(제\s*출|접\s*수)\s*마\s*감')


# Audit R2-E: the briefing's date may sit several lines below its heading ("제안요청 설명회" / blank lines / "일시 : …"): after
# the three lines read so far, read on until the next numbered heading (at most 14 lines).
NEXT_ITEM = re.compile(r'^\s*(\d+\s*[\.\)]|[가-하]\s*[\.\)]|[①-⑳]|\(\s*\d+\s*\)|[ⅰ-ⅹⅠ-Ⅹ]\.)')


def briefing_block(b, ln):
    window = b.notice.window(ln.i, 0, 3)
    for w in b.notice.window(ln.i, 0, 20)[len(window):]:
        if not w.text.strip():
            continue
        if NEXT_ITEM.match(w.text) and not re.search(r'일\s*시|장\s*소|일\s*자', w.text) or len(window) >= 14:
            break
        window.append(w)
    return window


def briefing_date(b):
    year = b.meta.posted.year if b.meta.posted else None
    # Audit R2-E: the longer block is read only when no briefing line has a date in its first lines, so a heading far
    # above another section's date does not take it.
    for block, ln in [(k, x) for k in ((False, True) if switches.AUDIT_FIXES2 else (False,)) for x in b.cands.get('brief', [])]:
        r = b.read('brief', ln)
        if r.get('참석') == '설명회 아님(제안 발표 등)' or not bid_briefing(ln):
            continue
        window = briefing_block(b, ln) if block else b.notice.window(ln.i, 0, 3)
        text = ' '.join(w.text for w in window)
        if (NO_BRIEF_FIX if switches.AUDIT_FIXES else NO_BRIEF).search(ln.text):
            continue
        found = dates.find(text, year)
        if found:
            return found[0][0], ln
    return None, None


def proposal_deadline(b):
    year = b.meta.posted.year if b.meta.posted else None
    best = None
    for ln in b.notice.lines:
        if not DEADLINE.search(ln.text):
            continue
        window = ' '.join(w.text for w in b.notice.window(ln.i, 0, 2))
        found = [d for d, _ in dates.find(window, year)]
        if found:
            d = max(found)
            best = d if best is None or d > best else best
    return best


def v23(b):
    if not (b.meta.local and b.meta.negotiation):
        return None
    brief, ln = briefing_date(b)
    if brief is None:
        return None
    P = b.meta.P or 0
    need = 40 if P >= 10 * EOK else (20 if P >= EOK else 10)
    deadline = proposal_deadline(b)
    short = (lambda gap, n: gap <= n) if switches.V23_LEGAL_COUNT else (lambda gap, n: gap < n)
    if deadline is not None and deadline > brief and short((deadline - brief).days, need):
        return ln
    if b.meta.posted is not None and brief > b.meta.posted and short((brief - b.meta.posted).days, 7):
        return ln
    return None


# ---------------------------------------------------------------- v24: notice vs 나라장터 input on the same field
TAG = re.compile(r'\((수의계약|제한경쟁|일반경쟁|지명경쟁)[·ㆍ・]\s*(\d+(?:천만|억)원)(미만|이상)\)')
BANDS = [('2천만원', 2e7), ('5천만원', 5e7), ('1억원', 1e8), ('3억원', 3e8), ('10억원', 1e9), ('50억원', 5e9), ('100억원', 1e10)]
# A field label, then its value (table cells separated by "|" included).
FIELD_SEP = r'[\s:：|]*(?:금\s*)?[₩￦]?\s*'
EST_LINE = re.compile(r'추\s*정\s*가\s*격' + FIELD_SEP + r'(\d[\d,]{3,})\s*원')
BUDGET_LINE = re.compile(r'(배\s*정\s*예\s*산|사\s*업\s*예\s*산|예\s*산\s*액|소\s*요\s*예\s*산|예\s*산\s*금\s*액)\s*(?:금\s*액)?' + FIELD_SEP + r'(\d[\d,]{3,})\s*원')
METHOD_FIELD = re.compile(r'(계\s*약\s*방\s*법|입\s*찰\s*방\s*법|계\s*약\s*방\s*식|입\s*찰\s*방\s*식)\s*[:：|]?\s*([^\n]{0,85})')
QUOTATION = re.compile(r'소\s*액\s*수\s*의|수\s*의\s*견\s*적|견\s*적\s*(제\s*출|입\s*찰)')
STATED_AMOUNT = re.compile(r'(\d{1,3}(?:,\d{3})+|\d{5,})\s*원')


def band_ok(value, label, side):
    if value is None:
        return None
    edges = dict(BANDS)
    edge = edges.get(label)
    if edge is None:
        return None
    if side == '이상':
        return value >= edge
    lower = 0.0
    for name, e in BANDS:
        if e == edge:
            break
        lower = e
    return lower <= value < edge


def v24_title(b):
    for ln in b.notice.notice_lines()[:80]:
        m = TAG.search(ln.text)
        if not m:
            continue
        method, label, side = m.group(1), m.group(2), m.group(3)
        if b.meta.method and method != b.meta.method:
            return ln
        oks = [band_ok(v, label, side) for v in (b.meta.P, b.meta.B) if v is not None]
        if oks and not any(oks):
            return ln
        return None
    return None


def v24_transposed(b):
    # A stated amount whose digits permute the registered 배정예산 or 추정가격 is that field mistyped (a transposition),
    # so the notice and 나라장터 disagree on it (dev DEV-029: 39,730,000 registered, 37,930,000 stated).
    registered = {str(int(v)) for v in (b.meta.B, b.meta.P) if v}
    for ln in b.notice.notice_lines():
        for m in STATED_AMOUNT.finditer(ln.text):
            d = m.group(1).replace(',', '')
            if switches.V24_FIXES and any(vat_related(float(d), r) for r in (b.meta.B, b.meta.P) if r):
                continue
            if any(len(d) == len(r) and d != r and sorted(d) == sorted(r) for r in registered):
                return ln
    return None


def v24_amount(b):
    if switches.AUDIT_FIXES:
        return v24_amount_parts(b)
    for ln in b.notice.notice_lines()[:150]:
        t = ln.text
        # Same field only: a value far from the registered one (unit price, one lot) is a different amount.
        m = EST_LINE.search(t)
        if m and b.meta.P is not None and '단가' not in t:
            v = float(m.group(1).replace(',', ''))
            if v >= 1e5 and abs(v - b.meta.P) > 1.5 and 0.5 <= v / b.meta.P <= 2:
                return ln
        m = BUDGET_LINE.search(t)
        if m and b.meta.B is not None and '단가' not in t:
            v = float(m.group(2).replace(',', ''))
            if v >= 1e5 and abs(v - b.meta.B) > 1.5 and abs(v - (b.meta.P or 0)) > 1.5 and 0.5 <= v / b.meta.B <= 2:
                return ln
    return None


def vat_related(v, ref):
    """Audit V24: an exact VAT relation explains a difference (REBUILD_BASIS: VAT-inclusive vs exclusive is no mismatch)."""
    return ref is not None and ref > 0 and (abs(v - ref * 1.1) <= 10 or abs(v * 1.1 - ref) <= 10)


# Audit PX (switch V24P_GUARDS): a stated amount within 0.01% of the registered one is rounding, not a mismatch (a 추정가격 split
# written 50,411,400 for 50,410,909; the synthetic edits change amounts by 40% or more and a transposition keeps its own axis).
# It serves agrees(), i.e. the wide reader and the AUDIT_FIXES reader; the P3a reader (v24_amount without AUDIT_FIXES) keeps
# its exact comparison.
ROUNDING = 1e-4


def rounding_gap(v, ref):
    return switches.V24P_GUARDS and ref is not None and ref > 0 and abs(v - ref) / ref < ROUNDING


def agrees(v, ref):
    """Audit F: a stated amount agrees with a registered one when equal, or when it is that value truncated or rounded to
    its own trailing-zero unit, up to 10만원 (81,800,000 for 81,818,182)."""
    if ref is None:
        return False
    if abs(v - ref) <= 1.5 or rounding_gap(v, ref):
        return True
    if switches.V24_FIXES and vat_related(v, ref):
        return True
    digits = str(int(round(v)))
    zeros = min(len(digits) - len(digits.rstrip('0')), 5)
    if zeros < (1 if switches.AUDIT_FIXES2 else 3):
        return False
    unit = 10 ** zeros
    if switches.AUDIT_FIXES2 and v == (ref + unit / 2) // unit * unit:
        return True      # audit R2-D: from 10원 up, and half-up rounding of a VAT split (414,545,455 → 414,545,460)
    if switches.AUDIT_FIXES3 and v == -(-ref // unit) * unit:
        return True      # audit R3-C: rounded up to the stated unit (109,858,880 for 109,858,873)
    return v in (ref // unit * unit, round(ref / unit) * unit)


# Audit R2-D: when one stated estimate or budget agrees with 나라장터, lines labelled as a breakdown (1차년도, 연도별, 단계) are
# parts of it, not the notice's own amount; so are disagreeing values that add up to a registered one.
BREAKDOWN = re.compile(r'\d+\s*(차\s*년\s*도|년\s*차|차\s*년|차\s*분|단\s*계|분\s*기)|연\s*도\s*별|년\s*도\s*별|차\s*수\s*별|내\s*역|세\s*부')


def v24_amount_parts(b):
    """Audit F, organizer definition (talkboard: the 예산 axis compares the notice's budget with meta 배정예산·입찰추정가격): a
    stated 추정가격 or budget disagrees only when it matches neither registered value, and the parts of a split order that
    sum to one of them agree."""
    P, B = b.meta.P, b.meta.B
    ests, buds = [], []
    for ln in b.notice.notice_lines()[:150]:
        t = ln.text
        if '단가' in t:
            continue
        m = EST_LINE.search(t)
        if m:
            ests.append((ln, float(m.group(1).replace(',', ''))))
        m = BUDGET_LINE.search(t)
        if m:
            buds.append((ln, float(m.group(2).replace(',', ''))))
    for found, ref in ((ests, P), (buds, B)):
        vals = [v for _, v in found if v >= 1e5]
        if len(vals) >= 2 and any(r is not None and abs(sum(vals) - r) <= 2 for r in (P, B)):
            continue
        total_ok = switches.AUDIT_FIXES2 and any(v >= 1e5 and (agrees(v, P) or agrees(v, B)) for _, v in found)
        parts = [v for _, v in found if v >= 1e5 and not (agrees(v, P) or agrees(v, B))]
        if total_ok and len(parts) >= 2 and any(r is not None and abs(sum(parts) - r) <= 2 for r in (P, B)):
            continue
        for ln, v in found:
            if v < 1e5 or ref is None or agrees(v, P) or agrees(v, B):
                continue
            if total_ok and BREAKDOWN.search(ln.text):
                continue
            if 0.5 <= v / ref <= 2:
                return ln
    return None


def stated_methods(value):
    v = re.sub(r'\s', '', re.sub(r'[(（]\s*(단가|총액)\s*[)）]', '', value))
    found = {k for k in ('제한경쟁', '일반경쟁', '지명경쟁') if k in v}
    if re.search(r'수의', v):
        found.add('수의계약')
    return found


def v24_method(b):
    # The contract-method field ("계약방법", "입찰방법 | 일반경쟁입찰") read wherever it is stated; every statement that names
    # one method must agree, and that method must differ from the registered one.
    if not b.meta.method:
        return None
    said, first = set(), None
    lines = b.notice.notice_lines()[:120]
    for ln in lines:
        for m in METHOD_FIELD.finditer(ln.text):
            found = stated_methods(m.group(2))
            if len(found) == 1:
                said |= found
                first = first or ln
    if len(said) != 1 or b.meta.method in said:
        return None
    # Quotation wording (수의 견적, 2인 이상 견적입찰, 소액수의) describes how quotes are taken, not the registered method; and a
    # notice whose own title tag carries the registered method agrees with 나라장터 on that field (dev DEV-144, DEV-191).
    if '수의계약' in said or b.meta.method == '수의계약' and (b.meta.award == '소액수의견적' or any(QUOTATION.search(ln.text) for ln in lines)):
        return None
    if any(m.group(1) == b.meta.method for ln in lines[:80] for m in TAG.finditer(ln.text)):
        return None
    return first


# Audit F: a 공동도급 partner's or licence supplement's location ("면허보완을 위하여 서울·경기·인천 소재 … 업체와 공동도급이
# 가능") is not the bidder's; v24's region axis compares the bidder location restriction with 나라장터 제한지역.
JV_PARTNER = re.compile(r'(면\s*허|자\s*격)\s*보\s*완|업\s*체\s*와\s*(의\s*)?공\s*동\s*(도\s*급|수\s*급|이\s*행|계\s*약)')
# Audit R3-C: "…허가를 받은 자와 분담이행을 허용" names the partner of a shared performance too.
JV_PARTNER3 = re.compile(JV_PARTNER.pattern + r'|(자|업\s*체|사)\s*와\s*(의\s*)?(분\s*담\s*이\s*행|공\s*동\s*(도\s*급|수\s*급|이\s*행))')


def v24_region(b):
    if b.meta.region_flag != 'Y' or not b.meta.region_sido:
        return None
    lines, sido, _ = region_restriction(b)
    if switches.AUDIT_FIXES and lines:
        lines = [ln for ln in lines if not (JV_PARTNER3 if switches.AUDIT_FIXES3 else JV_PARTNER).search(clause_text(b.notice, ln))]
        sido = set()
        # Audit R3-C: the 공고문's own location clause sets the 시·도, as in region_restriction (an attachment's 시·도 add nothing).
        for ln in ([x for x in lines if x.doc_type == '공고문'] or lines) if switches.AUDIT_FIXES3 else lines:
            sido |= regions.mentions(clause_text(b.notice, ln))['sido']
        if lines and sido and len(b.meta.region_sido) >= 2 and sido <= set(b.meta.region_sido):
            sido = set(b.meta.region_sido)
    if lines and sido and sido != b.meta.region_sido:
        return lines[0]
    return None


LICENSE_CODE = re.compile(r'(?:업종\s*코드|업종\s*번호)\s*[:：]?\s*(\d{4})(?!\d)|\((\d{4})\)|\[(\d{4})\]')


# Audit F: allowed-licence guidance ("허용업종: …") and "참여 불가" lines name no required licence.
# Audit R2-D: a capability clarification that offers alternatives ("…(업종코드 6728)으로 등록한 자 또는 … 장비기준을 충족한 자")
# names no required licence.
LICENSE_ALT = re.compile(r'또\s*는[^.。]{0,80}(충\s*족|갖\s*춘|보\s*유)\s*한\s*(자|업\s*체)|능\s*력\s*을\s*갖\s*춘\s*자\s*[:：]')
LICENSE_GUIDE = re.compile(r'허\s*용\s*업\s*종|참\s*(여|가)\s*불\s*가|제\s*외\s*(업\s*종|대\s*상)|불\s*가\s*업\s*종')


# Audit PX (switch V24P_GUARDS): a code in one item of a list whose head asks for any one of the items ("라. 다음 각 호의 자격
# 요건 중 어느 하나에 해당하는 업체" … "2) … 산학협력단[업종코드 : 3178]") is one alternative eligibility, not the licence the
# notice requires.
ANY_ONE = re.compile(r'(어\s*느|중)\s*하\s*나|중\s*(1|한)\s*(개|가\s*지)|택\s*(일|1)')
ITEM_KINDS = (re.compile(r'^\s*\d{1,2}\s*\)'), re.compile(r'^\s*\(\s*\d{1,2}\s*\)'), re.compile(r'^\s*\d{1,2}\s*\.'),
              re.compile(r'^\s*[가-하]\s*\)'), re.compile(r'^\s*\(\s*[가-하]\s*\)'), re.compile(r'^\s*[가-하]\s*\.'), re.compile(r'^\s*[①-⑳]'))


def alternative_item(notice, ln):
    """ln opens an item whose list head (the nearest line above that is not an item of the same kind) asks for any one."""
    kind = next((k for k in ITEM_KINDS if k.match(ln.text)), None)
    if kind is None:
        return False
    for x in reversed(notice.window(ln.i, 16, 0)[:-1]):
        t = x.text.strip()
        if not t or kind.match(t):
            continue
        return bool(ANY_ONE.search(t))
    return False


def v24_license(b):
    if b.meta.license_flag != 'Y' or not b.meta.license:
        return None
    meta_codes = set(re.findall(r'\((\d{4})\)', str(b.meta.license)))
    if not meta_codes:
        return None
    for ln in b.notice.lines:
        if ln.sec != 'QUAL' or '업종' not in ln.text:
            continue
        t = clause_text(b.notice, ln) if switches.V24_FIXES else ln.text
        if switches.AUDIT_FIXES and LICENSE_GUIDE.search(t):
            continue
        if switches.AUDIT_FIXES2 and LICENSE_ALT.search(t):
            continue
        if switches.V24P_GUARDS and alternative_item(b.notice, ln):
            continue
        codes = {x for g in LICENSE_CODE.findall(t) for x in g if x}
        if codes and not (codes & meta_codes):
            return ln
    return None


# Audit V24R (switches V24_AMOUNT_WIDE, V24_TRANSPOSED_WIDE, V24_LICENSE_WIDE): planted edits in amounts stated under other
# labels or in vertical tables, in amounts written without 원, and in licence codes written after an industry name.
AMOUNT_FIELD = re.compile(
    r'(추\s*정\s*가\s*격|추\s*정\s*금\s*액|배\s*정\s*예\s*산|사\s*업\s*예\s*산|예\s*산\s*액|소\s*요\s*예\s*산|예\s*산\s*금\s*액|'
    r'(?:구\s*매|용\s*역|사\s*업|계\s*약|공\s*사|구\s*입)\s*예\s*정\s*금\s*액|사\s*업\s*금\s*액|총\s*사\s*업\s*비|'
    r'(?:대\s*행\s*)?사\s*업\s*비|용\s*역\s*비)')
# 기초금액 (the base of the 예정가격, often "배정예산의 98%") and a bare 예정금액 are other fields than meta 배정예산·추정가격.
OTHER_AMOUNT_FIELD = re.compile(r'기\s*초\s*금\s*액|(?<![매역업약사입])\s*예\s*정\s*금\s*액|예\s*정\s*가\s*격')
ESTIMATE_FIELD = re.compile(r'추\s*정\s*가\s*격')
# The fields meta registers itself (추정가격 ↔ P; 배정·사업·소요 예산, 예산액·예산금액 ↔ B).
REGISTERED_FIELD = re.compile(r'추\s*정\s*가\s*격|배\s*정\s*예\s*산|사\s*업\s*예\s*산|소\s*요\s*예\s*산|예\s*산\s*액|예\s*산\s*금\s*액')
AMOUNT_AFTER_LABEL = re.compile(r'^\s*(?:[\(（][^)）]{0,14}[\)）])?[\s:：|]*(?:금\s*)?(?:[가-힣]{0,14}원정?\s*[\(（]\s*)?[₩￦]?\s*'
                                r'(\d{1,3}(?:,\d{3})+|\d{6,})(?![\d,])')
AMOUNT_LEAD = re.compile(r'^[\s:：|]*(?:금\s*)?[₩￦]?\s*(\d{1,3}(?:,\d{3})+|\d{6,})(?![\d,])')
AMOUNT_ANY = re.compile(r'(?<![\d,.])(\d{1,3}(?:,\d{3})+|\d{6,})(?![\d,])')
CODE_AFTER_INDUSTRY = re.compile(r'업\s*[\(\[【]\s*(\d{4})\s*[\)\]】]')
CODE_LABELLED = re.compile(r'(?:업종\s*코드|업종\s*번호)\s*[:：]?\s*(\d{4})(?!\d)')
PARTNER_WORK = re.compile(r'분\s*담\s*이\s*행|공\s*동\s*(이\s*행|수\s*급|도\s*급)')


def stated_amounts(b):
    """[(line, value, is 추정가격, is a field meta registers)] of the budget/estimate statements, inline or on the next line."""
    out, lines = [], b.notice.notice_lines()[:150]
    for k, ln in enumerate(lines):
        t = ln.text
        if '단가' in t:
            continue
        found = False
        for m in AMOUNT_FIELD.finditer(t):
            a = AMOUNT_AFTER_LABEL.match(t[m.end():])
            if a and not OTHER_AMOUNT_FIELD.search(t[m.end():m.end() + a.start(1)]):
                out.append((ln, float(a.group(1).replace(',', '')), bool(ESTIMATE_FIELD.fullmatch(m.group(1))),
                            bool(REGISTERED_FIELD.fullmatch(m.group(1)))))
                found = True
        if found or not AMOUNT_FIELD.search(t) or AMOUNT_ANY.search(t) or OTHER_AMOUNT_FIELD.search(t):
            continue
        for nxt in lines[k + 1:k + 3]:          # vertical table: the value opens the next non-empty line
            if not nxt.text.strip():
                continue
            a = AMOUNT_LEAD.match(nxt.text)
            if a:
                label = AMOUNT_FIELD.search(t).group(1)
                out.append((nxt, float(a.group(1).replace(',', '')), bool(ESTIMATE_FIELD.fullmatch(label)),
                            bool(REGISTERED_FIELD.fullmatch(label))))
            break
    return out


def v24_amount_wide(b):
    P, B = b.meta.P, b.meta.B
    found = [x for x in stated_amounts(b) if x[1] >= 1e5]
    if not found:
        return None
    vals = [v for _, v, _, _ in found]
    if len(vals) >= 2 and any(r is not None and abs(sum(vals) - r) <= 2 for r in (P, B)):
        return None
    total_ok = any(agrees(v, P) or agrees(v, B) for v in vals)
    parts = [v for v in vals if not (agrees(v, P) or agrees(v, B))]
    if total_ok and len(parts) >= 2 and any(r is not None and abs(sum(parts) - r) <= 2 for r in (P, B)):
        return None
    for ln, v, est, same in found:
        if agrees(v, P) or agrees(v, B) or BREAKDOWN.search(ln.text):
            continue
        ref = (P if est else B) or P or B
        lo, hi = (0.2, 5) if same and not total_ok else (0.5, 2)
        if not ref or not lo <= v / ref <= hi:
            continue
        if any(abs(r / v - k) <= 0.005 * k for r in (P, B) if r for k in (2, 3, 4, 5)):
            continue          # an exact 1/k share of a registered total is a per-period or per-lot amount
        return ln
    return None


def v24_amount_any(b):
    """V24_AMOUNT_WIDE adds the wide reader to the current amount axis (9/26 LB: dropping stated-vs-registered firings such as
    a VAT-included 추정가격 cost 0.00028, so none is dropped)."""
    return v24_amount(b) or v24_amount_wide(b)


def v24_transposed_wide(b):
    registered = {str(int(v)) for v in (b.meta.B, b.meta.P) if v}
    for ln in b.notice.notice_lines():
        t = ln.text
        for m in AMOUNT_ANY.finditer(t):
            raw = m.group(1)
            if ',' not in raw and not re.match(r'\s*원', t[m.end():]) and not re.search(r'(금|[₩￦])\s*$', t[:m.start()]):
                continue
            d = raw.replace(',', '')
            if int(d) < 1e5:
                continue
            if switches.V24_FIXES and any(vat_related(float(d), r) for r in (b.meta.B, b.meta.P) if r):
                continue
            if any(len(d) == len(r) and d != r and sorted(d) == sorted(r) for r in registered):
                return ln
    return None


def v24_license_wide(b):
    hit = v24_license(b)
    if hit is not None or b.meta.license_flag != 'Y' or not b.meta.license:
        return hit
    meta_codes = set(re.findall(r'\((\d{4})\)', str(b.meta.license)))
    if not meta_codes:
        return None
    for ln in b.notice.lines:
        if ln.sec == 'QUAL' and '업종' not in ln.text:
            pat = CODE_AFTER_INDUSTRY
        elif ln.sec != 'QUAL' and ln.doc_type == '공고문' and CODE_LABELLED.search(ln.text):
            pat = CODE_LABELLED
        else:
            continue
        t = clause_text(b.notice, ln) if switches.V24_FIXES else ln.text
        if JV_PARTNER3.search(t) or PARTNER_WORK.search(t):
            continue      # a joint or shared performer's licence ("…(4444) 등 공동 또는 분담이행 가능") is not the bidder's
        if switches.AUDIT_FIXES and LICENSE_GUIDE.search(t) or switches.AUDIT_FIXES2 and LICENSE_ALT.search(t):
            continue
        if switches.V24P_GUARDS and alternative_item(b.notice, ln):
            continue
        codes = set(pat.findall(t))
        if codes and not (codes & meta_codes):
            return ln
    return None


# Audit fable_v24 S1 (switch V24_BASE_ZONE): a 기초금액 stated in the 공고문 against the nearest registered amount (B, 1.1·P, P,
# B/1.1). Natural statements sit within 2% (t2500 704/767) or below half (unit prices, 41), so the notice is silent when one
# statement agrees within 5% and fires at 0.5–0.95× or 1.05–2× (t2500 1/2502); a larger ratio is another field.
BASE_AMOUNT = re.compile(r'기\s*초\s*금\s*액[\s:：|]*(?:금\s*)?[₩￦\\]?\s*(\d{1,3}(?:,\d{3})+|\d{5,})')
UNIT_CUE = re.compile(r'단\s*가|/\s*(톤|건|회|인|명|kg|㎏|개|식|매|시간|일|월|㎡|m)|1\s*(회|건|인|명)\s*당|당\s*(금|단가)')


def v24_base_zone(b):
    B, P = b.meta.B, (b.meta.P if b.meta.P_source == 'meta' else None)
    refs = [r for r in (B, P * 1.1 if P else None, P, B / 1.1 if B else None) if r]
    if not refs:
        return None
    found = []
    for ln in b.notice.notice_lines():
        for m in BASE_AMOUNT.finditer(ln.text):
            v = float(m.group(1).replace(',', ''))
            if v >= 1e5:
                found.append((ln, min((v / r for r in refs), key=lambda x: abs(x - 1)), bool(UNIT_CUE.search(ln.text))))
    if any(abs(r - 1) <= 0.05 for _, r, _ in found):
        return None
    for ln, r, unit in found:
        if not unit and (0.5 <= r < 0.95 or 1.05 < r <= 2):
            return ln
    return None


# The model copies the stated 예산·추정가격 and names the stated contract method; the CPU compares them with meta on the
# same field (1원 rounding, VAT-inclusive budget vs estimate, and values far from the registered one are not mismatches).
V24_MODEL_VALUES = False
METHOD_OF = {'일반경쟁': '일반경쟁', '제한경쟁': '제한경쟁', '지명경쟁': '지명경쟁', '수의계약': '수의계약'}


def _digits(v):
    v = re.sub(r'[^0-9]', '', v or '')
    return float(v) if v else None


def v24_values(b):
    if not V24_MODEL_VALUES or not b.values:
        return None
    line = next(iter(b.cands.get('values', [])), None)
    budget, estimate = _digits(b.values.get('예산')), _digits(b.values.get('추정가격'))
    B, P = b.meta.B, b.meta.P
    hit = None
    if budget and B and budget >= 1e5 and abs(budget - B) > 1.5 and abs(budget - (P or 0)) > 1.5 and 0.5 <= budget / B <= 2:
        hit = 'budget'
    if estimate and P and estimate >= 1e5 and abs(estimate - P) > 1.5 and 0.5 <= estimate / P <= 2:
        hit = 'estimate'
    said = METHOD_OF.get(b.values.get('계약방법'))
    if said and b.meta.method and said != b.meta.method:
        hit = 'method'
    if hit is None:
        return None
    return line if line is not None else True


V24_AXES = (v24_title, v24_amount, v24_transposed, v24_method, v24_region, v24_license, v24_values)


V24_FIX_SWITCHES = ('AUDIT_FIXES', 'AUDIT_FIXES2', 'AUDIT_FIXES3', 'REGION_PARTICLE')


def v24(b):
    if switches.V24_PIPELINE and getattr(b, 'v24p', None):
        hit = v24p_compare.verdict(b)          # v24 전용 판독 단계(pps_c/v24): 발화하면 그 줄, 아니면 기존 경로(합집합)
        if hit is not None:
            return hit
    if not switches.V24_FIXES:
        return v24_axes(b)
    saved = {k: getattr(switches, k) for k in V24_FIX_SWITCHES}
    for k in V24_FIX_SWITCHES:
        setattr(switches, k, True)
    try:
        return v24_axes(b)
    finally:
        for k, v in saved.items():
            setattr(switches, k, v)


def v24_axes(b):
    for axis in V24_AXES:
        if switches.V24_NO_METHOD and axis is v24_method:
            continue
        if switches.V24_AMOUNT_WIDE and axis is v24_amount:
            axis = v24_amount_any
        elif switches.V24_TRANSPOSED_WIDE and axis is v24_transposed:
            axis = v24_transposed_wide
        elif switches.V24_LICENSE_WIDE and axis is v24_license:
            axis = v24_license_wide
        ln = axis(b)
        if ln is not None:
            return ln
    return v24_base_zone(b) if switches.V24_BASE_ZONE else None


RULES = {'v1': v1, 'v2': v2, 'v3': v3, 'v4': v4, 'v5': v5, 'v6': v6, 'v7': v7, 'v8': v8, 'v9': v9, 'v10': v10,
         'v11': v11, 'v12': v12, 'v13': v13, 'v14': v14, 'v15': v15, 'v16': v16, 'v17': v17, 'v18': v18, 'v19': v19,
         'v20': v20, 'v21': v21, 'v22': v22, 'v23': v23, 'v24': v24}


def segment_of(b):
    """The notice segment switches.SEGMENT_OFF probes select on: meta fields, 추정가격 band and attachments."""
    P = b.meta.P
    band = 'P?' if P is None else '<5천만' if P < 5e7 else '<1억' if P < 1e8 else '<2.3억' if P < 2.3e8 else '≥2.3억'
    attach = '첨부' if any(ln.doc_type != '공고문' for ln in b.notice.lines) else '공고문만'
    return {'work': b.meta.work, 'method': b.meta.method, 'award': b.meta.award, 'law': b.meta.law, 'band': band, 'attach': attach}


def judge(b):
    """{item: (0|1, evidence text)}; evidence is an exact source line (≤500 chars) or '' for absence items."""
    out = {}
    for it in ITEMS:
        if it in switches.AF_ITEMS or it in switches.AF1_ITEMS or it in switches.AF3_ITEMS:
            saved = switches.AUDIT_FIXES, switches.AUDIT_FIXES2, switches.AUDIT_FIXES3
            switches.AUDIT_FIXES = True
            switches.AUDIT_FIXES2 = saved[1] or it in switches.AF_ITEMS or it in switches.AF3_ITEMS
            switches.AUDIT_FIXES3 = saved[2] or it in switches.AF3_ITEMS
            try:
                hit = RULES[it](b)
            finally:
                switches.AUDIT_FIXES, switches.AUDIT_FIXES2, switches.AUDIT_FIXES3 = saved
        else:
            hit = RULES[it](b)
        if hit is None:
            out[it] = (0, '')
            continue
        ev = '' if (it in ABSENCE or hit is True) else evidence(hit.text)
        out[it] = (1, ev)
    if switches.SEGMENT_OFF:
        seg = segment_of(b)
        for it, field, value in switches.SEGMENT_OFF:
            if seg.get(field) == value:
                out[it] = (0, '')
    return out


def evidence(text):
    t = (text or '').strip()
    if len(t) > 500:
        t = t[:500].rstrip()
    while t and t[0] in '=+@':
        t = t[1:].lstrip()
    return t
