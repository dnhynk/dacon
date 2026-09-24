"""Track B rules (runs/rebuild_20260924/track_b/DESIGN_B.md): competition-service classifier, the general size block of
qualification.infer, and the track B precision gates with their exception guards (CPU only)."""
import pytest

from submission.pps import csc_probe, precision_gates
from submission.pps.csc_probe import classify, software_project
from submission.pps.products import ProductFacts
from submission.pps.qualification import infer
from submission.pps.semantic_rules import v9_lines
from tests.test_independent_audit import DATA, synthetic_notice
from tests.test_precision_gates import row_with


@pytest.fixture(scope='module')
def pf():
    return ProductFacts(DATA / '법령패키지/중기부고시/중기부고시_경쟁제품_세부품명.csv')


def service(title, estimate, *, license='', body='', **meta):
    rec = synthetic_notice(f'입찰공고\n용역명: {title}\n1. 사업개요\n2. 입찰참가자격\n{body}\n3. 제출서류',
                           입찰추정가격=estimate, 면허업종제한목록=license, **meta)
    rec['id'] = 'track-b-case'
    return rec


# Competition-service classifier

@pytest.mark.parametrize('estimate, competition', [(299_999_999, True), (300_000_000, False)])
def test_festival_needs_an_estimate_below_three_hundred_million(estimate, competition):
    result = classify(service('2026 봄꽃 축제 운영 대행 용역', estimate))
    assert (result['family'], result['competition']) == ('festival', competition)


@pytest.mark.parametrize('estimate, competition', [(1_800_000_000, True), (1_850_000_000, False)])
def test_software_limit_applies_to_the_project_amount_with_vat(estimate, competition):
    result = classify(service('통합 정보시스템 유지관리 용역', estimate, license='소프트웨어사업자'))
    assert (result['family'], result['competition']) == ('sw', competition)


def test_design_needs_design_in_the_title_and_excludes_spatial_work():
    assert classify(service('홍보물 디자인 제작 용역', 50_000_000))['family'] == 'design'
    assert classify(service('공공디자인 공간 조성 설계 용역', 50_000_000))['family'] is None


def test_security_excludes_machine_guarding():
    assert classify(service('청사 시설경비 용역', 50_000_000, license='시설경비'))['family'] == 'security'
    assert classify(service('청사 기계경비 용역', 50_000_000))['family'] is None


def test_goods_are_not_classified():
    rec = service('2026 봄꽃 축제 운영 대행 용역', 50_000_000)
    rec['meta']['업무구분'] = '물품(내자)'
    assert classify(rec)['reason'] == 'not_general_service'


CLEANING_CODE = {'7611150101': {'세부품명': '청소서비스'}}
CITED = '가. 중소기업자간 경쟁제품 직접생산 확인증명서(세부품명번호 7611150101)를 소지한 업체'


def test_cited_catalog_code_selects_the_service_when_the_title_matches():
    result = classify(service('청사 환경미화 및 청소 관리 용역', 50_000_000, body=CITED), CLEANING_CODE)
    assert (result['family'], result['competition'], result['signals']) == ('cleaning', True, {'cited_code': True})


def test_cited_catalog_code_for_an_unrelated_task_is_not_followed():
    # DEV-056 style: the clause cites a catalog code, the project is another task (organizer label: general purchase).
    result = classify(service('해운 부문 외부사업 운영 지원 용역', 50_000_000, body=CITED), CLEANING_CODE)
    assert result['family'] is None


def test_catalog_codes_without_runtime_products_never_raise(monkeypatch, tmp_path):
    monkeypatch.setattr(csc_probe, '_CATALOG', None)
    monkeypatch.setattr(csc_probe, '__file__', str(tmp_path / 'submission/pps/csc_probe.py'))
    assert csc_probe.catalog_service_codes() == {}


def test_software_project_reads_family_license_or_opening_phrase():
    assert software_project(service('통합 정보시스템 유지관리 용역', 50_000_000, license='소프트웨어사업자'))
    assert software_project(service('기관 누리집 운영 용역', 50_000_000, body='가. 홈페이지 유지관리 사업 수행'))
    assert not software_project(service('청사 청소 용역', 50_000_000))


# General size block of qualification.infer

def general(estimate, body, **meta):
    rec = synthetic_notice('입찰공고\n용역명: 청사 조경 관리 용역\n1. 사업개요\n2. 입찰참가자격\n' + body + '\n3. 제출서류\n가. 사업자등록증',
                           입찰추정가격=estimate, 배정예산금액=int(estimate * 1.1), 세부품명번호목록='', **meta)
    rec['id'] = 'track-b-size'
    return rec


BASE = {**{f'v{k}': '0' for k in range(1, 25)}, **{f'e{k}': '' for k in range(1, 25)}}
OPEN = '가. 「국가를 당사자로 하는 계약에 관한 법률 시행령」 제12조에 따른 경쟁입찰참가자격을 갖춘 자'
SMALL_ONLY = '가. 「중소기업제품 구매촉진 및 판로지원에 관한 법률」에 따른 소기업 또는 소상공인으로서 확인서를 소지한 자'


def test_general_service_between_one_hundred_and_two_hundred_thirty_million(pf):
    open_result, _ = infer(general(150_000_000, OPEN), dict(BASE), pf)
    small_result, _ = infer(general(150_000_000, SMALL_ONLY), dict(BASE), pf)
    assert (open_result['v16'], open_result['v15']) == ('1', '0')
    assert (small_result['v16'], small_result['v15']) == ('0', '1')


def test_general_service_between_twenty_million_and_one_hundred_million_without_size_bound(pf):
    result, _ = infer(general(50_000_000, OPEN), dict(BASE), pf)
    assert result['v18'] == '1'


def test_private_contracts_are_left_to_the_earlier_decisions(pf):
    result, _ = infer(general(150_000_000, OPEN, 계약방법='수의계약'), dict(BASE), pf)
    assert result['v16'] == '0'


def test_block_failure_keeps_the_values_from_before_the_block(pf, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError('classifier failure')
    monkeypatch.setattr(csc_probe, 'classify', broken)
    result, _ = infer(general(150_000_000, OPEN), dict(BASE), pf)
    assert result['v16'] == '0'


# Track B precision gates

def notice_of(*lines, **meta):
    return {'id': 'gate-case', 'meta': {'업무구분': '일반용역', '적용계약법': '국가계약법', **meta},
            'docs': [{'type': '공고문', 'doc_id': 'gate-case-0', 'text': '\n'.join(lines)}],
            'input_completeness': {'완전관측': True}, 'dropped_doc_counts': {}}


def test_v20_needs_a_software_project_and_no_participation_statement():
    cleaning = notice_of('입찰공고', '용역명: 청사 청소 용역')
    row = row_with(v20='')
    assert precision_gates.apply(cleaning, row, ('v20_software_project',)) == [(20, 'v20_software_project')]
    software = notice_of('입찰공고', '용역명: 통합 정보시스템 유지관리 용역', '가. 정보시스템 유지관리 사업',
                         면허업종제한목록='소프트웨어사업자')
    assert precision_gates.apply(software, row_with(v20=''), ('v20_software_project',)) == []
    stated = notice_of('입찰공고', '가. 대기업인 소프트웨어사업자의 참여를 제한합니다')
    assert precision_gates.apply(stated, row_with(v20=''), ('v20_participation_statement',)) == [
        (20, 'v20_participation_statement')]


def test_v23_is_kept_only_for_local_negotiated_contracts():
    national = notice_of('입찰공고', 낙찰방법='협상에의한계약')
    assert precision_gates.apply(national, row_with(v23=''), ('v23_local_negotiated',)) == [(23, 'v23_local_negotiated')]
    local = notice_of('입찰공고', 적용계약법='지방계약법', 소관구분='지방자치단체', 낙찰방법='협상에의한계약')
    assert precision_gates.apply(local, row_with(v23=''), ('v23_local_negotiated',)) == []


def test_v21_minimum_share_below_the_statutory_floor():
    low = '가. 공동수급체 구성원별 최소 지분율은 3% 이상으로 한다'
    row = row_with()
    assert precision_gates.apply(notice_of('입찰공고', low), row, ('v21_minimum_share',)) == [(21, 'v21_minimum_share')]
    assert row['e21'] == low
    assert precision_gates.apply(notice_of('입찰공고', low.replace('3%', '10%')), row_with(), ('v21_minimum_share',)) == []
    assert precision_gates.apply(notice_of('입찰공고', '가. 분담이행 방식', low), row_with(), ('v21_minimum_share',)) == []


def test_v24_amount_that_permutes_a_registered_amount():
    line = '나. 용역금액: 금37,930,000원(추정가격 34,481,818원)'
    rec = notice_of('입찰공고', line, 입찰추정가격=34_418_818, 배정예산금액=37_930_000)
    row = row_with()
    assert precision_gates.apply(rec, row, ('v24_amount_permutation',)) == [(24, 'v24_amount_permutation')]
    assert row['e24'] == line
    same = notice_of('입찰공고', line, 입찰추정가격=34_481_818, 배정예산금액=37_930_000)
    assert precision_gates.apply(same, row_with(), ('v24_amount_permutation',)) == []


def test_v2_price_band_reads_the_registered_estimate_first():
    rec = notice_of('입찰공고', '가. 최근 3년 이내 유사용역 실적이 있는 업체', 입찰추정가격=230_000_000)
    assert precision_gates.apply(rec, row_with(v2=''), ('v2_price_band',)) == [(2, 'v2_price_band')]
    rec['meta']['입찰추정가격'] = 229_999_999
    assert precision_gates.apply(rec, row_with(v2=''), ('v2_price_band',)) == []


def test_local_private_contract_exception_clears_record_and_region_items():
    rec = notice_of('견적제출 공고', 적용계약법='지방계약법', 소관구분='지방자치단체', 계약방법='수의계약')
    row = row_with(v2='', v6='', v7='', v8='', v1='')
    changed = precision_gates.apply(rec, row, ('local_private_exception',))
    assert changed == [(k, 'local_private_exception') for k in (2, 6, 7, 8)]
    assert row['v1'] == 1


def test_v8_needs_a_region_restriction():
    rec = notice_of('입찰공고', '가. 최근 3년 이내 유사용역 실적이 있는 업체', 지역제한여부='N')
    assert precision_gates.apply(rec, row_with(v8=''), ('v8_region_required',)) == [(8, 'v8_region_required')]


def test_v9_designation_names_a_manufacturer_but_not_an_equivalent():
    named = '○ 제조사 : 한빛전자'
    assert v9_lines(notice_of('규격', named)) == [(0, named, 'label')]
    assert v9_lines(notice_of('규격', '○ 제조사 : 동등 이상 제품')) == []
    row = row_with()
    assert precision_gates.apply(notice_of('규격', named), row, ('v9_designation',)) == [(9, 'v9_designation')]
    assert row['e9'] == named


def test_a_failing_track_b_gate_is_skipped(monkeypatch):
    def broken(record):
        raise RuntimeError('gate failure')
    monkeypatch.setattr(precision_gates, '_amount_permutation', broken)
    monkeypatch.setattr(precision_gates, '_low_joint_share', broken)
    rec = notice_of('입찰공고', '가. 최근 3년 이내 유사용역 실적이 있는 업체', 입찰추정가격=300_000_000)
    row = row_with(v2='')
    assert precision_gates.apply(rec, row, ('v24_amount_permutation', 'v21_minimum_share', 'v2_price_band')) == [
        (2, 'v2_price_band')]
    assert (row['v21'], row['v24']) == (0, 0)
