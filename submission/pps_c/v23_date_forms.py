"""Briefing dates in further shapes (switch V23_DATE_FORMS): a two-digit-year date ("’26.03.05.") and a date line that
follows a briefing heading as the next item of the same list ("- 사업설명회" / "- 일시: …")."""
import re
from . import judge, dates

DATE_LABEL = re.compile(r'^\W*(?:[가-하]\s*[.\)]\s*)?(?:개\s*최\s*)?(?:일\s*시|일\s*자|일\s*정|날\s*짜|개\s*최\s*일)\s*[:：]?')


def find(text, year):
    """dates.find plus two-digit-year dates after an apostrophe, which the event-date check otherwise skips."""
    out = dates.find(text, year)
    taken = [(s, s + 1) for _, s in out]
    for m in dates.YY.finditer(text or ''):
        if any(a <= m.start() <= b for a, b in taken) or any(m.start() <= s < m.end() for _, s in out):
            continue
        d = dates._mk(2000 + int(m.group(1)), m.group(2), m.group(3))
        if d:
            out.append((d, m.start()))
    return sorted(out, key=lambda x: x[1])


def decide(b, old_hit):
    if not (b.meta.local and b.meta.negotiation):return None
    year=b.meta.posted.year if b.meta.posted else None
    briefing=None;evidence=None
    for ln in b.cands.get('brief',[]):
        if b.read('brief',ln).get('참석')=='설명회 아님(제안 발표 등)' or not judge.bid_briefing(ln):continue
        if judge.V23_PROPOSAL_EVENT.search(ln.text) or judge.briefing_not_held(b,ln) or judge.NO_BRIEF_FIX.search(ln.text):continue
        found=find(ln.text,year)
        if not found:
            for nxt in b.notice.window(ln.i,0,8)[1:]:
                if not nxt.text.strip():continue
                # The next item of the heading's own list returns to the enclosing section; its date label still
                # belongs to this briefing heading.
                if nxt.sec!=ln.sec and not (ln.head==ln.i and DATE_LABEL.match(nxt.text)):break
                located=find(nxt.text,year)
                if not located and judge.NEXT_ITEM.match(nxt.text) and not judge.re.search(r'일\s*시|일\s*자',nxt.text):break
                if located:
                    prefix=nxt.text[:located[0][1]]
                    if judge.re.fullmatch(r'[\W\d_]*(?:[가-하]\s*[.\)]\s*)?(?:개\s*최\s*)?(?:(?:일\s*시|일\s*자|시\s*간)[\W\d_]*)*',prefix):found=located
                    break
                if judge.DEADLINE.search(nxt.text) or judge.re.search(r'개\s*찰|접\s*수|발\s*표|평\s*가',nxt.text):break
                if nxt.sec!=ln.sec:break
        if found:
            briefing,evidence=found[0][0],ln;break
    if briefing is None:return None
    deadline=None
    for ln in b.notice.lines:
        if not judge.DEADLINE.search(ln.text):continue
        found=dates.find(ln.text,year)
        if not found:
            nxt=next((w for w in b.notice.window(ln.i,0,2)[1:] if w.text.strip()),None)
            located=dates.find(nxt.text,year) if nxt else []
            if located and judge.V23_VALUE_PREFIX.match(nxt.text[:located[0][1]]):found=located
        if found:
            value=max(d for d,_ in found);deadline=value if deadline is None else max(deadline,value)
    need=40 if (b.meta.P or 0)>=10*judge.EOK else 20 if (b.meta.P or 0)>=judge.EOK else 10
    if deadline is not None and deadline>=briefing and (deadline-briefing).days<=need:return evidence
    if b.meta.posted is not None and briefing>=b.meta.posted and (briefing-b.meta.posted).days<=7:return evidence
    return None
