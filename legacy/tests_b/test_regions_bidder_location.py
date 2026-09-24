"""Office-holder grammar keeps procurement premises and exceptions intact."""
import pytest

from submission.pps.regions import multiple_region_check, above_ceiling_region_check


def record(body, price=80_000_000):
    return {'id': 'unseen-region-wording', 'meta': {
        '적용계약법': '국가계약법', '업무구분': '일반용역', '계약방법': '제한경쟁',
        '입찰추정가격': price, '배정예산금액': price * 1.1},
        'input_completeness': {'완전관측': True}, 'dropped_doc_counts': {},
        'docs': [{'doc_id': 'notice', 'type': '공고문',
                  'text': '1. 입찰참가자격\n' + body}]}


@pytest.mark.parametrize('body', [
    '가. 주된 영업 소재지가 대전광역시 또는 충청남도에 있는 업체',
    '나. 본점이 부산광역시 또는 경상남도에 위치한 업체이어야 합니다.',
    '다. 본점이 인천광역시 또는 경기도에 소재한 교육기관',
])
def test_affirmative_office_location_wording(body):
    rec=record(body)
    check=multiple_region_check(rec)
    assert check and check['value']==1
    assert check['evidence'] in rec['docs'][0]['text']


@pytest.mark.parametrize('body', [
    '가. 본점이 부산광역시에 위치한 업체이어야 합니다.',
    '나. 교육 장소는 인천광역시 또는 경기도에 소재한 교육기관입니다.',
    '다. 참고용 예시: 본점이 인천광역시 또는 경기도에 소재한 교육기관',
    '라. 본점이 인천광역시 또는 경기도에 소재한 교육기관이라는 조건은 삭제합니다.',
])
def test_single_province_delivery_example_and_withdrawal_stay_quiet(body):
    assert multiple_region_check(record(body)) is None


def test_explicit_multiple_venue_exception_remains():
    rec=record('가. 본점이 부산광역시 또는 경상남도에 위치한 업체이어야 합니다.\n'
               '2. 납품장소: 부산광역시 및 경상남도')
    assert multiple_region_check(rec) is None


def test_actual_quote_and_price_gates_remain():
    rec=record('가. 본점이 부산광역시 또는 경상남도에 위치한 업체이어야 합니다.')
    rec['meta']['계약방법']='수의계약'
    assert multiple_region_check(rec) is None
    rec=record('가. 본점이 부산광역시에 위치한 업체이어야 합니다.',300_000_000)
    assert multiple_region_check(rec) is None
    assert above_ceiling_region_check(rec)['value']==1
