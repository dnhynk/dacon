"""Round-6 v19 CPU fallbacks (switches X3_V19_TIME, X3_V19_ISSUER), consulted by judge.v19 after its own loop finds nothing.

v19 (항목표 "물품공급 확약서 입찰 시 제출", 비고 "입찰 전 발급, 계약시 제출 등 표현 다양"; talkboard 9/28: the 확약서 is
submitted at the bid or issued or held before it; 집행기준 제5조의3③: the winner obtains it from the maker after the award).
A pledge line passes when it satisfies judge.v19's own conditions with these widenings:
- X3_V19_TIME: bid-stage wording the judge's timing checks do not know: "입찰에 앞서", "(입찰·견적·제안·응찰) … 접수·제출·참여
  시/전/마감/기간/동시", "응찰 시", "참가신청서 접수 시", "가격입찰 개시 전", "공고 마감일까지", the pledge placed in the bid or
  proposal documents ("제안서에는 … 첨부", "입찰서류에 포함", "제안서 부록에 … 수록"), a bar on bidders without it ("…없는 업체는
  입찰에 참가할 수 없", "…제출한 업체에 한하여 입찰참가", "누락 시 … 입찰은 무효"), a 참가 요건·조건 label, or a list entry
  marked "(입찰서류)"; clauses that also name the 적격심사, award or contract stage, the lawful 제5조의3 regime or a mere
  capability are left to the other checks, except a pre-bid issuance or holding in these words ("입찰에 앞서 … 발급받아
  보유하고, 계약 체결 시 제출"). Like the judge's other CPU timing checks it applies whatever the model read.
- X3_V19_ISSUER: third-party issuers the judge's issuer lists omit (생산업체·생산자, 제작업체, 개발사·개발업체, 수입사·수입원,
  파트너사, 공급권자, 저작권자, an anonymised company token), when the reading does not call it the bidder's own; and the
  "정품공급·기술지원 보증서" as the same maker's pledge document.
"""
import re

from . import switches

ISSUER_MORE = re.compile(r'생\s*산\s*(업\s*체|자|사)(?!\s*업)|제\s*작\s*(업\s*체|자)|개\s*발\s*(사|업\s*체|자)(?!\s*업)|수\s*입\s*(사|원|업\s*체)'
                         r'|파\s*트\s*너\s*사|공\s*급\s*권\s*자|저\s*작\s*권\s*자|\[\s*(업\s*체|회\s*사|제\s*조\s*사)\s*\(?[^\]]{0,12}\]')
DOC_MORE = re.compile(r'(공\s*급|기\s*술\s*지\s*원)\s*(및\s*|[·ㆍ]\s*)?(기\s*술\s*지\s*원\s*)?(\(\s*A\s*/\s*S\s*\)\s*)?보\s*증\s*서')
# bid-stage wording (see the module docstring)
TIME_MORE = re.compile(
    r'입\s*찰\s*에\s*앞\s*서|입\s*찰\s*(에\s*)?참\s*(가|여)\s*하\s*려\s*면'
    r'|(입\s*찰|견\s*적|제\s*안|응\s*찰|투\s*찰|가\s*격\s*입\s*찰)\s*(서\s*류|서)?\s*(의\s*)?(접\s*수|제\s*출|개\s*시|참\s*여|참\s*가)\s*'
    r'(시|전|전\s*까\s*지|마\s*감|기\s*간|기\s*한|와\s*동\s*시|과\s*동\s*시|와\s*함\s*께|과\s*함\s*께)'
    r'|응\s*찰\s*(시|전)|참\s*가\s*신\s*청\s*(서\s*)?(접\s*수|제\s*출)?\s*(시|전|마\s*감)|공\s*고\s*마\s*감\s*(일|시)?\s*(까\s*지|전)'
    r'|(제\s*안\s*서|입\s*찰\s*서\s*류?|견\s*적\s*서)\s*(에\s*는|에|의\s*부\s*록\s*에|부\s*록\s*에)\s*[^.。]{0,40}(첨\s*부|포\s*함|수\s*록|동\s*봉)'
    r'|(입\s*찰\s*서\s*류|제\s*안\s*서|견\s*적\s*서)\s*에\s*포\s*함'
    r'|(없\s*는|미\s*보\s*유|갖\s*추\s*지\s*(못|않)\s*한|구\s*비\s*하\s*지\s*(않|못)\s*은|미\s*구\s*비)\s*(업\s*체|자|입\s*찰\s*자)[^.。]{0,24}'
    r'(입\s*찰|투\s*찰|참\s*가|참\s*여|응\s*찰)[^.。]{0,16}(수\s*없|무\s*효|불\s*가|부\s*적\s*격|제\s*외)'
    r'|(없\s*는|미\s*보\s*유|구\s*비\s*하\s*지\s*(않|못)\s*은)\s*(업\s*체|자|입\s*찰\s*자)[^.。]{0,6}(는|은|의)?\s*부\s*적\s*격'
    r'|(제\s*출|보\s*유|구\s*비)\s*한\s*(업\s*체|자)\s*(에\s*한\s*하|만)[^.。]{0,20}(입\s*찰|참\s*가|참\s*여|투\s*찰)'
    r'|누\s*락\s*(시|된\s*경\s*우)[^.。]{0,20}(입\s*찰|투\s*찰|견\s*적)[^.。]{0,10}무\s*효'
    r'|(제\s*출|첨\s*부|보\s*유|확\s*보|구\s*비)\s*되\s*어\s*야\s*(만\s*)?(입\s*찰|견\s*적|투\s*찰)'
    r'|(제\s*출|첨\s*부)\s*(없\s*이\s*는|없\s*으\s*면)[^.。]{0,16}(입\s*찰|투\s*찰|참\s*가|참\s*여)[^.。]{0,10}(불\s*가|수\s*없|무\s*효)'
    r'|(첨\s*부|제\s*출|포\s*함)\s*되\s*지\s*(않|아\s*니)\s*(은|한)\s*(입\s*찰\s*서?|견\s*적\s*서?|제\s*안\s*서)[^.。]{0,12}(무\s*효|제\s*외|불\s*가)'
    r'|(입\s*찰\s*서|견\s*적\s*서|제\s*안\s*서)\s*(와|과)\s*[^.。]{0,40}동\s*시\s*에?\s*제\s*출'
    r'|(소\s*지|보\s*유|구\s*비|확\s*보)\s*하\s*지\s*(않|못)\s*(은|한)\s*(자|업\s*체|입\s*찰\s*자)[^.。]{0,10}(입\s*찰|투\s*찰|참\s*가|참\s*여)[^.。]{0,10}(수\s*없|불\s*가|무\s*효)'
    r'|(입\s*찰|개\s*찰|투\s*찰|마\s*감)\s*일\s*(로\s*부\s*터|기\s*준)\s*\d{1,2}\s*일\s*(전|이\s*전)'
    r'|입\s*찰\s*참\s*가\s*자\s*격\s*(등\s*록|신\s*청|사\s*전\s*심\s*사)\s*(시|전|마\s*감)'
    r'|(입\s*찰\s*)?참\s*가\s*(요\s*건|조\s*건)\s*[:：][^.。]{0,60}(제\s*출|보\s*유|구\s*비|소\s*지|첨\s*부)'
    r'|[\(（]\s*(입\s*찰|견\s*적|제\s*안)\s*(참\s*가\s*|제\s*출\s*)?서\s*류\s*[\)）]')
# capability wording attached to the pledge document ("확약서를 제출할 수 있어야", "확약서 발급이 가능한") states no time
# (talkboard 9/28: undecided); the same words elsewhere in the clause ("전자입찰서 제출이 가능합니다") are not about the pledge
CAPABILITY = re.compile(r'(확\s*약\s*서|확\s*인\s*서|증\s*명\s*원?|협\s*약\s*서|보\s*증\s*서)[^.。]{0,30}?'
                        r'(제\s*출|발\s*급|증\s*명)\s*(이\s*)?(할\s*수\s*있|가\s*능)')
HOLD_MORE = re.compile(r'(입\s*찰\s*에\s*앞\s*서|응\s*찰\s*(시|전)|입\s*찰\s*(참\s*여|참\s*가)\s*전|(가\s*격\s*)?입\s*찰\s*개\s*시\s*전'
                       r'|(투\s*찰|견\s*적|제\s*안|응\s*찰)\s*(서\s*)?(제\s*출\s*)?(개\s*시\s*)?(이\s*전|전)\s*에?)'
                       r'[^.。,，]{0,40}(발\s*급|보\s*유|확\s*보|취\s*득|구\s*비|보\s*관|받\s*아\s*두)')


def _on(name):
    return bool(getattr(switches, name, False))


def _J():
    from . import judge
    return judge


def _issuer_wide(b, ln, r):
    who = str(r.get('발급 주체', ''))
    if who.startswith('제3자'):
        return True
    return (_on('X3_V19_ISSUER') and not who.startswith('입찰자') and bool(ISSUER_MORE.search(ln.text))
            and not _J().OWN_PLEDGE.search(ln.text))


def _names_issuer(ln):
    if _J().names_issuer(ln):
        return True
    return _on('X3_V19_ISSUER') and bool(ISSUER_MORE.search(ln.text))


def _doc(b, ln):
    J = _J()
    if J.pledge_document(b, ln):
        return True
    if not _on('X3_V19_ISSUER'):
        return False
    clause = J.clause_text(b.notice, ln)
    return bool(DOC_MORE.search(clause)) and not J.ISSUED_BETWEEN.search(clause) and not J.REFERENCE_ONLY.search(ln.text)


def _sentence(b, ln):
    J = _J()
    if J.pledge_sentence(b, ln):
        return True
    if not _on('X3_V19_ISSUER'):
        return False
    for s in J.PLEDGE_SENTENCE.split(J.clause_text(b.notice, ln)):
        s = J.OWN_PLEDGE.sub(' ', s)
        if (J.PLEDGE_DOC.search(s) or DOC_MORE.search(s)) and (ISSUER_MORE.search(s) or J.THIRD_PARTY_FIX.search(s)
                                                              or J.SUPPLY_PLEDGE.search(s)):
            return True
    return False


def _demanded(ln):
    J = _J()
    t = ln.text
    if J.ORDERER_AGREEMENT.search(t) or J.PLEDGE_NOT_DEMANDED.search(t) or not _names_issuer(ln):
        return False
    return not ((J.UNTIMED_CAPABILITY2 if switches.AUDIT_FIXES2 else J.UNTIMED_CAPABILITY).search(t) and not J.BID_TIME.search(t))


def _violation(b, ln):
    """judge.x6_pledge_violation with the 보증서 accepted as the pledge document (X3_V19_ISSUER) and a pre-bid issuance or
    holding in the X3_V19_TIME words standing although the copy is submitted at contract (_hold_more)."""
    J = _J()
    if J.x6_pledge_violation(b, ln):
        return True
    t = J.clause_text(b.notice, ln)
    hold = _hold_more(t)
    if not (_on('X3_V19_ISSUER') or hold):
        return False
    cap_pre = _on('V19_CAP_PRE') and hasattr(J, 'cap_qualification') and J.cap_qualification(b, ln)
    if J.x6_pledge_stage(b, ln) in ('QUAL_STAGE', 'POST', 'LAWFUL', 'PRE_CAP') and not cap_pre and not hold:
        return False
    if (J.X6_QUAL_STAGE.search(t) or J.X6_POST.search(t)) and not J.X6_PRE_TIME.search(t) \
            and not (switches.C2_PLEDGE_VOCAB and J.X6_PRE_TIME_C2.search(t)) \
            and not (J.X6_PRE_HOLD.search(t) and J.X6_SUBJECT_BIDDER.search(t)) and not hold:
        return False
    if J.X6_CAPABILITY.search(t) and not J.X6_PRE_TIME.search(t) and not cap_pre:
        return False
    doc = J.X6_PLEDGE.search(t) or switches.C2_PLEDGE_VOCAB and J.X6_PLEDGE_C2.search(t)
    return bool(DOC_MORE.search(t) if not hold else (doc or _on('X3_V19_ISSUER') and DOC_MORE.search(t)))


def _hold_more(t):
    """X3_V19_TIME: issuance or holding set before the bid in the words TIME_MORE adds ("입찰에 앞서 … 발급받아 보유하고,
    계약 체결 시 제출"): a pre-bid demand although the copy is handed over at contract (talkboard 9/28)."""
    return _on('X3_V19_TIME') and bool(HOLD_MORE.search(t)) and not _J().X6_LAWFUL.search(t) and not CAPABILITY.search(t)


def _time_more(b, ln):
    J = _J()
    t = J.clause_text(b.notice, ln)
    if _hold_more(t):
        return True
    if J.X6_LAWFUL.search(t) or J.X6_QUAL_STAGE.search(t) or J.X6_POST.search(t) or CAPABILITY.search(t):
        return False
    return bool(TIME_MORE.search(t))


def _timed(b, ln, r):
    """judge.v19's own timing tests (as there), then the X3_V19_TIME wording."""
    J = _J()
    timed = (str(r.get('시점', '')).startswith('입찰 전') or (switches.AUDIT_FIXES and J.before_award(ln.text))
             or switches.V19_HOLD_BY_BID and bool(J.HOLD_BY_BID.search(J.clause_text(b.notice, ln))))
    if not timed and switches.V19_CPU_TIMED and J.x6_pledge_stage(b, ln) in ('PRE', 'PRE_LIST'):
        timed = True
    if not timed and switches.V19_LIST_DEADLINE and J.list_deadline_pre(b, ln):
        timed = True
    if not timed and _on('V19_CAP_PRE') and hasattr(J, 'cap_qualification') and J.cap_qualification(b, ln):
        timed = True
    if not timed and switches.V19_BID_BAR and J.bid_bar_timed(b, ln):
        timed = True
    qual = switches.V19_QUAL_STAGE and J.x6_qual_stage_demand(b, ln)
    if not timed and qual:
        timed = True
    if (not timed and switches.AUDIT_FIXES2 and switches.V19_LIST_STAGE and r.get('시점', '') in ('불명', '')
            and (ln.doc_type == '공고문' and ln.sec == 'QUAL' or J.pre_award_list(b, ln))):
        timed = True
    if not timed and _on('X3_V19_TIME') and _time_more(b, ln):      # explicit wording, whatever the reading
        timed = True
    if not timed and _hold_more(J.clause_text(b.notice, ln)):      # as V19_HOLD_BY_BID, whatever the reading
        timed = True
    if switches.W3_V19_REVIEW_LIST and timed:
        from . import w3_v19_review
        timed = w3_v19_review.timed(b, ln, r, timed)
    return timed, qual


def _passes(b, ln, r):
    if not _issuer_wide(b, ln, r):
        return False
    timed, qual = _timed(b, ln, r)
    return bool(timed and _demanded(ln)
                and not ((switches.AUDIT_FIXES2 or switches.V19_PLEDGE_FIXES) and not _doc(b, ln))
                and not ((switches.AUDIT_FIXES3 or switches.V19_PLEDGE_FIXES) and not _sentence(b, ln))
                and not (switches.V19_STAGE and not (_violation(b, ln) or qual)))


def extra(b):
    """A pledge line judge.v19 did not take that passes with the X3 widenings, or None."""
    for ln in b.cands.get('pledge', []):
        if _passes(b, ln, b.read('pledge', ln)):
            return ln
    return None
