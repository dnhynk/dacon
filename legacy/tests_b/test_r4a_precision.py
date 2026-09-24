"""Experience eligibility needs an operative gate and usable project prices."""
import pytest

from submission.pps.performance import performance_facts
from submission.pps.regions import multiple_region_check
from submission.pps.rules import apply_rules
from submission.pps.v2_quote_check import check_item


def notice(text, price=90_000_000, budget=99_000_000):
    return {'id': 'purpose-controls', 'meta': {
        '적용계약법': '지방계약법', '업무구분': '일반용역',
        '계약방법': '제한경쟁', '입찰추정가격': price, '배정예산금액': budget},
        'docs': [{'type': '공고문', 'text': '2. 입찰 참가자격\n' + text}],
        'input_completeness': {'완전관측': True}, 'dropped_doc_counts': {}}


GATE = '가. 최근 4년간 단일 계약 2억원 이상의 수행 실적이 있는 업체이어야 한다.'
REGION = '나. 본점 소재지를 충청남도에 둔 업체이어야 한다.'


@pytest.mark.parametrize('raw', [0, 1])
@pytest.mark.parametrize('price,budget,expected2,expected3', [
    (1, 1, 0, 0), (1, 99_000_000, 0, 0), (90_000_000, 1, 1, 0),
    (90_000_000, 99_000_000, 1, 1),
])
def test_one_won_metadata_cannot_prove_project_price_predicate(raw, price, budget, expected2, expected3):
    rec = notice(GATE + '\n' + REGION, price, budget)
    row, trace = apply_rules(rec, {'v2': raw, 'e2': GATE, 'v3': raw, 'e3': GATE, 'v8': 0}, items=(2, 3, 8))
    assert (row['v2'], row['v3'], row['v8']) == (expected2, expected3, 1)
    assert all(t['semantic_value'] is None for t in trace if t.get('reason', '').startswith('metadata_placeholder'))


def test_comparable_notice_totals_override_metadata_sentinels():
    rec = notice(GATE, 1, 1)
    rec['docs'][0]['text'] = '추정가격: 90,000,000원\n사업예산: 99,000,000원\n' + rec['docs'][0]['text']
    facts = performance_facts(rec)
    assert facts['overlays']['v2']['value'] == facts['overlays']['v3']['value'] == 1


def test_one_won_metadata_cannot_certify_small_quote_exception():
    rec = notice(GATE, 1, 1)
    rec['meta']['계약방법'] = '수의계약'
    rec['docs'][0]['text'] = '소액수의 견적제출 안내공고\n' + rec['docs'][0]['text']
    assert check_item(rec, performance_facts(rec), 2) is None


@pytest.mark.parametrize('statement', [
    '과거 수행실적을 입찰참가자격으로 요구하지 않습니다. 실적은 평가항목으로만 반영합니다.',
    '수행실적에 의한 참가자격 제한을 두지 않으며, 이행실적은 심사에서 평가합니다.',
    '실적 보유 여부는 평가요소일 뿐 입찰참가자격은 아닙니다.',
    '실적증명서, 사업자등록증, 경쟁입찰참가자격등록증은 발주기관 확인서로 대체 가능합니다.',
    '실적증명서는 해당 업체만 선택 제출하며 참가자격등록증은 확인서로 갈음합니다.',
])
@pytest.mark.parametrize('raw', [0, 1])
def test_denied_gate_and_document_substitution_are_not_experience_eligibility(statement, raw):
    rec = notice('가. ' + statement + '\n' + REGION)
    facts = performance_facts(rec)
    assert not any(c['status'] == 'mandatory' for c in facts['candidates'])
    row, _ = apply_rules(rec, {'v2': raw, 'e2': statement, 'v8': raw, 'e8': statement}, items=(2, 8))
    assert row['v2'] == row['v8'] == 0


def test_certificate_substitution_does_not_remove_separate_minimum_gate():
    text = '다. 실적증명서와 참가자격등록증은 발주기관 확인서로 대체 가능합니다.'
    rec = notice(GATE + '\n' + REGION + '\n' + text)
    row, _ = apply_rules(rec, {'v2': 0, 'v3': 0, 'v8': 0}, items=(2, 3, 8))
    assert row['v2'] == row['v3'] == row['v8'] == 1


def test_separate_debarment_negation_does_not_waive_experience():
    rec = notice('가. 실적이 우수하고 건실한 업체이며, 부정당업자로 입찰참가자격 '
                 '제한중에 있지 아니하여야 한다.\n' + REGION)
    assert performance_facts(rec)['overlays']['v8']['value'] == 1


@pytest.mark.parametrize('count', ['10인 미만', '10인에 미달', '10개 미만', '10개에 미달', '10명에 미달', '십인 미만'])
def test_explicit_qualified_supplier_shortage_abstains(count):
    rec = notice('가. 본점 소재지가 충청남도 또는 대전광역시에 있는 업체이어야 한다.\n'
                 '나. 관내 자격업체가 ' + count + '하여 인접 시·도까지 소재지 범위를 확대한다.')
    assert multiple_region_check(rec) is None


def test_unrelated_small_count_is_not_a_qualified_supplier_exception():
    rec = notice('가. 본점 소재지가 충청남도 또는 대전광역시에 있는 업체이어야 한다.\n'
                 '나. 작업반 인원이 10인에 미달하는 경우 안전교육을 실시한다.')
    assert multiple_region_check(rec)['value'] == 1
