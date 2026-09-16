"""Task identity requires a complete real field, not a document/header name."""
import copy

import pytest

from submission.b4_entry import B4Pipeline
from submission.pps.catalog_scope import whole_task_witnesses
from submission.pps.retrieval import Span
from submission.pps.source_units import unitize
from tests.test_catalog_scope_contract import DATA, response, setup
from tests.test_notice_search import record


def witness(text):
    return whole_task_witnesses(record(text), [Span(0, '공고문', 0, len(text), text)], [1])


@pytest.mark.parametrize('text', (
    '과 업 내 용 서', '과업내용서', 'Ⅰ | | 과업내용서',
    '과업내용: [공고명] 설계서', '용역명: [공고명] 제안요청서',
    '1.1 용 역 명 : [공고명]설계서',
    '○○물자(용역) 구매입찰공고서',
    '과업내용 관련', '가. 용역 내용 및 평가 관련', '○ 용역내용 등 기술관련사항',
    '과업 내용 및 범위', '과업 내용\n\n• 추진 배경',
    '사업명\n사업개요', '용역명\n구분', '용역내용\n기간',
    '1. 용역명 : 2026년도 [공고명]', 'o 공 고 명 : 2026년도 [공고명]',
    '건 명 용역내역 계약방법 용역기간',
    '사업명 | 참여기간 (년월～년월) | 담당업무 (구체적으로 기재) | 발주처 | 비고',
    '업체명 | 사업명 | 지정기관 | 지정일자 | 제한기간 (개월수) | 처분사유 (관련법령) | 비고',
    '안전보건관리 준수 서약서 사업명 | | 계약액 |',
    '사 업 명 | 위 치 | 사업내용 | 설계금액 | 기초금액 | 용역기간 (착수일로부터)',
    '용 역 개 요: 900톤', '용역량: 120건',
    '용역개요: 제안요청서 참조',
    '용 역 명\n용 역 개 요\n용 역 기 간\n연구 자료 분석\n1년',
    '입 찰 건 명 |\n우리는 위의 입찰에 공동수급체를 결성하며 조건을 승낙하고 합의각서를 제출합니다.',
    '※ 입찰금액과 인재개발원에 제출하는 가격제안서의 금액은 동일하여야 하며 불일치하는 경우 입찰가격을 인정합니다.',
    'Ⅱ. | 과업내용 | 10?과 업 내 용2',
    '용역 내용\n\n용역 배경',
))
def test_document_titles_columns_and_uninformative_extents_do_not_become_tasks(text):
    assert witness(text) == []


@pytest.mark.parametrize('field', ('용역개요', '용 역 개 요', '용역의 개요', '용역량', '과업개요'))
def test_explicit_extent_with_task_content_is_an_original_source_witness(field):
    text = f'다. {field}: 폐기물 수집·운반 및 처리(900톤)'
    assert any(item['text'] == text for item in witness(text))


@pytest.mark.parametrize('text', (
    '사업명: 과업내용서 작성 및 편집 용역',
    '과업내용: 규격서를 분석하고 제안요청서를 작성한다.',
    '용역개요: 120개소 현장자료 조사 및 연구보고서 작성',
    '사업명: 입찰금액과 가격제안서의 불일치 검증 시스템 개발',
    '과업명: 입찰 합의각서 작성 및 검토 용역',
))
def test_a_contract_to_prepare_documents_is_a_real_task(text):
    assert any(item['text'] == text for item in witness(text))


def test_extent_continuation_requires_every_source_word_without_reordering():
    text = '다. 용역량: 폐기물 처리(콘크리트 650톤, 혼합\n폐기물 190톤)\n라. 용역기간: 1년'
    rec = record(text)
    end = text.index('\n라.')
    units = unitize([Span(0, '공고문', 0, end, text[:end])])
    assert whole_task_witnesses(rec, units, [1]) == []
    assert whole_task_witnesses(rec, units, [1, 2])[0]['text'] == text[:end]
    value = text.index('폐기물')
    assert whole_task_witnesses(rec, [Span(0, '공고문', value, end, text[value:end])], [1]) == []


@pytest.mark.parametrize('text', (
    '용역개요: 폐기물 처리(콘크리트\n용역기간: 1년)',
    '용역개요: 폐기물 처리(콘크리트\n\n900톤)',
    '용역개요: 폐기물 수집 및\n다. 입찰참가자격',
    '용역개요: 폐기물 처리(900톤',
))
def test_an_unresolved_continuation_is_not_completed_from_another_field(text):
    assert witness(text) == []


def test_real_consumer_joins_extent_identity_and_qualification_without_model_repair():
    rec, packet, obj, _ = setup()
    original = rec['docs'][0]['text']
    old_title = original.splitlines()[0]
    extent = '다. 용역개요: 연구자료 분석 및 자문'
    rec['docs'][0]['text'] = original.replace(old_title, extent)
    packet['spans'] = [Span(0, '공고문', 0, len(extent), extent)]
    snapshot = copy.deepcopy((rec, packet, obj))
    row, log = B4Pipeline(DATA, None).consume(rec, packet, response(obj))
    assert row['v14'] == 1 and log['source_scope_promoted']
    assert log['product']['identity_evidence'][0]['text'] == extent
    assert (rec, packet, obj) == snapshot
