"""Explicit list numbering keeps the same parents with or without spaces."""
import pytest

from submission.pps.notice_search import NoticeSearch, is_heading
from tests.test_notice_search import CharacterTokenizer, record


def context(text, policy='ancestors'):
    search = NoticeSearch(record(text), CharacterTokenizer(), heading_context=policy)
    target = next(i for i, s in enumerate(search.chunks) if '제조사의 기술지원확약서' in s.text)
    result = search.read(search._contexts[target], token_budget=3000)
    return '\n'.join(s['text'] for s in result['spans']), result


@pytest.mark.parametrize('parent,child', [
    ('3.낙찰 후 계약 체결 시 제출서류', '3.1.기술지원 서류'),
    ('3. 낙찰 후 계약 체결 시 제출서류', '3.1.기술지원 서류'),
    ('3.낙찰 후 계약 체결 시 제출서류', '3.1. 기술지원 서류'),
    ('3.낙찰 후 계약 체결 시 제출서류', '가.기술지원 서류'),
    ('3.낙찰 후 계약 체결 시 제출서류', '1)기술지원 서류'),
])
def test_compact_numbering_preserves_governing_stage(parent, child):
    text = parent + '\n' + '문서 보관 절차를 안내한다.\n' * 35
    text += child + '\n' + '문서 보관 절차를 안내한다.\n' * 35
    text += '제조사의 기술지원확약서 1부를 제출하여야 한다.'
    shown, result = context(text)
    assert parent in shown
    assert child in shown
    assert result['source_tokens'] == sum(len(s['text']) for s in result['spans'])
    assert not result['coverage']['absence_verified']


@pytest.mark.parametrize('line', [
    '3.14억원', '3.14 억원', '2.5%', '2.5 %', '1.2mm', '1.2 mm',
    '2.4GHz', '2.4 GHz', '1.5V', '1.5 V', '2.3세트', '2.3 세트',
    '2026.4.15', '2026. 4. 15.', '3.14', '3.14.억원',
    '2.5 억 원', '2.5 억', '2.5 이상', '2.5 이하',
    '2026.4.15 공고일', '2026.4.15. 공고일', '2026.4 납품 월',
])
def test_quantities_dates_and_decimals_are_not_numbered_headings(line):
    assert not is_heading(line)


@pytest.mark.parametrize('line', [
    '3.제출서류', '3.1.제출서류', '3.1 제출서류', '3)제출서류',
    '가.제출서류', 'Ⅱ. 납품조건', '제3장 과업 범위',
    '1. 명 칭 : 사업명', '3. 대 표 자 성 명 :',
    '1)2억원 이상의 금액에 대한 보증', '1. 2억원 이상의 금액에 대한 보증',
    '3. 세트 구성',
    '3.資料提出', '3.1 資料提出', '3)Λογισμικό',
])
def test_explicit_section_markers_remain_supported(line):
    assert is_heading(line)


def test_compact_sibling_and_orphan_sections_do_not_borrow_earlier_stage():
    text = '2.입찰 전 제출서류\n' + '문서 보관 절차를 안내한다.\n' * 35
    text += '3.1.기술지원 서류\n' + '문서 보관 절차를 안내한다.\n' * 35
    text += '제조사의 기술지원확약서 1부를 제출하여야 한다.'
    shown, _ = context(text)
    assert '3.1.기술지원 서류' in shown
    assert '2.입찰 전 제출서류' not in shown


def test_decimal_quantity_cannot_reset_a_proven_governing_parent():
    text = '3. 낙찰 후 계약 체결 시 제출서류\n' + '문서 보관 절차를 안내한다.\n' * 35
    text += '2.5 GHz\n' + '문서 보관 절차를 안내한다.\n' * 35
    text += '제조사의 기술지원확약서 1부를 제출하여야 한다.'
    shown, _ = context(text)
    assert '3. 낙찰 후 계약 체결 시 제출서류' in shown
    assert '2.5 GHz' not in shown


def test_legacy_control_explicitly_retains_old_source_selection():
    parent = '3. 낙찰 후 계약 체결 시 제출서류'
    child = '3.1.기술지원 서류'
    text = parent + '\n' + '문서 보관 절차를 안내한다.\n' * 35
    text += child + '\n' + '문서 보관 절차를 안내한다.\n' * 35
    text += '제조사의 기술지원확약서 1부를 제출하여야 한다.'
    shown, _ = context(text, 'legacy_ancestors')
    assert child in shown and parent not in shown
    assert is_heading('2.5 GHz', compact_numbering=False)


@pytest.mark.parametrize('earlier,later', [
    ('3. 입찰 전 제출서류', '4. 낙찰 후 계약 체결 시 다음 서류를 제출해야 한다.'),
    ('3.입찰 전 제출서류', '4.낙찰 후 계약 체결 시 다음 서류를 제출해야 한다.'),
    ('가. 입찰 전 제출서류', '나. 낙찰 후 계약 체결 시 다음 서류를 제출해야 한다.'),
    ('3. 입찰 전 제출서류', '4. 낙찰 후 계약 체결 시 ' + '계약상대자가 확인한 ' * 12 + '서류를 제출해야 한다.'),
])
def test_numbered_operative_sentence_closes_the_previous_sibling(earlier, later):
    text = earlier + '\n' + '문서 보관 절차를 안내한다.\n' * 35
    text += later + '\n' + '문서 보관 절차를 안내한다.\n' * 35
    text += '제조사의 기술지원확약서 1부를 제출하여야 한다.'
    shown, _ = context(text)
    assert later in shown
    assert earlier not in shown
