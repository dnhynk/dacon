"""Joint-member share floors in further notations (switch V21_SHARE_FORMS); read through the literal share check."""
import re
from dataclasses import replace

from . import rtd_v21_share

JV = rtd_v21_share.JV
# "3/100", "3프로", "삼(3)퍼센트" / "(3)%" are the percentages 3%.
SLASH = re.compile(r'(?<![\d/.])(\d{1,2}(?:\.\d+)?)\s*/\s*100(?![\d/])')
PRO = re.compile(r'(?<=\d)\s*프\s*로(?![가-힣])')
PAREN = re.compile(r'(?:[일이삼사오육칠팔구십]+\s*)?[(（]\s*(\d{1,2}(?:\.\d+)?)\s*[)）]\s*(%|퍼\s*센\s*트|％)')
# "지분율이 3% 미만인 경우 구성원이 될 수 없음": a share below N% bars membership, i.e. the floor is N%.
BELOW_BARRED = re.compile(r'(\d{1,2}(?:\.\d+)?)\s*(?:%|퍼\s*센\s*트|％)\s*미\s*만\s*(?:인|일)?\s*(?:경\s*우|때|업\s*체|자|구\s*성\s*원)?[^.。;]{0,30}?'
                          r'(?:될\s*수\s*없|참\s*여\s*할\s*수\s*없|참\s*여\s*(?:가\s*)?불\s*가|구\s*성\s*(?:할\s*)?수\s*없|허\s*용\s*(?:하\s*지\s*않|되\s*지\s*않|불\s*가))')
# A member's own participation floor without a share noun ("구성원은 각 3퍼센트 이상 참여", "구성원은 3% 이상 참여").
MEMBER_PARTICIPATES = re.compile(r'(구\s*성\s*원\s*(?:은|는|별|마\s*다|각\s*각|의)?\s*(?:각\s*(?:각)?\s*)?(?:최\s*소\s*)?)'
                                 r'(?=\d{1,2}(?:\.\d+)?\s*(?:%|퍼\s*센\s*트|％)\s*이\s*상\s*(?:의\s*비\s*율\s*로\s*|으\s*로\s*)?참\s*여)')
# A continuation line of a share field opens with its own bullet ("구성원별 지분율" / "- 최소 3% 이상"), not a range sign.
BULLET = re.compile(r'^\s*[-–·•○◦ㅇ※]\s*')


RANGE = re.compile(r'(\d{1,2}(?:\.\d+)?)\s*%\s*[~∼]\s*(\d{1,2}(?:\.\d+)?)\s*%')


def normalize(text):
    t = RANGE.sub(lambda m: m.group(1) + '% 이상 ' + m.group(2) + '% 이하', text)
    t = SLASH.sub(lambda m: m.group(1) + '%', t)
    t = PRO.sub('%', t)
    t = PAREN.sub(lambda m: m.group(1) + '%', t)
    t = BELOW_BARRED.sub(lambda m: m.group(1) + '% 이상' + (' 참여' if re.search(r'참\s*여', m.group(0)) else ''), t)
    if JV.search(t):
        t = MEMBER_PARTICIPATES.sub(lambda m: m.group(1) + '지분 ', t)
    return t


def augment(b, hit):
    if hit is not None or b.meta.law not in ('국가', '지방'):
        return hit
    lines = list(b.notice.lines)
    changed = False
    for k, ln in enumerate(lines):
        t = normalize(ln.text)
        prev = next((x for x in reversed(b.notice.window(ln.i, 3, 0)[:-1]) if x.text.strip()), None)
        if prev is not None and prev.sec == ln.sec and rtd_v21_share.SHARE.search(prev.text) and BULLET.match(t) \
                and not rtd_v21_share.SHARE.search(t):
            t = BULLET.sub('', t)
        if t != ln.text:
            lines[k] = replace(ln, text=t)
            changed = True
    if not changed:
        return hit
    view = replace(b, notice=replace(b.notice, lines=lines))
    got = rtd_v21_share.augment(view, None)
    return b.notice.lines[got.i] if got is not None and hasattr(got, 'i') else hit
