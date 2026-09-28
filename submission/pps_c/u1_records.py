"""Red team U1 (round 4): v2, v4 and v8 requirements stated where the judge rules do not look.

Each section below registers one detector under its own switch (switches.py, all default False). judge.v2, judge.v4 and
judge.v8 consult the registered detectors only after their own rules found nothing, so with every switch off every judgment
is unchanged.
"""
from __future__ import annotations

import re

from . import judge, regions, switches

RECORDS = []    # (switch, fn(b) -> line or None): a bidder record requirement (v2, and v8's record side)
REGIONS = []    # (switch, fn(b) -> line or None): a bidder-location requirement (v8's location side)
LIMITS = []     # (switch, fn(b) -> line or None): a required record limited to a buyer kind or to a named record (v4)


def _on(sources):
    return [fn for name, fn in sources if getattr(switches, name, False)]


def _first(fns, b):
    for fn in fns:
        hit = fn(b)
        if hit is not None:
            return hit
    return None


def v2_more(b):
    fns = _on(RECORDS)
    if not fns:
        return None
    P = b.meta.P                  # the amount conditions of judge._rtd_v2_base
    if switches.AUDIT_FIXES3 and P is not None and P < 1e6 and b.meta.B and b.meta.B >= 1e6:
        P = b.meta.B / 1.1
    if P is None or P >= judge.NOTICE_AMOUNT or b.meta.local_private:
        return None
    return _first(fns, b)


def v4_more(b):
    fns = _on(LIMITS)
    return _first(fns, b) if fns else None


def _v8_records(b):
    """The record side of judge._v8_base."""
    perf = judge.perf_lines(b)
    if switches.X2_RECORD_NOISE:
        perf = [ln for ln in perf if not judge.x2_not_bidder_record(b, ln)]
    if switches.X2_V8_ADDS:
        known = {ln.i for ln in perf}
        perf += [ln for ln in judge.x2_unread_records(b) if ln.i not in known]
    if switches.X3_RECORD_FORMS and not perf:
        perf = judge.x3_consequence_records(b)
    return perf


def _v8_region(b):
    """The location side of judge._v8_base (restriction lines, the registered restriction, CPU and token readers)."""
    lines, _, _ = judge.region_restriction(b)
    if lines:
        return lines[0]
    if b.meta.region_flag == 'Y':
        return True
    if switches.X2_V8_ADDS:
        hit = judge.x2_cpu_region(b)
        if hit is not None:
            return hit
    if switches.V8_TOKEN_REGION:
        return judge.v8_token_region(b)
    return None


def v8_more(b):
    """v8 when a registered detector supplies the side (record or location) that judge._v8_base did not find."""
    recs, regs = _on(RECORDS), _on(REGIONS)
    if not (recs or regs) or b.meta.local_private:
        return None
    rec = reg = None
    if not _v8_records(b):
        rec = _first(recs, b)
        if rec is None:
            return None
    if _v8_region(b) is None:
        reg = _first(regs, b)
        if reg is None:
            return None
    return reg if reg is not None else rec


# ---------------------------------------------------------------- U1_ATTACH_RECORD
_AR_SUBJ = re.compile(r'^\W*(?:(?:\d{1,2}|[가-하])\s*[.)]\s*)?(?:입\s*찰\s*참\s*가\s*(?:자|업\s*체)|입\s*찰\s*(?:에\s*)?참\s*가\s*(?:하\s*려\s*는|할\s*수\s*있\s*는)\s*(?:자|업\s*체)'
                      r'|참\s*가\s*업\s*체|참\s*여\s*(?:가\s*능\s*)?업\s*체|제\s*안\s*(?:사|업\s*체|자)|입\s*찰\s*자|본\s*사\s*업\s*에\s*참\s*여\s*하\s*는\s*(?:업\s*체|자)'
                      r'|사\s*업\s*참\s*여\s*업\s*체|입\s*찰\s*참\s*가\s*자\s*격|참\s*가\s*자\s*격|참\s*가\s*업\s*체\s*자\s*격)')
_AR_REQ = re.compile(r'실\s*적\s*(?:을|이)?\s*(?:보\s*유\s*하\s*여\s*야|보\s*유\s*해\s*야|있\s*어\s*야|갖\s*추\s*어\s*야)'
                     r'|(?:실\s*적|경\s*험|이\s*력)\s*(?:이|을)?\s*(?:있\s*는|보\s*유\s*한)\s*(?:업\s*체|자|기\s*관|법\s*인)\s*(?:이\s*어\s*야|로\s*(?:한\s*다|함|한\s*정|제\s*한)|에\s*한|만)'
                     r'|(?:실\s*적|경\s*험|이\s*력)\s*보\s*유\s*(?:업\s*체|자|기\s*관|법\s*인)')
_AR_NOT = re.compile(r'평\s*가|배\s*점|점\s*수|가\s*점|감\s*점|기\s*재|작\s*성|서\s*식|양\s*식|제\s*출|증\s*빙|첨\s*부|하\s*도\s*급|협\s*력|공\s*동|분\s*담'
                     r'|우\s*대|인\s*력|책\s*임\s*자|대\s*표\s*자|직\s*원')


def attach_records(b):
    for ln in b.notice.lines:
        if ln.doc_type == '공고문' or ln.sec in ('EVAL', 'DOCS'):
            continue
        t = ' '.join(ln.text.split())
        if not t or not _AR_SUBJ.search(t) or not _AR_REQ.search(t) or _AR_NOT.search(t) or judge.X2_STAFF_CTX.search(t):
            continue
        if judge.evaluation_context(b, ln) or judge.in_form_annex(b, ln):
            continue
        return ln
    return None


RECORDS.append(('U1_ATTACH_RECORD', attach_records))


# ---------------------------------------------------------------- U1_NOTE_ONLY
_NT_ONLY = re.compile(r'(?:실\s*적|경\s*험|이\s*력)\s*(?:을|이)?\s*(?:보\s*유|있\s*는|보\s*유\s*한|가\s*진)\s*(?:업\s*체|자|기\s*관|법\s*인)\s*'
                      r'(?:만|에\s*(?:한\s*(?:함|하여|한다|정)|한\s*정))')
_NT_DOC = re.compile(r'증\s*명\s*서|현\s*황|\d\s*부(?![가-힣])|사\s*본|서\s*류')


def note_only(b):
    for ln in b.notice.lines:
        if ln.doc_type != '공고문' or ln.sec in ('EVAL', 'DOCS') or not ln.text.strip():
            continue
        t = ' '.join(ln.text.split())
        if not _NT_ONLY.search(t) or _NT_DOC.search(t) or judge.X3_NOT_FORM.search(t) or judge.X3_WAIVER.search(t) \
                or judge.X2_STAFF_CTX.search(t) or judge.METHOD_SUMMARY.search(t):
            continue
        if judge.evaluation_context(b, ln) or judge.in_form_annex(b, ln):
            continue
        return ln
    return None


RECORDS.append(('U1_NOTE_ONLY', note_only))


# ---------------------------------------------------------------- U1_RECORD_FORMS2 (read by judge.x3_record_form)
_F2_FIRM = r'(?:업\s*체|자|기\s*관|법\s*인|사\s*업\s*자)(?!\s*[명격료재산본체동원])'
U1_FORMS2 = re.compile(
    r'수\s*주\s*(?:한\s*)?(?:후|하\s*여|하\s*고)\s*(?:완\s*료|이\s*행|수\s*행|준\s*공|납\s*품)\s*(?:한|하였던|했던)\s*' + _F2_FIRM
    + r'|(?:수\s*행|납\s*품|공\s*급|이\s*행|준\s*공|설\s*치|시\s*공|운\s*영|대\s*행|구\s*축)\s*완\s*료\s*' + _F2_FIRM
    + r'|(?:납\s*품|공\s*급|수\s*행|운\s*영|대\s*행|시\s*공|설\s*치|제\s*작|구\s*축)\s*(?:경\s*력|경\s*험|이\s*력)\s*(?:보\s*유\s*)?' + _F2_FIRM
    + r'|(?:여\s*부|유\s*무)\s*[:：]?\s*(?:있\s*음|유)\s*[\(（]?\s*필\s*수'
    + r'|(?:을|를)\s*\d+\s*(?:회|건)\s*이\s*상\s*(?:완\s*수|수\s*행|납\s*품|이\s*행|완\s*료|공\s*급)\s*한\s*' + _F2_FIRM
    + r'|(?:공\s*급|납\s*품|수\s*행|운\s*영|대\s*행|제\s*작|설\s*치|시\s*공|구\s*축|개\s*발|이\s*행|준\s*공|개\s*최)\s*(?:이\s*력|경\s*력)\s*(?:을\s*)?보\s*유'
      r'\s*(?:필\s*수)?\s*[.。]?\s*$')


# ---------------------------------------------------------------- U1_RECORD_LIST
_L_MARK = re.compile(r'^\W{0,2}?(?:[-·•◦○●ㆍ▪■□◇◆▶►▷*]|\(?\d{1,2}[.)]|[가-하][.)]|[①-⑳])\s*')
_L_ACT = (r'(?:공\s*급|납\s*품|수\s*행|운\s*영|대\s*행|제\s*작|설\s*치|시\s*공|구\s*축|개\s*발|유\s*지\s*보\s*수|유\s*지\s*관\s*리|이\s*행|준\s*공'
          r'|개\s*최|판\s*매|임\s*대|교\s*육|정\s*비|보\s*수)')
_L_TAIL = re.compile(_L_ACT + r'\s*(?:경\s*험|실\s*적|이\s*력|경\s*력)\s*(?:보\s*유)?\s*(?:\d+\s*(?:건|회)\s*이\s*상)?\s*(?:보\s*유)?'
                     r'\s*(?:\(?\s*필\s*수\s*\)?)?\s*[.。]?\s*$')
# An item with its own predicate, a proof or evaluation word, or a label is read by the other rules.
_L_PRED = re.compile(r'있\s*는|있\s*어\s*야|한\s*(?:업\s*체|자)|하\s*였|보\s*유\s*한|갖\s*춘|없\s*는|제\s*출|증\s*명|증\s*빙|평\s*가|배\s*점|점\s*수'
                     r'|가\s*점|우\s*대|기\s*재|작\s*성|확\s*인|여\s*부|유\s*무|:|：')
_L_LEAD = re.compile(r'(?:아\s*래|다\s*음|하\s*기|각\s*호)[^.。]{0,25}?(?:요\s*건|조\s*건|자\s*격|사\s*항)[^.。]{0,25}?'
                     r'(?:갖\s*춘|충\s*족|갖\s*추\s*어\s*야|구\s*비|해\s*당\s*하\s*는|모\s*두)')
_L_LEAD_NOT = re.compile(r'평\s*가|배\s*점|점\s*수|가\s*점|제\s*출|서\s*류|인\s*력|책\s*임|기\s*술\s*자|투\s*입|우\s*대|결\s*격|제\s*외'
                         r'|제\s*한\s*사\s*유|감\s*점')


def _list_item(t):
    m = _L_MARK.match(t)
    if not m:
        return False
    body = t[m.end():].strip()
    if len(body) > 45 or not _L_TAIL.search(body) or _L_PRED.search(body):
        return False
    return not (judge.X2_STAFF_CTX.search(body) or judge.NOT_RECORD.search(body))


def list_records(b):
    for ln in b.notice.lines:
        if ln.doc_type != '공고문' or not judge.qual_section(ln, b.notice):
            continue
        t = ' '.join(ln.text.split())
        if not _list_item(t) or judge.evaluation_context(b, ln) or judge.in_form_annex(b, ln):
            continue
        for x in reversed([x for x in b.notice.window(ln.i, 8, 0)[:-1] if x.doc == ln.doc and x.text.strip()]):
            tx = ' '.join(x.text.split())
            if _L_MARK.match(tx) and not _L_LEAD.search(tx) and len(tx) <= 60:
                continue                      # a sibling item of the same list
            if _L_LEAD.search(tx) and not _L_LEAD_NOT.search(tx):
                return ln
            break
    return None


RECORDS.append(('U1_RECORD_LIST', list_records))


# ---------------------------------------------------------------- U1_V4_BUYER_KINDS
_BK_MIN = re.compile(r'[가-힣]{1,10}(?:부|처|청|위원회)\s*(?:및|또는|과|와|,|·|ㆍ)\s*(?:그\s*)?(?:소\s*속|산\s*하)\s*(?:기\s*관|청|공\s*공\s*기\s*관|단\s*체)'
                     r'(?:\s*\([^()]{0,12}\))?\s*(?:의|에\s*서|이|가|에|과|와|으\s*로\s*부\s*터)?\s*[^.。,;]{0,25}?(?:실\s*적|발\s*주|납\s*품|수\s*행|계\s*약|이\s*행)')
_BK_PUB = re.compile(r'공\s*공\s*발\s*주\s*(?:[가-힣]{0,6}\s*)?(?:실\s*적|사\s*업\s*실\s*적|용\s*역\s*실\s*적)')
# Public offices, constitutional bodies, public broadcasters and public facilities as the record's buyer or place of work.
_BK_MORE = re.compile(r'(?<![(\[])(?:행\s*정\s*복\s*지\s*센\s*터|주\s*민\s*(?:자\s*치\s*)?센\s*터|국\s*회|법\s*원|헌\s*법\s*재\s*판\s*소|선\s*거\s*관\s*리\s*위\s*원\s*회'
                      r'|공\s*영\s*방\s*송\s*사?|우\s*체\s*국|경\s*찰\s*서|소\s*방\s*서|공\s*공\s*(?:체\s*육|문\s*화|복\s*지|교\s*육|의\s*료)?\s*시\s*설'
                      r'|(?:시|도|구|군)\s*립\s*[가-힣]{1,6})\s*(?:에\s*서|에|이|가|과|와|의)?\s*[^.。,;]{0,25}?'
                      r'(?:납\s*품|발\s*주|계\s*약|공\s*급|위\s*탁\s*운\s*영|운\s*영|수\s*행|설\s*치)\s*(?:한|하였|실\s*적|이\s*력|경\s*험)')


def buyer_kinds(b):
    recs = judge.perf_lines(b)
    known = {ln.i for ln in recs}
    recs = recs + [ln for ln in judge.x2_unread_records(b) if ln.i not in known]
    for ln in recs:
        c = judge.LAW_REF.sub(' ', judge.clause_text(b.notice, ln))
        if (judge.ORDERER_OPEN2 if switches.AUDIT_FIXES2 else judge.ORDERER_OPEN).search(c) or judge.PRIVATE_SECTOR.search(c):
            continue
        if switches.V4_PRIVATE_ENUM and judge.x1_private_in_enum(c):
            continue
        if _BK_MIN.search(c) or _BK_PUB.search(c) or _BK_MORE.search(c):
            return ln
    return None


LIMITS.append(('U1_V4_BUYER_KINDS', buyer_kinds))


# ---------------------------------------------------------------- U1_V4_BUYER_NOTE
_BN_KIND = (r'(?:국\s*가\s*기\s*관|국\s*가|정\s*부\s*(?:기\s*관|부\s*처)?|중\s*앙\s*행\s*정\s*기\s*관|행\s*정\s*기\s*관|지\s*방\s*자\s*치\s*단\s*체|지\s*자\s*체'
            r'|공\s*공\s*기\s*관|관\s*공\s*서|공\s*기\s*업|준\s*정\s*부\s*기\s*관|국\s*공\s*립\s*(?:학\s*교|기\s*관)?|공\s*공\s*부\s*문|교\s*육\s*청|관\s*급|공\s*공)')
_BN_JOIN = r'(?:\s*(?:,|·|ㆍ|및|또\s*는|이\s*나|과|와)\s*(?:그\s*)?' + _BN_KIND + r')*'
_BN_REL = (r'(?:\s*(?:에\s*서|이|가|과|와|으\s*로\s*부\s*터|에)?\s*(?:발\s*주|납\s*품|계\s*약|시\s*행|발\s*급|발\s*행)'
           r'\s*(?:한|된|하\s*여|받\s*은)?)')
_BN_OBJ = r'\s*(?:[가-힣]{0,8}\s*)?(?:실\s*적|분|건|사\s*업|용\s*역|것|실\s*적\s*증\s*명\s*서)'
_BN_ONLY = (r'\s*(?:만\s*(?:을\s*)?(?:인\s*정|해\s*당|유\s*효|제\s*출|가\s*능)|에\s*(?:한\s*(?:함|하여|하며|한다|정)|대\s*해\s*서\s*만)'
            r'|(?:으\s*로|로)\s*한\s*정)')
_BN_ONLY_BUYER = re.compile(_BN_KIND + _BN_JOIN + _BN_REL + _BN_OBJ + _BN_ONLY)
_BN_LABEL = re.compile(r'(?:발\s*주\s*처|발\s*주\s*기\s*관|인\s*정\s*범\s*위|실\s*적\s*범\s*위)\s*[:：]\s*' + _BN_KIND + _BN_JOIN
                       + r'[^.。]{0,20}?(?:에\s*한\s*(?:함|하여|정)|만|발\s*주)')
_BN_NO_PRIVATE = re.compile(r'(?:민\s*간|민\s*자|사\s*기\s*업|일\s*반\s*기\s*업)\s*(?:발\s*주\s*|부\s*문\s*|기\s*업\s*)?(?:실\s*적|분|용\s*역|사\s*업)\s*(?:은|는|의\s*경\s*우)?\s*'
                            r'(?:제\s*외|인\s*정\s*(?:하\s*지\s*(?:않|아\s*니)|불\s*가|되\s*지\s*않)|불\s*인\s*정|해\s*당\s*(?:되\s*지|하\s*지)\s*않)')
_BN_PRIVATE = re.compile(r'민\s*간|민\s*자|사\s*기\s*업|일\s*반\s*기\s*업|개\s*인|사\s*립')
_BN_PROOF = re.compile(r'증\s*명\s*서\s*(?:로|으\s*로)\s*만|만\s*제\s*출|세\s*금\s*계\s*산\s*서|계\s*약\s*서\s*사\s*본|증\s*빙|첨\s*부|원\s*본|거\s*래\s*명\s*세')
_BN_EVAL = re.compile(r'평\s*가|배\s*점|점\s*수|가\s*점|감\s*점|기\s*재|작\s*성|서\s*식|양\s*식|적\s*격\s*심\s*사|정\s*량|정\s*성|심\s*사')
_BN_SUBJ = re.compile(r'^\W*(?:(?:\d{1,2}|[가-하])\s*[.)]\s*)?(?:입\s*찰\s*참\s*가\s*(?:자|업\s*체)|참\s*가\s*업\s*체|제\s*안\s*(?:사|업\s*체|자)|입\s*찰\s*자'
                      r'|참\s*가\s*자\s*격|실\s*적\s*(?:인\s*정\s*)?(?:범\s*위|기\s*준|요\s*건)\s*[:：])')


def _buyer_note(t):
    if not re.search(r'실\s*적', t) or _BN_EVAL.search(t) or _BN_PROOF.search(t):
        return False
    if (_BN_ONLY_BUYER.search(t) or _BN_LABEL.search(t)) and not _BN_PRIVATE.search(t):
        return True
    return bool(_BN_NO_PRIVATE.search(t))


def buyer_notes(b):
    for ln in b.notice.lines:
        if ln.sec == 'EVAL' or not ln.text.strip():
            continue
        t = ' '.join(ln.text.split())
        if ln.doc_type != '공고문' and (ln.sec in ('DOCS', 'NOTE') or not _BN_SUBJ.search(t)):
            continue
        if not _buyer_note(t) or judge.evaluation_context(b, ln) or judge.in_form_annex(b, ln):
            continue
        return ln
    return None


LIMITS.append(('U1_V4_BUYER_NOTE', buyer_notes))


# ---------------------------------------------------------------- U1_V4_NAMED_ONLY
_NO_SUBJ = (r'(?:(?:(?:수\s*행|납\s*품|이\s*행|사\s*업|용\s*역|공\s*사|참\s*가\s*자\s*격|위|해\s*당|인\s*정\s*되\s*는|인\s*정\s*하\s*는)\s*)?실\s*적\s*(?:은|는)\s*'
            r'|(?:실\s*적\s*(?:인\s*정\s*)?(?:범\s*위|기\s*준|요\s*건|인\s*정)|인\s*정\s*(?:실\s*적|범\s*위))\s*[:：]\s*)')
_NO_OBJ = r'(?P<x>[가-힣A-Za-z][가-힣A-Za-z0-9\s·ㆍ\-()/]{1,40}?)\s*'
_NO_ACT = (r'(?:운\s*영|구\s*축|설\s*치|유\s*지\s*보\s*수|유\s*지\s*관\s*리|조\s*성|보\s*수|감\s*리|납\s*품|제\s*작|대\s*행|공\s*사|사\s*업|용\s*역|정\s*비'
           r'|시\s*공|개\s*발|교\s*육|행\s*사)')
_NO_ONLY = r'(?:\s*실\s*적)?\s*(?:만\s*(?:을\s*)?(?:인\s*정|해\s*당|유\s*효)|에\s*한\s*(?:함|하여|하며|한다|정)|(?:으\s*로|로)\s*한\s*정)'
_NO_NAMED = re.compile(_NO_SUBJ + _NO_OBJ + r'(?:' + _NO_ACT + r'|명\s*칭\s*의\s*[가-힣\s]{0,8})\s*(?:실\s*적)?' + _NO_ONLY)
_NO_BIDDER = re.compile(r'(?P<x>[가-힣A-Za-z0-9][가-힣A-Za-z0-9\s·ㆍ\-()/]{1,50}?)\s*' + _NO_ACT
                        + r'\s*실\s*적\s*이\s*있\s*는\s*(?:업\s*체|자)\s*(?:에\s*)?만')
_NO_TIME = re.compile(r'^\s*(?:최\s*근|과\s*거|공\s*고\s*일[^0-9]{0,8})?\s*\d+\s*년\s*(?:이\s*내|간|동\s*안)?\s*(?:에\s*)?')
# Words that make the admitted record a kind, a period, an amount, a status or a buyer, not one named work.
_NO_GENERIC = re.compile(r'동\s*종|유\s*사|관\s*련|해\s*당|이\s*와|동\s*등|최\s*근|이\s*내|\d|(?:만|억|천)\s*원|이\s*상|완\s*료|준\s*공|이\s*행|계\s*약|도\s*급|단\s*독'
                         r'|공\s*동|지\s*분|부\s*가|기\s*준|공\s*고|단\s*일|건\s*당|금\s*액|규\s*모|실\s*적\s*증\s*명|증\s*명|발\s*주|국\s*가(?!\s*지\s*정)'
                         r'|지\s*방\s*(?:자\s*치|공\s*기\s*업)|공\s*공\s*(?:기\s*관|부\s*문|발\s*주)|기\s*관|민\s*간|입\s*찰|참\s*가|본\s*건|본\s*사\s*업|당\s*해'
                         r'|제\s*출|평\s*가|기\s*재|위\s*와|상\s*기|아\s*래|다\s*음|분\s*야|업\s*종|면\s*허|등\s*록')
_NO_NOT = re.compile(r'평\s*가|배\s*점|점\s*수|기\s*재|적\s*격|심\s*사|가\s*점|감\s*점|하\s*도\s*급|공\s*동|컨\s*소\s*시\s*엄|분\s*담')


def _named_only(t):
    for m in _NO_NAMED.finditer(t):
        if not _NO_GENERIC.search(m.group('x')):
            return True
    for m in _NO_BIDDER.finditer(t):
        x = _NO_TIME.sub('', m.group('x'))
        if len(x.strip()) >= 2 and not _NO_GENERIC.search(x):
            return True
    return False


def named_only(b):
    for ln in b.notice.lines:
        if ln.doc_type != '공고문' or ln.sec == 'EVAL' or not ln.text.strip():
            continue
        t = ' '.join(ln.text.split())
        if _NO_NOT.search(t) or not _named_only(t) or judge.evaluation_context(b, ln) or judge.in_form_annex(b, ln):
            continue
        return ln
    return None


LIMITS.append(('U1_V4_NAMED_ONLY', named_only))


# ---------------------------------------------------------------- U1_V8_ATTACH_REGION
_AG_SUBJ = re.compile(r'^\W*(?:(?:\d{1,2}|[가-하])\s*[.)]\s*)?(?:입\s*찰\s*참\s*가\s*(?:자|업\s*체)|입\s*찰\s*(?:에\s*)?참\s*가\s*(?:하\s*려\s*는|할\s*수\s*있\s*는)\s*(?:자|업\s*체)'
                      r'|참\s*가\s*업\s*체|참\s*여\s*(?:가\s*능\s*)?업\s*체|제\s*안\s*(?:사|업\s*체|자)|입\s*찰\s*자|본\s*사\s*업\s*에\s*참\s*여\s*하\s*는\s*(?:업\s*체|자)'
                      r'|사\s*업\s*참\s*여\s*업\s*체|입\s*찰\s*참\s*가\s*자\s*격|참\s*가\s*자\s*격|참\s*가\s*업\s*체\s*자\s*격|본\s*사\s*업\s*은)')
_AG_LABEL = re.compile(r'^\W*(?:(?:\d{1,2}|[가-하])\s*[.)]\s*)?(?:지\s*역|소\s*재\s*지|사\s*업\s*자\s*소\s*재\s*지|업\s*체\s*소\s*재\s*지|참\s*가\s*지\s*역'
                       r'|참\s*여\s*지\s*역)\s*(?:요\s*건|제\s*한|자\s*격)\s*[:：]')
_AG_ONLY = re.compile(r'(?:소\s*재\s*(?:하\s*는|한)|(?:본\s*점|본\s*사|주\s*된\s*영\s*업\s*소|사\s*업\s*장)\s*(?:을|를)\s*둔)\s*(?:업\s*체|자|법\s*인|사\s*업\s*자)\s*'
                      r'(?:에\s*한\s*하\s*여|만)\s*[^.。]{0,15}?(?:제\s*안|입\s*찰|참\s*가|참\s*여|응\s*찰|견\s*적)')
_AG_LOC = re.compile(r'본\s*사\s*(?:를|가|의|소\s*재)')
_AG_REQ = re.compile(r'(?:업\s*체|자|법\s*인|사\s*업\s*자)\s*(?:로|으\s*로)\s*(?:한\s*다|함|한\s*정|제\s*한)|에\s*한\s*(?:함|하여|한다|정)'
                     r'|만\s*(?:참\s*여|참\s*가|입\s*찰|제\s*안|응\s*찰)|(?:이\s*어\s*야|두\s*어\s*야|있\s*어\s*야|하\s*여\s*야|해\s*야)'
                     r'|업\s*체\s*[.。]?\s*$')
_AG_NOT = re.compile(r'평\s*가|배\s*점|점\s*수|가\s*점|감\s*점|기\s*재|작\s*성|서\s*식|양\s*식|제\s*출\s*서\s*류|증\s*빙|첨\s*부|하\s*도\s*급|협\s*력'
                     r'|공\s*동|분\s*담|우\s*대|A\s*/\s*S|서\s*비\s*스\s*센\s*터|사\s*후\s*관\s*리|인\s*력|책\s*임\s*자|대\s*표\s*자|직\s*원|숙\s*박'
                     r'|행\s*사\s*장|납\s*품\s*(?:장\s*소|지)')


def attach_region(b):
    for ln in b.notice.lines:
        if ln.doc_type == '공고문' or ln.sec in ('EVAL', 'DOCS'):
            continue
        t = ' '.join(ln.text.split())
        if not t or _AG_NOT.search(t) or judge.REGION_NONE.search(t) or judge.JV_PARTNER3.search(t) or judge.V8_RW_NOT.search(t):
            continue
        subj, label, only = _AG_SUBJ.search(t), _AG_LABEL.search(t), _AG_ONLY.search(t)
        if not (subj or label or only):
            continue
        if not (judge.X2_BIDDER_LOC.search(t) or judge.V8_RW.search(t) or _AG_LOC.search(t)):
            continue
        m = regions.mentions(t)
        if not (m['sido'] or m['basic']) or not (label or only or _AG_REQ.search(t)):
            continue
        if judge.evaluation_context(b, ln) or judge.in_form_annex(b, ln):
            continue
        return ln
    return None


REGIONS.append(('U1_V8_ATTACH_REGION', attach_region))


# ---------------------------------------------------------------- U1_V8_REGION_LABEL2
_RL_LABEL = re.compile(r'(?:^|[|│])\W*(?:(?:[가-하]|\d{1,2})\s*[.)]\s*|[①-⑳]\s*)?(?:(?:입\s*찰\s*)?참\s*가\s*자\s*격\s*(?:지\s*역|소\s*재\s*지)'
                       r'|(?:(?:참\s*가|참\s*여|업\s*체|사\s*업\s*자)\s*)?(?:지\s*역|소\s*재\s*지)\s*(?:요\s*건|조\s*건))\s*[:：|]\s*(?P<v>[^|:：]{1,40})')


def region_label2(b):
    for ln in b.notice.lines:
        if ln.sec in ('EVAL', 'DOCS') or not ln.text.strip():
            continue
        t = ' '.join(ln.text.split())
        if judge.REGION_NONE.search(t) or judge.JV_PARTNER3.search(t) or judge.V8_RW_NOT.search(t):
            continue
        for m in _RL_LABEL.finditer(t):
            v = m.group('v')
            if judge.X3_REGION_NOT.search(v):
                continue
            mm = regions.mentions(v)
            if mm['sido'] or mm['basic']:
                return ln
    return None


REGIONS.append(('U1_V8_REGION_LABEL2', region_label2))
