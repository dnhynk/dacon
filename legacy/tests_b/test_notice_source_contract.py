"""The search/read tool accepts JSON coordinates and validates before compute."""
import copy
import json

import pytest

from submission.pps.notice_search import NoticeSearch, merge_ranges
from tests.test_notice_search import CharacterTokenizer, record


def test_explicit_read_survives_json_serialization_without_losing_source_identity():
    rec = record('소스 인계 조건\n단, 개발 의무는 없다.')
    before = copy.deepcopy(rec)
    coordinates = [(0, 0, 8), (0, 9, len(rec['docs'][0]['text']))]
    search = NoticeSearch(rec, CharacterTokenizer())
    assert search.read(json.loads(json.dumps(coordinates)), token_budget=100) == search.read(coordinates, token_budget=100)
    assert rec == before


@pytest.mark.parametrize('policy', ['rrf', 'facet_cover', 'evidence_cover', 'evidence_refill'])
def test_json_required_coordinates_are_paid_and_kept_by_every_selector(policy):
    rec = record('소프트웨어 소스 인계', '별도 필수 자료')
    search = NoticeSearch(rec, CharacterTokenizer())
    result = search.search([20], method='lexical', token_budget=8,
        required_ranges=[[1, 0, 8]], selection_policy=policy)
    assert result['source_tokens'] == 8
    assert [(s['doc_index'], s['start'], s['end']) for s in result['spans']] == [(1, 0, 8)]


@pytest.mark.parametrize('invalid', [
    (False, 0, 3), (0, False, 3), (0, 0, 3.), ('0', 0, 3),
    (0, 0, True), (0, 0, 3, 4), (0, 0), None, 4, '003', {'doc': 0},
])
@pytest.mark.parametrize('invalid_first', [True, False])
def test_invalid_coordinate_cannot_hide_behind_an_equal_valid_coordinate(invalid, invalid_first):
    search = NoticeSearch(record('소프트웨어 소스 인계'), CharacterTokenizer())
    ranges = [invalid, (0, 0, 3)] if invalid_first else [(0, 0, 3), invalid]
    with pytest.raises(ValueError):
        merge_ranges(ranges, search.rec['docs'])


@pytest.mark.parametrize('budget', [None, True, False, 0, -1, 1., '1'])
def test_explicit_read_rejects_invalid_budget_before_tokenizing(budget):
    class NoToken:
        def encode(self, *args, **kwargs):
            pytest.fail('Invalid source budget reached tokenizer')
    search = NoticeSearch(record('소스'), NoToken())
    with pytest.raises(ValueError, match='budget'):
        search.read([[0, 0, 2]], token_budget=budget)


def test_invalid_required_coordinates_fail_before_any_embedding():
    class NoCall:
        def encode(self, texts):
            pytest.fail('Invalid source coordinates reached encoder')
    search = NoticeSearch(record('소프트웨어 소스 인계'), CharacterTokenizer(), NoCall())
    with pytest.raises(ValueError):
        search.search([20], method='hybrid', token_budget=100, required_ranges=[[0, 0, 3], [False, 0, 3]])
