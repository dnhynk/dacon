"""Literal-sweep group A switches (2026-09-27): V12_OBJECT_VOCAB — v12 follows an informative
title that names the procured object with none of the cited certificate product's words — and V9_MAKER — v9 takes a maker or
model named without a Latin code or a label. Each test shows the switch-off behaviour (current) and the switch-on behaviour on
a small synthetic notice, with a guard case that stays silent when the switch is on."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))

from pps_c import catalog, facts, judge, record, switches  # noqa: E402


DESIGNATION_READING = {'성격': '구매 대상의 제조사·모델 지정', '동등': '명시 없음'}
SAME_KIND_REQUIREMENT = {'역할': '참가자격 소지 요구', '인증 품목': '과업과 같은 종류'}


def notice_of(*texts, doc_type='공고문'):
    return record.build({'id': 'T', 'meta': {}, 'docs': [{'type': doc_type, 'text': '\n'.join(texts)}]})


class FakeBundle:
    def __init__(self, notice, readings=None, cands=None, **meta):
        self.notice, self._readings, self.cands, self.titles = notice, readings or {}, cands or {}, []
        base = {'P': 5e7, 'B': 5.5e7, 'law': '국가', 'work': '물품', 'codes': [('원심분리기', '4115151201')], 'clause': None,
                'region_sido': set(), 'region_basic': 0, 'T_lo': 2.3e8, 'T_hi': 2.3e8, 'local_private': False,
                'method': '제한경쟁', 'posted': None, 'region_flag': None}
        base.update(meta)
        self.meta = type('M', (), base)()

    def read(self, fam, ln):
        return self._readings.get((fam, ln.i), {})


def service(title_line, qual):
    """A non-competitive 용역 whose qualification line the model read as a same-kind certificate requirement."""
    m = {'적용계약법': '국가계약법', '업무구분': '일반용역', '계약방법': '제한경쟁', '낙찰방법': '협상에의한계약',
         '입찰추정가격': '90000000', '배정예산금액': '99000000', '조항호내용': 'None'}
    docs = [{'type': '공고문', 'text': '\n'.join(['입찰공고', '1. 입찰에 부치는 사항', title_line, '2. 입찰참가자격', qual])}]
    b = facts.build({'id': 'T', 'meta': m, 'docs': docs}, catalog.load())
    ln = next(x for x in b.notice.lines if x.text == qual)
    b.readings.setdefault('dp', {})[ln.i] = dict(SAME_KIND_REQUIREMENT)
    if all(x.i != ln.i for x in b.cands.get('dp', [])):
        b.cands['dp'] = sorted(b.cands.get('dp', []) + [ln], key=lambda x: x.i)
    return b, ln


def v9_bundle(text):
    n = notice_of('3. 규격', text)
    ln = n.lines[-1]
    return FakeBundle(n, {('model', ln.i): dict(DESIGNATION_READING)}, {'model': [ln]}), ln


def test_sweep_a_switches_default_off():
    assert switches.V12_OBJECT_VOCAB is False
    assert switches.V9_MAKER is False


# ---------------------------------------------------------------- v12: V12_OBJECT_VOCAB
EVENT_CERT = ('가. 「중소기업제품 구매촉진 및 판로지원에 관한 법률」 제9조에 의한 직접생산확인증명서'
              '[기타행사기획및대행서비스(8014199001)]를 소지한 자')


def test_v12_object_vocab_fires_when_the_title_names_another_object(monkeypatch):
    # DEV-053 form: a school trip with an event certificate (organizer 1); a drone class with the same certificate.
    trip, trip_ln = service('가. 용역명 : 2026학년도 1학년 수련활동 위탁용역(숙박)(제한경쟁·1억원미만)', EVENT_CERT)
    course, course_ln = service('가. 용역명 : 청소년 드론 체험교육 프로그램 운영 용역(제한경쟁·1억원미만)', EVENT_CERT)
    for b in (trip, course):
        assert b.scope.competitive is False
        assert judge.v12(b) is None                       # current: the same-kind reading of a listed product is trusted
    monkeypatch.setattr(switches, 'V12_OBJECT_VOCAB', True)
    assert judge.v12(trip) is trip_ln
    assert judge.v12(course) is course_ln


def test_v12_object_vocab_guards_stay_silent(monkeypatch):
    monkeypatch.setattr(switches, 'V12_OBJECT_VOCAB', True)
    # The title names the certified product family (an event): the object is the competition service.
    fair, _ = service('가. 용역명 : 2026 청소년 과학 행사 운영 용역(제한경쟁·1억원미만)', EVENT_CERT)
    assert judge.v12(fair) is None
    # A licence route "또는" the certificate (006352 form): the certificate restricts nobody.
    route, _ = service('가. 용역명 : 생활 안전·안심 경관개선사업 용역(제한경쟁·1억원미만)',
                       '가. 「건설산업기본법」에 의한 실내건축공사업 또는 「판로지원법」 제9조에 따른 직접생산확인증명서[디자인서비스(8214150201)]를 '
                       '소지하여 위 항목 중 1개 이상 충족하는 업체')
    assert judge.v12(route) is None
    # The certificate is one of several capability proofs (014423 form).
    proofs, _ = service('가. 용역명 : 무룡터널 외 4개소 환경 정비용역(제한경쟁·1억원미만)',
                        '가. 생산능력 입증서류(직접생산확인증, GR, 단체표준 등)를 갖춘 건물청소서비스(7611150101) 업체')
    assert judge.v12(proofs) is None
    # A table-header title decides nothing.
    header, _ = service('가. 용역명 : 용역기간', EVENT_CERT)
    assert judge.v12(header) is None


def test_v12_product_words_follow_the_title_object():
    cat = catalog.load()
    cleaning, event = cat.by_name['건물청소서비스'], cat.by_name['기타행사기획및대행서비스']
    assert judge.v12_product_named(cleaning, '청사 청소용역')
    assert not judge.v12_product_named(cleaning, '지방정부 도로시설물 청소용역')          # roads are not buildings
    assert not judge.v12_product_named(cleaning, '청소년 드론 체험교육 프로그램 운영 용역')   # 청소년 is no 청소 (fix_sweep_a_cleaning.py)
    assert judge.v12_product_named(event, '대한민국 전통조경대전 운영')                  # a 대전 is an exhibition
    assert not judge.v12_product_named(event, '드론 체험교육 프로그램 운영')
    assert judge.V12_ALT_LIST.search('생산능력은 직접생산확인증, GR, 단체표준 등') and not judge.V12_ALT_LIST.search('직접생산확인증명서를 소지한 자')
    assert judge.V12_TABLE_TITLE.search('용역기간') and judge.V12_TABLE_TITLE.search('설계금액(원) | 사업기간(일)')


# ---------------------------------------------------------------- v9: V9_MAKER
def test_v9_maker_fires_on_a_maker_named_without_a_code(monkeypatch):
    maker, maker_ln = v9_bundle('전자전극봉식가습기는 스위스 노드만사 동등이상의 외산 제품을 사용하여야 한다.')
    cpu, cpu_ln = v9_bundle('① CPU : Core i5-3550 또는 동등 이상')
    cell, cell_ln = v9_bundle('제조사 엔젤')
    for b in (maker, cpu, cell):
        assert judge.v9(b) is None                        # current: no designation wording and no Latin model code
    monkeypatch.setattr(switches, 'V9_MAKER', True)
    assert judge.v9(maker) is maker_ln
    assert judge.v9(cpu) is cpu_ln
    assert judge.v9(cell) is cell_ln


def test_v9_maker_guards_stay_silent(monkeypatch):
    monkeypatch.setattr(switches, 'V9_MAKER', True)
    os_line, _ = v9_bundle('④ O/S : Windows 11 또는 동등 이상')                      # a platform name is no maker
    issuer, _ = v9_bundle('○ 물품제조자증명서 : 물품의 원제조사(KULTHORN KIRBY社)에서 발행하는 제조자증명서')   # DEV-152 = 0
    same, _ = v9_bundle('동일 제조사')
    blank, _ = v9_bundle('제조사 성적서')
    for b in (os_line, issuer, same, blank):
        assert judge.v9(b) is None
    for t in ('계약 때 규격 제품 공급이 가능한 제조사를 선택', '발주기관의 요구사항', '주식회사 ㅇㅇ', '자회사 제품'):
        assert not judge.maker_named(t), t
    for t in ('원제조사 MITSUBISHI社 제품일 것', '감압밸브는 스파이렉스사코(주) 동등이상품을 사용한다', 'MICOM DDC400의 콘트롤 시스템'):
        assert judge.maker_named(t), t
