"""v17 (switch T4_V17_NOTICE_CLASS): the 공고문's class decides when only an attachment names 소기업·소상공인.

The 입찰공고 states the participation qualification (국가계약법 시행령 제36조, 지방계약법 시행령 제33조). When its
qualification clauses admit the whole SME class (중기업 included) and only an attachment (제안요청서, 과업지시서, 규격서)
names the small class, the notice restricts to 중소기업 below 1억, which 판로지원법 시행령 제2조의2 ①1 does not allow
unless a 단서 reason is stated. A 공고문 line that only names the kind of bid ("방법: 제한경쟁(소기업·소상공인)") states no
qualification (talkboard DEV-039, rtd4_bid_decl) and does not outweigh the qualification clause.
"""
from . import judge, switches


def notice_class_v17(b):
    """The first 공고문 SME-class clause, when only attachment clauses name the small class (None otherwise)."""
    if not judge._general(b) or b.meta.P >= judge.EOK or judge.sme_widening_allowed(b):
        return None
    if switches.REG_CONSISTENCY and judge.sme_registered(b):
        return None
    state, lines, _ = judge.size_state(b, positive=True, gate_normal=switches.RTD_SIZE_TYPOGRAPHY)
    if state != 'small' or judge.x5_positive_off(b, lines):
        return None
    if switches.X5_V17_CLASS and judge.small_with_companions(lines):
        return None
    from .rtd4_bid_decl import _decl
    cls = {ln.i: judge.size_class_for_gate(b, ln) for ln in lines}
    notice = [ln for ln in lines if ln.doc_type == '공고문' and cls[ln.i] in ('sme', 'small')
              and not (switches.RTD4_BID_DECL and _decl(b, ln))]
    if not notice or any(cls[ln.i] != 'sme' for ln in notice):
        return None
    return notice[0]
