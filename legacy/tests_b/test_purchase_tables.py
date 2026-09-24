"""Reading structural evidence never certifies purchase identity."""
import pytest

from submission.pps.purchase_tables import purchase_tables
from submission.pps.products import scope_spans
from submission.pps.notice_search import NoticeSearch


class Characters:
    def encode(self, text, add_special_tokens=False):
        return list(text)


def record(text):
    return {'id': 'synthetic', 'meta': {}, 'docs': [{'doc_id': 'D0', 'type': '규격서', 'text': text}]}


@pytest.mark.parametrize('layout', ['품 명 | 규 격 | 수 량\n온도계 | 실외용 | 2\n압력계 | 2MPa | 1',
    '품 명\n\n규 격\n\n수 량\n\n온도계\n\n실외용\n\n2\n\n압력계\n\n2MPa\n\n1'])
def test_reading_range_preserves_every_original_character_and_stops_at_the_next_section(layout):
    text = '1. 물품 규격\n' + layout + '\n2. 납품조건\n부속품을 포함한다.'
    tables = purchase_tables(text)
    assert len(tables) == 1
    table = tables[0]
    assert table['text'] == text[table['start']:table['end']] == layout
    assert text[slice(*table['parent_range'])] == '1. 물품 규격'
    assert not table['cells_reconstructed'] and not table['whole_purchase_certified']


def test_repeated_tables_and_identical_item_names_keep_separate_source_locations():
    text = '품명 | 규격\n장치 | A\n품명 | 규격\n장치 | B'
    a, b = purchase_tables(text)
    assert a['end'] < b['start'] and a['text'].endswith('A') and b['text'].endswith('B')


@pytest.mark.parametrize('text', ['품명\n\n수량', '품명 | 규격 | 수량',
    '이 장치는 품명과 규격을 표시하여야 한다.', '품명\n\n영문\nTemperature Sensor'])
def test_labels_and_prose_alone_are_not_populated_purchase_tables(text):
    assert purchase_tables(text) == []


@pytest.mark.parametrize('label', ['단 위', '수 량', '영 문', '국 문'])
def test_vertical_column_label_is_not_an_actual_purchase_name(label):
    assert not scope_spans(record('품 명\n\n'+label))
    assert scope_spans(record('품 명\n\n온도측정장치'))


def test_atomic_context_is_optional_and_all_source_text_counts_against_the_cap():
    text = '1. 구매 내역\n품명 | 규격\n'+'\n'.join('측정장치'+str(i)+' | 실외용' for i in range(60))
    text += '\n2. 납품조건\n설명서도 납품한다.'
    rec = record(text)
    tool = NoticeSearch(rec, Characters(), table_context='atomic_purchase')
    table = tool.purchase_tables[0][0]
    assert any(any(lo <= table['start'] and hi >= table['end'] for _, lo, hi in ranges)
               for ranges in tool._contexts)
    result = tool.search([9], method='lexical', token_budget=220, queries=['측정장치'])
    assert result['source_tokens'] <= 220
    assert result['diagnostics']['table_context'] == 'atomic_purchase'
    with pytest.raises(ValueError):
        NoticeSearch(rec, Characters(), table_context='invent_cells')
