"""Changing A scheduling must consume the same packets and preserve later phases."""
import dataclasses

import pytest

from submission.pps.prompts import Config
from submission.runtime import call_plan
from tests.test_submission_runtime import packets


@pytest.mark.parametrize('value',[0,-1,33,True,2.0])
def test_unsafe_or_ambiguous_cohort_size_rejected(value):
    with pytest.raises(ValueError):
        dataclasses.replace(Config(),a_cohort_size=value)
    with pytest.raises(ValueError):
        call_plan([],[],a_cohort_size=value)


def test_small_A_cohorts_preserve_input_order_and_all_later_profiles():
    records=[{'id':f'position-{50-i}'} for i in range(5)]
    source=packets(records)
    old=call_plan(records,source)
    small=call_plan(records,source,a_cohort_size=2)
    assert [b['profile'] for b in small]==['A1','A10','A19']*3+['L19']
    assert [len(b['request_keys']) for b in small]==[2]*6+[1]*3+[5]
    assert small[0]['request_keys']==old[0]['request_keys'][:2]
    assert small[1]['request_keys']==old[1]['request_keys'][:2]
    assert small[-1]['request_keys']==old[-1]['request_keys']
    all_keys=[k for b in small for k in b['request_keys']]
    assert len(all_keys)==len(set(all_keys))==len(source)
