"""Red team W3 (switch W3_REC_CONTEXT): v2, v4, v8 — a 공고문 line outside the qualification section that the perf family read as
a participation condition and whose own wording makes it one — a bidder predicate not under scoring text, or a consequence for bidders
without the record ("…납품실적이 있는 업체", "…업체이어야 합니다. 위 사항을 충족치 못한 업체가 투찰 할 경우 사전 부적격", "라. 입찰참가를
위한 제출서류 … 거래 실적이 있는 업체(…)을 제출하여야 한다") — is a record requirement although it sits in an evaluation,
document or notes block (organizer 9/28: evaluation material or participation limit is decided by context, not position).
Lines with scoring or 적격심사 wording, forms, notes on documents and staffing careers stay out."""
import re
from . import judge, meta, switches

# a consequence for bidders without the record: it makes the line a participation condition wherever it stands
STRONG = re.compile(r'부\s*적\s*격|참\s*가\s*할\s*수\s*없|참\s*가\s*불\s*가|참\s*가\s*자\s*격\s*(?:이\s*)?없|입\s*찰\s*(?:을\s*)?무\s*효|투\s*찰\s*할\s*수\s*없')
SCORING = re.compile(r'적\s*격\s*심\s*사|심\s*사|평\s*가|배\s*점|점\s*수|평\s*점|가\s*점|감\s*점|만\s*점|\d\s*점(?![가-힣])|우\s*대')


def records(b):
    known = {ln.i for ln in judge.perf_lines(b)}
    out = []
    for ln in b.cands.get('perf', []):
        if ln.i in known or ln.doc_type != '공고문' or judge.qual_section(ln, b.notice):
            continue
        r = b.read('perf', ln)
        if r.get('역할') != '참가자격' or judge.METHOD_SUMMARY.search(ln.text):
            continue
        c = judge.clause_text(b.notice, ln)
        strong = bool(STRONG.search(c) and judge.ONLY_REC.search(c))
        if not (strong or judge.PERF_BIDDER_REQ.search(c)):
            continue
        if SCORING.search(c) or judge.PERF_FORMISH.search(ln.text) or judge.X2_STAFF_CTX.search(c):
            continue
        # without a consequence of its own, a bidder predicate under scoring or evaluation text is that text's item
        if not strong and any(SCORING.search(x.text) for x in b.notice.window(ln.i, 6, 0)[:-1] if x.doc == ln.doc):
            continue
        if judge.evaluation_context(b, ln) or judge.not_record_limit(b, ln):
            continue
        out.append(ln)
    if switches.X2_RECORD_NOISE:
        out = [ln for ln in out if not judge.x2_not_bidder_record(b, ln)]
    return out


def v2(b):
    P = b.meta.P
    if switches.AUDIT_FIXES3 and P is not None and P < 1e6 and b.meta.B and b.meta.B >= 1e6:
        P = b.meta.B / 1.1
    if P is None or P >= meta.NOTICE_AMOUNT or b.meta.local_private:
        return None
    recs = records(b)
    return recs[0] if recs else None


def v4(b):
    for ln in records(b):
        clause = judge.clause_text(b.notice, ln)
        c = judge.LAW_REF.sub(' ', clause)
        verdict = judge.buyer_limit(clause)
        if verdict == 'specific' and not (switches.V4_PRIVATE_ENUM and not judge.BENEFICIARY.search(c) and judge.x1_private_in_enum(c)):
            return ln
        bare = judge.LAW_NAME.sub(' ', clause)
        named = any(not m.group(0).startswith('[수요기관') for m in judge.buyer_matches(bare))
        if (verdict is None and named and not judge.PRIVATE_SECTOR.search(bare) and not judge.x1_private_in_enum(c)
                and b.read('perf', ln).get('발주처') == '특정 발주기관만'):
            return ln
    return None


def v8(b):
    if b.meta.local_private:
        return None
    recs = records(b)
    if not recs:
        return None
    lines, _, _ = judge.region_restriction(b)
    if lines or b.meta.region_flag == 'Y':
        return lines[0] if lines else recs[0]
    return None
