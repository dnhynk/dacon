"""A complete system-formatted notice title grounds the Q10 whole-task anchor.

The consumer-only title cells must not change discovery or prompt field hints,
and a title mentioned inside a sentence, a masked title, a generic title or an
administrative line still leaves the anchor unresolved.
"""
import pytest

from submission.pps.catalog_scope import ITEMS, review, service_catalog
from submission.pps.knowledge import Knowledge
from submission.pps.retrieval import Span
from submission.pps.task_scope import candidate_fields, notice_title_fields
from tests.test_catalog_scope_contract import DATA, response
from tests.test_comparison import record


ELIGIBILITY = '\n2. 입찰참가자격\n가. 중소기업 확인서를 소지한 업체이어야 한다.\n3. 계약조건\n'
COLUMNS = ('1. 입찰에 부치는 사항\n\n용 역 명\n\n용 역 개 요\n\n용 역 기 간\n\n용 역 금 액(원)\n\n'
           '기초자치단체 생태하천 임목폐기물 처리 용역(제한경쟁·3억원미만)\n\n'
           '용역설명서 및 과업지시서 참조\n\n착수일로부터 2026.12.18.까지\n')
ROWS = ('1. 입찰에 부치는 사항\n용 역 명 | 용 역 개 요 | 용 역 기 간 | 용 역 금 액(원)\n'
        '공공시설 고분군 발굴조사 현장인부 공급용역(제한경쟁·3억원미만) | 과업내용서 참조 | 계약일 ~ 2026. 12. 10.\n')


def setup(notice, cited):
    rec = record(notice + ELIGIBILITY, 업무구분='일반용역', 입찰추정가격=300_000_000,
                 배정예산금액=330_000_000)
    rec['docs'][0]['doc_id'] = 'D0'
    text = rec['docs'][0]['text']
    start = text.index(cited)
    spans = [Span(0, '공고문', start, start + len(cited), cited)]
    k = Knowledge(DATA)
    packet = {'spans': spans, 'catalog_scope': {'catalog': service_catalog(k.products),
        'all_supplied_service_rows_present': True}, 'items': list(ITEMS), 'family': 'Q',
        'generation': {'response_format': 'catalog_scope'}}
    obj = {'purchase_kind': 'service', 'whole_task_units': [1], 'task_summary': '폐기물을 처리한다.',
        'catalog_relation': 'outside_all_listed_service_categories', 'relationships': [],
        'unresolved_scope': False}
    return rec, packet, obj, k


@pytest.mark.parametrize('notice,cited', [
    (COLUMNS, '기초자치단체 생태하천 임목폐기물 처리 용역(제한경쟁·3억원미만)'),
    (ROWS, '공공시설 고분군 발굴조사 현장인부 공급용역(제한경쟁·3억원미만) | 과업내용서 참조 | 계약일 ~ 2026. 12. 10.'),
])
def test_unlabeled_table_title_cell_grounds_the_cited_whole_task(notice, cited):
    rec, packet, obj, k = setup(notice, cited)
    result, log = review(rec, response(obj), packet, k)
    assert log['task_anchor_grounding'] == 'complete_notice_title_cell'
    assert log['product']['status'] == 'general' and result['v14'] == 1
    title = cited.split(' |')[0]
    assert log['product']['identity_evidence'][0]['text'] == title


@pytest.mark.parametrize('notice,cited', [
    ('※ 본 공고는 생태하천 임목폐기물 처리 용역(제한경쟁·3억원미만)에 관한 안내입니다.\n',
     '※ 본 공고는 생태하천 임목폐기물 처리 용역(제한경쟁·3억원미만)에 관한 안내입니다.'),
    ('1. 입찰에 부치는 사항\n\n[공고명](제한경쟁·3억원미만)\n', '[공고명](제한경쟁·3억원미만)'),
    ('1. 입찰에 부치는 사항\n\n[수요기관(기초자치단체)] 용역(제한경쟁·3억원미만)\n',
     '[수요기관(기초자치단체)] 용역(제한경쟁·3억원미만)'),
    ('1. 입찰에 부치는 사항\n\n과업지시서 참조(제한경쟁·3억원미만)\n', '과업지시서 참조(제한경쟁·3억원미만)'),
    ('나. 최근 3년 이내 폐기물 처리 실적을 보유한 업체\n', '나. 최근 3년 이내 폐기물 처리 실적을 보유한 업체'),
])
def test_mentions_masks_generic_titles_and_administration_do_not_ground(notice, cited):
    rec, packet, obj, k = setup(notice, cited)
    result, log = review(rec, response(obj), packet, k)
    assert result is None and log['gate'] == 'no_original_whole_task_anchor'
    assert 'task_anchor_grounding' not in log


def test_a_labeled_title_field_keeps_the_existing_anchor():
    rec, packet, obj, k = setup('1. 용역명: 생태하천 임목폐기물 처리 용역(제한경쟁·3억원미만)\n',
                                '1. 용역명: 생태하천 임목폐기물 처리 용역(제한경쟁·3억원미만)')
    result, log = review(rec, response(obj), packet, k)
    assert result['v14'] == 1 and 'task_anchor_grounding' not in log


def test_title_cells_are_consumer_only_and_leave_discovery_unchanged():
    rec, _, _, _ = setup(COLUMNS, '기초자치단체 생태하천 임목폐기물 처리 용역(제한경쟁·3억원미만)')
    titles = notice_title_fields(rec)
    assert [t['text'] for t in titles] == ['기초자치단체 생태하천 임목폐기물 처리 용역(제한경쟁·3억원미만)']
    assert not any(field['start'] <= titles[0]['start'] and titles[0]['end'] <= field['end']
                   for field in candidate_fields(rec))
