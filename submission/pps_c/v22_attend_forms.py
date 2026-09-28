"""Briefing attendance as a participation condition in further wordings (switch V22_ATTEND_FORMS), negotiated contracts only.

Shapes: a qualification item naming the attendees ("라. 현장설명회 참석 업체", "입찰참가자격: 사업설명회 참석업체"), attendance
stated as a duty or a qualification requirement ("참석은 필수", "의무참석", "참석은 입찰참가자격 요건"), only attendees admitted
in other words ("참석 업체에 한해 입찰참가 자격을 부여", "참석업체만 입찰 가능", "참석업체 외에는 입찰에 참가할 수 없음",
"참석하여 … 제출한 업체에 한하여 제안서를", "참석자 명부에 서명한 업체만", "참석 확인서를 받은 자만"), and absence barring
participation ("미참석 시 입찰참가자격 박탈", "미참가 시 제안서 접수 불가", "현장설명에 참가하지 아니한 자는 입찰에 참가할 수
없다", "참석확인서 … 미제출 시 입찰 무효"). The line names the orderer's briefing (현장·사업·과업설명, 요청서 설명), or says only
"설명회" right below a line that names it. The proposer's own presentation and evaluation-stage consequences stay out.
"""
import re
from . import judge, families, rtd_brief_attendance as ra

FIRM = r'(?:업\s*체|기\s*업|사\s*업\s*자|자|사)'
PART = r'(?:입\s*찰|제\s*안\s*서?|응\s*찰|투\s*찰|참\s*가|참\s*여)'
BAR = r'(?:불\s*가|없|허\s*용\s*되\s*지\s*않|허\s*용\s*하\s*지\s*않|무\s*효|박\s*탈|제\s*외|제\s*한|반\s*려|접\s*수\s*하\s*지\s*않)'
ATTEND = r'(?:참\s*석|출\s*석|참\s*가)'
SHAPES = [
    # "현장설명회 참석 업체" as a qualification item (judged in the qualification section only, below).
    ('item', re.compile(r'설\s*명\s*(?:회)?\s*(?:에\s*)?(?:반\s*드\s*시\s*)?' + ATTEND + r'\s*(?:한\s*|하\s*(?:여|고)\s*[^.。]{0,30}?(?:받\s*은|제\s*출\s*한|서\s*명\s*한)\s*)?'
                        + FIRM + r'(?:\s*[(（][^)）]{0,30}[)）])?\s*(?:로\s*제\s*한|으\s*로\s*제\s*한|에\s*한\s*함|일\s*것)?\s*[.。]?\s*$')),
    ('duty', re.compile(r'(?:참\s*석\s*(?:을|를)\s*(?:입\s*찰\s*)?참\s*가\s*(?:의\s*)?(?:조\s*건|요\s*건|자\s*격)\s*(?:으\s*)?로|의\s*무\s*참\s*석|필\s*수\s*참\s*석|참\s*석\s*(?:은|이)?\s*(?:필\s*수|의\s*무)|' + ATTEND + r'\s*(?:은|이|는)?\s*(?:입\s*찰\s*)?참\s*가\s*자\s*격\s*(?:의\s*)?(?:요\s*건|조\s*건))')),
    ('only', re.compile(ATTEND + r'\s*(?:한\s*)?' + FIRM + r'\s*(?:에\s*)?(?:한\s*해|한\s*하\s*여|만)[^.。]{0,30}?' + PART + r'[^.。]{0,25}?(?:가\s*능|할\s*수\s*있|자\s*격\s*을\s*부\s*여|부\s*여|허\s*용)')),
    ('grant', re.compile(ATTEND + r'\s*(?:한\s*)?' + FIRM + r'\s*(?:에\s*게|에)\s*(?:입\s*찰\s*)?(?:참\s*가|제\s*안)\s*(?:자\s*격|권)\s*(?:을|를)?\s*부\s*여')),
    ('limit', re.compile(ATTEND + r'\s*(?:한\s*)?' + FIRM + r'\s*(?:으\s*)?로\s*(?:한\s*정|제\s*한)')),
    ('except', re.compile(ATTEND + r'\s*(?:한\s*)?' + FIRM + r'\s*(?:외|이\s*외)\s*(?:에\s*는|의)?[^.。]{0,25}?' + PART + r'[^.。]{0,25}?' + BAR)),
    ('via', re.compile(ATTEND + r'\s*하\s*여[^.。]{0,40}?' + FIRM + r'\s*(?:에\s*)?(?:한\s*해|한\s*하\s*여|만)[^.。]{0,40}?' + PART)),
    ('roster', re.compile(r'(?:명\s*부|확\s*인\s*서)\s*(?:에\s*서\s*명\s*한|를\s*(?:받\s*은|발\s*급\s*받\s*은|제\s*출\s*한))\s*' + FIRM + r'\s*(?:만|에\s*한)[^.。]{0,30}?' + PART)),
    ('absent', re.compile(r'(?:미\s*참\s*석|미\s*참\s*가|불\s*참|' + ATTEND + r'\s*하\s*지\s*(?:아\s*니\s*한|않\s*은|않\s*을|않\s*는))\s*(?:시|경\s*우|때|' + FIRM + r')?[^.。]{0,30}?'
                        + PART + r'[^.。]{0,25}?' + BAR)),
    ('cert', re.compile(ATTEND + r'\s*확\s*인\s*서[^.。]{0,30}?(?:미\s*제\s*출|제\s*출\s*하\s*지\s*않)[^.。]{0,20}?(?:무\s*효|불\s*가|제\s*외|없)')),
]
BARE = re.compile(r'설\s*명\s*회')
# "발주기관이 주관하는 사업설명회" names who hosts the briefing; it is no contract work event the contractor attends.
ORDERER_HOSTS = re.compile(r'(?:발\s*주|수\s*요)\s*기\s*관\s*(?:이|에\s*서|의)?\s*(?:주\s*관|주\s*최|개\s*최|실\s*시)\s*하\s*는')
QUAL_LABEL = re.compile(r'(?:입\s*찰\s*)?참\s*가\s*자\s*격\s*[:：|]')
EVAL_ONLY = re.compile(r'평\s*가\s*(?:대\s*상\s*)?(?:에\s*서\s*)?제\s*외|(?<!\d)0\s*점|영\s*점|감\s*점|평\s*가\s*하\s*지\s*않')


def names_orderer(b, ln, text):
    if judge.BID_BRIEFING.search(text):
        return True
    if not BARE.search(text):
        return False
    for prev in reversed(b.notice.window(ln.i, 6, 0)[:-1]):
        if prev.doc != ln.doc:
            break
        if judge.BID_BRIEFING.search(prev.text):
            return not judge.proposer_event(prev.text)
    return False


def in_qual_list(b, ln):
    """A short item of the qualification list that the layout parser took for a heading of its own (it names 설명회)."""
    if ln.head != ln.i or not judge.ITEM_START.match(ln.text):
        return False
    prev = next((x for x in reversed(b.notice.window(ln.i, 4, 0)[:-1]) if x.text.strip()), None)
    return prev is not None and prev.doc == ln.doc and judge.qual_section(prev, b.notice)


def sentence(b, ln):
    text = ln.text
    if not judge.SENT_END.search(text):
        for nxt in b.notice.window(ln.i, 0, 5)[1:]:
            if not nxt.text.strip():
                continue
            if nxt.sec != ln.sec or judge.ITEM_START.match(nxt.text):
                break
            text += ' ' + nxt.text.strip()
            if judge.SENT_END.search(nxt.text):
                break
    return text


def augment(b, hit):
    if hit is not None or not b.meta.negotiation:
        return hit
    for ln in b.notice.lines:
        if not ln.text.strip() or not (BARE.search(ln.text) or judge.BID_BRIEFING.search(ln.text) or re.search(r'설\s*명', ln.text)):
            continue
        attach = ln.doc_type != '공고문'
        text = sentence(b, ln)
        if attach and not (ln.sec == 'QUAL' or QUAL_LABEL.search(text) or re.search(r'제\s*안\s*서?\s*제\s*출|입\s*찰\s*참\s*가', text)):
            continue
        if not names_orderer(b, ln, text):
            continue
        qual = judge.qual_section(ln, b.notice) or QUAL_LABEL.search(text) or in_qual_list(b, ln)
        kinds = [k for k, p in SHAPES if p.search(text) and (k != 'item' or qual)]
        if not kinds:
            continue
        if ra.POSTCONTRACT.search(text) or families.OPTIONAL.search(text) or ra.ABSENT_ALLOWED.search(text) or judge.NO_BRIEF_FIX.search(text):
            continue
        if judge.proposer_event(text) or EVAL_ONLY.search(text) and not re.search(r'입\s*찰|참\s*가', text):
            continue
        if not judge.bid_briefing(judge._Clause(ORDERER_HOSTS.sub('', text))):
            continue
        if judge.BID_BRIEFING.search(text) is None and judge.PRESENT_LINE.search(text):
            continue
        return ln
    return hit
