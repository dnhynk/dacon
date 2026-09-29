"""RT-T: witness-local v2 exclusions for explicit non-requirements.

Each filter re-runs only v2 on a copied bundle after removing disproved
witnesses. A later independent record requirement must remain discoverable.
"""
import re
from copy import copy
from dataclasses import replace

RECORD = re.compile(r'실\s*적|경\s*험|이\s*력')
NONE_ROW = re.compile(r'^\W*(?:(?:용역|납품|수행|사업)\s*)?실\s*적\s*(?:제\s*한|요\s*건)?\W*'
                      r'(?:없\s*음|해\s*당\s*없\s*음|미\s*요\s*구)(?=$|[\s(（:：.。])')
OR = re.compile(r'또\s*는|혹\s*은')
CAPACITY = re.compile(r'(?:능\s*력[^.。;；]{0,20}장\s*비|장\s*비[^.。;；]{0,20}능\s*력)'
                      r'[^.。;；]{0,20}(?:갖\s*춘|보\s*유\s*한|구\s*비\s*한)')
CERTIFICATE = re.compile(r'(?:사\s*전\s*)?품\s*질\s*인\s*증[^.。;；]{0,50}'
                         r'(?:판\s*정\s*을\s*받\s*은|받\s*은|보\s*유\s*한|취\s*득\s*한)')
EVAL_HEAD = re.compile(r'적\s*격\s*심\s*사[^.。]{0,30}이\s*행\s*실\s*적\s*평\s*가\s*기\s*준')
QUAL_HEAD = re.compile(r'^\W*(?:\d+[.)]\s*)?(?:입\s*찰\s*)?참\s*가\s*자\s*격\s*[:：]?$')


def own_clause(b, ln):
    # Do not borrow an alternative or waiver from the next numbered item.
    from . import judge
    text = ln.text
    if judge.SENT_END.search(text):
        return text
    for nxt in b.notice.window(ln.i, 0, 4)[1:]:
        if not nxt.text.strip():
            continue
        if nxt.sec != ln.sec or judge.ITEM_START.match(nxt.text) or judge.REGION_ROW_LABEL.match(nxt.text):
            break
        text += ' ' + nxt.text.strip()
        if judge.SENT_END.search(nxt.text):
            break
    return text


def none_value(b, ln):
    text = own_clause(b, ln)
    match = NONE_ROW.search(text)
    # The rest may impose a certificate, but another record clause remains.
    return bool(match and not RECORD.search(text[match.end():]))


def nonrecord_alternative(b, ln):
    text = own_clause(b, ln)
    # An OR of record *types* or alternative buyers is still a record limit.
    # Only the positive, complete equipment/certification route qualifies.
    for join in OR.finditer(text):
        left, right = text[:join.start()], text[join.end():]
        right = re.split(r'(?<!\d)\.(?!\d)|[。;；]', right, maxsplit=1)[0]
        # OR must attach directly to the record noun, not a later condition.
        if not re.search(r'(?:실\s*적|경\s*험|이\s*력)\s*(?:[(（][^)）]{0,80}[)）]\s*)?$', left):
            continue
        if len(RECORD.findall(left)) != 1 or RECORD.search(text[join.end():]):
            continue
        if re.search(r'불가|불허|제외|아니|않', right):
            continue
        if not re.search(r'업체|사업자|기관|단체|법인|기업', right):
            continue
        if CAPACITY.search(right) or CERTIFICATE.search(right):
            return True
    return False


def evaluation_context(b, ln):
    from . import judge
    if ln.sec == 'QUAL' or not RECORD.search(ln.text):
        return False
    # A bidder holding history in its own clause is an independent condition.
    if judge.PERF_BIDDER_REQ.search(own_clause(b, ln)):
        return False
    for prev in reversed(b.notice.window(ln.i, 18, 0)[:-1]):
        if QUAL_HEAD.search(prev.text):
            return False
        if EVAL_HEAD.search(prev.text):
            return True
    return False


FILTERS = {'NONE': none_value, 'OR': nonrecord_alternative, 'EVAL': evaluation_context}


def filter_hit(b, hit, base, kind):
    reject = FILTERS[kind]
    if hit is None or not hasattr(hit, 'i') or not reject(b, hit):
        return hit
    bad = {ln.i for ln in b.notice.lines if reject(b, ln)}
    # Preserve indices, document/section boundaries and the original bundle.
    # Candidate objects and model roles must be replaced too: changing notice
    # text alone leaves an old cands object able to re-fire the same witness.
    lines = [replace(ln, text='[non-requirement]') if ln.i in bad else ln for ln in b.notice.lines]
    current = copy(b)
    current.notice = replace(b.notice, lines=lines)
    current.cands = {fam: [lines[ln.i] for ln in cands] for fam, cands in b.cands.items()}
    current.readings = {fam: {i: ({**r, '역할': '안내·기타'} if i in bad else r)
                              for i, r in readings.items()}
                        for fam, readings in b.readings.items()}
    got = base(current)
    return b.notice.lines[got.i] if got is not None and hasattr(got, 'i') else got
