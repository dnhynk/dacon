"""Joint-member share floors in two further notations (switch V21_SHARE_FORMS2); read through the literal share check.

A floor stated as a part of the whole: "구성원별 최소 지분은 전체의 3%로 함", "각 구성원의 지분은 전체 계약금액의 3% 이상",
"구성원별 참여지분은 공동수급 총 지분의 3% 이상", "구성원은 전체 사업비의 3% 이상을 지분으로 참여", "전체 지분 중 3%",
"전체 대비 3%", "전체 지분의 100분의 3". "전체의" / "총 지분의" / "전체 계약금액의" / "전체 대비" name the base of the
percentage, not a total or a representative's share. The phrase is dropped (kept as the share noun when it names one) and the
line is judged by the same check, which still binds the number to a member's minimum and leaves a representative's share
out. And the floor word 최저 (최저한도, 최저선) read as 최소 ("구성원별 지분율 최저한도: 3%"); "최저가" (lowest price) is not.
"""
import re
from dataclasses import replace

from . import rtd_v21_share, v21_share_forms

PERCENT_AHEAD = r'(?=\s*(?:\d{1,2}(?:\.\d+)?\s*(?:%|％|퍼\s*센\s*트|프\s*로)|(?:100|백)\s*분\s*의\s*\d))'
WHOLE = re.compile(r'(?:전\s*체|총)\s*(?:의\s*)?(?P<noun>(?:참\s*여\s*|계\s*약\s*(?:참\s*여\s*)?)?지\s*분|계\s*약\s*금\s*액|사\s*업\s*(?:비|금\s*액)|공\s*사\s*금\s*액'
                   r'|용\s*역\s*금\s*액|금\s*액)?\s*(?:의|중|대\s*비)' + PERCENT_AHEAD)


LOWEST = re.compile(r'최\s*저(?!\s*가)')


def normalize(text):
    if not rtd_v21_share.JV.search(text):
        return text
    t = WHOLE.sub(lambda m: '지분 ' if m.group('noun') and re.search(r'지\s*분', m.group('noun')) else '', text)
    if rtd_v21_share.SHARE.search(t):
        t = LOWEST.sub('최소', t)
    return v21_share_forms.normalize(t) if t != text else text


def augment(b, hit):
    if hit is not None or b.meta.law not in ('국가', '지방'):
        return hit
    lines = list(b.notice.lines)
    changed = False
    for k, ln in enumerate(lines):
        t = normalize(ln.text)
        if t != ln.text:
            lines[k] = replace(ln, text=t)
            changed = True
    if not changed:
        return hit
    view = replace(b, notice=replace(b.notice, lines=lines))
    got = rtd_v21_share.augment(view, None)
    return b.notice.lines[got.i] if got is not None and hasattr(got, 'i') else hit
