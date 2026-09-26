"""Structural counterexamples for the probe-switch fixes of the 2026-09-26 code audit (Claude-owned regions).

Each case pairs a form the switch's rationale targets with a form it must leave alone. No competition IDs or labels.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))
from pps_c import catalog, facts, judge, switches


def bundle(*lines, work='일반용역', law='국가계약법', method='제한경쟁', price=90_000_000, budget=99_000_000, **meta):
    m = {'적용계약법': law, '업무구분': work, '계약방법': method, '낙찰방법': '적격심사',
         '입찰추정가격': price, '배정예산금액': budget}
    m.update(meta)
    return facts.build({'id': 'audit-example', 'meta': m, 'docs': [{'type': '공고문', 'text': '\n'.join(lines)}]},
                       catalog.load())


def test_perf_unread_skips_disqualification_and_career_lines(monkeypatch):
    monkeypatch.setattr(switches, 'PERF_UNREAD', True)
    ban = 'o 인·허가 등록 취소 등 행정처분을 받은 이력이 있는 업체'
    career = '- 패턴 제작 경험을 포함하여 총 5년 이상의 실무 경력을 보유한 자'
    record = '다. 최근 3년 이내 유사 용역 수행 실적이 있는 업체'
    b = bundle('2. 입찰참가자격', ban, career, record)
    b.cands['perf'] = []
    got = [ln.text for ln in judge.perf_lines(b)]
    assert record in got
    assert ban not in got and career not in got


def test_private_enum_member_needs_a_joiner_before_the_buyer_word():
    # the bidder's own noun before the buyer word is no private buyer
    assert not judge.x1_private_in_enum('기술경력자 4인 이상 보유한 자(업체)로 최근 5년 내 대학에서 5천만원 이상의 유지보수 실적')
    assert not judge.x1_private_in_enum('중소기업 중 국가기관에 납품한 실적')
    # an enumeration member stays one
    assert judge.x1_private_in_enum('반도체 기업 또는 공공기관에 납품한 실적')
    assert judge.x1_private_in_enum('민간기관 또는 공공기관에 납품한 실적')
    assert judge.x1_private_in_enum('산업체 및 공공기관 등에 납품한 실적')


def test_staff_role_heading_is_not_the_bidder_qualification():
    assert judge.X2_STAFF_HEAD.search('① 수행 안내원 자격 요건')
    assert not judge.X2_STAFF_HEAD.search('2. 입찰참가자격 요건')


def test_generic_registration_is_no_statutory_institution_type():
    generic = '대학 또는 연구기관으로서 조달청 입찰참가자격 등록을 필한 기관'
    statutory = '평생교육법에 따라 평생교육시설로 관할청의 인가를 받은 기관'
    assert not any(not judge.X1_GENERIC_REG.search(m.group(0)) for m in judge.X1_REGISTERED_TYPE2.finditer(generic))
    assert any(not judge.X1_GENERIC_REG.search(m.group(0)) for m in judge.X1_REGISTERED_TYPE2.finditer(statutory))


def test_institution_list_takes_the_local_small_private_exemption(monkeypatch):
    monkeypatch.setattr(switches, 'V1_INST_LIST', True)
    b = bundle('2. 견적참가자격', '가. 대학, 연구기관만 참여 가능', law='지방계약법', method='수의계약', price=15_000_000,
               budget=16_500_000)
    assert b.meta.local_private
    monkeypatch.setattr(switches, 'V1_LOCAL_PRIVATE_INST', True)
    assert judge.x1_institution_list(b) is None


def test_basic_tokens_under_a_whole_registered_sido_are_no_basic_restriction():
    whole = bundle('공고', 제한지역코드목록='경기도, [등록지역:r1|단위=기초|광역=경기도]')
    other = bundle('공고', 제한지역코드목록='서울특별시, [등록지역:r1|단위=기초|광역=경기도]')
    assert judge.meta_basic_effective(whole) == 0
    assert judge.meta_basic_effective(other) == other.meta.region_basic == 1


def test_method_tag_line_stating_a_bidder_class_is_a_clause():
    b = bundle('국내입찰/제한경쟁(중소기업자간)', '제한경쟁(중소기업자간) 입찰로서 소기업·소상공인 확인서를 소지한 자로서 참가 가능')
    tag, clause = b.notice.lines[0], b.notice.lines[1]
    assert judge.tag_only([tag])
    assert not judge.tag_only([clause])
    assert not judge.tag_only([tag, clause])


def test_waste_exemption_reads_the_contract_name_or_content():
    service = bundle('1. 용역명: 생활폐기물 수집·운반 대행 용역', '2. 입찰참가자격')
    table = bundle('공고명', '', '관내 공사장 정비 용역', '사업내용', '', '건설폐재류 및 혼합건설폐기물 운반 및 처리', '2. 입찰참가자격')
    goods = bundle('1. 물품명: 폐기물 수거용 마대', '2. 입찰참가자격', work='물품')
    boiler = bundle('1. 용역명: 악취 실태 조사 용역', '※ 건설폐기물처리용역 적격업체 평가기준을 준용한다')
    assert judge.waste_service(service)
    assert judge.waste_service(table)
    assert not judge.waste_service(goods)
    assert not judge.waste_service(boiler)


def test_v23_date_selection_rides_code_audit_base_only(monkeypatch):
    for name in ('CODE_AUDIT_BASE', 'V23_LEGAL_COUNT'):
        monkeypatch.setattr(switches, name, False)
    b = bundle('1. 제안서 제출 마감', '2026. 10. 20. 17:00', '개찰 일시 2026. 10. 30.', posted='2026-10-01')
    old = judge.proposal_deadline(b)
    monkeypatch.setattr(switches, 'V23_LEGAL_COUNT', True)      # the counting rule alone keeps the original date algorithm
    assert judge.proposal_deadline(b) == old
    monkeypatch.setattr(switches, 'CODE_AUDIT_BASE', True)
    new = judge.proposal_deadline(b)
    assert new is not None and old is not None and new <= old


def test_base_zone_reads_whole_amounts_and_unit_contracts_by_title_or_binding():
    assert judge.BASE_AMOUNT.search('- 기초금액: 금94,467,00048,864,000원') is None
    assert judge.BASE_AMOUNT.search('기초금액: 금94,467,000원').group(1) == '94,467,000'

    def zone(*lines, title='청사 정비 용역'):
        return judge.v24_base_zone(bundle(f'1. 용역명: {title}', *lines, price=100_000_000, budget=110_000_000))
    assert zone('2. 기초금액: 금60,000,000원') is not None
    assert zone('2. 기초금액: 금60,000,000원', '※ 기초금액은 품목별 단가총액으로 산정함') is None
    assert zone('2. 기초금액: 금60,000,000원', title='사무용품 구매(단가계약)') is None
    # an unrelated unit-price clause does not erase the stated total
    assert zone('2. 기초금액: 금60,000,000원', '3. 단가 산출내역서를 제출하여야 함') is not None


def test_agrees_is_false_on_a_non_finite_amount():
    assert not judge.agrees(float('inf'), 100_000_000)
    assert judge.agrees(100_000_000.0, 100_000_000)


def server(*lines):
    return facts.build({'id': 'audit-example', 'meta': {'적용계약법': '국가계약법', '업무구분': '물품', '계약방법': '일반경쟁',
                                                        '입찰추정가격': 90_000_000, '배정예산금액': 99_000_000,
                                                        '세부품명번호목록': '컴퓨터서버[4321150102]'},
                        'docs': [{'type': '공고문', 'text': '1. 구매 물품: 컴퓨터서버'}, {'type': '규격서', 'text': '\n'.join(lines)}]},
                       catalog.load())


def test_server_base_clock_is_read_on_the_cpu_line_only():
    assert judge.designation_excluded(server('CPU: 3.8GHz, 2개'))
    assert judge.designation_excluded(server('CPU: Xeon 3.8GHz, 최대 4.5GHz x 2'))
    assert judge.designation_excluded(server('CPU 3.2GHz 초과 2개'))
    assert not judge.designation_excluded(server('CPU: 2.4GHz, 2개', '지원 주파수: 5.0GHz (Wi-Fi)'))
    assert not judge.designation_excluded(server('CPU: 2.4GHz, 2개', '메모리: DDR5 5.6GHz'))
    assert not judge.designation_excluded(server('CPU: 2.4GHz, 2개', 'CPU: 4.8GHz (Turbo), 기본 클럭 2.4GHz'))
    assert not judge.designation_excluded(server('CPU: Xeon 3.8GHz 1개'))
    assert not judge.designation_excluded(server('CPU: 2.4GHz, 2개', 'Wi-Fi: 3.2GHz 초과'))
    assert not judge.designation_excluded(server('CPU: 2.4GHz x 2; 5.0GHz (Wi-Fi)'))
    assert not judge.designation_excluded(server('CPU: 3.2GHz 초과 1개'))
    assert not judge.designation_excluded(server('CPU: 2.4GHz x 2', '메모리 클럭: DDR5 4.8GHz'))
    assert judge.designation_excluded(server('CPU: 3.8GHz x 2'))
    # a spec table states the clock under the CPU heading that carries the count
    assert judge.designation_excluded(server('나. 서버 전용 고성능 CPU (개당 사양, 총 2개 탑재)',
                                             '3) 동작 속도(Clock Speed): 기본 동작 클럭 3.25GHz 이상 보장'))
    # a generic clock line belongs to the CPU only while the CPU is the current subject
    assert judge.designation_excluded(server('CPU: Xeon x 2', '동작 속도: 3.8GHz'))
    assert not judge.designation_excluded(server('CPU: Xeon 2.4GHz x 2', 'Wi-Fi 모듈', '동작 속도: 5.0GHz'))
    assert not judge.designation_excluded(server('CPU: Xeon 2.4GHz x 2', '메모리', '동작 속도: 5.0GHz'))


def test_open_enumeration_token_names_no_specific_buyer():
    assert not judge.x1_token_buyer('가. 민간기업 및 [수요기관(학교)] 납품 실적이 있는 업체')
    assert judge.x1_token_buyer('가. 직장 [수요기관(보육시설)] 납품 실적이 있는 업체')


def test_preference_is_no_unread_record_requirement(monkeypatch):
    monkeypatch.setattr(switches, 'X2_HELD_RECORD_X', True)
    for text, kept in (('가. 유사 사업 수행실적이 있는 법인사업자 우대', False), ('가. 유사 사업 수행실적이 있는 법인사업자', True)):
        b = bundle('2. 입찰참가자격', text)
        b.cands['perf'] = []
        assert (text in [ln.text for ln in judge.x2_unread_records(b)]) is kept


def test_work_site_and_bonus_lines_are_no_bidder_location(monkeypatch):
    monkeypatch.setattr(switches, 'V6_ORDERER_ANY', True)
    assert judge.x7_v6_extra(bundle('1. 공고', '3. 납품 및 설치 장소: 각 사업장 ([수요기관(기초자치단체)] 관내 12개소)')) is None
    assert judge.x7_v6_extra(bundle('1. 공고', '가. 본점 소재지가 [수요기관(기초자치단체)] 관내에 있는 업체는 3점 가점')) is None
    assert judge.x7_v6_extra(bundle('1. 공고', '가. 본점 소재지가 [수요기관(기초자치단체)]내에 있는 업체')) is not None
