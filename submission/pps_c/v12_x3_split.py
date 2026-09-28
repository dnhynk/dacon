"""V12 X3 exclusions: affirmative alternatives and verification, independently gated.

These helpers only filter an existing v12 candidate. They never create candidates.
"""
import re

CERT = re.compile(r'직\s*접\s*생\s*산\s*(?:확\s*인\s*)?증\s*명\s*서')
ALT_CERT_ROUTE = re.compile(
    r'또는\s*(?:정품\s*)?(?:공급\s*(?:증명서|확약서)|제조사[^.。]{0,20}?확약서)'
    r'\s*(?:를|을)?\s*(?:제출|소지|보유)\s*'
    r'(?:할\s*수\s*있는\s*(?:자|업체)|가능(?:한\s*)?(?:자|업체)?|한\s*(?:자|업체))')
ALT_DENIED = re.compile(r'불\s*가|금\s*지|허용\s*하지|인정\s*하지|제외|할\s*수\s*없|모두|함께|필수|별도로|추가로')
VERIFY_UNCONFIRMED = re.compile(r'확\s*인\s*(이|가)?\s*(안\s*되거나|되지\s*않거나)')
VERIFY_REQUIREMENT = re.compile(
    r'(?:입\s*찰\s*)?참\s*가\s*자\s*격[^.。]{0,24}(?:없|박탈|상실|제한|불가)'
    r'|(?:입\s*찰|참\s*가|참\s*여)[^.。]{0,20}(?:할\s*수\s*없|불가|제한|배제)'
    r'|무\s*효|부\s*적\s*격|소\s*지|보\s*유|구\s*비|제출\s*(?:하여야|해야|할\s*것)')


def alternative_match(clause):
    """Only a certificate-or-supply route with affirmative eligibility wording."""
    for match in ALT_CERT_ROUTE.finditer(clause):
        # Keep the DP certificate and the alternative in the same sentence.
        start = max((m.end() for m in re.finditer(r'[.。]\s+', clause[:match.start()])), default=0)
        before = clause[start:match.start()]
        end = re.search(r'[.。](?:\s|$)', clause[match.end():])
        after = clause[match.end():match.end() + end.start()] if end else clause[match.end():]
        if CERT.search(before) and not ALT_DENIED.search(before + match.group(0) + after):
            return match
    return None


def verification_match(text):
    """An explicit qualification failure is a requirement, not verification-only."""
    if not CERT.search(text) or VERIFY_REQUIREMENT.search(text):
        return None
    return VERIFY_UNCONFIRMED.search(text)


def exclusion_match(clause, verification_text, *, alt=False, verify=False):
    if alt:
        match = alternative_match(clause)
        if match:
            return 'ALT_CERT_ROUTE', match.group(0)
    if verify:
        match = verification_match(verification_text)
        if match:
            return 'VERIFY_UNCONFIRMED', match.group(0)
    return None
