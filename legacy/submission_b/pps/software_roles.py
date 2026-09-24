"""Reject explicit mismatches between a software action and its source roles.

This is a shallow necessary-support check, not a Korean semantic parser. It
uses only the original action's clause and leaves unnamed products, omitted
subjects and uncertain attachment unresolved by this check. Passing cannot
certify software procurement or legal applicability.
"""
import re

from .software_assertion import predicate_review


_DOCUMENT = (r'(?:계획서?|보고서|명세서|매뉴얼|안내서|설명서|계약서|확인서|증명서|'
             r'요구\s*사항|요구서|목록|대본|교안|실적)')
_OBJECT_END = (r'(?:만|도)?(?:을|를|은|는)?'
               r'(?:\s*(?:반드시|먼저|추가로|별도로|모두|전부|직접|각각|매년|매월|다시))*\s*$')
_DOCUMENT_OBJECT = re.compile(_DOCUMENT + _OBJECT_END)
_NOMINAL_TAIL = re.compile(
    r'^\s*(?:의\s*)?(?:계획서?|방안|실적|경력|경험|능력|역량|자격|이력|'
    r'요구(?:\s*사항)?|요건|목록|항목|개요|방향|교육|훈련|실습|연습|과정|'
    r'가이드|매뉴얼|설명서|안내서|절차서|방법|절차)')
_RELATIVE_ACTION_TAIL = re.compile(r'^\s*(?:한|된|했던|하였던|되었던)\s+')
_AGENT_OR_FIELD_TAIL = re.compile(
    r'^\s*[)）]?\s*(?:자|사|업체|기관|처|수량|품명|입찰)'
    r'(?:은|는|이|가|의|에|에게|을|를)?(?=[^가-힣]|$)')
_ORDERED_STEP = re.compile(r'^(?:후|뒤|다음)(?:에|에는|부터|까지)?(?:\s|$)')
_SUPPLIER = r'(?:계약\s*(?:업체|상대자)|수급인|수행업체|용역업체|입찰\s*(?:참가자|업체)|낙찰자)'
_OTHER = r'(?:수강생|교육생|연수생|학생|발주\s*(?:기관|처|자)|수요기관)'
_SUBJECT = re.compile(r'(?P<actor>' + _SUPPLIER + '|' + _OTHER + r')(?P<particle>은|는|이|가)\s*')
_JOINT_BEFORE = re.compile(_SUPPLIER + r'(?:와|과)\s*$')
_JOINT_AFTER = re.compile(r'^\s*' + _SUPPLIER + r'(?:와|과)\s*(?:함께|공동으로)\s*')
# An inner subject of "students will use ..." does not govern the outer
# contractor development action. Do not guess attachment across a relative verb.
_RELATIVE = re.compile(r'(?:할|될|한|된|하는|되는|했던|하던|있는|있을|받은|쓸|쓰는)\s+')
_COORDINATE = re.compile(r'\s+(?:및|그리고)\s+|(?:와|과)\s+|[,·+]\s*')
_MATERIAL = re.compile(r'(?:원본\s*)?소스(?:\s*코드)?')
_ANCILLARY = re.compile(r'PCB|F\s*/\s*W|펌웨어|부품|내장', re.I)
_INDEPENDENT_OBJECT = re.compile(
    r'(?:사용권|라이[선센]스|license|실행\s*파일|별도(?:의)?\s*소프트웨어)'
    + _OBJECT_END, re.I)


def work_review(text, start, end, *, action=None):
    """Keep exact source offsets while rejecting narrowly identified role errors.

    An invalid occurrence cannot erase a separate valid occurrence. The caller
    combines occurrences, including *all* locations of an ambiguous short quote.
    """
    review = predicate_review(text, start, end)
    head, tail = text[review['start']:start], text[end:review['end']]
    issues = list(review['issues'])
    if _AGENT_OR_FIELD_TAIL.search(tail):
        issues.append('source_action_is_an_actor_or_field_name_not_a_predicate')
    if action == 'install' and re.match(r'^\s*(?:을|를)?\s*지원(?:\s|[.)]|$)', tail):
        # A specification's installation support/capability does not establish
        # an obligation to perform installation. A separate install predicate
        # or actual technical-support relation remains independently usable.
        issues.append('source_expresses_installation_support_not_an_installation_duty')
    if _NOMINAL_TAIL.search(tail):
        issues.append('source_action_modifies_a_plan_qualification_or_training_noun')
    relative = _RELATIVE_ACTION_TAIL.match(tail)
    if relative and not _ORDERED_STEP.match(tail[relative.end():]):
        # "제공된" alone does not date the provision before this contract.
        # It also does not independently command the claimed current action.
        # Separate the tests so whitespace backtracking cannot erase "후에".
        issues.append('source_action_is_a_relative_modifier_not_a_current_duty')

    subjects = list(_SUBJECT.finditer(head))
    if subjects:
        subject = subjects[-1]
        after_subject = head[subject.end():]
        if (re.fullmatch(_OTHER, subject['actor'])
                and not _JOINT_BEFORE.search(head[:subject.start()])
                and not _JOINT_AFTER.search(after_subject)
                and (subject['particle'] in {'은', '는'} or not _RELATIVE.search(after_subject))):
            issues.append('source_action_has_a_different_explicit_actor')
        # Direct object coordination starts after the nearest subject. A named
        # product with its manual must not fail a generic-SW-word requirement.
        object_head = after_subject
    else:
        object_head = head
    object_parts = [p.strip() for p in _COORDINATE.split(object_head) if p.strip()]
    if object_parts and all(_DOCUMENT_OBJECT.search(p) for p in object_parts):
        issues.append('source_action_targets_documentation_or_qualification')

    if (action == 'provide' and _MATERIAL.search(head) and _ANCILLARY.search(head)
            and not any(_INDEPENDENT_OBJECT.search(p) for p in object_parts)):
        # A license or development word in another (possibly negated) clause
        # cannot turn embedded-source handover into separate SW procurement.
        issues.append('embedded_source_handover_does_not_establish_separate_software_procurement')
    return {**review, 'issues': list(dict.fromkeys(issues))}
