import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))

from pps_c import amounts, dates, families as F, judge, regions  # noqa: E402


def vals(text):
    return [m.value for m in amounts.money(text)]


def test_money_forms():
    assert vals('금310,000,000원(부가가치세 포함)') == [310000000]
    assert vals('3억원 이상') == [3e8]
    assert vals('3억 5천만원') == [3.5e8]
    assert vals('3억5,000만원') == [3.5e8]
    assert vals('5천만원 이상') == [5e7]
    assert vals('5,000만원') == [5e7]
    assert vals('1억 2,345만 6,789원') == [123456789]
    assert vals('단일 건 3억 원(VAT포함) 이상') == [3e8]
    assert vals('실적이 5억 이상인 업체') == [5e8]
    assert vals('5천만 이상의 실적') == [5e7]
    assert vals('2026. 2. 11. 까지') == []


def test_ratio():
    assert amounts.ratios('455,000,000원 이상(기초금액의 130% 이상)') == [('기초금액', 1.3)]
    assert amounts.ratios('추정가격의 2배 이상') == [('추정가격', 2.0)]


def test_regions():
    m = regions.mentions('주된 영업소가 서울특별시 [지역:r1|단위=기초|광역=서울특별시] 내에 소재')
    assert m['sido'] == {'서울특별시'} and m['basic'] == {('r1', '서울특별시')}
    m = regions.mentions('경기도 [지역:r1|단위=기초|광역=경기도] 또는 제주도에 둔 업체')
    assert m['sido'] == {'경기도', '제주특별자치도'}
    assert regions.mentions('광주광역시, 전라남도의 관할구역')['sido'] == {'광주광역시', '전라남도'}
    assert regions.mentions('부산물 처리 및 경기장 운영')['sido'] == set()
    assert regions.mentions('서울·경기 지역 업체')['sido'] == {'서울특별시', '경기도'}


def test_member_minimum():
    assert judge.member_minimum('구성원별 계약참여 최소지분율은 5% 이상이어야 함') == 5.0
    assert judge.member_minimum('(최소 지분율 3% 이상)') == 3.0
    assert judge.member_minimum('대표사 지분율 51% 이상') is None
    assert judge.member_minimum('구성 비율과 실제 비율이 각 ± 5% 차이가 발생할 경우') is None
    assert judge.member_minimum('구성원의 지분율이 3% 미만인 경우 참여할 수 없음') == 3.0


def test_size_words():
    assert F.size_words('「중소기업제품 구매촉진 및 판로지원에 관한 법률」제9조에 따른 직접생산확인증명서를 소지한 자') is None
    assert F.size_words('「중소기업기본법」 제2조에 따른 소기업자 또는 「소상공인 보호및 지원에 관한 법률」 제2조에 따른 소상공인') == 'small'
    assert F.size_words('중소기업기본법 제2조에 따른 중소기업자 또는 소상공인으로서 중소기업확인서를 소지한 업체') == 'sme'
    assert F.size_words('중·소기업 또는 소상공인') == 'sme'
    assert F.size_words('소기업·소상공인 확인서를 소지한 자') == 'small'


def test_dates():
    got = [d for d, _ in dates.find('일시 : 2026.03.03.(화요일) 15:00, 접수마감 2026.03.11.')]
    assert got == [dt.date(2026, 3, 3), dt.date(2026, 3, 11)]
    got = [d for d, _ in dates.find('현장설명회 : 3. 18.(수) 14:00', year=2026)]
    assert got == [dt.date(2026, 3, 18)]


def test_title_band():
    assert judge.band_ok(4.5e7, '5천만원', '미만') is True
    assert judge.band_ok(1.5e7, '5천만원', '미만') is False
    assert judge.band_ok(2.8e8, '3억원', '미만') is True
    assert judge.band_ok(1.2e10, '100억원', '이상') is True


def test_size_words_forms():
    w = F.size_words
    assert w('「중소기업 범위 및 확인에 관한 규정」에 따라 발급된 소기업·소상공인 확인서를 소지한 업체') == 'small'
    assert w('중소기업 또는 「소상공인 보호 및 지원에 관한 법률」 제2조에 따른 소상공인으로서 발급된 소기업·소상공인 확인서를 소지한 업체') == 'small'
    assert w('중/소기업자 및 소기업 및 소상공인으로서 중/소기업·소상공인확인서를 소지한 업체') == 'sme'
    assert w('중․소기업자 또는 소상공인으로서 <중‧소기업 또는 소상공인 확인서>를 소지한 업체') == 'sme'
    assert w('중･소기업 또는 소상공인으로서 중･소기업･소상공인 확인서를 소지하고') == 'sme'
    assert w('시행령 제2조의2(중소기업자와의 우선조달계약)에 의한 소기업 또는 소상공인에 해당하는 자로서 발급된 확인서') == 'small'
    assert w('「중소기업법」제2조제2항에 따른 소기업 또는 소상공인 (중기업 ×)') == 'small'
    assert w('소기업 규정에 따라 ‘중·소기업·소상공인 및 장애인 기업 확인 요령’에 따라 발급된 소기업 또는 소상공인 확인서를 소지한 자') == 'small'
    assert w('중소기업기본법 제2조에 따른 중소기업 또는 소상공인으로서 중소기업 또는 소상공인확인서를 소지한 업체') == 'sme'


def test_size_words_generic_certificate():
    w = F.size_words
    assert w('소기업 및 소상공인으로서 중 ‧ 소기업 · 소상공인 확인서를 소지한 업체') == 'small'
    assert w('중소기업 또는 소상공인으로서 중소기업·소상공인 확인서를 소지한 자') == 'sme'
    assert w('중·소기업·소상공인 확인서를 소지한 업체') == 'sme'


def test_size_words_certificate_options():
    w = F.size_words
    assert w('중소기업 또는 소상공인으로서 발급된 중소기업확인서, 소상공인확인서 중 하나를 소지한 업체') == 'sme'
    assert w('중‧소기업 또는 소상공인으로서 발급된 중기업 또는 소기업․소상공인확인서를 소지한 업체') == 'sme'
    assert w('중기업·소기업 또는 소상공인으로 발급된 중기업·소기업 또는 소상공인확인서를 소지한 사업자') == 'sme'
    assert w('중소기업 또는 소상공인으로서 「중소기업 범위 및 확인에 관한 규정」에 따라 발급된 소기업·소상공인 확인서를 소지한 업체') == 'small'


def test_size_words_certificate_after_entity():
    w = F.size_words
    assert w('판로지원법에 의한 중소기업으로서, 소기업 ․ 소상공인확인서(중소기업현황 정보시스템 발급)를 소지한 업체') == 'small'
    assert w('소기업 및 소상공인으로서 중 ‧ 소기업 · 소상공인 확인서를 소지한 업체') == 'small'


def test_dates_korean_short():
    got = [d for d, _ in dates.find('현장설명회: 3월 5일 14시, 대회의실', year=2026)]
    assert got == [dt.date(2026, 3, 5)]


def test_institution_limit_forms():
    lim = judge.institution_limit
    assert lim('본 용역은 「고등교육법」 제2조에 따른 대학 또는 산학협력단만 참여 가능함')
    assert lim('국공립 연구기관이 아닌 자는 입찰에 참가할 수 없음')
    assert lim('본 입찰은 ○○협회 회원사에 한하여 참가할 수 있으며, 그 외 업체는 참가할 수 없습니다')
    assert not lim('-「중소기업제품 구매촉진 및 판로지원에 관한 법률 시행령」제2조의3에 해당되지 않은 용역으로 소기업 확인서가 없는 비영리법인은 입찰 참가 불가합니다.')
    assert not lim('[수요기관(의료기관)]')


def test_values_parse_and_patterns():
    fam = F.FAMILIES['values']
    top = F.top_fields('values')
    lines, got = F.parse('{"예산":"39,730,000","추정가격":"","계약방법":"수의계약"}', fam, [], top)
    assert got == {'예산': '39,730,000', '추정가격': '', '계약방법': '수의계약'}
    lines, got = F.parse('{"예산":"약 4천만","추정가격":"x","계약방법":"경쟁"}', fam, [], top)
    assert got == {}


def test_sw_statement_and_title_head():
    from pps_c import catalog
    assert judge.SW_STATEMENT.search('총 사업금액 20억 미만인 사업으로, 「소프트웨어 진흥법」제48조에 따라, 중소 소프트웨어사업자만 입찰참가 가능')
    assert judge.SW_STATEMENT.search('대기업인 소프트웨어사업자의 참여를 제한합니다')
    assert not judge.SW_STATEMENT.search('「독점규제 및 공정거래에 관한 법률」에 따른 상호출자제한기업집단 소속 회사는 참여할 수 없음')
    assert catalog.head_conflict('exhibition', '함평엑스포공원 관광인프라 정비사업 폐기물처리 용역(제한경쟁·3억원미만)')
    assert not catalog.head_conflict('exhibition', '기초자치단체 2026 정원박람회 기획 운영 대행용역(제한경쟁·10억원미만)')
    assert not catalog.head_conflict('cleaning', '기초자치단체 직영 공영주차장 청소 용역(수의계약·5천만원미만)')


def test_institution_limit_natural_misreads():
    lim = judge.institution_limit
    assert not lim('2) 비영리법인(정관, 법인등기사항전부증명서 목적에 방과후학교 위탁 운영에 대한 내용이 기재되어 있어야 하며, 법인허가증 등 증빙자료를 별도 제출하여야 합니다.)')
    assert not lim('라. 비영리법인일 경우 비영리법인으로 법인설립 허가를 받은 자')
    assert not lim('가. 지방자치단체를 당사자로 하는 계약에 관한 법률 시행령 제13조 규정에 의한 참가자격을 갖추고, 주된 영업소재지가 [수요기관(기초자치단체)]인 업체이어야 합니다.')
    assert not lim('2) 「행정기관 및 공공기관 정보시스템 구축·운영 지침」 제10조에 따라 “대기업참여제한사업”')
    assert not lim('국민행복 실현에 기여하는 [기관(공공기관)]')
    assert not lim('* 중소기업제품 구매촉진 및 판로지원에 관한 법률 시행령 제2조의3 제2호에 따라 비영리')
    assert lim('라. 체육교육학 전공이 있는 [기관(대학)]만 입찰 참여 가능합니다.')
    assert lim('갖춘 대학교 혹은 산학협력단')
