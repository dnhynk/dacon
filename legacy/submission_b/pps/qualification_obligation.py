"""Source-addressed bidder-size obligations, independent of paper submission.

Only a commercial upper bound is returned. An ambiguous entity conjunction
does not certify the exact allowed set, a certificate's issue date, or a product.
"""
import re
from . import sme
from .products import normalized_map


_PREDICATE = re.compile(r'자격(?:을)?(?:구비(?:하여야|해야)|갖추어야|갖춰야)'
                        r'(?:합니다|한다|함)(?=$|[.。])')
_EXCLUDE = re.compile(r'참고|예시|가정|인용|권장|삭제|철회|경우|예외|다만|'
    r'비영리|벤처|창업|특별법인|협동조합|중견기업|대기업|비중소|'
    r'낙찰자|계약상대자|계약체결|선정이후|선정후|하도급|협력업체|협력사|'
    r'분담|구성원|납품후|수요기관|발주기관|발주자|제조사|제조업체|'
    r'않|아니|아닌|면제|선택|조건부|[「『“\"]')
_WITHDRAWN = re.compile(r'(?:자격(?:조건|요건|제한)?|기업규모(?:조건|제한)?|규정|조건|요건|요구사항)'
                        r'(?:을|를|은|는|이|가)?(?:모두|별도로|일괄)?'
                        r'(?:삭제|철회|적용하지|요구하지|면제)')
_ENTITY = re.compile(sme.CLASS+r'(?:자)?')
_BRIDGE = re.compile(r'(?:또는|혹은|및|[·ㆍ,])'
    r'(?:제\d+조(?:의\d+)?(?:제\d+항)?(?:제\d+호)?(?:에따른|에의한|에의하여)?)?')


def entity_obligations(record, entries):
    observed = {}
    for entry in entries:
        ev = entry['evidence']
        if entry['section_role'] != 'eligibility' or ev['document_role'] != '공고문':
            continue
        text = record['docs'][ev['doc_index']]['text']
        normalized, addresses = normalized_map(ev['text'])
        masked = sme.mask_laws(normalized)
        keep = [i for i, char in enumerate(masked) if not char.isspace()]
        compact = ''.join(masked[i] for i in keep)
        positions = [addresses[i] for i in keep]
        if _WITHDRAWN.search(compact):
            continue
        for predicate in _PREDICATE.finditer(compact):
            prefix = compact[:predicate.start()]
            if _EXCLUDE.search(prefix):
                continue
            entities = list(_ENTITY.finditer(prefix))
            if not entities or entities[-1].end() != len(prefix):
                continue
            if any(not _BRIDGE.fullmatch(prefix[a.end():b.start()])
                   for a, b in zip(entities, entities[1:])):
                continue
            # A wrapped fragment cannot lose a preceding conditional governor.
            previous = text[:ev['start']].rstrip().rsplit('\n', 1)[-1]
            if _EXCLUDE.search(sme.mask_laws(sme.norm(previous))):
                continue
            lo, hi = ev['start'], ev['start']+positions[predicate.end()-1]+1
            if hi < len(text) and text[hi] in '.。':
                hi += 1
            bound = sorted(set().union(*(sme.class_set(m.group()) for m in entities)))
            key = (ev['doc_index'], hi)
            observation = {'status': 'mandatory_eligibility',
                'scope': 'bidder_entity_qualification', 'commercial_upper_bound': bound,
                'exact_allowed_set_certified': False, 'certificate_possession_derived': False,
                'evidence': sme.evidence(record, ev['doc_index'], lo, hi)}
            # Overlapping windows for one predicate keep its complete prefix.
            # Identical text at a different original address remains separate.
            if key not in observed or lo < observed[key]['evidence']['start']:
                observed[key] = observation
    return list(observed.values())


def ordinary_commercial_bounds(record, entries):
    """Keep the ordinary-company size bound when special entities are alternatives.

    Venture/startup/non-profit routes broaden the eligible entity types. They
    do not silently admit an ordinary medium enterprise when the same operative
    list names only small and micro enterprises. Exact branch equivalence and
    the special entities themselves remain visible in the returned fact.
    """
    result = []
    for entry in entries:
        if (entry.get('section_role') != 'eligibility'
                or not entry.get('alternative_size_branch_unresolved')):
            continue
        ev = entry['evidence']
        if ev.get('document_role') != '공고문':
            continue
        governors = ' '.join(item.get('text', '') for item in entry.get('heading_ancestors', []))
        if not re.search(r'(?:다음|아래).{0,60}(?:모두|전부).{0,30}(?:갖춘|갖추|충족)',
                         sme.norm(governors)):
            continue
        normalized = sme.mask_laws(sme.norm(ev['text']))
        if re.search(r'예시|참고|가정|삭제|철회|않|아니|면제', normalized):
            continue
        special = set(re.findall(r'벤처기업|창업자|창업기업|비영리법인', normalized))
        if not special:
            continue
        ordinary = sme.class_set(normalized)
        if not ordinary or 'medium' in ordinary:
            continue
        result.append({
            'commercial_upper_bound': sorted(ordinary),
            'ordinary_medium_enterprises_excluded': True,
            'special_entity_alternatives': sorted(special),
            'exact_allowed_set_certified': False,
            'evidence': ev,
        })
    return result
