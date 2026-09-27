"""Only a written SME-priority waiver; no categorical or registration-based exemption."""
import re
from . import judge

def filter_hit(b,hit):
    if hit is None:return hit
    # These are asserted applications/non-applications, not permission to invoke
    # an exception and not admission of a nonprofit alongside small businesses.
    apply=re.compile(r'예\s*외\s*(?:를\s*적\s*용\s*(?:함|합\s*니\s*다|한\s*다)|가\s*적\s*용\s*(?:됨|됩\s*니\s*다|된\s*다))')
    nonapply=re.compile(r'(?:우\s*선\s*조\s*달(?:\s*계\s*약)?|제\s*한\s*경\s*쟁\s*입\s*찰|판\s*로\s*지\s*원\s*법(?:\s*시\s*행\s*령)?)'
        r'\s*(?:의|을|은|는|상)?\s*(?:(?:의\s*무\s*)?적\s*용\s*대\s*상\s*이?\s*아\s*닙\s*니\s*다|아\s*님|아\s*니\s*다|미\s*적\s*용\s*(?:함|합\s*니\s*다|$)|적\s*용\s*하\s*지\s*않\s*(?:습\s*니\s*다|는\s*다|음))')
    rebid=re.compile(r'유\s*찰\s*로\s*인\s*해\s*(?:(?:중\s*소\s*기\s*업|소\s*기\s*업|소\s*상\s*공\s*인)[^.。]{0,20}|입\s*찰\s*참\s*가\s*자\s*격\s*조\s*건\s*(?:을|의)?)'
                     r'[^.。]{0,15}(?:완\s*화|확\s*대)\s*(?:함|합\s*니\s*다|하\s*였\s*음|$)')
    for doc in b.notice.docs:
        for part in judge.PLEDGE_SENTENCE.split(doc['text']):
            sentence=' '.join(part.split())
            if not judge.X5_PREFERENCE.search(sentence):continue
            if nonapply.search(sentence) or rebid.search(sentence):return None
            if apply.search(sentence) and re.search(r'우\s*선\s*조\s*달|제\s*2\s*조\s*의\s*3\s*(?:제\s*1\s*항\s*)?제?\s*[1345]\s*호',sentence):return None
    return hit
