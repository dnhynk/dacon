"""Expert audit X5 criteria for v14–v18 (runs/rebuild_c/transfer_20260925/audit/expert/X5/REPORT.md). Each switch is off by
default; each test shows the current verdict with the switch off and the practitioner's verdict with it on."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))

from pps_c import catalog, facts, judge, switches  # noqa: E402

X5 = ('X5_WASTE_EXEMPT', 'X5_SW_EXEMPT', 'X5_EXC_WORDING', 'X5_DP_LISTED_ANY', 'X5_V17_CLASS', 'X5_TAG_NOT_POSITIVE',
      'X5_NOT_PROCUREMENT', 'X5_INSURANCE_EXEMPT', 'X5_METHOD_STATEMENT', 'X5_DECL_BID')


def service(title_line, qual='가. 경쟁입찰 참가자격을 갖춘 자', extra=(), P='90000000', body=None):
    m = {'적용계약법': '국가계약법', '업무구분': '일반용역', '계약방법': '제한경쟁', '낙찰방법': '협상에의한계약',
         '입찰추정가격': P, '배정예산금액': str(int(int(P) * 1.1)), '조항호내용': 'None'}
    body = body or ['입찰공고', '1. 입찰에 부치는 사항', title_line, *extra, '2. 입찰참가자격', qual]
    return facts.build({'id': 'T', 'meta': m, 'docs': [{'type': '공고문', 'text': '\n'.join(body)}]}, catalog.load())


def read(b, fam, start, **fields):
    ln = next(x for x in b.notice.lines if x.text.startswith(start))
    b.cands.setdefault(fam, []).append(ln)
    b.readings.setdefault(fam, {})[ln.i] = fields
    return ln


def toggle(monkeypatch, name, b, rule):
    monkeypatch.setattr(switches, name, False)
    off = rule(b)
    monkeypatch.setattr(switches, name, True)
    return off, rule(b)


def test_switches_default_to_current():
    assert not any(getattr(switches, k) for k in X5)


def test_waste_service_is_an_absence_exception(monkeypatch):
    off, on = toggle(monkeypatch, 'X5_WASTE_EXEMPT', service('가. 용역명: 건설폐기물 처리 용역'), judge.v18)
    assert off is True and on is None
    off, on = toggle(monkeypatch, 'X5_WASTE_EXEMPT', service('가. 용역명: 청사 시설물 유지관리 용역'), judge.v18)
    assert off is True and on is True


def test_sw_project_below_20eok_is_the_sw_law_band(monkeypatch):
    b = service('가. 용역명: 청사 시설물 유지관리 용역', P='150000000')
    b.sw_project = '소프트웨어 개발·구축·유지관리·운영'
    off, on = toggle(monkeypatch, 'X5_SW_EXEMPT', b, judge.v16)
    assert off is True and on is None


def test_exception_wording_in_any_document(monkeypatch):
    b = service('가. 용역명: 청사 시설물 유지관리 용역', extra=('※ 본 건은 판로지원법 시행령에 의거한 제한경쟁입찰 의무적용 대상이 아닙니다.',))
    off, on = toggle(monkeypatch, 'X5_EXC_WORDING', b, judge.v18)
    assert off is True and on is None


def test_listed_goods_by_direct_production_is_a_competition_product(monkeypatch):
    b = service('가. 용역명: 전시물 제작 설치', qual='가. 중소기업자로서 중소기업 확인서를 소지한 자', P='300000000',
                extra=('나. 실물모형및전시물(6010989901) 직접생산확인증명서를 소지한 자',))
    read(b, 'size', '가. 중소기업자로서', 역할='참가자격 제한')
    dp = read(b, 'dp', '나. 실물모형', 역할='참가자격 소지 요구')
    b.readings['dp'][dp.i]['인증 품목'] = '과업과 같은 종류'
    monkeypatch.setattr(judge, 'qual_section', lambda ln, notice=None: True)
    off, on = toggle(monkeypatch, 'X5_DP_LISTED_ANY', b, judge.v14)
    assert off is not None and on is None


class Line:
    def __init__(self, text, sec='QUAL', i=0):
        self.text, self.sec, self.i, self.doc, self.doc_type = text, sec, i, 0, '공고문'


def test_v17_small_class_companions_and_tag_only(monkeypatch):
    assert judge.small_with_companions([Line('소기업·소상공인·벤처기업·창업기업으로서 확인서를 소지한 자')])
    assert judge.small_with_companions([Line('중소기업 확인서를 소지한 자(소기업,소상공인으로 제한)')])
    assert not judge.small_with_companions([Line('중소기업자 또는 소상공인으로서 중·소기업·소상공인 확인서를 소지한 자')])
    assert judge.tag_only([Line('(국내입찰/규격가격동시/제한경쟁_소기업)', sec='TOP')])
    assert not judge.tag_only([Line('중소기업자로서 확인서를 소지한 자')])
    b = service('가. 용역명: 청사 시설물 유지관리 용역')
    venture = Line('소기업·소상공인·벤처기업·창업기업으로서 확인서를 소지한 자')
    monkeypatch.setattr(judge, 'size_state', lambda b, positive=False: ('sme', [venture], False))
    off, on = toggle(monkeypatch, 'X5_V17_CLASS', b, judge.v17)
    assert off is venture and on is None
    tag = Line('(국내입찰/규격가격동시/제한경쟁_중소기업)', sec='TOP')
    monkeypatch.setattr(judge, 'size_state', lambda b, positive=False: ('sme', [tag], False))
    off, on = toggle(monkeypatch, 'X5_TAG_NOT_POSITIVE', b, judge.v17)
    assert off is tag and on is None


def test_operator_pays_and_quote_titles(monkeypatch):
    b = service('가. 용역명: 구내식당 운영자 선정', extra=('나. 월 임대료는 정액제 300,000원 이상',))
    off, on = toggle(monkeypatch, 'X5_NOT_PROCUREMENT', b, judge.v18)
    assert off is True and on is None
    off, on = toggle(monkeypatch, 'X5_NOT_PROCUREMENT', service('가. 용역명: 청사 시설물 유지관리 견적제출 안내'), judge.v18)
    assert off is True and on is None


def test_insurance_contract_probe(monkeypatch):
    b = service('가. 용역명: 청소차량 자동차보험 가입')
    off, on = toggle(monkeypatch, 'X5_INSURANCE_EXEMPT', b, judge.v18)
    assert off is True and on is None


def test_method_statement_restricts_nobody(monkeypatch):
    b = service('가. 용역명: 식자재 납품업체 선정', extra=('나. 입찰방법: 제한경쟁(총액)이며 협상에 의한 계약, 소기업 및 소상공인',))
    read(b, 'size', '나. 입찰방법', 역할='참가자격 제한')
    off, on = toggle(monkeypatch, 'X5_METHOD_STATEMENT', b, judge.v18)
    assert off is None and on is True
    b = service('가. 용역명: 식자재 납품업체 선정', qual='가. 소기업 또는 소상공인으로서 확인서를 소지한 자',
                extra=('나. 입찰방법: 제한경쟁(총액)이며 협상에 의한 계약, 소기업 및 소상공인',))
    read(b, 'size', '나. 입찰방법', 역할='참가자격 제한')
    off, on = toggle(monkeypatch, 'X5_METHOD_STATEMENT', b, judge.v18)
    assert off is None and on is None                  # the qualification section names a class


def test_bid_section_declaration_restricts(monkeypatch):
    b = service('', body=['입찰공고', '1. 입찰에 부치는 사항', '가. 용역명: 청사 시설물 유지관리 용역', '2. 입찰 및 계약방법',
                          '가. 중소기업제품 구매촉진 및 판로지원에 관한 법률 시행령 제2조의2에 따라 중소기업자(소상공인 포함)간 '
                          '제한경쟁입찰로 진행합니다.', '3. 입찰참가자격', '가. 경쟁입찰 참가자격을 갖춘 자'])
    ln = read(b, 'size', '가. 중소기업제품', 역할='참가자격 제한')
    assert ln.sec == 'BID'
    off, on = toggle(monkeypatch, 'X5_DECL_BID', b, judge.v17)
    assert off is None and on is ln
