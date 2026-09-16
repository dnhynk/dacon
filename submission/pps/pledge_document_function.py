"""Review the document function established by a positive v19 witness.

Issuer identity, dealership and authenticity do not establish a promise to
supply or support the current purchase. This rejects a specific unsupported
witness; it does not certify the absence of another undertaking.
"""
from __future__ import annotations

import re


def compact(text):
    return re.sub(r'\s+', '', text)


CERTIFICATE = re.compile(r'(?:물품)?제조자증명서|판매대리점계약서|공급자증명서')
CAPABILITY_DOCUMENT = re.compile(
    r'제조사(?:파트너십)?(?:인증)?확인(?:문서|서류)|'
    r'입찰사확인(?:문서|서류)|제조사파트너십인증(?:확인)?(?:문서|서류)')
DEALERSHIP = re.compile(r'(?:원제조[사자]?|제조사|제조업체)와공급자간의판매대리점계약서')
MANUFACTURER = re.compile(
    r'(?:원제조[사자]?|제조사|제조업체)'
    r'(?:인[\w\[\]().·ㆍ&-]{1,80}|[(（][^()（）;；。]{1,80}[)）])?'
    r'에서발행하는(?:물품)?제조자증명서')
SUPPLIER = re.compile(r'판매대리점(?:이|에서)발행하는공급자증명서')
FACTUAL_CERTIFICATION = re.compile(
    r'(?:원제조사|제조사|정식판매대리점|공식판매대리점)(?:임을|여부를)(?:확인|증명)|'
    r'정품임을증명|제조되었음을증명')
PERFORMANCE_CONTENT = re.compile(
    r'확약|협약|약속|(?<!입찰)보증(?!금)|보장|기술지원|유지보수|A/S|사후관리|'
    r'(?:공급|납품)(?!자|사|업체|증명서|원|품)')
CONTENT_REFERENCE = re.compile(r'(?:해당|동|본|이)(?:증명서|계약서|인증서|서류)')
FORM_REFERENCE = re.compile(r'별지|별첨|붙임|첨부|서식|양식')
UNRESOLVED = re.compile(r'가정|예시|작성예|삭제|철회|진위여부미정')
BARE_DOCUMENT_HEADING = re.compile(
    r'(?:[\d가-하]+[.)]|[○●※\[\]()])*'
    r'(?:(?:물품)?제조자증명서|판매대리점계약서|공급자증명서)[\[\]():：]*')
CAPABILITY_FUNCTION = re.compile(
    r'(?:규격|기능).{0,80}(?:지원될수있|지원할수있|지원이가능|지원가능).{0,80}'
    r'(?:증명|명시|확인)|'
    r'(?:증명|명시|확인).{0,80}(?:규격|기능).{0,80}'
    r'(?:지원될수있|지원할수있|지원이가능|지원가능)|'
    r'제조사파트너십인증확인')
CAPABILITY_COMMITMENT = re.compile(
    r'확약|협약|약속|물품공급|기술지원확약|유지보수확약|사후관리확약|'
    r'(?:공급|기술지원|유지보수|사후관리).{0,45}(?:보장|보증|이행)')


def passages(record):
    """Keep complete original paragraphs, including wrapped list statements."""
    for di, doc in enumerate(record['docs']):
        text = doc['text']
        start = 0
        for boundary in [*re.finditer(r'\r?\n[ \t]*\r?\n', text), None]:
            end = boundary.start() if boundary else len(text)
            if start < end:
                yield {'doc_index':di, 'start':start, 'end':end, 'quote':text[start:end]}
            start = boundary.end() if boundary else len(text)


def review(record, quote):
    n = compact(quote)
    names = set(CERTIFICATE.findall(n))
    capability_names = set(CAPABILITY_DOCUMENT.findall(n))
    if (capability_names and CAPABILITY_FUNCTION.search(n)
            and not CAPABILITY_COMMITMENT.search(n)):
        occurrences = []
        for di, doc in enumerate(record['docs']):
            for match in re.finditer(re.escape(quote), doc['text']):
                occurrences.append({'doc_index': di, 'start': match.start(),
                    'end': match.end(), 'quote': match.group()})
        if occurrences:
            return {'item':19, 'value':0, 'evidence':'', 'semantic_value':None,
                'reason':'model_witness_only_establishes_feature_support_capability',
                'source':'pledge_witness_validation', 'absence_verified':False,
                'rejected_witness':quote, 'occurrences':occurrences,
                'document_functions':['feature_support_capability_or_partnership_confirmation']}
    if not names or PERFORMANCE_CONTENT.search(n) or UNRESOLVED.search(n):
        return None
    # A bare document name or a deadline cannot establish its function. Require
    # a source relation about existing identity/dealership, and cover every
    # document alternative in the supplied witness.
    dealership = DEALERSHIP.search(n)
    factual = FACTUAL_CERTIFICATION.search(n)
    if not dealership and not factual:
        return None
    for name in names:
        if name.endswith('제조자증명서') and not MANUFACTURER.search(n):
            return None
        if name == '판매대리점계약서' and not dealership:
            return None
        if name == '공급자증명서' and not SUPPLIER.search(n):
            return None

    related, occurrences = [], []
    for passage in passages(record):
        text = compact(passage['quote'])
        named = bool(CERTIFICATE.search(text))
        referred = bool(CONTENT_REFERENCE.search(text))
        if not named and not referred:
            continue
        # A named document may itself contain a supply promise, or delegate its
        # contents to a form. Unknown form contents must not be certified away.
        if (PERFORMANCE_CONTENT.search(text) or FORM_REFERENCE.search(text) or UNRESOLVED.search(text)
                or BARE_DOCUMENT_HEADING.fullmatch(text)):
            return None
        related.append(passage)
    for di, doc in enumerate(record['docs']):
        for match in re.finditer(re.escape(quote), doc['text']):
            covering = [p for p in related if p['doc_index']==di
                        and p['start'] < match.end() and match.start() < p['end']]
            if not covering:
                return None
            occurrences.append({'doc_index':di, 'start':match.start(), 'end':match.end(),
                                'quote':match.group(), 'document_function_context':covering})
    if not occurrences:
        return None
    return {'item':19, 'value':0, 'evidence':'', 'semantic_value':None,
        'reason':'model_witness_only_establishes_identity_or_dealership',
        'source':'pledge_witness_validation', 'absence_verified':False,
        'rejected_witness':quote, 'occurrences':occurrences,
        'document_functions':['existing_manufacturer_or_dealership_relation'],
        'related_source_passages':related}
