"""Source observations of production-certificate exceptions, not legal waivers.

This bounded scanner separates possession/requirement from document submission.
An observed exception never certifies its statutory basis, amount, item identity
or scope. Consumers decide which proposed inference needs further review.
"""
from __future__ import annotations

import re


_CERTIFICATE = re.compile(r'직접\s*생산(?:\s*확인)?(?:\s*증명)?(?:\s*서)?')
_EXCEPTION = re.compile(
    r'(?:요구|필요|보유|소지|제출)(?:하|하지|가|를|할|할\s*필요가)?\s*'
    r'(?:않|아니|없)|불필요|미요구|면제|생략|제외|대상(?:이)?\s*아니')
_DENIAL = re.compile(r'(?:면제|생략|제외)(?:하|하지|되|되지)?\s*(?:않|아니)|'
                     r'(?:면제|생략)(?:할|될)\s*수\s*없|(?:면제|생략)(?:는|가)?\s*없')


def exception_observations(record):
    """Read exact physical lines; never join unrelated dumped table fragments.

    This is deliberately not an absence detector. An empty result cannot prove
    that no exception exists, especially across damaged line breaks.
    """
    result = []
    for di, doc in enumerate(record['docs']):
        if doc['type'] not in {'공고문', '규격서', '과업지시서', '제안요청서', '예외공표서'}:
            continue
        for line in re.finditer(r'[^\r\n]+', doc['text']):
            text = line[0]
            subject = _CERTIFICATE.search(text)
            if not subject:
                continue
            # Do not borrow an exception after a different certificate or a
            # completed sentence. Preserve uncertainty rather than relabel it.
            tail = text[subject.end():]
            end = re.search(r'(?<!\d)\.(?!\d)|[!?。]|(?:중소기업|소기업|소상공인)\s*확인', tail)
            tail = tail[:end.start()] if end else tail
            match = _EXCEPTION.search(tail)
            if not match or _DENIAL.search(tail):
                continue
            actions = re.findall(r'제출|사본|출력|등록|보유|소지|자격', tail[:match.end()])
            submit = bool(actions and actions[-1] in {'제출', '사본', '출력', '등록'})
            possession = bool(actions or re.search(r'요구', tail[:match.end()]))
            result.append({'kind': 'direct_production_exception_observation',
                'action': 'submission' if submit else
                          'possession_or_requirement' if possession else 'unclear',
                'item_scope_certified': False, 'waiver_certified': False,
                'evidence': {'doc_index': di, 'doc_id': doc.get('doc_id'),
                    'doc_type': doc['type'], 'start': line.start(), 'end': line.end(), 'text': text}})
    return result
