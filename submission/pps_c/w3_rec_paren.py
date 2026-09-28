"""Red team W3 (switch W3_REC_PAREN): v2, v4, v8 — a 공고문 qualification line stating a held record ("최근 3년 이내 … 환경컨설팅
1건이상 수행경험을 보유한 기관 (관련 실적증명서류 제출, 별도의 양식은 없으며, 계약서 사본 및 세금계산서 사본으로 대체 가능)")
is a record requirement although a parenthetical says how to prove it: the substitution-note and exclusion words (대체·갈음,
등록·제출·신청 …) are read outside parentheses, for the lines the perf family read as 참가자격 and for those PERF_UNREAD
scans; every other gate of judge.perf_lines holds. Basis: organizer 9/28 (amount-less records are records; context, not
position, decides)."""
import re
from . import judge, meta, switches

PAREN = re.compile(r'[\(（][^()（）]{0,160}[\)）]')


def records(b):
    known = {ln.i for ln in judge.perf_lines(b)}
    out = []
    # the perf family's qualification readings that perf_lines dropped for a note word inside a parenthetical
    for ln in judge.lines_where(b, 'perf', section=True, 역할='참가자격'):
        if ln.i in known or ln.doc_type != '공고문' or not judge.PERF_NOTE.search(ln.text):
            continue
        if judge.PERF_NOTE.search(PAREN.sub(' ', ln.text)):
            continue
        if judge.not_record_limit(b, ln) or judge.evaluation_context(b, ln):
            continue
        out.append(ln)
    # PERF_UNREAD lines dropped for an exclusion word inside a parenthetical
    cands = {ln.i for ln in b.cands.get('perf', [])}
    for ln in b.notice.lines:
        if ln.i in known or ln.i in cands or ln.doc_type != '공고문' or ln.sec != 'QUAL' or not judge.qual_section(ln, b.notice):
            continue
        t = ln.text
        if not (judge.NOT_RECORD.search(t) or judge.PERF_NOTE.search(t)):
            continue          # PERF_UNREAD already judged this line
        bare = PAREN.sub(' ', t)
        if not (judge.HELD_RECORD.search(bare) or judge.X2_HELD_VERBS_R3.search(bare)) or judge.NOT_RECORD.search(bare):
            continue
        if (judge.PERF_FORMISH.search(bare) or judge.PERF_NOTE.search(bare) or judge.METHOD_SUMMARY.search(t)
                or judge.evaluation_context(b, ln) or judge.not_record_limit(b, ln) or judge.X2_STAFF_CTX.search(bare)
                or judge.PU_EXCLUSION.search(judge.clause_text(b.notice, ln)) or judge.preference_bound(bare, judge.HELD_RECORD)):
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
        # the model-reading path of judge.v4_base: a named buyer kind other than the orderer token, no private party
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
