"""v14-v18 (switch RTD4_SIZE_UNREAD): a participation clause that states the enterprise-size class in the notice's own words
decides when the size reading returned no restriction.

The 입찰공고 states who may bid (국가계약법 시행령 제36조, 지방계약법 시행령 제33조). A clause of the form "<class> … 으로서
(인 자, 인 업체) … 확인서를 소지(보유, 제출)" or "입찰참가업체는 … <class> …" is such a statement wherever the 공고문 puts it
outside its evaluation criteria and document lists; for the absence items (v16, v18) the same clause in an attachment
counts too, since the documents then do state a size restriction. A method statement, a 나라장터 tag, a document-list entry,
a bonus, a subcontracting rule, an amount tier, an admission of 비영리법인·특별법인 or a SW진흥법 sentence states no class
(talkboard: 입찰방법 표시·확인서 서류·조항호 등록 without a 참가자격 clause is no restriction).
"""
import re
from . import families, judge, switches
from .rtd_size_typography import normal

HELD = re.compile(r'확\s*인\s*서[^.。]{0,40}?(소\s*지|보\s*유|제\s*출\s*(하\s*여\s*야|해\s*야|하\s*는\s*자|한\s*자|한\s*업\s*체))')
CLASS_AS = re.compile(r'(소\s*기\s*업\s*자?|소\s*상\s*공\s*인|중\s*소\s*기\s*업\s*자?|중\s*·\s*소\s*기\s*업\s*자?)[^.。]{0,80}?'
                      r'(으\s*로\s*서|로\s*서|인\s*자(?![가-힣])|인\s*업\s*체|업\s*체\s*로\s*서)')
BIDDER_IS = re.compile(r'입\s*찰\s*참\s*가\s*(업\s*체|자)\s*(는|은)[^.。]{0,80}?(소\s*기\s*업|소\s*상\s*공\s*인|중\s*소\s*기\s*업)')
NOT_BIDDER = re.compile(r'하\s*도\s*급|가\s*점|배\s*점|평\s*가|우\s*대|신\s*인\s*도|해\s*당\s*(시|하\s*는\s*경\s*우|되\s*는\s*경\s*우)'
                        r'|실\s*적|교\s*육\s*생|사\s*업\s*대\s*상|수\s*혜|지\s*원\s*대\s*상|모\s*집|참\s*여\s*기\s*업')
SKIP_SECTIONS = ('EVAL', 'DOCS')


def _class(text):
    cls = families.size_words(families.size_normal(normal(text)))
    return cls if cls in ('small', 'sme') else None


def stated(b, any_doc):
    """[(line, class)] of size-class participation clauses in the notice's own words."""
    out = []
    for ln in b.notice.lines:
        if ln.sec in SKIP_SECTIONS or not (any_doc or ln.doc_type == '공고문'):
            continue
        if not judge.SIZE_WORD_CHEAP.search(ln.text) or judge.METHOD_SUMMARY.search(ln.text):
            continue
        own = ' '.join(judge.own_clause(b, ln).split())
        if not (CLASS_AS.search(own) and HELD.search(own) or BIDDER_IS.search(own)):
            continue
        if (NOT_BIDDER.search(own) or judge.AMOUNT_TIER.search(own) or judge.SIZE_ADMISSION.search(own)
                or judge.SIZE_WAIVER.search(own) or judge.sw_only(own)):
            continue
        cls = _class(own)
        if cls:
            out.append((ln, cls))
    return out


def adjust(b, it, hit):
    if not switches.RTD4_SIZE_UNREAD:
        return hit
    if it in ('v16', 'v18'):
        return None if hit is not None and stated(b, True) else hit
    if hit is not None or not judge._general(b):
        return hit
    P = b.meta.P
    if not {'v14': P >= judge.NOTICE_AMOUNT, 'v15': judge.EOK <= P < judge.NOTICE_AMOUNT, 'v17': P < judge.EOK}[it]:
        return hit
    state, lines, _ = judge.size_state(b, positive=True, gate_normal=switches.RTD_SIZE_TYPOGRAPHY)
    state, lines = judge.x5_declared(b, state, lines)
    if state is not None or judge.x5_positive_off(b, lines):
        return hit
    if it == 'v17' and (judge.sme_widening_allowed(b) or switches.REG_CONSISTENCY and judge.sme_registered(b)):
        return hit
    found = stated(b, False)
    classes = {c for _, c in found}
    if not found or it == 'v15' and classes != {'small'} or it == 'v17' and classes != {'sme'}:
        return hit
    return found[0][0]
