"""v14/v15/v17 (switch T4_SIZE_WIDE): a clause that limits bidders to an enterprise-size class counts wherever the documents
state it, when the qualification section states no class; v16/v18 (switch T4_SIZE_WIDE_ABS): such a clause is a stated
restriction.

The 입찰공고 states who may bid (국가계약법 시행령 제36조, 지방계약법 시행령 제33조); a notice that limits bidding to 중소기업자
or to 소기업·소상공인 in its notes (유의사항, 기타사항), its opening lines or an attachment (제안요청서, 과업지시서) outside a
qualification heading restricts participation just the same. Only a clause whose own words limit who may bid, with the
class word next to the limiting words, counts: "…만 참가(참여, 입찰, 제안서 제출)할 수 있", "…에 한함", "…(으)로 제한·한정",
"…이어야 합니다", "…(확인서)가 없는 업체는 참여할 수 없", "… 외의 자는 참가할 수 없", "…확인서를 제출하여야 입찰에 참가",
"참가대상: …", a 참가자격 label ("[참가자격] …", "| 참가자격 | …"), the eligibility form "…으로서 … 확인서를 소지한",
"…과의 우선조달계약 대상", "…간 제한경쟁입찰" citing the 판로지원 decree outside the 공고문's bid-method section, and "대기업
및 중견기업은 참가할 수 없음" (only 중소기업자 remain). Evaluation and document-list sections,
document entries, bonuses, subcontracting, programme beneficiaries, amount tiers, admissions of 비영리법인, waivers,
negations, certificate issuance and validity notes, reports of a failed small-firm bid (유찰), SW진흥법 and software-project
sentences, a class that admits 중견기업, and a bare statement of the kind of bid (talkboard DEV-039) state no restriction.
"""
import re
from . import judge, switches
from .rtd4_size_unread import CLASS_AS, HELD, BIDDER_IS, NOT_BIDDER, _class

# Predicates that limit who may bid; the class word must stand just before them ("…소기업·소상공인만 참가", "…중소기업으로 제한").
LIMIT = re.compile(r'만\s*(을\s*)?(본\s*)?(입\s*찰\s*에\s*|사\s*업\s*에\s*)?(참\s*가|참\s*여|입\s*찰|응\s*찰|투\s*찰|제\s*안|견\s*적)'
                   r'|에\s*한\s*(함|한\s*다|합\s*니\s*다|하\s*여|정)'
                   r'|(으\s*로|로)\s*(입\s*찰\s*)?(참\s*가\s*|참\s*여\s*)?(자\s*격\s*을\s*)?(제\s*한|한\s*정)\s*(합|하|함|한|됨|됩|되)'
                   r'|이\s*어\s*야\s*(합|함|하|한)'
                   r'|(없\s*는|않\s*은|아\s*닌)\s*(업\s*체|자|법\s*인)\s*(는|은)?[^.。]{0,20}?(참\s*가|참\s*여|입\s*찰)[^.。]{0,10}?(할\s*수\s*없|불\s*가)'
                   r'|외\s*의?\s*(자|업\s*체|기\s*업)\s*(는|은)\s*[^.。]{0,12}?(참\s*가|참\s*여|입\s*찰)[^.。]{0,10}?(할\s*수\s*없|불\s*가)'
                   r'|(제\s*출|소\s*지|보\s*유)\s*(하\s*여\s*야|해\s*야)\s*[^.。]{0,12}?(참\s*가|참\s*여|입\s*찰)')
# Labels the class follows ("참가대상: 중소기업자", "[참가자격] 소기업·소상공인", "| 참가자격 | …").
LABEL = re.compile(r'참\s*(가|여)\s*(대\s*상|가\s*능\s*(한\s*)?(업\s*체|자))\s*[:：은는]|(참\s*가|참\s*여|입\s*찰)\s*자\s*격\s*[\)）\]】>:：|]')
CLASS_WORD = re.compile(r'소\s*기\s*업|소\s*상\s*공\s*인|중\s*[·ㆍ・]?\s*소\s*기\s*업|중\s*기\s*업')
# 중소기업자간 경쟁제품 is the product regime (판로지원법 제6조·제7조), not a class limit.
BID_KIND = re.compile(r'(소\s*기\s*업|소\s*상\s*공\s*인|중\s*[·ㆍ・]?\s*소\s*기\s*업\s*자?)[^.。]{0,12}?간\s*(의\s*)?(제\s*한\s*)?(경\s*쟁|입\s*찰)'
                      r'(?!\s*(제\s*품|물\s*품|품\s*목|대\s*상\s*품))')
LAW_CUE = re.compile(r'판\s*로|제\s*2\s*조\s*의\s*2|우\s*선\s*조\s*달|시\s*행\s*령')
LARGE_BARRED = re.compile(r'대\s*기\s*업\s*(은|,|·|ㆍ|및|과|와|또\s*는)\s*중\s*견\s*기\s*업\s*(은|는|의)?[^.。]{0,20}?(참\s*가|참\s*여|입\s*찰|응\s*찰)'
                          r'[^.。]{0,12}?(할\s*수\s*없|불\s*가|제\s*한\s*(합|하|함|됨|됩)|제\s*외)')
# "…소기업·소상공인과의 우선조달계약 대상입니다" declares the class the bid is limited to (판로지원법 시행령 제2조의2); the article
# title "제2조의2(중소기업자와의 우선조달계약)" does not.
PRIORITY = re.compile(r'(?P<cls>소\s*기\s*업|소\s*상\s*공\s*인|중\s*[·ㆍ・]?\s*소\s*기\s*업\s*자?)\s*(과|와)?\s*(의)?\s*(간\s*(의\s*)?)?'
                      r'우\s*선\s*조\s*달\s*(계\s*약)?\s*(대\s*상|으\s*로|로\s*(진\s*행|추\s*진|실\s*시))')
NEGATED = re.compile(r'(제\s*한|해\s*당|대\s*상)\s*(이|은|는|사\s*항\s*(이|은))?\s*없|제\s*한\s*(을\s*)?하\s*지\s*않|아\s*니|아\s*닙|아\s*님')
CERT_NOTE = re.compile(r'미\s*발\s*급|발\s*급\s*(을\s*)?받\s*지\s*(못|않)|발\s*급\s*되\s*지\s*(않|못)|신\s*청\s*한|유\s*효\s*기\s*간|판\s*단\s*기\s*준\s*일')
MIDSIZE = re.compile(r'중\s*견')
SOFTWARE = re.compile(r'소\s*프\s*트\s*웨\s*어|S\s*W\s*사\s*업|정\s*보\s*(화\s*사\s*업|시\s*스\s*템)')
DOC_ENTRY = re.compile(r'\d\s*부|사\s*본|원\s*본|제\s*출\s*서\s*류|구\s*비\s*서\s*류')
SKIP_SECTIONS = ('EVAL', 'DOCS')


def _limits(ln, own):
    return bool(any(CLASS_WORD.search(own[max(0, m.start() - 24):m.end()]) for m in LIMIT.finditer(own))
                or any(CLASS_WORD.search(own[m.end():m.end() + 30]) for m in LABEL.finditer(own))
                or CLASS_AS.search(own) and HELD.search(own) or BIDDER_IS.search(own)
                or BID_KIND.search(own) and LAW_CUE.search(own) and not (ln.doc_type == '공고문' and ln.sec == 'BID'))


def _states_none(own):
    return bool(NOT_BIDDER.search(own) or MIDSIZE.search(own) or DOC_ENTRY.search(own) or NEGATED.search(own)
                or CERT_NOTE.search(own) or judge.VERIFY_NOTE.search(own) or judge.AMOUNT_TIER.search(own)
                or judge.PAST_FAIL.search(own) or judge.CLAUSE_1.search(own)
                or judge.SIZE_ADMISSION.search(own) or judge.SIZE_WAIVER.search(own) or judge.sw_only(own))


def wide(b):
    """[(line, class)] of clauses limiting bidders to a size class, outside evaluation and document-list sections."""
    out = []
    for ln in b.notice.lines:
        if ln.sec in SKIP_SECTIONS or judge.METHOD_SUMMARY.search(ln.text):
            continue
        if not (judge.SIZE_WORD_CHEAP.search(ln.text) or MIDSIZE.search(ln.text)):
            continue
        own = ' '.join(judge.own_clause(b, ln).split())
        if (LARGE_BARRED.search(own) and not NOT_BIDDER.search(own) and not judge.SW_LARGE.search(own)
                and not SOFTWARE.search(own) and not judge.SIZE_WAIVER.search(own)):
            out.append((ln, 'sme'))
            continue
        pm = PRIORITY.search(own)
        if pm and not _states_none(own):
            out.append((ln, 'sme' if re.match(r'중', pm.group('cls')) else 'small'))
            continue
        if not _limits(ln, own) or _states_none(own):
            continue
        cls = _class(own)
        if cls:
            out.append((ln, cls))
    return out


def adjust(b, it):
    """v14/v15/v17 when the item's rule found nothing: the first wide clause of the item's class, provided the qualification
    section states no class."""
    if not judge._general(b):
        return None
    P = b.meta.P
    if not {'v14': P >= judge.NOTICE_AMOUNT, 'v15': judge.EOK <= P < judge.NOTICE_AMOUNT, 'v17': P < judge.EOK}[it]:
        return None
    state, lines, _ = judge.size_state(b, positive=True, gate_normal=switches.RTD_SIZE_TYPOGRAPHY)
    state, lines = judge.x5_declared(b, state, lines)
    if state is not None or judge.x5_positive_off(b, lines):
        return None
    if it == 'v17' and (judge.sme_widening_allowed(b) or switches.REG_CONSISTENCY and judge.sme_registered(b)):
        return None
    found = wide(b)
    classes = {c for _, c in found}
    if not found or it == 'v15' and classes != {'small'} or it == 'v17' and classes != {'sme'}:
        return None
    return found[0][0]
