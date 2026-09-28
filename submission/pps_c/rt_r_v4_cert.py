"""A quality approval explicitly offered instead of a delivery record is not a mandatory record."""
import re

# This reader deliberately recognizes only the complete, affirmative alternative.
# Whitespace (including extracted wraps) and a parenthetical object are harmless.
# An additional record noun keeps the original verdict: it could be an independent
# purchaser restriction, even when the same clause also offers certification.
_RECORD = re.compile(r'실적|이력|경험')
_ALT = re.compile(
    r'납품실적(?:\([^()]{0,60}\))?(?:또는|혹은)사전품질인증을'
    r'(?:입찰방법)?통해품질적격판정을받은업체'
)


def admits_certificate(text):
    compact = re.sub(r'\s+', '', text)
    return len(_RECORD.findall(compact)) == 1 and bool(_ALT.search(compact))
