"""Source ranges for connected software participation disclosures.

Physical wraps can separate a legal basis from its application. Extend only
unfinished syntax or an explicit heading/child relation, retaining every byte
of the original range. Neighbourhood alone is not a relation.
"""
import re


ANCHOR = re.compile(r'소프트웨어\s*진흥법|하한제도|사업금액의\s*하한')
_ITEM = re.compile(r'^\s*(?:\d+[.)]|[가-하][.)]|[①-⑳]|[○●□■※*-])')
_OPEN = re.compile(r'(?:에\s*따른?|에|의|따라|및|또는|[,，]|참여\s*제한을|하한제도를)\s*$')
_HEADING = re.compile(r'소프트웨어\s*진흥법\s*제\s*48\s*조(?:\s*제?\s*[34]\s*항)?\s*[:：]?\s*$')
_APPLICATION_CHILD = re.compile(r'^\s*[○●□■-]?\s*(?:적용\s*(?:사항|내용|기준)|참여\s*제한)\s*[:：]')


def passages(text):
    """Yield original offsets; never span blank lines or independent items."""
    lines = list(re.finditer(r'[^\r\n]+', text))
    for i, line in enumerate(lines):
        if not ANCHOR.search(line[0]):
            continue
        end = line.end()
        for following in lines[i + 1:i + 4]:
            gap = text[end:following.start()]
            if gap.replace('\r\n', '\n').count('\n') != 1:
                break
            if following.end() - line.start() > 720:
                break
            previous = text[line.start():end].rsplit('\n', 1)[-1]
            child = bool(_HEADING.search(previous) and _APPLICATION_CHILD.search(following[0]))
            if not child and (_ITEM.search(following[0]) or not _OPEN.search(previous)):
                break
            end = following.end()
        yield line.start(), end
