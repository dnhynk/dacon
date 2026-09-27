"""An explicit briefing-attendance prerequisite, scoped to negotiated contracts."""
import re
from . import judge,families
ONLY=re.compile(r'(?:참\s*석|출\s*석)\s*(?:한\s*)?(?:업\s*체|기\s*업|사\s*업\s*자|자)\s*(?:에\s*)?'
                r'(?:한\s*하\s*여|한\s*해\s*서|만)[^.。]{0,45}(?:입\s*찰|제\s*안\s*서)[^.。]{0,35}(?:참\s*가|제\s*출)')
ABSENT=re.compile(r'(?:미\s*참\s*석|불\s*참|(?:참\s*석|출\s*석)\s*하\s*지\s*(?:아\s*니\s*한|않\s*은))'
                  r'[^.。]{0,40}(?:업\s*체|기\s*업|자)[^.。]{0,25}(?:입\s*찰|제\s*안\s*서)[^.。]{0,35}'
                  r'(?:불\s*가|허\s*용\s*되\s*지\s*않|허\s*용\s*되\s*지\s*아\s*니|할\s*수\s*없|무\s*효)')

POSTCONTRACT=re.compile(r'계\s*약\s*상\s*대\s*자|수\s*행\s*업\s*체|낙\s*찰\s*자\s*(?:는|가|의)')
REVOKED=re.compile(r'(?:부\s*여|허\s*용|가\s*능|참\s*가)[^.。]{0,10}(?:않|없|불\s*가|아\s*니)|삭\s*제|폐\s*지|미\s*적\s*용')

ABSENT_ALLOWED=re.compile(r'(?:미\s*참\s*석|불\s*참)[^.。]{0,20}(?:업\s*체|기\s*업|자)[^.。]{0,30}(?:입\s*찰|제\s*안\s*서)[^.。]{0,25}(?:가\s*능|허\s*용|할\s*수\s*있)')

def augment(b,hit):
    if hit is not None or not b.meta.negotiation:return hit
    for ln in b.notice.lines:
        if ln.doc_type!='공고문' or not judge.BID_BRIEFING.search(ln.text):continue
        text=ln.text
        # Join an unfinished sentence only. A separate presentation field owns itself.
        if not judge.SENT_END.search(text):
            for nxt in b.notice.window(ln.i,0,5)[1:]:
                if not nxt.text.strip():continue
                if nxt.sec!=ln.sec or judge.ITEM_START.match(nxt.text):break
                text+=' '+nxt.text.strip()
                if judge.SENT_END.search(nxt.text):break
        only=ONLY.search(text);absent=ABSENT.search(text)
        if not (only or absent):continue
        if POSTCONTRACT.search(text):continue
        if only and not absent and REVOKED.search(text[only.start():]):continue
        if families.OPTIONAL.search(text) or ABSENT_ALLOWED.search(text) or judge.NO_BRIEF_FIX.search(text):continue
        # Reuse the measured presentation/attendee/work-duty distinctions.
        if not judge.bid_briefing(judge._Clause(text)) or judge.presentation_context(b,ln):continue
        return ln
    return hit
