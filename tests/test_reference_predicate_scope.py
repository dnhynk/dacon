"""A missing technical reference does not own a neighboring registration duty."""
import copy

import pytest

from submission.pps.reference_coverage import assess
from tests.test_comparison import record
from tests.test_reference_coverage import TEXT, facts


@pytest.mark.parametrize('documents', ['과업지시서', '제안요청서', '과업지시서 및 제안요청서'])
@pytest.mark.parametrize('registration', [
    '입찰참가자격을 해당 업종으로 등록한 자로서, ',
    '입찰참가자격을 해당 업종으로 등록한 업체이며, ',
])
def test_reference_to_task_capability_does_not_inherit_registration_property(documents, registration):
    text = TEXT + registration + documents + ' 내용에 따라 과업 수행이 가능한 업체.'
    rec = record(text)
    original = copy.deepcopy(rec)
    coverage = assess(rec)
    assert coverage['references'] and coverage['referenced_unavailable_docs']
    assert coverage['qualification_deferrals_resolved']
    assert not coverage['reference_contents_inferred']
    assert facts(rec)['no_size'] and facts(rec)['no_direct']
    for reference in coverage['references']:
        ev = reference['evidence']
        assert text[ev['start']:ev['end']] == ev['text']
    assert rec == original


@pytest.mark.parametrize('statement', [
    '입찰참가자격은 제안요청서에 따르며, 과업 수행이 가능한 업체이어야 한다.',
    '입찰참가자격을 해당 업종으로 등록한 자로서, 제안요청서에 정한 참가요건을 갖춘 업체.',
    '입찰참가자격 등록서류는 제안요청서 내용에 따라 제출하여야 한다.',
    '입찰참가자격은 제안요청서의 과업 수행능력 및 구비서류 요건에 따른다.',
    '입찰참가자격을 등록한 자로서, 제안요청서에 따른 서류 일체 각 1부',
    '입찰참가자격을 등록한 자로서, 과업지시서 내용에 따라 과업 수행이 가능하고 '
    '제안요청서의 입찰참가자격을 갖춘 업체.',
])
def test_actual_qualification_or_submission_deferral_cannot_be_downgraded(statement):
    rec = record(TEXT + statement)
    assert not assess(rec)['qualification_deferrals_resolved']
    assert not facts(rec)['no_size'] and not facts(rec)['no_direct']
