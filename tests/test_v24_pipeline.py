"""v24p(pps_c/v24): 기본값 꺼짐은 판정에 영향이 없고, 검증 게이트·파서·축이 계약(DESIGN_V24.md)대로 동작한다."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'submission'))
from pps_c import catalog, facts, judge, switches  # noqa: E402
from pps_c.runner import MockEngine  # noqa: E402
from pps_c.v24 import compare, parse, prompt, select, stage  # noqa: E402

CAT = catalog.load()
META = {'적용계약법': '지방계약법', '업무구분': '일반용역', '계약방법': '제한경쟁', '낙찰방법': '협상에 의한 계약',
        '배정예산금액': 170000000, '입찰추정가격': 154545455, '지역제한여부': 'N', '제한지역코드목록': None,
        '업종제한여부': 'N', '면허업종제한목록': None, '공고게시일자': '2026-03-02'}


class Req:
    def __init__(self, *a):
        self.rec, self.fam, self.cands, self.extra, self.token_ids, self.schema, self.max_tokens, self.budget = a


def bundle(*lines, **meta):
    m = dict(META, **meta)
    text = '\n'.join(('입찰공고', '1. 입찰에 부치는 사항') + lines + ('2. 입찰참가자격', '가. 자격을 갖춘 자'))
    return facts.build({'id': 'T', 'meta': m, 'docs': [{'type': '공고문', 'text': text}]}, CAT)


def fed(b, **fields):
    """모델 출력을 흉내 내어 b.v24p를 세운다. fields: 항목 → [(줄 텍스트 일부, 값)]."""
    by_text = {ln.text: ln for ln in b.notice.lines}
    obj = {name: [] for name in prompt.FIELD_NAMES}
    for name, items in fields.items():
        for hint, value in items:
            ln = next(x for t, x in by_text.items() if hint in t)
            obj[name].append({'줄': ln.i, '이름표': name, '값': value})
    assert stage.consume(b, select.excerpt(b.notice), json.dumps(obj, ensure_ascii=False))
    return b


def test_switch_defaults_off():
    assert getattr(switches, 'V24_PIPELINE', False) is False
    assert getattr(switches, 'V24P_ATTACH', False) is False


def test_off_has_no_effect_on_judge():
    b = fed(bundle('나. 사업예산 : 61,560,000원', ), 예산=[('사업예산', '61,560,000원')])
    b.meta.B, b.meta.P = 61000000, 55454545
    assert compare.verdict(b) is not None                        # v24p 자체는 발화한다
    saved = getattr(switches, 'V24_PIPELINE', False)
    switches.V24_PIPELINE = False
    try:
        with_items = judge.judge(b)
        b.v24p = {}
        without = judge.judge(b)
    finally:
        switches.V24_PIPELINE = saved
    assert with_items == without                                  # 꺼짐: b.v24p가 있어도 판정이 같다


def test_gate_drops_values_not_in_the_line():
    b = bundle('나. 사업예산 : 금일억칠천만원( ￦ 170,000,000원, 부가가치세 포함)')
    cands = select.excerpt(b.notice)
    ln = next(x for x in b.notice.lines if '사업예산' in x.text)
    out = {name: [] for name in prompt.FIELD_NAMES}
    out['예산'] = [{'줄': ln.i, '이름표': '사업예산', '값': '180,000,000원'},          # 지어낸 값
                 {'줄': ln.i, '이름표': '사업예산', '값': '금일억칠천만원( ￦ 170,000,000원'},
                 {'줄': 0, '이름표': '사업예산', '값': '170,000,000원'}]           # 잘못 가리킨 줄: 발췌에서 찾는다
    assert stage.consume(b, cands, json.dumps(out, ensure_ascii=False))
    assert [v for _, _, v in b.v24p['예산']] == ['금일억칠천만원( ￦ 170,000,000원', '170,000,000원']
    assert all(x.i == ln.i for x, _, _ in b.v24p['예산'])
    assert not stage.consume(b, cands, 'not json')


def test_parse_money_and_codes():
    assert parse.money('금일억칠천만원( ￦ 170,000,000원') == [170000000.0]
    assert parse.money('3.5억원') == [350000000.0]
    assert parse.money('2천5백만원') == [25000000.0]
    assert parse.money('금 39,730,000원') == [39730000.0]
    assert parse.money('일금삼천칠백구십삼만원정') == [37930000.0]
    assert parse.codes('식품판매업(업종코드: 5210)') == {'5210'}
    assert parse.codes('학술연구용역(1169)') == {'1169'}
    assert parse.codes('5246') == {'5246'}
    assert parse.permutes(37930000, 39730000) and not parse.permutes(39730000, 39730000)


def test_amount_axis_fires_only_on_a_same_field_difference():
    hit = lambda b: compare.explain(b)[0]
    assert hit(fed(bundle('나. 사업예산 : 61,560,000원', 배정예산금액=61000000, 입찰추정가격=55454545),
                   예산=[('사업예산', '61,560,000원')])) == 'amount'
    assert hit(fed(bundle('나. 사업예산 : 170,000,000원'), 예산=[('사업예산', '170,000,000원')])) is None      # 동일
    assert hit(fed(bundle('나. 추정가격 : 170,000,000원'), 추정가격=[('추정가격', '170,000,000원')])) is None  # VAT 포함 값
    assert hit(fed(bundle('나. 사업예산 : 154,545,000원'), 예산=[('사업예산', '154,545,000원')])) is None    # 절사
    assert hit(fed(bundle('나. 사업예산 : 37,930,000원', 배정예산금액=39730000, 입찰추정가격=36118183),
                   예산=[('사업예산', '37,930,000원')])) == 'transposed'
    assert hit(fed(bundle('가. 1차년도 : 85,000,000원', '나. 사업예산 : 170,000,000원'),
                   예산=[('1차년도', '85,000,000원'), ('사업예산', '170,000,000원')])) is None            # 내역
    assert hit(fed(bundle('나. 사업예산 : 60,000,000원'), 예산=[('사업예산', '60,000,000원')])) is None      # 0.5배 아래


def test_base_zone_axis():
    hit = lambda b: compare.explain(b)[0]
    assert hit(fed(bundle('다. 기초금액 : 166,600,000원'), 기초금액=[('기초금액', '166,600,000원')])) is None  # 98%
    assert hit(fed(bundle('다. 기초금액 : 250,833,000원', 배정예산금액=228030000, 입찰추정가격=207300000),
                   기초금액=[('기초금액', '250,833,000원')])) is None                 # 정확히 B×1.1: 부가가치세 관계는 불일치가 아니다
    assert hit(fed(bundle('다. 기초금액 : 260,000,000원', 배정예산금액=228030000, 입찰추정가격=207300000),
                   기초금액=[('기초금액', '260,000,000원')])) == 'base_zone'


def test_licence_region_method_axes():
    hit = lambda b: compare.explain(b)[0]
    lic = dict(업종제한여부='Y', 면허업종제한목록='[식품판매업(집단급식소식품판매업)(5246)]')
    assert hit(fed(bundle('식품판매업(업종코드: 5210)으로 입찰참가자격을 등록한 자', **lic),
                   업종=[('업종코드', '식품판매업(업종코드: 5210)')])) == 'licence'
    assert hit(fed(bundle('식품판매업(업종코드: 5246)으로 입찰참가자격을 등록한 자', **lic),
                   업종=[('업종코드', '식품판매업(업종코드: 5246)')])) is None
    assert hit(fed(bundle('허용업종: 식품판매업(5210)은 참여 불가', **lic), 업종=[('허용업종', '식품판매업(5210)')])) is None
    reg = dict(지역제한여부='Y', 제한지역코드목록='경기도')
    assert hit(fed(bundle('본점 소재지를 계속 경기도 또는 제주도에 둔 업체', **reg),
                   지역제한=[('본점 소재지', '경기도 또는 제주도')])) == 'region'
    assert hit(fed(bundle('본점 소재지를 계속 경기도에 둔 업체', **reg), 지역제한=[('본점 소재지', '경기도')])) is None
    assert hit(fed(bundle('면허보완을 위하여 서울특별시 소재 업체와 공동도급이 가능', **reg),
                   지역제한=[('공동도급', '서울특별시')])) is None
    assert hit(fed(bundle('ㅇ 공 고 명 : 감축사업 활성화 용역(제한경쁁·1억원 미만)'.replace('쁁', '쟁'), 배정예산금액=188000000, 입찰추정가격=170909091),
                   계약방법=[('공 고 명', '제한경쟁·1억원 미만')])) == 'method'                          # 금액대가 다르다
    assert hit(fed(bundle('마. 계약방법 : 일반경쟁( 총액, 협상에 의한 계약)'),
                   계약방법=[('계약방법', '일반경쟁( 총액, 협상에 의한 계약)')])) == 'method'              # 머리 12줄, meta 제한경쟁
    assert hit(fed(bundle('마. 계약방법 : 제한경쟁( 총액)'), 계약방법=[('계약방법', '제한경쟁( 총액)')])) is None


def test_excerpt_titles_cap_and_request():
    b = bundle(*[f'{k}) 항목 {k} 금액 : {k + 1},000,000원' for k in range(90)])
    cands = select.excerpt(b.notice, cap=20)
    assert len(cands) == 20 and cands[0].i == 0                     # 제목 줄은 남는다
    eng = MockEngine()
    r = stage.request(eng, b, 0, Req)
    assert r.fam == 'v24p' and len(r.cands) <= select.CAP and r.max_tokens == prompt.MAX_TOKENS
    assert set(r.schema['required']) == set(prompt.FIELD_NAMES)
    text, finish, _ = eng.generate([(r.token_ids, r.schema, r.max_tokens, 0)])[0]
    assert stage.consume(b, r.cands, text) and all(v == [] for v in b.v24p.values())   # schema-valid empty lists
    assert compare.verdict(b) is None


def test_guards_bands_addresses_phone_codes_and_lots():
    hit = lambda b: compare.explain(b)[0]
    assert hit(fed(bundle('가. 적격심사(추정가격 5억원미만 2.5억원이상인 용역 평가기준)에 의함'),
                   추정가격=[('적격심사', '추정가격 5억원미만 2.5억원이상')])) is None                   # 금액대
    assert hit(fed(bundle('- 추정가격 1억원 미만 : 5인 이상'), 추정가격=[('추정가격', '1억원')])) is None     # 줄에서 미만이 따른다
    assert hit(fed(bundle('▪입 찰 명: 유속계 구매설치(일반경쟁·3억원미만) ▪납 품 기 한: 40일'),
                   예산=[('입 찰 명', '유속계 구매설치(일반경쟁·3억원미만)')])) is None                      # 제목 괄호 금액대
    assert hit(fed(bundle('기초금액 1) 1호차: 59,196,160원', 배정예산금액=102314420, 입찰추정가격=93013109),
                   기초금액=[('기초금액', '59,196,160원')])) is None                                        # 호차별 부분 금액
    reg = dict(지역제한여부='Y', 제한지역코드목록='충청남도')
    assert hit(fed(bundle('- 오프라인 : 방문 또는 우편(전남 [지역:r2|단위=기초|광역=전라남도] [상세주소] 응답센터)', **reg),
                   지역제한=[('오프라인', '전남 [지역:r2|단위=기초|광역=전라남도]')])) is None                # 주소
    assert hit(fed(bundle('나. 제출장소: [지역:r1|단위=기초|광역=경상북도] [상세주소] 행정실', **reg),
                   지역제한=[('제출장소', '[지역:r1|단위=기초|광역=경상북도]')])) is None
    lic = dict(업종제한여부='Y', 면허업종제한목록='[검체검사수탁업(1447)]')
    assert hit(fed(bundle('- 규격 관련: 진검사의학과(☎ [전화번호]), 병리과(1851), 직업환경의학과(4792)', **lic),
                   업종=[('규격', '병리과(1851), 직업환경의학과(4792)')])) is None                          # 내선번호
    assert hit(fed(bundle('합 계(총 4건, ‘26년 사업비 417백만원)', 배정예산금액=300000000, 입찰추정가격=272727273),
                   예산=[('사업비', '417백만원')])) is None                                                  # 여러 건 합계
    assert parse.codes('폐기물수집ㆍ운반업(사업장비배출시설계폐기물)(1227)을 등록한 자') == {'1227'}
    assert parse.codes('연구용역비 산정 및 정산 기준(2026)') == set()


def test_group_agreement_listings_and_line_codes():
    hit = lambda b: compare.explain(b)[0]
    # 같은 등록 필드(추정가격)에 등록 금액이 적혀 있으면 다른 추정가격 칸은 구역별 금액이다.
    assert hit(fed(bundle('추정가격', '269,890,909', '추정가격(원)', '100,752,800', '169,140,200', 배정예산금액=296880000, 입찰추정가격=269890909),
                   추정가격=[('269,890,909', '269,890,909'), ('100,752,800', '100,752,800'), ('169,140,200', '169,140,200')])) is None
    # 다른 필드가 일치해도 해당 필드는 따로 본다(추정가격 하나만 적혀 있고 다르면 발화).
    assert hit(fed(bundle('추정금액', '105,223,550', '추정가격', '83,747,950', 배정예산금액=105223550, 입찰추정가격=105223550),
                   추정금액=[('105,223,550', '105,223,550')], 추정가격=[('83,747,950', '83,747,950')])) == 'amount'
    # 서로 다른 값 3개 이상은 여러 건의 목록이다.
    assert hit(fed(bundle('1 | 사업 A | 2,883,000,000', '3 | 사업 C | 1,725,000,000', '4 | 사업 D | 5,732,000,000', 배정예산금액=1354000000, 입찰추정가격=1230909091),
                   예산=[('사업 A', '2,883,000,000'), ('사업 C', '1,725,000,000'), ('사업 D', '5,732,000,000')])) is None
    # 한 값 안의 숫자 표기가 일치하면 한글 표기의 오기는 공고 내부 문제다.
    assert hit(fed(bundle('❍ 금161,589,000원(금일억오천일백팔십만원) [부가가치세 포함]', 배정예산금액=161589000, 입찰추정가격=146899090),
                   예산=[('금161,589,000원', '금161,589,000원(금일억오천일백팔십만원)')])) is None
    # 업종: 한 줄의 대안 코드 중 하나가 등록돼 있으면 일치(모델이 한 조각만 옮겨도).
    lic = dict(업종제한여부='Y', 면허업종제한목록='[국내여행업(1263)]업종 또는[종합여행업(1261)]')
    assert hit(fed(bundle('- [국내외여행업(1262)] 업종 또는 [종합여행업(1261)] 업종을 등록한 업체', **lic),
                   업종=[('업종', '[국내외여행업(1262)]')])) is None
    lic2 = dict(업종제한여부='Y', 면허업종제한목록='[폐기물종합재활용업(지정폐기물외 폐기물)(6786)]업종 또는[폐기물중간재활용업(6770)]')
    assert hit(fed(bundle('• 폐기물수집ㆍ운반업(사업장비배출시설계폐기물)(1227)을 등록한 자', **lic2),
                   업종=[('폐기물수집', '폐기물수집ㆍ운반업(사업장비배출시설계폐기물)(1227)')])) == 'licence'
    # 기초금액: 이름표가 그 줄이나 바로 위 줄에 없는 칸(학교별 행)은 비교하지 않는다.
    assert hit(fed(bundle('기초금액', '[수요기관(중학교)]: 8,088,110원', '[수요기관(초등학교)]: 55,124,200원', 배정예산금액=71300420, 입찰추정가격=64818564),
                   기초금액=[('초등학교', '55,124,200원')])) is None
    assert hit(fed(bundle('다. 기초금액 : 250,833,000원', 배정예산금액=228030000, 입찰추정가격=207300000),
                   기초금액=[('기초금액', '250,833,000원')])) is None                 # 정확히 B×1.1: 부가가치세 관계는 불일치가 아니다
    assert hit(fed(bundle('다. 기초금액 : 260,000,000원', 배정예산금액=228030000, 입찰추정가격=207300000),
                   기초금액=[('기초금액', '260,000,000원')])) == 'base_zone'
    # 계약방법: meta 수의계약 + 견적 문구는 기존 축의 예외와 같이 침묵.
    assert hit(fed(bundle('나. 입찰방법 : 지명경쟁(조합추천)', '다. 소액수의 견적 제출', 계약방법='수의계약'),
                   계약방법=[('입찰방법', '지명경쟁(조합추천)')])) is None
    # 게이트: 지역 표식만 있는 줄에서 모델이 광역 이름을 옮겨도 대조된다.
    assert parse.norm_gate('계속 [지역:r1|단위=기초|광역=경기도]에 소재한 업체') == '계속경기도에소재한업체'
    assert parse.norm_gate('[지역:r2|단위=읍면동|광역=미상] 가사') == '가사'


def test_base_zone_parts_beside_a_total_and_unit_formulas():
    hit = lambda b: compare.explain(b)[0]
    assert hit(fed(bundle('2.예상산출금액: 금92,000,000원(2,500원×160명×230일)', '기초금액 가. 본교: 금23,000,000원(2,500원×40명×230일)',
                          '나. 복용분교: 금69,000,000원(2,500원×120명×230일)', 배정예산금액=92000000, 입찰추정가격=83636364),
                   기초금액=[('복용분교', '금69,000,000원')])) is None                                     # 단가 산식 + 옆의 총액
    assert hit(fed(bundle('구분', '유치원', '계', '기초금액', '금88,930,000원', '금94,865,000원', '금183,795,000원',
                          배정예산금액=183795000, 입찰추정가격=167086364),
                   기초금액=[('금88,930,000원', '금88,930,000원')])) is None                                # 구역별 행 옆에 합계 = B
