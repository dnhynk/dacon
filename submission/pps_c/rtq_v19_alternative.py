"""Recognize an explicit bidder A/S undertaking allowed instead of a maker pledge."""
import re

SPACE = re.compile(r'\s+')
# The same maker document is named in the conditional exception, followed by
# the delivering bidder's own A/S undertaking. Mere inability is not a waiver.
REPLACEMENT = re.compile(
    r'(?:제조사|제조업체)의?(?:정품|물품)?공급확약서(?:를|의)?제출이?'
    r'(?:불가한|불가능한)경우[,，]?(?:납품사|납품업체|입찰업체|입찰자)의?'
    r'(?:A/?S|사후관리)확약서(?:각)?(?:1부|제출|로(?:대체|갈음))', re.I)
MAKER_DOC = re.compile(r'(?:제조사|제조업체).{0,45}확약서')
NEW_ITEM = re.compile(r'^\s*(?:\(?\d{1,2}\s*[.)]|[가-하]\s*[.)]|[①-⑳])')


def exempt(notice, ln):
    """Only this demand and its immediate exception; other demands survive."""
    text = SPACE.sub('', ln.text)
    if REPLACEMENT.search(text):
        return True
    if not MAKER_DOC.search(text):
        return False
    following = [x for x in notice.lines[ln.i + 1:] if x.text.strip()][:2]
    for x in following:
        if x.doc != ln.doc or NEW_ITEM.match(x.text):
            return False
        s = SPACE.sub('', x.text)
        if REPLACEMENT.search(s):
            return True
        # Do not look beyond a different nonempty instruction.
        return False
    return False
