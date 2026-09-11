"""Clause-local modality guards for positive deterministic predicates."""
from __future__ import annotations

import re


def compact(text):
    return re.sub(r'\s+', '', text)


def clause(text, start, end):
    """Keep wrapped text, but never borrow a predicate across a numbered item."""
    boundaries = list(re.finditer(r'[.。;；](?=\s|$)|\n\s*\n|\n\s*(?:\d+[.)]|[가-하][.)]|[①-⑳])', text))
    left = max((m.end() for m in boundaries if m.end() <= start), default=0)
    right = min((m.start() for m in boundaries if m.start() >= end), default=len(text))
    return max(left, start - 420), min(right, end + 240)


SUBJECTS = {
    'region': re.compile(r'지역\s*제한|본점|본사|영업소|소재지'),
    'share': re.compile(r'지분율|최소\s*지분|출자\s*비율|참여\s*비율'),
    'software': re.compile(r'(?:소프트웨어|SW)\s*사업', re.I),
    'floor': re.compile(r'하한제도|사업금액별\s*참여|제\s*48\s*조'),
}
CONNECTIVE = re.compile(r'하며|이며|이고|하되|하지만|그러나|(?:하여야|해야|이어야)\s*하고')
REFERENCE = re.compile(r'^\s*(?:다만\s*)?(?:이|그|위|상기|해당|당해)(?:의)?\s*(?:조건|요건|제한|의무|요구사항|문구|선언|분류)')


def assertion_scope(text, start, end, subject):
    """Bind a predicate to its subject inside connected clauses.

    Separate explicit subjects keep their own polarity. A repeated subject or
    an anaphoric 'that condition' carries withdrawal back to the target.
    Ambiguous references remain in scope and can only block a forced decision.
    """
    lo, hi = clause(text, start, end)
    cuts = [(lo, lo)] + [(m.start(), m.end()) for m in CONNECTIVE.finditer(text, lo, hi)] + [(hi, hi)]
    segments = [(cuts[i][1], cuts[i+1][0]) for i in range(len(cuts)-1)]
    containing = [i for i, (a,b) in enumerate(segments) if a <= start < b or a < end <= b]
    if not containing:
        return text[lo:hi]
    first, last = containing[0], containing[-1]
    included = [text[segments[first][0]:segments[last][1]]]
    for a, b in segments[last+1:]:
        continuation = text[a:b]
        if SUBJECTS[subject].search(continuation) or REFERENCE.search(continuation):
            included.append(continuation)
    return ' '.join(included)


def has_withdrawal(record, subject):
    """An explicit later correction blocks an earlier forced requirement.

    This is an abstention on contradictory source clauses, never a blanket
    negative judgment about the legal item.
    """
    for doc in record.get('docs', []):
        text = doc['text']
        for match in SUBJECTS[subject].finditer(text):
            scope = assertion_scope(text, match.start(), match.end(), subject)
            if re.search(r'삭제|철회|폐지', scope) and not re.search(r'예시|가정|참고용', scope):
                if unresolved_assertion(scope):
                    return True
    return False


def unresolved_assertion(text):
    """A quoted, withdrawn, optional or unresolved assertion proves no duty.

    This function can only reject a proof. It never certifies compliance.
    Call it on the matched clause, not the entire notice.
    """
    n = compact(text)
    patterns = (
        r'예시|가정|참고용|작성예|주장|단정할수없|검토가필요|확인되지|확인불가|불확실|미확정|미정',
        r'(?:조건|요건|규정|요구사항|문구|안내|제한|의무)(?:은|는|을|를|도)?(?:삭제|철회|폐지|생략|면제)',
        r'(?:삭제|철회|폐지)(?:한다|합니다|함|되었|된|됨)',
        r'(?:제한|적용|요구|운행|위탁|보유|제출|임차)하지(?:않|아니)',
        r'(?:지역제한|지분율제한|참여제한)(?:이|은|는)?없',
        r'필요없|않아도|아닌것은아니|아니라고|아님으로단정',
        r'전국(?:의)?업체(?:가|도|는)?(?:참가|참여)가능|업체도참가할수',
    )
    return any(re.search(pattern, n) for pattern in patterns)
