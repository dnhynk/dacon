"""Source context retains actual governing headings, with sibling boundaries."""
import pytest

from submission.pps.notice_search import NoticeSearch
from tests.test_notice_search import CharacterTokenizer, record


def expanded_target(text):
    search = NoticeSearch(record(text), CharacterTokenizer())
    target = next(i for i, span in enumerate(search.chunks) if '제조사의 기술지원확약서' in span.text)
    output = search.read(search._contexts[target], token_budget=3000)
    return '\n'.join(span['text'] for span in output['spans']), output


@pytest.mark.parametrize('outer', ['', 'Ⅱ. 계약 단계\n' + '서류는 담당자가 확인한다.\n' * 35])
def test_parent_submission_stage_survives_long_child_section(outer):
    text = outer + '3. 낙찰 후 계약 체결 시 제출서류\n' + '서류는 담당자가 확인한다.\n' * 35
    text += '3.1. 기술지원 서류\n' + '문서 보관 절차를 안내한다.\n' * 35
    text += '제조사의 기술지원확약서 1부를 제출하여야 한다.\n4. 기타사항'
    shown, output = expanded_target(text)
    assert '3. 낙찰 후 계약 체결 시 제출서류' in shown
    assert '3.1. 기술지원 서류' in shown
    if outer:
        assert 'Ⅱ. 계약 단계' in shown
    assert sum(len(s['text']) for s in output['spans']) == output['source_tokens']
    assert output['coverage']['absence_verified'] is False


def test_a_previous_sibling_stage_is_not_borrowed_as_an_ancestor():
    text = '3. 입찰 전에 제출할 서류\n' + '문서를 사전에 확인한다.\n' * 35
    text += '4. 낙찰 후 계약 체결 시 제출서류\n' + '서류는 담당자가 확인한다.\n' * 35
    text += '4.1. 기술지원 서류\n' + '문서 보관 절차를 안내한다.\n' * 35
    text += '제조사의 기술지원확약서 1부를 제출하여야 한다.'
    shown, _ = expanded_target(text)
    assert '4. 낙찰 후 계약 체결 시 제출서류' in shown
    assert '3. 입찰 전에 제출할 서류' not in shown


def test_orphan_decimal_section_does_not_borrow_a_different_numeric_parent():
    text = '2. 입찰 전 제출서류\n' + '문서를 사전에 확인한다.\n' * 35
    text += '3.1 기술지원 서류\n' + '문서 보관 절차를 안내한다.\n' * 35
    text += '제조사의 기술지원확약서 1부를 제출하여야 한다.'
    shown, _ = expanded_target(text)
    assert '3.1 기술지원 서류' in shown
    assert '2. 입찰 전 제출서류' not in shown
