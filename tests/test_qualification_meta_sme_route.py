"""A registered SME route supports only a resolved missing-size check."""
import copy

import pytest

from submission.pps.qualification import infer
from submission.pps.knowledge import Knowledge
from tests.test_service_identity import DATA


ROUTES = [
    '[판로지원법 시행령] 소기업,소상공인제한',
    '[판로지원법 시행령] 중기업,소기업,소상공인 제한',
    '추정가격 1억원 미만 물품·용역(소기업, 소상공인, 벤처기업, 창업자)',
    '추정가격 1억원이상 고시금액 미만 물품·용역(중소기업자)',
]


def notice(estimate=80_000_000, route=ROUTES[0], clause='가. 사업자등록을 한 업체'):
    return {'id': 'synthetic-meta-priority-route',
        'meta': {'업무구분': '일반용역', '적용계약법': '국가계약법',
                 '계약방법': '제한경쟁', '입찰추정가격': estimate, '조항호내용': route},
        'docs': [{'type': '공고문', 'doc_id': 'notice', 'text':
            '1. 사업개요\n사업명: 미확정업무\n3. 입찰참가자격\n'+clause+
            '\n5. 계약조건\n기한 준수'}],
        'input_completeness': {'완전관측': True}, 'dropped_doc_counts': {}}


def decide(record, state=None):
    knowledge = Knowledge(DATA)
    knowledge.detailed_product_facts(record)
    baseline = {**{f'v{i}': '0' for i in range(1, 25)},
                **{f'e{i}': '' for i in range(1, 25)}}
    if state is None:
        return infer(record, baseline, knowledge._product_facts)
    _, facts = infer(record, baseline, knowledge._product_facts)
    return infer(record, baseline, knowledge._product_facts,
                 product_override={**facts['product'], 'status': state})


@pytest.mark.parametrize('route', ROUTES)
@pytest.mark.parametrize('estimate,item', [(20_000_001, 18), (99_999_999, 18),
                                         (100_000_000, 16), (229_999_999, 16)])
def test_unknown_route_checks_only_missing_size(route, estimate, item):
    record = notice(estimate, route)
    original = copy.deepcopy(record)
    row, facts = decide(record)
    control = copy.deepcopy(record)
    control['meta'].pop('조항호내용')
    control_row, _ = decide(control)
    assert facts['product']['status'] == 'unknown'
    assert facts['qualification']['no_size']
    assert facts['decisions'][f'v{item}']['reason'] == 'registered_SME_priority_route_complete_no_size_requirement'
    assert row[f'v{item}'] == '1' and row[f'e{item}'] == ''
    assert {k:v for k,v in row.items() if k != f'v{item}'} == {
        k:v for k,v in control_row.items() if k != f'v{item}'}
    assert record == original


@pytest.mark.parametrize('route', [None, '', '일반경쟁',
    '[판로지원법] 중기간경쟁제품 소기업 소상공인 제한경쟁, 공동사업',
    '[판로지원법 시행령] 소기업,소상공인제한 예외',
    '추정가격 1억원 미만 공사(소기업, 소상공인)'])
def test_other_metadata_route_does_not_fire(route):
    _, facts = decide(notice(route=route))
    assert not facts['qualification']['meta_sme_priority_route']
    assert 'v16' not in facts['decisions'] and 'v18' not in facts['decisions']


@pytest.mark.parametrize('estimate', [None, 20_000_000, 230_000_000, 300_000_000])
def test_unconfirmed_or_outside_price_band_abstains(estimate):
    _, facts = decide(notice(estimate))
    assert 'v16' not in facts['decisions'] and 'v18' not in facts['decisions']


def test_competition_identity_stays_excluded():
    row, facts = decide(notice(), 'competition')
    assert row['v18'] == '0'
    assert facts['decisions']['v18']['reason'] == 'identified_purchase_in_conditional_catalog'


def test_actual_small_quote_abstains():
    record = notice(clause='가. 사업자등록을 한 업체\n본 공고는 소액수의 견적제출 안내입니다.')
    record['meta']['계약방법'] = '수의계약'
    _, facts = decide(record)
    assert facts['qualification']['quote_evidence']
    assert 'v18' not in facts['decisions']


@pytest.mark.parametrize('change', ['incomplete', 'unclosed', 'size', 'exception'])
def test_existing_absence_and_exception_guards_remain(change):
    record = notice()
    if change == 'incomplete':
        record['input_completeness']['완전관측'] = False
        record['dropped_doc_counts'] = {'공고문': 1}
    elif change == 'unclosed':
        record['docs'][0]['text'] = record['docs'][0]['text'].split('5. 계약조건')[0]
    elif change == 'size':
        record = notice(clause='가. 소기업·소상공인확인서를 소지한 업체')
    else:
        record = notice(clause='가. 사업자등록을 한 업체\n비영리법인은 입찰에 참여할 수 있습니다.')
    row, facts = decide(record)
    assert row['v18'] == '0'
    if change == 'exception':
        assert facts['qualification']['exceptions']


@pytest.mark.parametrize('heading', ['※ 제출서류: 사업자등록증, 기술자격증',
                                     '※ 실적 평가항목 및 배점 안내'])
def test_misclassified_holder_clause_blocks_only_new_route(heading):
    record = notice(clause='가. 사업자등록을 한 업체\n'+heading+
                    '\n○ 중·소기업·소상공인 확인서를 소지한 업체')
    row, facts = decide(record)
    assert facts['qualification']['no_size']
    assert row['v18'] == '0'
    assert facts['deferred_decisions']['v18']['reason'] == 'meta_priority_route_size_mentions_require_review'


@pytest.mark.parametrize('clause', ['제출서류\n소기업·소상공인 확인서 1부',
                                  '6) 중소기업 및 소상공인 확인 서류 1부(해당시)',
                                  '실적평가\n소기업, 소상공인은 신용평가 배점한도를 부여한다.'])
def test_bare_document_list_or_scoring_is_not_a_holder_predicate(clause):
    record = notice(clause='가. 사업자등록을 한 업체\n'+clause)
    row, _ = decide(record)
    assert row['v18'] == '1'
