"""Conservative checks on claimed software relations, after exact-source validation.

These checks reject unsupported uses of a witness; passing is not certification
of legal meaning. Original claims and source locations remain in the decision.
"""
import re
from .software_roles import work_review
from .software_objects import object_review

_METHOD = re.compile(r'(?:(?:개발|설치|운영|수정|변경|보완|업데이트|패치)\s*[,·/]?\s*)+(?:방법|절차|설명|매뉴얼)')
_ACTION = {
    'create': re.compile(r'개발|구현|프로그래밍|코딩|작성|제작|생성|신규\s*구축', re.I),
    'modify': re.compile(r'수정|변경|개선|보완|업데이트|패치|커스터마이징', re.I),
    'maintain': re.compile(r'유지\s*(?:보수|관리)|기술\s*지원|복구|장애\s*처리|패치|업데이트|Care\s*Pack|Support', re.I),
    'renew': re.compile(r'갱신|연장|renew', re.I),
    'provide': re.compile(r'제공|납품|인도|공급|구매|도입|제출|인계', re.I),
    'install': re.compile(r'설치|인스톨|install', re.I),
    'operate': re.compile(r'운영|운용|가동', re.I),
}
_OTHER_RENEWAL = re.compile(r'사업자\s*등록증?|인감\s*증명서|보증\s*보험|이행\s*보증서')
_LICENSE = re.compile(r'사용권|라이[선센]스|license|subscription|구독', re.I)
_SIZE = re.compile(r'중소기업|소기업|소상공인')
_FLOOR = re.compile(r'하한|사업\s*금액별|대기업[^\n]{0,30}참여|제\s*48\s*조|중소\s*소프트웨어\s*사업자')
_SOFTWARE = re.compile(r'소프트웨어|S/W|\bSW\b', re.I)


def relation_review(relation, rec=None):
    trace = []

    def result(issues):
        return {'issues': issues, 'source_actions': trace}

    if not (relation['actor'] in {'contractor', 'bidder'} and relation['object'] in {'software', 'license'}
            and relation['obligation'] == 'required' and relation['role'] == 'software_task'):
        return result([])
    # No concatenation across omitted source ranges can manufacture an action.
    action = relation['action']
    if action in _ACTION:
        issues, supported, found = [], False, False
        for wi, witness in enumerate(relation['witnesses']):
            # Mask in place so an action's offsets remain exact original offsets.
            masked = _METHOD.sub(lambda m: ' ' * len(m[0]), witness['quote'])
            for match in _ACTION[action].finditer(masked):
                found = True
                contexts = [(None, witness['quote'], match.start(), match.end())]
                if rec is not None:
                    # Every occurrence is retained by exact-source validation.
                    # An ambiguous short quote cannot pick the favorable one.
                    contexts = [(p['doc_index'], rec['docs'][p['doc_index']]['text'],
                                 p['start'] + match.start(), p['start'] + match.end())
                                for p in witness['locations']]
                reviews = []
                for di, q, a, b in contexts:
                    review = work_review(q, a, b, action=action)
                    target = object_review(q, a, b, review)
                    review['object_support'] = target
                    review['issues'].extend(target['issues'])
                    if (action == 'renew' and _OTHER_RENEWAL.search(review['quote'])
                            and not _LICENSE.search(review['quote'])):
                        review['issues'].append('source_renewal_is_for_a_different_object')
                    reviews.append({**review, 'doc_index': di,
                        'coordinate_space': 'original_document' if di is not None else 'selected_quote',
                        'action_start': a, 'action_end': b, 'action_quote': q[a:b]})
                contrary = [issue for review in reviews for issue in review['issues']]
                trace.append({'witness_index': wi, 'selected_action_start': match.start(),
                    'selected_action_end': match.end(), 'contexts': reviews,
                    'passes_necessary_checks': not contrary})
                if contrary:
                    issues.extend(contrary)
                else:
                    supported = True
        if not found:
            return result(['selected_witness_does_not_express_claimed_' + action])
        if not supported:
            return result(list(dict.fromkeys(issues)))
        # Named products may be software without containing a generic SW word.
        # Do not reintroduce a vocabulary gate after semantic candidate discovery.
    return result([])


def relation_issues(relation, rec=None):
    return relation_review(relation, rec)['issues']


def disclosure_audit(disclosure, rec):
    witnesses = disclosure['witnesses']
    result = {'status': 'not_rejected', 'absence_verified': False, 'witnesses': witnesses}
    if disclosure['status'] != 'observed' or not witnesses:
        return result
    if not all(_SIZE.search(w['quote']) and not _FLOOR.search(w['quote'])
               and not _SOFTWARE.search(w['quote']) for w in witnesses):
        return result
    # A wrapped floor basis adjacent to the quoted SME clause must remain
    # unresolved; a narrow quote cannot erase its governing source context.
    for witness in witnesses:
        for location in witness['locations']:
            text = rec['docs'][location['doc_index']]['text']
            start, end = location['start'], location['end']
            first = text.rfind('\n', 0, start) + 1
            last = text.find('\n', end)
            last = len(text) if last < 0 else last
            preceding = text[:first].rstrip('\n\r')
            before = preceding[preceding.rfind('\n')+1:]
            following = text[last:].lstrip('\n\r').split('\n', 1)[0]
            if any(_FLOOR.search(q) for q in [before, text[first:last], following]):
                return result
    return {**result, 'status': 'unrelated_generic_size_witness',
            'meaning': 'General SME qualification does not itself disclose the software participation floor.'}


def audit(facts, rec):
    reviews = [relation_review(r, rec) for r in facts['relations']]
    return {'relations': [{'index': i, 'issues': r['issues']} for i, r in enumerate(reviews)],
            'source_actions': [{'index': i, 'actions': r['source_actions']} for i, r in enumerate(reviews)],
            'disclosure': disclosure_audit(facts['disclosure'], rec),
            'passing_is_semantic_certification': False}
