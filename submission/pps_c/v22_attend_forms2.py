"""Briefing attendance as a participation condition in two further shapes (switch V22_ATTEND_FORMS2), negotiated contracts.

A session named only "설명회" whose absence bars the bid or the proposal itself ("설명회 미참석 시 입찰참가자격 박탈", "설명회 참석
업체만 입찰할 수 있음", "설명회 참석자 명부에 서명한 업체만 제안서 제출 가능", "설명회 참석업체 이외의 업체는 제안서를 제출할
수 없습니다"): it is held before bidding, so it is the orderer's briefing, not the proposer's presentation (which follows the
proposal). Read with the V22_ATTEND_FORMS shapes only when no briefing or presentation is named on the line or in the six lines
above (V22_ATTEND_FORMS owns those), outside evaluation sections and presentation contexts, and the barred act must be the bid
or the proposal. Four further shapes, for a named briefing as V22_ATTEND_FORMS reads it or for a bare one as above:
attendees granted the right in other words ("과업설명회 참석 업체에 한하여 제안서 제출 자격이 주어짐"), absence that leaves
the bidder without it ("불참 업체에 대하여는 입찰참가자격을 인정하지 않음"), a qualification item naming the attendance
certificate ("사업설명회 참석 확인서를 발급받은 업체") and attendance as the bid's condition ("본 입찰은 사업설명회 참석을
조건으로 합니다").
"""
import re

from . import judge, families, rtd_brief_attendance as ra, v22_attend_forms as va

BID_ACT = re.compile(r'입\s*찰|제\s*안\s*서|제\s*안\s*(?:이\s*)?(?:가\s*능|불\s*가|할\s*수)|응\s*찰|투\s*찰|참\s*가\s*자\s*격')
NAMED_EVENT = re.compile(judge.BID_BRIEFING.pattern + r'|' + judge.PROPOSAL_SESSION.pattern + r'|' + judge.PROPOSER_EVENT.pattern
                         + r'|제\s*안\s*(?:서\s*)?설\s*명|발\s*표')
GIVEN = re.compile(va.ATTEND + r'\s*(?:한\s*)?' + va.FIRM + r'\s*(?:에\s*)?(?:한\s*해|한\s*하\s*여|만)[^.。]{0,30}?' + va.PART + r'[^.。]{0,25}?'
                   r'(?:자\s*격\s*(?:이|을)\s*(?:주\s*어|갖|가\s*지|인\s*정)|(?:이|가)\s*주\s*어\s*(?:짐|진|집)|인\s*정\s*(?:함|됨|한\s*다|된\s*다))')
# Attendance stated as not required or unrelated to taking part ("참석은 의무사항이 아니며, 참석여부는 입찰참가와 관계없음").
NOT_REQUIRED = re.compile(r'(?:의\s*무|필\s*수|요\s*건|조\s*건)\s*(?:사\s*항\s*)?(?:이|은|는|가)?\s*아\s*니|관\s*계\s*(?:가\s*)?없|상\s*관\s*(?:이\s*)?없|무\s*관|자\s*율|선\s*택'
                          r'|참\s*석\s*하\s*지\s*않\s*(?:아\s*도|더\s*라\s*도)')
# Absence that leaves the bidder without the right ("불참 업체에 대하여는 입찰참가자격을 인정하지 않음").
NOT_GIVEN = re.compile(r'(?:미\s*참\s*석|미\s*참\s*가|불\s*참|' + va.ATTEND + r'\s*하\s*지\s*(?:아\s*니\s*한|않\s*은|않\s*을|않\s*는))\s*(?:시|경\s*우|때|' + va.FIRM
                       + r')?[^.。]{0,30}?' + va.PART + r'[^.。]{0,25}?(?:인\s*정\s*(?:하\s*지|되\s*지)\s*않|부\s*여\s*(?:하\s*지|되\s*지)\s*않|주\s*어\s*지\s*지\s*않)')
# A qualification item naming the attendance certificate ("사업설명회 참석 확인서를 발급받은 업체"), in the qualification section.
ITEM_CERT = re.compile(r'설\s*명\s*(?:회)?\s*(?:에\s*)?' + va.ATTEND + r'\s*(?:확\s*인\s*(?:서|증)|필\s*증|증\s*명\s*서)\s*(?:을|를)?\s*(?:발\s*급\s*|교\s*부\s*)?'
                       r'(?:받\s*은|제\s*출\s*한|소\s*지\s*한)\s*' + va.FIRM + r'\s*[.。]?\s*$')
# Attendance as the condition of the bid ("본 입찰은 사업설명회 참석을 조건으로 합니다").
CONDITION = re.compile(va.ATTEND + r'\s*(?:을|를)\s*(?:입\s*찰\s*(?:참\s*가\s*)?(?:의\s*)?)?(?:필\s*수\s*)?(?:조\s*건|요\s*건)\s*(?:으\s*)?로\s*(?:하|함|한|합)')
EXTRA = [('given', GIVEN), ('not_given', NOT_GIVEN), ('item', ITEM_CERT), ('condition', CONDITION)]
ATTACH_ACT = re.compile(r'제\s*안\s*서?\s*(?:를|을|의)?\s*(?:제\s*출|접\s*수)|입\s*찰\s*(?:에\s*)?참\s*(?:가|여)')


def augment(b, hit):
    if hit is not None or not b.meta.negotiation:
        return hit
    for ln in b.notice.lines:
        if not ln.text.strip() or not (va.BARE.search(ln.text) or judge.BID_BRIEFING.search(ln.text) or re.search(r'설\s*명', ln.text)):
            continue
        attach = ln.doc_type != '공고문'
        text = va.sentence(b, ln)
        if attach and not (ln.sec == 'QUAL' or va.QUAL_LABEL.search(text) or ATTACH_ACT.search(text)):
            continue
        bare = (va.BARE.search(ln.text) and not NAMED_EVENT.search(text)
                and not any(x.doc == ln.doc and NAMED_EVENT.search(x.text) for x in b.notice.window(ln.i, 6, 0)[:-1]))
        if bare:
            if not attach and ln.sec == 'EVAL' or judge.presentation_context(b, ln):
                continue
            shapes = va.SHAPES + EXTRA
        elif va.names_orderer(b, ln, text):
            shapes = EXTRA
        else:
            continue
        qual = judge.qual_section(ln, b.notice) or va.QUAL_LABEL.search(text) or va.in_qual_list(b, ln)
        kinds = [k for k, p in shapes if p.search(text) and (k != 'item' or qual)]
        # A bare session's absence must bar the bid or the proposal, unless the line is itself a qualification item.
        if not kinds or bare and not BID_ACT.search(text) and 'item' not in kinds:
            continue
        if NOT_REQUIRED.search(text) or ra.POSTCONTRACT.search(text) or families.OPTIONAL.search(text) or ra.ABSENT_ALLOWED.search(text) or judge.NO_BRIEF_FIX.search(text):
            continue
        if judge.proposer_event(text) or va.EVAL_ONLY.search(text) and not re.search(r'입\s*찰|참\s*가', text):
            continue
        if not judge.bid_briefing(judge._Clause(va.ORDERER_HOSTS.sub('', text))):
            continue
        if judge.BID_BRIEFING.search(text) is None and judge.PRESENT_LINE.search(text):
            continue
        return ln
    return hit
