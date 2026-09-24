"""Only explicit uncertainty about the necessary fact is a review trigger."""
import json

import pytest

from tools.audit_fact_consistency import PRODUCT_FIELD, review, uncertain_statement


@pytest.mark.parametrize('text,kind', [
    ('디자인서비스가 후보이나 구매대상 일치 여부는 불명확함.', 'catalog'),
    ('디자인서비스 등 경쟁제품에 해당할 수 있음.', 'catalog'),
    ('경쟁제품 해당 여부가 확인되지 않음.', 'catalog'),
    ('SW사업 여부가 불명확하여 대기업 참여제한 안내를 판단할 근거가 없음.', 'software'),
    ('소프트웨어 사업 여부는 불분명하며, 하한제도 안내는 없음.', 'software'),
])
def test_explicit_applicability_uncertainty_is_visible(text, kind):
    assert uncertain_statement(text, kind)


@pytest.mark.parametrize('text,kind', [
    ('경쟁제품인 디자인서비스이며, 업체 실적은 불명확함.', 'catalog'),
    ('경쟁제품 코드가 공고 제목에 명시되지 않음.', 'catalog'),
    ('경쟁제품에 해당하며 직접생산확인서는 요구하지 않음.', 'catalog'),
    ('경쟁제품 해당 여부는 불명확하지 않음.', 'catalog'),
    ('일부는 경쟁제품이며 나머지 구매대상 일치 여부는 불명확함.', 'catalog'),
    ('SW사업이며 하한제도 안내 여부가 불명확함.', 'software'),
    ('SW사업 여부는 불분명하지 않음.', 'software'),
    ('소프트웨어를 개발하는 부분이 있으면 협의한다.', 'software'),
    ('예시: 경쟁제품에 해당할 수 있음.', 'catalog'),
    ('가정: SW사업 여부가 불명확함.', 'software'),
    ('SW사업 여부가 불분명하며, 그러나 실제 개발 의무는 확인됨.', 'software'),
])
def test_title_absence_other_uncertainty_or_negated_unknown_is_not_the_trigger(text, kind):
    assert uncertain_statement(text, kind) is None


def test_source_unknown_without_the_models_own_uncertainty_never_flags():
    assert not review({PRODUCT_FIELD: '디자인서비스에 해당함.'}, {10: 1},
                      product={'status': 'unknown', 'uncertainty': []})


@pytest.mark.parametrize('product', [
    {'status': 'competition', 'uncertainty': []},
    {'status': 'general', 'uncertainty': []},
    {'status': 'unknown', 'uncertainty': ['mixed_or_differently_conditioned_purchase_candidates']},
    {'status': 'unknown', 'uncertainty': [], 'products': [{'listed': True, 'condition': {'status': 'met'}}]},
])
def test_independent_classification_or_mixed_scope_keeps_its_own_review(product):
    assert not review({PRODUCT_FIELD: '구매대상 일치 여부는 불명확함.'}, {10: 1}, product=product)


def test_known_source_sw_work_does_not_get_erased_by_uncertain_model_prose():
    facts = {'하한제도_적용근거_안내의실제존재_미확정정보': 'SW사업 여부가 불명확함.'}
    assert not review(facts, {20: 1}, sw={'value': None, 'reason': 'exception_unresolved',
                                       'facts': {'actual_work': [{'quote': 'source work'}]}})


def test_only_positive_relevant_items_are_reviewed_and_unknown_is_preserved():
    flags = review({PRODUCT_FIELD: '경쟁제품에 해당할 수 있음.'}, {10: 1, 11: 0, 13: 1, 14: 1},
                   product={'status': 'unknown', 'uncertainty': []})
    assert [f['item'] for f in flags] == [10, 13]
    assert all(f['semantic_value'] is None for f in flags)


def candidate_product(*, status='unknown', uncertainty=None, eligible=True):
    return {
        'status': status,
        'catalog_scope': 'supplied_catalog_only',
        'uncertainty': list(uncertainty or ['event_component_does_not_establish_whole_purchase']),
        'products': [{
            'code': '9015189001',
            'name': '축제기획및대행서비스',
            'listed': eligible,
            'condition': {'status': 'met' if eligible else 'unlisted'},
        }],
    }


def test_same_response_cannot_select_competition_and_general_product_branches():
    from submission.pps.fact_consistency import apply
    response = factored_response(
        tuple(range(10, 19)), PRODUCT_FIELD,
        '거리공연 행사 운영 전반(행사기획및대행서비스 후보군에 해당하며 고시조건 충족)',
        {10, 11, 16})
    row = {f'v{i}': int(i in {10, 11, 16}) for i in range(10, 19)}
    row.update({f'e{i}': 'evidence' if i == 16 else '' for i in range(10, 19)})
    result, details = apply(row, response, {i: int(i in {10, 11, 16}) for i in range(10, 19)},
                            product=candidate_product())
    assert result['v10'] == result['v11'] == 1
    assert result['v16'] == 0 and result['e16'] == ''
    assert [d['item'] for d in details] == [16]
    assert details[0]['reason'] == 'same_model_response_selects_incompatible_product_branches'
    assert details[0]['normality_certified'] is False


@pytest.mark.parametrize('text,product', [
    ('행사기획및대행서비스 구성품이 후보군에 해당함.', candidate_product()),
    ('행사기획및대행서비스 후보군에 해당함.', candidate_product(eligible=False)),
    ('행사기획및대행서비스 후보군에 해당함.',
     candidate_product(uncertainty=['explicit_multiple_items_not_all_identified'])),
    ('행사기획및대행서비스 후보군에 해당함.', candidate_product(status='general')),
    ('경쟁제품이 아닌 일반용역에 해당함.', candidate_product()),
])
def test_branch_consistency_guard_does_not_choose_across_ambiguous_boundaries(text, product):
    flags = review({PRODUCT_FIELD: text}, {10: 1, 16: 1}, product=product)
    assert not [f for f in flags if f.get('reason') ==
                'same_model_response_selects_incompatible_product_branches']


def test_branch_consistency_guard_leaves_an_already_consistent_response_unchanged():
    flags = review(
        {PRODUCT_FIELD: '행사기획및대행서비스 후보군에 해당함.'},
        {10: 1, 11: 1, 16: 0}, product=candidate_product())
    assert not [f for f in flags if f.get('reason') ==
                'same_model_response_selects_incompatible_product_branches']


def factored_response(items, field, text, positives):
    from submission.pps.prompts import fact_fields
    facts = {k: '해당 사항 확인 불가' for k in fact_fields(items)}
    facts[field] = text
    return {'text': json.dumps({'facts': facts, 'judgments': {
        f'v{k}': {'reason': '원문을 기준으로 판단함.', 'v': int(k in positives), 'e': 0} for k in items}}, ensure_ascii=False),
        'finish_reason': 'stop'}


def test_qualification_consumer_records_unknown_without_certifying_general_scope():
    from submission.pps.knowledge import Knowledge
    from submission.pps.pipeline import _response_row
    from submission.pps.prompts import Config
    from tests.test_service_identity import DATA
    from tests.test_software_facts import source
    rec, spans = source('용역명: 생활환경 현황 조사\n1. 입찰참가자격\n관련 업종으로 등록한 업체\n2. 계약기간')
    rec['meta'].update({'업무구분': '일반용역', '적용계약법': '국가계약법', '입찰추정가격': 180000000})
    items = tuple(range(10, 19))
    response = factored_response(items, PRODUCT_FIELD, '경쟁제품 해당 여부는 불명확함.', {10, 11})
    raw_text = response['text']
    row, details = _response_row(rec, response, {'spans': spans}, items,
        Config(qualification_checks=True, require_positive_evidence=False), Knowledge(DATA), items)
    assert row['v10'] == row['v11'] == 0
    source_facts = next(d['facts'] for d in details if d['source'] == 'supplied_catalog_qualification_v2')
    assert source_facts['product']['status'] == 'unknown'
    reviews = next(d['details'] for d in details if d['source'] == 'model_applicability_consistency')
    assert len(reviews) == 2 and all(r['applied'] and r['semantic_value'] is None for r in reviews)
    assert all(not r['normality_certified'] for r in reviews)
    assert response['text'] == raw_text


@pytest.mark.parametrize('source_text,expected', [
    ('부품 소스를 인계한다.', 0),
    ('본 사업은 소프트웨어 사업이다.', 1),
])
@pytest.mark.parametrize('enable_thinking', [False, True])
def test_uniform_fourth_call_preserves_an_independent_source_proof(source_text, expected, enable_thinking):
    from submission.pps.prompts import Config
    from submission.pps.v20_route import UniformV20Route
    from tests.test_service_identity import DATA
    from tests.test_software_facts import source
    rec, spans = source(source_text)
    route = UniformV20Route(DATA, None, audited_config=Config(input_strategy='audited', enable_thinking=enable_thinking))
    response = factored_response((20,), '하한제도_적용근거_안내의실제존재_미확정정보',
                                 'SW사업 여부가 불분명하며, 하한제도 안내는 없음.', {20})
    raw_text = response['text']
    row, details = route.consume(rec, response, {'items': (20,), 'spans': spans})
    assert row == {'v20': expected, 'e20': ''}
    reviews = [d for d in details if d['source'] == 'model_applicability_consistency']
    if expected:
        assert not reviews and details[0]['decision']['value'] == 1
    else:
        assert reviews[0]['details'][0]['semantic_value'] is None
        assert reviews[0]['details'][0]['applied']
    assert response['text'] == raw_text


def test_uniform_fourth_call_cannot_clear_A_when_only_absence_is_unobservable():
    from submission.pps.prompts import Config
    from submission.pps.v20_route import UniformV20Route
    from tests.test_service_identity import DATA
    from tests.test_software_facts import source
    rec, spans = source('본 사업은 소프트웨어 사업이다.', complete=False)
    rec['dropped_doc_counts'] = {'제안요청서': 1}
    route = UniformV20Route(DATA, None, audited_config=Config(input_strategy='audited'))
    response = factored_response((20,), '하한제도_적용근거_안내의실제존재_미확정정보',
                                 '누락 문서의 하한제도 안내 여부는 확인할 수 없음.', set())
    row, details = route.consume(rec, response, {'items': (20,), 'spans': spans})
    assert row == {}
    assert details[0]['decision']['reason'] == 'missing_documents_prevent_absence_conclusion'
    assert details[1]['source'] == 'deferred_negative_preserves_independent_A'
