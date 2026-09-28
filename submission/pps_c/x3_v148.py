"""Red team X3 (round 6): v1, v4 and v8 participation requirements written where, or as, the judge rules and the U1 detectors
do not read them.

Each detector sits behind its own switch (switches.py, all default False). judge.v1, judge.v4 and judge.v8 consult this module
only after their own rules and every earlier detector found nothing, so with the switches off every judgment is unchanged.
The organizer (9/28): whether a clause limits participation is read from its context, not from where it is written.
"""
from __future__ import annotations

import re

from . import judge, regions, switches


def _norm(t):
    return ' '.join((t or '').split())


# ---------------------------------------------------------------- shared frames
_MARK = r'^\W*(?:(?:\d{1,2}|[가-하])\s*[.)]\s*)?'
# The bidder or the firm that performs the work as the subject of a requirement ("과업 수행업체는", "입찰참가자는", "제안사는").
_SUBJ = re.compile(_MARK + r'(?:(?:과\s*업|용\s*역|사\s*업)\s*수\s*행\s*(?:업\s*체|기\s*관|사|자)\s*(?:는|은)'
                   r'|입\s*찰\s*참\s*가\s*(?:자|업\s*체)\s*(?:는|은)|입\s*찰\s*에\s*참\s*가\s*하\s*려\s*는\s*(?:자|업\s*체)\s*(?:는|은)'
                   r'|참\s*가\s*업\s*체\s*(?:는|은)|제\s*안\s*(?:사|업\s*체|자)\s*(?:는|은)|납\s*품\s*업\s*체\s*(?:는|은))')
# A participation label with a procedure prefix ("제안참가자격 :", "견적참가자격 :").
_LABEL = re.compile(_MARK + r'(?:제\s*안|입\s*찰|견\s*적|사\s*업)\s*참\s*가\s*자\s*격\s*[:：|]')
# A record-requirement label of an attachment table or list ("| 실적요건 | …", "납품실적 : …").
_REC_LABEL = re.compile(_MARK + r'(?:실\s*적\s*(?:요\s*건|제\s*한|기\s*준)|납\s*품\s*실\s*적|수\s*행\s*실\s*적|이\s*행\s*실\s*적)\s*[:：|]')
_NOT = re.compile(r'평\s*가|배\s*점|점\s*수|가\s*점|감\s*점|기\s*재|작\s*성|서\s*식|양\s*식|제\s*출\s*서\s*류|증\s*빙\s*서\s*류|하\s*도\s*급|협\s*력'
                  r'|공\s*동|분\s*담|컨\s*소\s*시\s*엄|우\s*대|인\s*력|책\s*임\s*자|대\s*표\s*자|직\s*원|숙\s*박|행\s*사\s*장|납\s*품\s*(?:장\s*소|지)'
                  r'|A\s*/\s*S|서\s*비\s*스\s*센\s*터|사\s*후\s*관\s*리')


def _placed(ln):
    """A line outside the 공고문 qualification section that can still state a qualification: an attachment line outside its
    evaluation and document sections, or a 공고문 line of the bidding, notes or other sections."""
    if not ln.text.strip():
        return False
    if ln.doc_type == '공고문':
        return ln.sec in ('BID', 'NOTE', 'OTHER')
    return ln.sec not in ('EVAL', 'DOCS')


# A duty of the contract party after the award ("계약 후 …에 사무소를 두어야") is a performance condition, not a qualification.
_POST_AWARD = re.compile(r'계\s*약\s*(?:체\s*결\s*)?(?:후|이\s*후|시|기\s*간\s*(?:중|동\s*안))|낙\s*찰\s*(?:후|이\s*후)|착\s*수\s*(?:시|후|전)'
                         r'|납\s*품\s*(?:시|후)|계\s*약\s*상\s*대\s*자|과\s*업\s*(?:수\s*행\s*)?기\s*간\s*(?:중|동\s*안)')


def _context_ok(b, ln):
    if _POST_AWARD.search(_norm(ln.text)):
        return False
    return not judge.evaluation_context(b, ln) and not judge.in_form_annex(b, ln)


# ---------------------------------------------------------------- X3_V8_PLACED (v8)
# The bidder's own seat (본점·본사·주된 영업소·사업장) held somewhere, or a firm located somewhere ("… 소재 업체").
_LOC = re.compile(r'(?:본\s*점|본\s*사|주\s*된\s*(?:영\s*업\s*소|사\s*무\s*소)|사\s*업\s*장)\s*(?:소\s*재\s*지)?\s*(?:을|를|이|가|의)?\s*[^.。]{0,25}?'
                  r'(?:둔|두\s*고|두\s*어\s*야|소\s*재\s*(?:한|하는|하여야|해야)|있\s*는|위\s*치\s*한)'
                  r'|(?:소\s*재|위\s*치)\s*(?:한|하는)?\s*(?:업\s*체|자(?![격료재원])|기\s*관|법\s*인|사\s*업\s*자)')
# "입찰참가 가능 지역은 강원특별자치도", "참여 가능 지역 : …".
_AREA = re.compile(r'(?:입\s*찰\s*)?참\s*(?:가|여)\s*가\s*능\s*지\s*역\s*(?:은|는|[:：|])|입\s*찰\s*참\s*(?:가|여)\s*지\s*역\s*[:：|]')
_LIMIT = re.compile(r'(?:업\s*체|자|기\s*관|법\s*인|사\s*업\s*자)\s*(?:로|으\s*로)\s*(?:한\s*정|제\s*한|한\s*다|함)|에\s*한\s*(?:함|하여|한다|정|합\s*니\s*다)'
                    r'|만\s*(?:참\s*여|참\s*가|입\s*찰|제\s*안|응\s*찰|가\s*능)|(?:업\s*체|자|기\s*관|법\s*인)\s*이\s*어\s*야|두\s*어\s*야|소\s*재\s*하\s*여\s*야')
_PERFORMER = re.compile(r'(?:과\s*업|용\s*역|사\s*업)\s*수\s*행\s*(?:업\s*체|기\s*관|사|자)\s*(?:는|은)')
_REC_REQ = re.compile(r'(?:실\s*적|경\s*험|이\s*력)\s*(?:이|을)?\s*(?:있\s*어\s*야|보\s*유\s*하\s*여\s*야|보\s*유\s*해\s*야|갖\s*추\s*어\s*야)'
                      r'|(?:실\s*적|경\s*험|이\s*력)\s*(?:이|을)?\s*(?:있\s*는|보\s*유\s*한)\s*(?:업\s*체|자|기\s*관|법\s*인)')


_DOC_PAREN = re.compile(r'[\(（]([^)）]{4,60})[\)）]')
_DOC_LIMIT = re.compile(r'(?:업\s*체|자|법\s*인|사\s*업\s*자)\s*(?:에\s*한\s*(?:함|한다|하여|정)|만\s*(?:참\s*가|참\s*여|입\s*찰)\s*(?:가\s*능|할\s*수))')
_SEAT_IS = re.compile(r'(?:본\s*점|본\s*사|주\s*된\s*(?:영\s*업\s*소|사\s*무\s*소)|사\s*업\s*장)\s*(?:소\s*재\s*지)?\s*(?:가|이)\s*[^.。()]{2,20}?(?:인|에\s*있\s*는)\s*(?:업\s*체|자)'
                      r'|소\s*재\s*(?:한|하는)?\s*(?:업\s*체|자|법\s*인|사\s*업\s*자)')


def doc_region(b):
    for ln in b.notice.lines:
        if ln.doc_type != '공고문' or ln.sec != 'DOCS':
            continue
        for m in _DOC_PAREN.finditer(_norm(ln.text)):
            v = m.group(1)
            if not _DOC_LIMIT.search(v) or not (_SEAT_IS.search(v) or _LOC.search(v)):
                continue
            if judge.REGION_NONE.search(v) or judge.JV_PARTNER3.search(v) or judge.V8_RW_NOT.search(v) or _NOT.search(v):
                continue
            mm = regions.mentions(v)
            if (mm['sido'] or mm['basic']) and _context_ok(b, ln):
                return ln
    return None


_ORD_AREA = re.compile(r'\[수\s*요\s*기\s*관\((?:기\s*초\s*자\s*치\s*단\s*체|광\s*역\s*자\s*치\s*단\s*체)[^\]]*\)[^\]]*\]\s*(?:관\s*내|내|안|관\s*할)')
_FIRM_ONLY = re.compile(r'(?:업\s*체|자|법\s*인|사\s*업\s*자)\s*만\s*(?:입\s*찰\s*에\s*)?(?:참\s*가|참\s*여|응\s*찰)\s*(?:할\s*수\s*있|가\s*능)')


def placed_region(b):
    """A bidder-location limit written in an attachment or a 공고문 bidding/notes line under a participation label, as the
    participation area, or as a limit on the firm that performs the work (시행규칙 제25조: the location of the bidder)."""
    for ln in b.notice.lines:
        if not _placed(ln):
            continue
        t = _norm(ln.text)
        if _NOT.search(t) or judge.REGION_NONE.search(t) or judge.JV_PARTNER3.search(t) or judge.V8_RW_NOT.search(t):
            continue
        m = regions.mentions(t)
        if not (m['sido'] or m['basic'] or _ORD_AREA.search(t)):
            continue
        frame = (_LABEL.search(t) and _LOC.search(t)) or _AREA.search(t) \
            or ((_PERFORMER.search(t) or _SUBJ.search(t)) and _LOC.search(t) and _LIMIT.search(t)) \
            or (_FIRM_ONLY.search(t) and _LOC.search(t))
        if frame and _context_ok(b, ln):
            return ln
    return None


def placed_record(b):
    """A held-record requirement written in an attachment line under a participation label or with the performing firm as
    its subject ("과업 수행업체는 … 실적이 있어야 한다")."""
    for ln in b.notice.lines:
        if ln.doc_type == '공고문' or not _placed(ln):
            continue
        t = _norm(ln.text)
        if not (_LABEL.search(t) or _PERFORMER.search(t)) or not _REC_REQ.search(t):
            continue
        if _NOT.search(t) or judge.X2_STAFF_CTX.search(t) or not _context_ok(b, ln):
            continue
        return ln
    return None


def v8(b):
    if not switches.X3_V8_PLACED or b.meta.local_private:
        return None
    from . import u1_records as U
    rec = U._v8_records(b) or U._first(U._on(U.RECORDS), b)
    new_rec = new_reg = None
    if not rec:
        new_rec = placed_record(b)
        if new_rec is None:
            return None
    reg = U._v8_region(b)
    if reg is None:
        reg = U._first(U._on(U.REGIONS), b)
    if reg is None:
        new_reg = placed_region(b) or doc_region(b)
        if new_reg is None:
            return None
    if new_rec is None and new_reg is None:
        return None
    return new_reg if new_reg is not None else new_rec


# ---------------------------------------------------------------- X3_V4_PLACED (v4)
# Buyer kinds a record note may be limited to, beyond U1's public-sector kinds: schools, kindergartens, universities, hospitals.
_KIND = (r'(?:국\s*가\s*기\s*관|국\s*가|정\s*부\s*(?:기\s*관|부\s*처)?|중\s*앙\s*행\s*정\s*기\s*관|행\s*정\s*기\s*관|지\s*방\s*자\s*치\s*단\s*체|지\s*자\s*체'
         r'|공\s*공\s*기\s*관|관\s*공\s*서|공\s*기\s*업|준\s*정\s*부\s*기\s*관|국\s*공\s*립\s*(?:학\s*교|기\s*관)?|공\s*공\s*부\s*문|교\s*육\s*청|관\s*급|공\s*공'
         r'|(?:초\s*[·ㆍ]?\s*중\s*[·ㆍ]?\s*고\s*등?\s*|초\s*등\s*|중\s*|고\s*등\s*|특\s*수\s*)?학\s*교|유\s*치\s*원|어\s*린\s*이\s*집|대\s*학\s*(?:교|병\s*원)?'
         r'|(?:상\s*급\s*)?종\s*합\s*병\s*원|의\s*료\s*기\s*관|군\s*부\s*대|\[(?:수\s*요\s*)?기\s*관\([^)\]]*\)[^\]]*\]|(?:그\s*)?(?:소\s*속|산\s*하)\s*기\s*관)')
_JOIN = r'(?:\s*(?:,|·|ㆍ|및|또\s*는|이\s*나|과|와)\s*(?:그\s*)?' + _KIND + r')*'
_REL = (r'(?:\s*(?:에\s*서|이|가|과|와|으\s*로\s*부\s*터|에)?\s*(?:직\s*접\s*)?(?:발\s*주|납\s*품|계\s*약|시\s*행|발\s*급|발\s*행|공\s*급)'
        r'\s*(?:한|된|하\s*여|받\s*은)?)')
_OBJ = r'\s*(?:[가-힣]{0,8}\s*)?(?:실\s*적|분|건|사\s*업|용\s*역|것)'
_ONLY = (r'\s*(?:만\s*(?:을\s*)?(?:인\s*정|해\s*당|유\s*효|가\s*능)|에\s*(?:한\s*(?:함|하여|하며|한다|정|합\s*니\s*다)|대\s*해\s*서\s*만)'
         r'|(?:으\s*로|로)\s*한\s*정)')
_ONLY_BUYER = re.compile(_KIND + _JOIN + _REL + _OBJ + _ONLY)
# "…공공기관 발주 실적증명서를 제출한 업체에 한하여 입찰에 참가할 수 있습니다"
_ONLY_BUYER2 = re.compile(_KIND + _JOIN + _REL + r'\s*(?:[가-힣]{0,8}\s*)?실\s*적\s*(?:증\s*명\s*서)?\s*(?:를|을)?\s*(?:제\s*출|보\s*유)\s*한\s*'
                          r'(?:업\s*체|자)\s*(?:에\s*한\s*(?:하\s*여|함|정)|만)')
# Private records refused ("민간 실적은 … 보지 않습니다", "민간 발주분은 포함하지 않음").
_NO_PRIVATE = re.compile(r'(?:민\s*간|민\s*자|사\s*기\s*업|일\s*반\s*기\s*업)\s*(?:발\s*주\s*|부\s*문\s*|기\s*업\s*|계\s*약\s*|납\s*품\s*)?(?:실\s*적|분|용\s*역|사\s*업)\s*(?:은|는|의\s*경\s*우)?\s*'
                         r'[^.。]{0,20}?(?:제\s*외|인\s*정\s*(?:하\s*지\s*(?:않|아\s*니)|불\s*가|되\s*지\s*않)|불\s*인\s*정|해\s*당\s*(?:되\s*지|하\s*지)\s*않'
                         r'|보\s*지\s*않|포\s*함\s*하\s*지\s*않|산\s*입\s*하\s*지\s*않)')
_PRIVATE = re.compile(r'민\s*간|민\s*자|사\s*기\s*업|일\s*반\s*기\s*업|개\s*인|사\s*립')
_EVAL = re.compile(r'평\s*가|배\s*점|점\s*수|가\s*점|감\s*점|기\s*재|작\s*성|서\s*식|양\s*식|적\s*격\s*심\s*사|정\s*량|정\s*성|심\s*사')
_PROOF = re.compile(r'증\s*명\s*서\s*(?:로|으\s*로)\s*만|세\s*금\s*계\s*산\s*서|계\s*약\s*서\s*사\s*본|거\s*래\s*명\s*세')


def _buyer_note(t):
    if not re.search(r'실\s*적', t) or _EVAL.search(t) or _PROOF.search(t):
        return False
    if _NO_PRIVATE.search(t):
        return True
    return bool(_ONLY_BUYER.search(t) or _ONLY_BUYER2.search(t)) and not _PRIVATE.search(t)


def placed_buyer(b):
    """A record counted only for a buyer kind (집행기준 제5조④3): a 공고문 note or an attachment line with a bidder subject or
    participation label that limits the counted records to public, school or hospital buyers or refuses private ones; or an
    attachment or 공고문 bidding/notes line whose bidder or performing firm must hold a record for a named buyer kind."""
    for ln in b.notice.lines:
        if ln.sec == 'EVAL' or not ln.text.strip():
            continue
        t = _norm(ln.text)
        subj = _SUBJ.search(t) or _LABEL.search(t) or ln.doc_type != '공고문' and _REC_LABEL.search(t)
        if ln.doc_type != '공고문' and (ln.sec in ('DOCS',) or not subj):
            continue
        if _buyer_note(t) and _context_ok(b, ln):
            return ln
        req = _REC_REQ.search(t) or (_LABEL.search(t) or _REC_LABEL.search(t)) and re.search(r'실\s*적', t)
        if not (subj and _placed(ln)) or not req or _NOT.search(t) or judge.X2_STAFF_CTX.search(t):
            continue
        c = judge.LAW_REF.sub(' ', judge.clause_text(b.notice, ln))
        if judge.buyer_limit(c) != 'specific':
            continue
        if switches.V4_PRIVATE_ENUM and not judge.BENEFICIARY.search(c) and judge.x1_private_in_enum(c):
            continue
        if _context_ok(b, ln):
            return ln
    return None


# A required record with the anonymised institution itself ("[수요기관(공기업)]에 동종 설비를 납품한 실적"): the record is limited
# to that one institution (집행기준 제5조④3 특정기관).
_TOKEN_BUYER = re.compile(r'\[(?:수\s*요\s*)?기\s*관\([^)\]]*\)[^\]]*\]\s*(?:에\s*게|에\s*서|으\s*로\s*부\s*터|로\s*부\s*터|에|과|와)\s*'
                          r'(?:직\s*접\s*)?[^.。,]{0,20}?(?:납\s*품|공\s*급|설\s*치|구\s*축|운\s*영|수\s*행|발\s*주|계\s*약|위\s*탁)'
                          r'\s*(?:한|하\s*였|받\s*은|된)?[^.。]{0,10}?(?:실\s*적|이\s*력|경\s*험)')


def token_buyer(b):
    recs = judge.perf_lines(b)
    known = {ln.i for ln in recs}
    recs = recs + [ln for ln in judge.x2_unread_records(b) if ln.i not in known]
    for ln in recs:
        c = judge.LAW_REF.sub(' ', judge.clause_text(b.notice, ln))
        if judge.PRIVATE_SECTOR.search(c) or switches.V4_PRIVATE_ENUM and judge.x1_private_in_enum(c):
            continue
        if _TOKEN_BUYER.search(c):
            return ln
    return None


def v4(b):
    if not switches.X3_V4_PLACED:
        return None
    return placed_buyer(b) or token_buyer(b)


# ---------------------------------------------------------------- X3_V1_PLACED (v1)
# The act of bidding as the subject ("입찰참가는 …만 가능", "※ 입찰참가는 … 연구기관에 한합니다") or a participation-target label
# ("참가대상 :", "| 참가대상 |", "참여 가능 기관 :").
_ACT = re.compile(r'(?:입\s*찰\s*)?참\s*(?:가|여)\s*(?:는|은)\s|(?:입\s*찰\s*)?참\s*(?:가|여)\s*(?:대\s*상|가\s*능\s*기\s*관)\s*(?:은|는|[:：|])')
_INST_ONLY = re.compile(r'만\s*(?:이\s*)?(?:(?:입\s*찰|견\s*적|제\s*안)(?:에|서)?\s*)?(?:참\s*여|참\s*가|응\s*찰|가\s*능|신\s*청|제\s*출)'
                        r'|에\s*한\s*(?:함|하여|한다|정|합\s*니\s*다)|(?:으\s*로|로)\s*한\s*정')
_SUBMIT_ONLY = re.compile(judge.X1_INST_ITEM + r'\s*(?:에\s*한\s*하\s*여|만)\s*[^.。]{0,12}?(?:입\s*찰\s*서|견\s*적\s*서|제\s*안\s*서|입\s*찰)\s*(?:를|을|에)?\s*[^.。]{0,6}?'
                          r'(?:제\s*출|참\s*가|참\s*여)\s*할\s*수\s*있')
_DOC_ONLY = re.compile(r'[\(（][^)）]{2,40}?만\s*(?:참\s*가|참\s*여|입\s*찰)\s*(?:가\s*능|할\s*수)[^)）]*[\)）]')
_STAFF = re.compile(r'(?:종\s*업\s*원|임\s*직\s*원|정\s*규\s*직|직\s*원|근\s*로\s*자|인\s*력|기\s*술\s*자)[^.。]{0,15}?\d{2,}\s*(?:명|인)\s*이\s*상'
                    r'[^.。]{0,15}?(?:고\s*용|보\s*유|확\s*보|갖\s*추)')
# A bidding-target label names the eligible bidders ("입찰참가 대상 : 정부출연연구기관 및 국공립대학").
_TARGET = re.compile(r'입\s*찰\s*참\s*(?:가|여)\s*(?:대\s*상|가\s*능\s*기\s*관)\s*(?:은|는|[:：|])|참\s*(?:가|여)\s*가\s*능\s*기\s*관\s*(?:은|는|[:：|])')
_UNWRAP = re.compile(r'\[기관\(([^)\]]*)\)[^\]]*\]')
_KIND_ONLY = re.compile(judge.X1_INST_ITEM + r'\s*(?:[\(（][^)）]{0,20}[\)）])?\s*(?:만\s*(?:입\s*찰\s*에\s*)?(?:참\s*가|참\s*여|응\s*찰)|에\s*한\s*하\s*여\s*(?:입\s*찰|참\s*가|참\s*여))')
# General businesses barred ("일반 영리업체 참가 불가"): the eligible institution kinds are the only bidders.
_FIRMS_BARRED = re.compile(r'(?:일\s*반|(?<!비)(?<!비\s)영\s*리|민\s*간)\s*(?:(?<!비)영\s*리\s*)?(?:업\s*체|기\s*업|법\s*인|사\s*업\s*자|회\s*사)[^.。]{0,20}?'
                           r'(?:참\s*가|참\s*여|입\s*찰|응\s*찰)\s*(?:불\s*가|제\s*한|할\s*수\s*없|하\s*실\s*수\s*없)')
# An attachment heading that names the performing institution's qualification ("3. 수행기관 자격").
_PERF_HEAD = re.compile(_MARK + r'[\[<【□■]?\s*(?:수\s*행\s*기\s*관|참\s*여\s*기\s*관|제\s*안\s*(?:사|기\s*관))\s*(?:의\s*)?(?:자\s*격|요\s*건)')
# An attachment qualification item that is only institution kinds and the limit ("- 대학 부설 연구소 또는 정부출연연구기관에 한함").
_KINDS_ONLY = re.compile(_MARK + r'(?:[^.。,()]{0,12}?' + judge.X1_INST_ITEM + r')(?:\s*(?:,|·|ㆍ|및|또\s*는|이\s*나)\s*[^.。,()]{0,12}?'
                         + judge.X1_INST_ITEM + r')*\s*(?:에\s*한\s*(?:함|한다|합\s*니\s*다)|만\s*(?:참\s*여|참\s*가)\s*(?:가\s*능|할\s*수\s*있\s*음)'
                         r'|(?:으\s*로|로)\s*한\s*정(?:함|한다)?)\s*[.。]?\s*$')
# A nationwide network the bidder must set up ("…각 시·도에 1개 이상의 지사를 두어야").
_NET_OBLIGE = re.compile(r'(?:전\s*국|각\s*(?:시\s*[·ㆍ]?\s*도|광\s*역|지\s*역|시\s*[·ㆍ]?\s*군|권\s*역)|모\s*든\s*(?:광\s*역|시\s*[·ㆍ]?\s*도|지\s*역|시\s*[·ㆍ]?\s*군)'
                         r'|\d+\s*개\s*(?:이\s*상\s*의?\s*)?(?:시\s*[·ㆍ]?\s*도|광\s*역|권\s*역|지\s*역))[^.。]{0,25}?'
                         r'(?:지\s*사|지\s*점|센\s*터|영\s*업\s*소|사\s*업\s*장|서\s*비\s*스\s*망|A\s*/\s*S|AS|정\s*비\s*소|대\s*리\s*점|출\s*장\s*소|사\s*무\s*소|거\s*점|창\s*고|공\s*장|직\s*영\s*점|매\s*장)'
                         r'[^.。]{0,15}?(?:두\s*어\s*야|갖\s*추\s*어\s*야|설\s*치\s*하\s*여\s*야|보\s*유\s*하\s*여\s*야|운\s*영\s*하\s*여\s*야|확\s*보\s*하\s*여\s*야)')


def _inst_words(t):
    bare = judge.LAW_NAME.sub(' ', judge.ORDERER_TOKEN.sub(' ', t))
    return bool(judge.INST_WORD.search(bare) or judge.INST_WORD_A2.search(bare) or judge.INST_WORD_T3.search(bare)
                or re.search(r'\[기관\((?:대학|공공기관|협회|의료기관|교육기관|공기업|기타공공기관|연합회|중앙회)\)', t))


def _inst_limit(b, ln, t):
    if judge.RESERVED_PROFESSION.search(t) or judge.BUYER_SUBJECT.search(t) or judge.disclaimed(b, ln):
        return False
    if _FIRMS_BARRED.search(t) and _inst_words(t):
        return True                     # the firms a commercial alternative would admit are barred
    c = judge.LAW_NAME.sub(' ', judge.clause_text(b.notice, ln))
    if judge.x1_commercial_alternative(c) or judge.x1_lead_options_commercial(b, ln):
        return False
    if _TARGET.search(t) and _inst_words(t) and judge.institution_limit(_UNWRAP.sub(lambda m: ' ' + m.group(1) + ' ', t)):
        return True
    return bool(_INST_ONLY.search(t)) and _inst_words(t) and judge.institution_limit(t)


def placed_inst(b):
    for ln in b.notice.lines:
        if not ln.text.strip() or ln.sec in ('EVAL',):
            continue
        t = _norm(ln.text)
        if judge.X3V1_NOT.search(t):
            continue
        if ln.doc_type == '공고문':
            if ln.sec not in ('BID', 'NOTE', 'OTHER', 'DOCS', 'QUAL', 'TOP', 'OVERVIEW'):
                continue
            # the qualification section is read by the inst family; here only a target label or the bidding act as subject
            docs_only = ln.sec == 'DOCS' and bool(_DOC_ONLY.search(t))
            submit = ln.sec in ('BID', 'NOTE', 'OTHER') and bool(_SUBMIT_ONLY.search(t)
                                                                 or judge.X3V1_SUBJECT.search(t) and _KIND_ONLY.search(t))
            if not (_ACT.search(t) or _FIRMS_BARRED.search(t) or submit or docs_only) \
                    or ln.sec == 'DOCS' and not (t.startswith('|') or docs_only):
                continue
            if _inst_limit(b, ln, t) and _context_ok(b, ln):
                return ln
            continue
        if ln.sec == 'DOCS':
            continue
        head = any(_PERF_HEAD.search(_norm(x.text)) and len(_norm(x.text)) <= 30
                   for x in b.notice.window(ln.i, 3, 0)[:-1] if x.doc == ln.doc) \
            or ln.sec == 'QUAL' and judge.x2_under_qual_heading(b, ln) and bool(_KINDS_ONLY.match(t))
        if not (_ACT.search(t) or _PERFORMER.search(t) or _LABEL.search(t) or head):
            continue
        if _inst_limit(b, ln, t) and _context_ok(b, ln):
            return ln
    return None


def placed_network(b):
    for ln in b.notice.lines:
        if not _placed(ln) and not (ln.doc_type == '공고문' and ln.sec == 'QUAL'):
            continue
        t = _norm(ln.text)
        staff = _placed(ln) and _STAFF.search(t) and not re.search(r'실\s*적', t) and not judge.SIZE_DEFINITION.search(t)
        if not (_NET_OBLIGE.search(t) or staff) or judge.X3V1_NOT.search(t) or judge.JV_PARTNER3.search(t):
            continue
        if not (judge.X3V1_SUBJECT.search(t) or _SUBJ.search(t) or ln.sec == 'QUAL'):
            continue
        if _context_ok(b, ln):
            return ln
    return None


_ITEM_START = re.compile(r'^\s*(?:\d{1,2}\s*[.)]|\(\s*\d{1,2}\s*\)|[가-하]\s*[.)]|[①-⑳]|[○●◎▶►▷ㅇ◦•❍□■※*\-|])')


def split_limit(b):
    """A qualification line read as an institution-kind limit whose kinds are anonymised tokens and whose limiting phrase
    ("…에 / 한하여 입찰에 참가할 수 있음") continues on the next physical line: the token rule reads the joined clause."""
    for ln in b.cands.get('inst', []):
        if not judge.qual_section(ln, b.notice):
            continue
        r = b.read('inst', ln)
        if r.get('역할') != '참가자격' or r.get('요건') != '기관 유형 한정' or judge.institution_limit(ln.text):
            continue
        nxt = next((x for x in b.notice.lines[ln.i + 1:ln.i + 3] if x.doc == ln.doc and x.text.strip()), None)
        if nxt is None or _ITEM_START.match(nxt.text) or judge.SENT_END.search(ln.text):
            continue
        t = _norm(ln.text + ' ' + nxt.text)
        if not re.search(r'\[기관\(', ln.text) or not _INST_ONLY.search(t) or _INST_ONLY.search(_norm(ln.text)):
            continue
        if judge.institution_limit(t) and _inst_limit(b, ln, t) and _context_ok(b, ln):
            return ln
    return None


def v1(b):
    if not switches.X3_V1_PLACED or b.meta.method == '지명경쟁' or b.meta.local_private:
        return None
    return placed_inst(b) or placed_network(b) or split_limit(b)
