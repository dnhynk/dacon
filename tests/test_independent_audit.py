"""Adversarial contracts exercised through the actual canonical consumer."""
import json
from pathlib import Path

import pytest

from submission.b4_entry import B4Pipeline
from submission.pps.pipeline import parse_output
from submission.pps.prompts import fact_fields

DATA = Path(__file__).resolve().parents[1] / 'data_open/data'


@pytest.mark.parametrize('raw', [
    '{"v":[1],"v":[0],"e":[0]}',
    '{"v1":{"reason":"a","v":1,"v":0,"e":0}}',
])
def test_duplicate_json_judgments_are_ambiguous_not_last_key_wins(raw):
    with pytest.raises(ValueError, match='Duplicate'):
        parse_output(raw, [], (1,))


@pytest.mark.parametrize('raw_value', [0, 1])
@pytest.mark.parametrize('text,expected', [
    ('본 사업은 소프트웨어 사업이 아닙니다.', 0),
    ('본 사업은 소프트웨어 사업이며 이 분류는 철회합니다.', None),
    ('본 사업은 소프트웨어 사업이다.', 1),
])
def test_final_l20_uses_current_scope_and_withdrawal_guards(text, expected, raw_value):
    record = {'id': 'synthetic-final-route',
              'meta': {'적용계약법': '국가계약법', '업무구분': '일반용역', '소관구분': '국가기관'},
              'docs': [{'type': '공고문', 'text': text}],
              'input_completeness': {'완전관측': True}, 'dropped_doc_counts': {}}
    items = tuple(range(19, 25))
    response = {'finish_reason': 'stop', 'text': json.dumps({
        'facts': dict.fromkeys(fact_fields(items), '원문 확인'),
        'judgments': {f'v{k}': {'reason': '원문 확인', 'v': raw_value if k == 20 else 0, 'e': 0}
                      for k in items}}, ensure_ascii=False)}
    packet = {'family': 'L', 'items': list(items), 'spans': [],
              'generation': {'response_format': 'factored'}}
    result, _ = B4Pipeline(DATA, None).consume(record, packet, response)
    assert set(result) == {'v20', 'e20'}
    assert result['v20'] == (raw_value if expected is None else expected)


def test_requested_items_cannot_be_duplicated_or_outside_contract():
    for items in ((0,), (25,), (1, 1), (True,)):
        with pytest.raises(ValueError):
            parse_output(json.dumps({'v': [0] * len(items), 'e': [0] * len(items)}), [], items)


def synthetic_notice(text, **meta):
    return {'meta': {'적용계약법': '국가계약법', '소관구분': '국가기관',
                     '업무구분': '일반용역', '계약방법': '제한경쟁',
                     '입찰추정가격': 100_000_000, '배정예산금액': 110_000_000, **meta},
            'docs': [{'type': '공고문', 'text': text}],
            'input_completeness': {'완전관측': True}, 'dropped_doc_counts': {}}


def test_unlisted_unbound_code_cannot_invent_general_product_obligations():
    from submission.pps.products import ProductFacts
    from submission.pps.qualification import infer
    pf = ProductFacts(DATA / '법령패키지/중기부고시/중기부고시_경쟁제품_세부품명.csv')
    rec = synthetic_notice('1. 사업개요\n2. 입찰참가자격\n가. 중소기업확인서를 소지한 업체\n3. 제출서류',
                           입찰추정가격=50_000_000, 세부품명번호목록='분류미상: 9999999999')
    result, facts = infer(rec, {'v17': '0', 'e17': ''}, pf)
    assert facts['product']['status'] == 'unknown'
    assert 'unlisted_code_is_not_proof_of_general_purchase' in facts['product']['uncertainty']
    assert result['v17'] == '0'


@pytest.mark.parametrize('title,expected', [
    ('투자기업 육성 프로그램 운영', 'unknown'),
    ('행사기획 및 운영 용역', 'competition'),
])
def test_component_event_keeps_its_scope_instead_of_classifying_entire_contract(title, expected):
    from submission.pps.products import ProductFacts
    from submission.pps.qualification import inventory, purchase_scope
    pf = ProductFacts(DATA / '법령패키지/중기부고시/중기부고시_경쟁제품_세부품명.csv')
    rec = synthetic_notice(f'1. 사업개요\n용역명: {title}\n과업내용: 부대 행사 운영\n2. 입찰참가자격\n등록한 업체\n3. 제출서류')
    parts = inventory(rec)
    scope = purchase_scope(rec, pf, parts[0], parts[4])
    assert scope['status'] == expected
    assert scope['detail_candidates_not_unique_identity']


@pytest.mark.parametrize('text,expected', [
    ('협상에 의한 계약체결기준 제7조의2에 따라 가격을 평가한다.', False),
    ('지역기업 공동사업을 지원하는 용역이다.', False),
    ('「중소기업제품 구매촉진 및 판로지원에 관한 법률」 제7조의2에 따른 계약', True),
    ('판로지원법 시행령 제7조의2에 따른 계약', True),
    ('소기업 공동사업 제품에 대한 제한경쟁', True),
])
def test_article_numbers_and_generic_joint_projects_do_not_cross_statute_namespaces(text, expected):
    from submission.pps.sme import extract_inventory, small_enterprise_special_reference
    assert small_enterprise_special_reference(text) == expected
    exceptions = extract_inventory(synthetic_notice(text))[3]
    assert any(e['kind'] == 'small_enterprise_special_case_reference' for e in exceptions) == expected


@pytest.mark.parametrize('work', ['일반용역', '물품(내자)'])
def test_v2_price_necessary_condition_does_not_depend_on_experience_parser(work):
    from submission.pps.rules import apply_rules
    rec = synthetic_notice('3. 제출서류\n과거 실적을 기재한다.',
                           업무구분=work, 입찰추정가격=230_000_000)
    result, trace = apply_rules(rec, {'v2': 1, 'e2': '과거 실적을 기재한다.'}, items=(2,))
    assert result == {'v2': 0, 'e2': ''}
    assert any(t.get('reason') == 'known_estimate_not_below_supplied_notice' for t in trace)
    rec['docs'][0]['text'] = '추정가격: 200,000,000원\n' + rec['docs'][0]['text']
    assert apply_rules(rec, {'v2': 1}, items=(2,))[0]['v2'] == 1


@pytest.mark.parametrize('authority', ['공기업', '기타기관', '교육기관'])
def test_v2_above_notice_amount_is_false_for_the_item_regardless_of_authority_label(authority):
    """The v2 item itself requires a below-notice-price contract.

    Authority-specific applicability can withhold a positive below the bound,
    but it cannot turn an observed above-bound price into v2.
    """
    from submission.pps.rules import apply_rules
    rec = synthetic_notice(
        '2. 입찰참가자격\n동등·유사 장비 납품 실적 보유 업체',
        업무구분='물품(내자)', 적용계약법='국가계약법',
        소관구분=authority, 입찰추정가격=272_727_273)
    result, trace = apply_rules(rec, {'v2': 1, 'e2': '동등·유사 장비 납품 실적 보유 업체'}, items=(2,))
    assert result['v2'] == 0 and result['e2'] == ''
    assert any(t.get('reason') == 'known_estimate_not_below_supplied_notice' for t in trace)


def test_v2_price_band_conflict_or_below_bound_preserves_model_decision():
    from submission.pps.rules import apply_rules
    rec = synthetic_notice(
        '추정가격: 220,000,000원\n추정가격: 240,000,000원\n'
        '2. 입찰참가자격\n동등·유사 장비 납품 실적 보유 업체',
        업무구분='물품(내자)', 적용계약법='국가계약법',
        소관구분='기타기관', 입찰추정가격=272_727_273)
    assert apply_rules(rec, {'v2': 1}, items=(2,))[0]['v2'] == 1
    rec = synthetic_notice(
        '2. 입찰참가자격\n동등·유사 장비 납품 실적 보유 업체',
        업무구분='물품(내자)', 적용계약법='국가계약법',
        소관구분='기타기관', 입찰추정가격=229_999_999)
    assert apply_rules(rec, {'v2': 1}, items=(2,))[0]['v2'] == 1


@pytest.mark.parametrize('bound', ['미만', '이하', '이상', '초과', '내외', '정도', '한도'])
def test_price_range_is_not_an_exact_project_amount(bound):
    from submission.pps.prices import project_prices
    from submission.pps.comparison import amount_facts
    rec = synthetic_notice(f'[별표] 시설용역 평가기준 적용(추정가격 5억원 {bound})')
    facts = amount_facts(rec)
    assert facts[0]['scope'] == 'bounded' and facts[0]['value_relation'] == bound
    assert project_prices(rec)['estimated_price']['value_won'] == 100_000_000


def test_region_applicability_uses_annotated_authority_and_exact_project_price():
    from submission.pps.regions import multiple_region_check, above_ceiling_region_check
    text = '[수요기관(기초자치단체)|지역=r1]\n2. 입찰참가자격\n가. 주된 영업소를 경상남도, 부산광역시 내에 둔 사업자로 제한'
    rec = synthetic_notice(text, 적용계약법='지방계약법', 입찰추정가격=300_000_000)
    assert multiple_region_check(rec)['value'] == 1
    rec = synthetic_notice('2. 입찰참가자격\n가. 본점 소재지가 서울특별시에 있는 업체', 입찰추정가격=230_000_000)
    assert above_ceiling_region_check(rec)['value'] == 1
    rec['docs'][0]['text'] += '라는 조건은 철회한다.'
    assert above_ceiling_region_check(rec) is None


@pytest.mark.parametrize('field,allowed', [
    ('입찰방법: 제한경쟁(소기업·소상공인)', ['micro', 'small']),
    ('예시 입찰방법: 제한경쟁(소기업·소상공인)', None),
    ('협상에 따른 계약방법: 제한경쟁(소기업·소상공인)', None),
    ('입찰방법: 일반경쟁', None),
])
def test_actual_procedure_field_is_an_operative_size_restriction(field, allowed):
    from submission.pps.qualification import inventory, qualification_facts
    rec = synthetic_notice(field + '\n1. 입찰참가자격\n법정 업종 등록한 업체\n2. 계약조건\n'
                           '비영리법인은 부가가치세 및 이윤을 제외하고 정산한다.')
    facts = qualification_facts(rec, inventory(rec))
    assert facts['allowed'] == allowed
    if allowed:
        assert not facts['no_size']
        assert facts['operative_procedure_size'][0]['evidence']['text'] == field
    assert not facts['size_conflict']


def test_procedure_narrowing_does_not_erase_broader_eligibility_branch():
    from submission.pps.qualification import inventory, qualification_facts
    rec = synthetic_notice('입찰방법: 제한경쟁(소기업·소상공인)\n'
                           '1. 입찰참가자격\n가. 중기업·소기업·소상공인 확인서를 보유한 업체\n2. 제출서류')
    facts = qualification_facts(rec, inventory(rec))
    assert facts['size_conflict']


def test_audited_actual_producer_uses_v6_and_a_single_item_l20():
    class Tokenizer:
        def apply_chat_template(self, messages, **kwargs):
            return [len(m['content']) for m in messages]
    rec = synthetic_notice('1. 사업개요\n사업명: 운영체제 라이선스 및 기술지원 갱신\n'
                           '2. 입찰참가자격\n소프트웨어사업자 등록 업체\n3. 제출서류')
    rec['id'] = 'synthetic-producer'
    control = dict(source_policy='current', legal_policy='current',
                   specification_review='current', catalog_review='current', software_review='current',
                   catalog_source_policy='shared',catalog_task_groups=False)
    pipeline = B4Pipeline(DATA, Tokenizer(), input_strategy='audited', **control)
    prompts = pipeline.bundle(rec)
    assert [p['batch'] for p in prompts] == ['A1', 'A10', 'A19', 'L19']
    assert all(p['rubric_version'] == 'v6' and p['input_strategy'] == 'audited' for p in prompts)
    assert prompts[-1]['items'] == [20]
    assert prompts[-1]['generation']['thinking_budget'] == 0
    assert prompts[2]['comparison_facts'] is not None
    assert 'N만으로 위반을 확정하지 않는다' in prompts[2]['messages'][-1]['content']
    assert '수강생 코딩과 계약업체 개발 의무' in prompts[-1]['messages'][0]['content']
    legacy = B4Pipeline(DATA, Tokenizer(), input_strategy='preserved', **control).bundle(rec)
    assert legacy[0]['rubric_version'] == 'v4'
    assert legacy[-1]['items'] == list(range(19, 25))


def test_specialized_l20_response_contract_and_actual_consumer():
    record = synthetic_notice('이 과업의 SW 해당 여부는 미확정이다.')
    record['id'] = 'synthetic-specialized-L'
    response = {'finish_reason': 'stop', 'text': json.dumps({
        'facts': dict.fromkeys(fact_fields((20,)), '미확정'),
        'judgments': {'v20': {'reason': '소프트웨어 범위 미확정', 'v': 1, 'e': 0}}}, ensure_ascii=False)}
    packet = {'family': 'L', 'items': [20], 'spans': [], 'generation': {'response_format': 'factored'}}
    from submission.b4_entry import parse_error
    assert parse_error(packet, response) is None
    result, _ = B4Pipeline(DATA, None).consume(record, packet, response)
    assert result == {'v20': 1, 'e20': ''}


@pytest.mark.parametrize('entity,law', [
    ('여성기업', '「여성기업지원에 관한 법률」 제2조 제1호'),
    ('장애인기업', '「장애인기업활동 촉진법」 제2조 제2호'),
])
def test_actual_national_special_supplier_quote_blocks_blind_absence_override(entity, law):
    from submission.pps.qualification import inventory, qualification_facts, specific_supplier_quote_review
    text = ('1. 사업개요\n입찰방법: 물품구매 소액수의견적\n2. 입찰참가자격\n'
            '아래의 사항을 입찰참가자격으로 등록한 자\n-\n'
            f'{law}에 따른 {entity}\n3. 제출서류\n사업자등록증')
    rec = synthetic_notice(text)
    rec['meta'].update({'적용계약법': '국가계약법', '계약방법': '수의계약'})
    facts = qualification_facts(rec, inventory(rec))
    review = specific_supplier_quote_review(rec, facts, 28_000_000)
    assert len(review) == 1 and review[0]['waiver_certified'] is False
    assert specific_supplier_quote_review(rec, facts, 100_000_001) == []
    assert specific_supplier_quote_review(rec, facts, None) == []
    rec['meta']['계약방법'] = '제한경쟁'
    assert specific_supplier_quote_review(rec, facts, 28_000_000) == []


def test_women_supplier_boilerplate_and_negation_are_not_quote_exception_proof():
    from submission.pps.qualification import inventory, qualification_facts, specific_supplier_quote_review
    for body in ('참고: 여성기업의 제품 사용을 권장한다.',
                 '「여성기업지원에 관한 법률」 제2조 제1호에 따른 여성기업이어야 한다는 조건은 철회한다.'):
        rec = synthetic_notice('소액수의견적\n2. 입찰참가자격\n'+body+'\n3. 제출서류')
        rec['meta'].update({'적용계약법': '국가계약법', '계약방법': '수의계약'})
        assert specific_supplier_quote_review(rec, qualification_facts(rec, inventory(rec)), 30_000_000) == []


def test_text_only_engine_constructor_does_not_reserve_multimodal_inputs(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from submission.pps.pipeline import VLLMRunner
    from submission.pps.prompts import Config
    observed = {}
    class FakeLLM:
        def __init__(self, **kwargs): observed.update(kwargs)
        def get_tokenizer(self): return object()
    monkeypatch.setitem(sys.modules, 'vllm', SimpleNamespace(LLM=FakeLLM, __version__='0.26.0'))
    for key in ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'VLLM_NO_USAGE_STATS', 'VLLM_WORKER_MULTIPROC_METHOD'):
        monkeypatch.setenv(key, 'test-value')
    VLLMRunner(tmp_path, Config(text_only=True))
    assert observed['limit_mm_per_prompt'] == {'image': 0, 'audio': 0, 'video': 0}
    assert observed['enable_prefix_caching'] is True
    assert observed['trust_remote_code'] is False
