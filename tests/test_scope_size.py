"""Competition-service scope and size-clause readings (v10–v18)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))

from pps_c import catalog, judge, record  # noqa: E402
from pps_c.families import size_words  # noqa: E402


def test_sw_service_needs_system_work():
    assert catalog.sw_service_title('26~27-N-출입통제체계 유지보수')
    assert catalog.sw_service_title('2026학년도 ○○대학 학습분석시스템(○○○) 유지보수')
    assert catalog.sw_service_title('행정재산 실태조사 및 공유재산 통합 DB구축 용역')
    assert not catalog.sw_service_title('공공시설 전산기기 임차 계약(제한경쟁·3억원미만)')
    assert not catalog.sw_service_title('지방정부 사랑의 그린PC 보급사업 용역')
    assert not catalog.sw_service_title('학산 누리플랫폼 조성 주민역량강화사업 운영 용역')
    assert not catalog.sw_service_title('태양광 발전시스템 유지관리 용역')
    assert not catalog.sw_service_title('행정기관 북부2-2구역 블록시스템 정비공사 폐기물 운반 및 처리 용역')


def test_license_words_keep_their_boundaries():
    fams = {f[0]: f for f in catalog.SERVICE_FAMILIES}
    import re
    assert not re.search(fams['cleaning'][3], '[청소년수련시설(청소년수련원)(3251)]')
    assert not re.search(fams['transport'][3], '[여객자동차운수사업(자동차대여사업)(1457)]')
    assert re.search(fams['transport'][3], '[여객자동차운수사업(구역여객자동차운송사업-전세버스)(5805)]')
    assert not re.search(fams['event'][2], '기초자치단체 소독업무 대행사업 용역')


def test_catalog_amount_limit():
    sw = catalog.Product(code='8014199001', name='기타행사기획및대행서비스', note='추정가격 10억원 미만에 한함', cap=1e9)
    assert catalog.admits(sw, 9e8)
    assert not catalog.admits(sw, 1e9)


def test_certificate_list_name_is_sme():
    assert size_words('중·소기업 또는 소상공인으로서‘중·소기업, 소상공인 확인서’를 소지한 자') == 'sme'
    assert size_words('중소기업 또는 소상공인으로서 발급된 소기업·소상공인 확인서를 소지한 업체') == 'small'


def test_clause_joins_across_blank_lines():
    rec = {'id': 'T', 'meta': {}, 'docs': [{'type': '공고문', 'text': '\n\n'.join([
        '3. 입찰참가자격',
        '다.「중소기업기본법」 제2조에 따른 중소기업자 또는 「소상공인기본법」제2조에',
        '따른 소상공인으로 「중소기업범위 및 확인에 관한 규정」에 따라 발급된 유효',
        '기간 내의 ‘중소기업 또는 소상공인확인서’를 소지한 업체',
        '라. 입찰보증금은 면제합니다.'])}]}
    notice = record.build(rec)
    ln = next(x for x in notice.lines if x.text.startswith('따른 소상공인'))
    text = judge.clause_text(notice, ln)
    assert text.startswith('다.') and text.endswith('소지한 업체')
    assert size_words(text) == 'sme'


def _quotation_notice(title, qual):
    from pps_c import facts
    return facts.build({'id': 'T', 'meta': {'적용계약법': '지방계약법', '업무구분': '일반용역', '계약방법': '수의계약',
                                            '낙찰방법': '소액수의견적', '입찰추정가격': '30000000', '배정예산금액': '33000000'},
                        'docs': [{'type': '공고문', 'text': '\n\n'.join([f'{title} 견적 제출 안내 공고', '1. 견적 개요',
                                                                        f'가. 용역명 : {title}', '2. 견적제출 참가자격', qual])}]},
                       catalog.load())


def test_bid_only_items_can_skip_quotations(monkeypatch):
    from pps_c import switches
    event = _quotation_notice('2026 지역 축제 행사 대행 용역',
                              '가. 「지방자치단체를 당사자로 하는 계약에 관한 법률 시행령」 제13조에 의한 자격을 갖춘 자')
    sw = _quotation_notice('2026 행정정보시스템 유지보수 용역',
                           '가. 「소프트웨어 진흥법」 제58조에 따라 소프트웨어사업자(컴퓨터관련서비스사업)로 신고한 자')
    assert event.meta.private and event.scope.competitive and sw.sw_project == '소프트웨어 개발·구축·유지관리·운영'
    assert switches.V10_PRIVATE and switches.V20_PRIVATE       # submitted P3a behaviour
    assert judge.v10(event) is True and judge.v20(sw) is True
    monkeypatch.setattr(switches, 'V10_PRIVATE', False)
    monkeypatch.setattr(switches, 'V20_PRIVATE', False)
    assert judge.v10(event) is None and judge.v20(sw) is None


def test_segment_off_zeroes_only_matching_cells(monkeypatch):
    from pps_c import switches
    event = _quotation_notice('2026 지역 축제 행사 대행 용역',
                              '가. 「지방자치단체를 당사자로 하는 계약에 관한 법률 시행령」 제13조에 의한 자격을 갖춘 자')
    assert switches.SEGMENT_OFF == ()                          # canonical package: no cell is switched off
    assert judge.judge(event)['v10'][0] == 1
    assert judge.segment_of(event)['method'] == '수의계약' and judge.segment_of(event)['attach'] == '공고문만'
    monkeypatch.setattr(switches, 'SEGMENT_OFF', (('v10', 'method', '일반경쟁'),))
    assert judge.judge(event)['v10'][0] == 1
    monkeypatch.setattr(switches, 'SEGMENT_OFF', (('v10', 'method', '수의계약'),))
    assert judge.judge(event)['v10'] == (0, '')


def _general_goods(clause, estimate='50000000'):
    from pps_c import facts
    return facts.build({'id': 'T', 'meta': {'적용계약법': '국가계약법', '업무구분': '물품(내자)', '계약방법': '제한경쟁', '낙찰방법': '적격심사제',
                                            '입찰추정가격': estimate, '배정예산금액': str(int(int(estimate) * 1.1)), '조항호내용': clause,
                                            '세부품명번호목록': '잡지[5510150601]'},
                        'docs': [{'type': '공고문', 'text': '\n\n'.join([
                            '2026 정기간행물 잡지 구매 입찰공고', '1. 입찰에 부치는 사항', '가. 품명 : 잡지', '2. 입찰참가자격',
                            '가. 「국가를 당사자로 하는 계약에 관한 법률 시행령」 제12조에 따른 경쟁입찰 참가자격을 갖춘 자'])}]},
                       catalog.load())


def test_size_absence_needs_a_registered_restriction(monkeypatch):
    from pps_c import switches
    registered = _general_goods('[판로지원법 시행령] 소기업,소상공인제한')
    unregistered = _general_goods('행정안전부령 금액 미만 본점소재지')
    assert judge._general(registered) and judge.size_registered(registered) and not judge.size_registered(unregistered)
    assert not judge.size_registered(_general_goods('[판로지원법] 중기간경쟁제품 소기업 소상공인 제한경쟁, 공동사업'))
    assert judge.v18(registered) is True and judge.v18(unregistered) is True      # P3a behaviour
    monkeypatch.setattr(switches, 'SIZE_ABSENCE_NEEDS_REG', True)
    assert judge.v18(registered) is True and judge.v18(unregistered) is None


def test_price_floor_skips_unit_prices(monkeypatch):
    from pps_c import switches
    unit_price = _general_goods('[판로지원법 시행령] 소기업,소상공인제한', estimate='5545')
    assert judge.v18(unit_price) is True                                          # P3a behaviour
    monkeypatch.setattr(switches, 'PRICE_FLOOR', 1e6)
    assert judge.v18(unit_price) is None and judge.v18(_general_goods('[판로지원법 시행령] 소기업,소상공인제한')) is True


def test_competitive_goods_judges_goods_only_for_listed_items(monkeypatch):
    from pps_c import facts, switches
    b = facts.build({'id': 'T', 'meta': {'적용계약법': '국가계약법', '업무구분': '물품(내자)', '계약방법': '제한경쟁', '낙찰방법': '적격심사제',
                                         '입찰추정가격': '50000000', '배정예산금액': '55000000', '조항호내용': 'None',
                                         '세부품명번호목록': '데스크톱컴퓨터[4321150701]'},
                     'docs': [{'type': '공고문', 'text': '\n\n'.join([
                         '데스크톱컴퓨터 구매 입찰공고', '1. 입찰에 부치는 사항', '가. 품명 : 데스크톱컴퓨터', '2. 입찰참가자격',
                         '가. 「국가를 당사자로 하는 계약에 관한 법률 시행령」 제12조에 따른 경쟁입찰 참가자격을 갖춘 자'])}]}, catalog.load())
    assert b.scope.competitive is True
    assert switches.COMPETITIVE_GOODS == () and judge.v10(b) is None
    monkeypatch.setattr(switches, 'COMPETITIVE_GOODS', ('v10',))
    assert judge.v10(b) is True
    assert judge.competitive_service(b, 'v11') is False



def test_v12_title_names_other_work():
    cat = catalog.load()
    clause = '직접생산확인증명서[세부품명 : 기타행사기획및대행서비스(8014199001)]를 소지한 업체'
    assert catalog.title_names_other_work(['청소년 수련활동 위탁 운영 용역'], clause, cat)
    assert not catalog.title_names_other_work(['2026 도민 화합 행사 대행 용역'], clause, cat)
    assert not catalog.title_names_other_work(['[용역명]'], clause, cat)
    sw = '직접생산확인증명서(세부품명번호 8111219901, 세부품명 : 인터넷지원개발서비스)'
    assert not catalog.title_names_other_work(['청소년 수련활동 위탁 운영 용역'], sw, cat)
