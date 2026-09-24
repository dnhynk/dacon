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
