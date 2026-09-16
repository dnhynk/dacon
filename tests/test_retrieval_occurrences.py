"""Equal text at different source addresses can govern different products."""
import copy

from submission.pps.retrieval import NoticeIndex
from tests.test_notice_search import record


def includes(spans, doc_index, start, end):
    cursor = start
    for s in spans:
        if s.doc_index == doc_index and s.start <= cursor < s.end:
            cursor = s.end
    return cursor >= end


def test_identical_permissions_under_distinct_products_survive_a_generous_budget():
    permission = '동등 이상의 제품으로 납품할 수 있으며 대체가 가능하다.'
    text = f'1. 첫 번째 장비\n모델명: Alpha A100\n{permission}\n2. 두 번째 장비\n모델명: Beta B200\n{permission}'
    rec = record(text)
    before = copy.deepcopy(rec)
    selected = NoticeIndex(rec).select(10000, mode='evidence_first')
    first, second = text.index(permission), text.rindex(permission)
    assert includes(selected, 0, first, first+len(permission))
    assert includes(selected, 0, second, second+len(permission))
    assert rec == before


def test_identical_condition_text_is_not_marked_represented_at_an_unread_address():
    clause = '납품 물품은 동등 이상 제품으로 대체 가능하다.'
    text = ('1. 첫 장비\n'+clause+'\n'+'배경 원문 자료\n'*90+'2. 다른 장비\n'+clause)
    index = NoticeIndex(record(text))
    _, groups = index._operative_candidates()
    positions = {c.start for group in groups for c in group if text[c.start:c.end] == clause}
    group_for = {c.start: n for n, group in enumerate(groups) for c in group if c.start in positions}
    assert len(positions) == 2 and len(set(group_for.values())) == 2


def test_repeated_table_headers_and_background_values_keep_original_positions():
    text = '장비 A\n품명 | 수량\n장치가 | 2\n장비 B\n품명 | 수량\n장치나 | 2'
    spans = NoticeIndex(record(text)).select(10000, mode='evidence_first')
    for needle in ('품명 | 수량',):
        for start in (text.index(needle), text.rindex(needle)):
            assert includes(spans, 0, start, start+len(needle))


def test_budgeted_occurrences_preserve_their_own_exceptions_and_original_text():
    text = ('1. 물품\n제품은 동등 이상으로 납품한다.\n단, 연결 규격은 변경할 수 없다.\n'*12)
    rec = record(text)
    selected = NoticeIndex(rec).select(500, mode='evidence_first')
    assert selected.diagnostics['charged_characters'] <= 500
    for span in selected:
        assert span.text == text[span.start:span.end]
    assert selected.diagnostics['unshown_candidates'] > 0
