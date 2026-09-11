"""Frozen v1 prose compatibility, used only by an explicit legacy profile."""

import re

from .sme import norm

UNCERTAIN=re.compile(r'불명|불확실|확인불가|확인되지|판단불가|가능성|여부|아닐수|아닐가능|해당하지않을|추정됨|추정된다|추정함|보임|일부|주된')

NEGATIVE=re.compile(r'경쟁제품(?:고시(?:대상)?품목)?(?:\([^)]{1,30}\))?(?:에(?:명확히)?해당하지않(?:는|음|습니다)|해당없음|에해당없음|이아닌|이아님|이아니다)')

POSITIVE=re.compile(r'경쟁제품(?:에해당(?:함|하는|한다)|임|이다|으로지정)')

def categorical_general(value):
    """Reject meta-statements, re-negation and unresolved qualifications.

    A matched substring inside a claim about somebody else's assertion is not
    an assertion by this response. The remaining source gates are still required.
    """
    if not isinstance(value, str):
        return False
    text = norm(value)
    discourse = re.compile(r'단정|주장|인용|틀렸|오류|부정|검토|다만|하지만|그러나|반면|별도|판단할수없|확정할수없|아니라고|않는다고|않음으로|않는다는|해당할수|지정대상|지정된대상')
    negative = NEGATIVE.search(text)
    if negative is None:
        return False
    remainder = text[:negative.start()] + text[negative.end():]
    return bool(not UNCERTAIN.search(text) and not POSITIVE.search(text)
                and not discourse.search(text) and not re.search(r'아니|아닌|아닙|않', remainder))
