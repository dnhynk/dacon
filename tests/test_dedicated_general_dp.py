"""v12 dedicated stage pps_c/dedicated/general_dp (switches.DEDICATED = ('v12',)): candidate selection, the decision rule on
hand-written model answers (blueprint positives and look-alike negatives), request building, consume, and the verdict without
a reading. Runs against the copied runtime in impl/submission."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))

from pps_c import catalog, dedicated, facts, judge, main, switches  # noqa: E402
from pps_c.dedicated import general_dp as G  # noqa: E402
from pps_c.runner import MockEngine  # noqa: E402

QUAL = ['1. 입찰에 부치는 사항', '가. 용 역 명 : {title}', '나. 추정가격 : 별도', '2. 입찰참가자격', '가. 나라장터 입찰참가자격을 등록한 자']
DEMAND_FEST = '다. 「중소기업제품 구매촉진 및 판로지원에 관한 법률」제9조 및 동법 시행령 제10조에 의한 직접생산확인증명서 [축제기획 및 대행 서비스(세부품명번호 9015189001)]를 소지한 업체'
DEMAND_EVENT = '③ 「중소기업제품 구매촉진 및 판로지원에 관한 법률」 제9조 및 같은법 시행령 제10조 규정에 의한 직접생산확인증명서[세부품명 : 기타행사기획및대행서비스(8014199001) 또는 전시회기획및대행서비스(8014198801)]를 소지한 업체'
DEMAND_PLAIN = '마. 해당업종 및 세부품명의 직접생산확인증명서를 보유한 업체여야 합니다.'
DEMAND_FRUIT = '마. 본 입찰 대상 물품(과일류, 세부품명번호 5030990101)에 대한 직접생산확인증명서를 보유한 자이어야 합니다.'
PENALTY = '2) 규격 착오 등으로 계약을 체결하지 않거나, 하도급 생산, 타사제품 납품 등 직접생산 조건을 위반할 경우 관계 법령에 따라 부정당업자로 제재되어 불이익을 받을 수 있습니다.'
VALIDITY = '※ 직접생산확인증명서는 전자입찰서 제출마감일 전일까지 발급된 것으로 공공구매정보망(www.smpp.go.kr)에 등록되어 있어야 하고, 유효기간 내에 있어야 합니다.'
NOT_REQUIRED = '※ 본 사업의 ‘드론’은 중소기업 간 경쟁제품에 해당하나 추정가격 1천만 원 미만으로, 직접생산확인증명서를 별도로 요구하지 않습니다.'
ALTERNATIVE = '다. 「중소기업제품 구매촉진 및 판로지원에 관한 법률」 제9조에 따른 직접생산여부를 확인 할 수 있는 증명서류(직접생산확인증명서) 또는 정품 공급증명서를 제출할 수 있는 자'


def rec(title, lines, work='일반용역', P=1e8, codes=None, license_=None, docs=None):
    meta = {'업무구분': work, '적용계약법': '지방계약법', '계약방법': '제한경쟁', '입찰추정가격': P, '세부품명번호목록': codes,
            '면허업종제한목록': license_, '조항호내용': None}
    text = '\n'.join(l.format(title=title) for l in QUAL) + '\n' + '\n'.join(lines)
    return {'id': 'T', 'meta': meta, 'docs': [{'type': '공고문', 'text': text}] + (docs or [])}


def bundle(*args, **kw):
    return facts.build(rec(*args, **kw), catalog.load())


def answer(demand='참가자격_필수', subject=G.NONE_ITEM, scope='조건없음_또는_판단불가', line=-1, cited=(), evidence=''):
    return {'demand': demand, 'demand_line': line, 'cited_items': list(cited), 'subject_summary': 'x', 'subject_item': subject,
            'scope_condition': scope, 'evidence': evidence}


def read(b, **kw):
    setattr(b, G.FAM, answer(**kw))
    return b


def line_of(b, needle):
    return next(ln for ln in b.notice.lines if needle in ln.text)


# ---------------------------------------------------------------- wiring
def test_stage_is_registered():
    assert switches.DEDICATED in ((), ('v12',))          # the copy sets ('v12',) for the mock run; the canonical default is ()
    assert dedicated.stage('v12') is G and G.FAM == 'd_general_dp'


# ---------------------------------------------------------------- candidate selection
def test_candidates_need_a_demand_or_bare_mention():
    assert G.candidate_windows(bundle('행사 대행 용역', [DEMAND_FEST]).notice)
    assert not G.candidate_windows(bundle('학술 연구 용역', [PENALTY]).notice)
    assert not G.candidate_windows(bundle('콘크리트관 구매', [VALIDITY], work='물품(내자)', codes='폴리에스테르수지콘크리트관[4014210906]').notice)
    assert not G.candidate_windows(bundle('드론 구매', [NOT_REQUIRED], work='물품(내자)', codes='드론[2513189901]').notice)
    assert not G.candidate_windows(bundle('청소 용역', ['가. 청소 인력 3명 이상']).notice)


def test_split_term_and_tags():
    b = bundle('통학차량 임차용역', ['라. 판로지원법 제9조에 의한 직접', '생산확인증명서(세부품명: 통학운송서비스, 세부품명번호: 7811189902)를 소지한 업체이어야 합니다.'])
    wins = G.candidate_windows(b.notice)
    assert len(wins) == 1 and wins[0]['kind'] == 'demand' and wins[0]['has_cert'] and len(wins[0]['core']) == 2
    kinds = {w['kind'] for w in G.windows(bundle('x', [DEMAND_FEST, PENALTY, VALIDITY, NOT_REQUIRED]).notice)}
    assert kinds == {'demand', 'penalty', 'validity', 'not_required'}


def test_cpu_demand_reads_possession_not_conditions():
    assert G.cpu_demand(G.windows(bundle('x', [DEMAND_PLAIN]).notice)) == '참가자격_필수'
    assert G.cpu_demand(G.windows(bundle('x', [ALTERNATIVE]).notice)) == '안내문구만'
    assert G.cpu_demand(G.windows(bundle('x', ['∘ 중소기업자간 경쟁제품으로 입찰공고한 경우 입찰참가자는 반드시 직접생산확인증명서를 보유(유효기간 내)하여야 하며']).notice)) == '안내문구만'
    assert G.cpu_demand(G.windows(bundle('x', ['바. 입찰참가자는 해당 물품을 직접 생산하는 업체이어야 하며, OEM 납품은 허용하지 않음']).notice)) == '안내문구만'


# ---------------------------------------------------------------- decision rule on hand-written answers
def test_registered_code_decides_goods():
    fruit = bundle('늘봄학교 간식 구매', [DEMAND_FRUIT], work='물품(내자)', P=3.3e7, codes='과일류[5030990101]')
    assert G.verdict(read(fruit)) is line_of(fruit, '과일류')                              # DEV-15: unlisted code
    server = bundle('서버 구매', [DEMAND_PLAIN], work='물품(내자)', P=2.4e7, codes='컴퓨터서버[4321150102]')
    assert G.precheck(server, catalog.load()) and G.verdict(read(server)) is None          # listed code: lawful demand
    cap = bundle('토목용 보강재 구매', [DEMAND_PLAIN], work='물품(내자)', P=12e8, codes='그리드형토목용보강재[3012178401]')
    assert G.verdict(read(cap)) is line_of(cap, '해당업종')                                 # listed but 10억 cap exceeded


def test_price_cap_on_the_cited_event_item():
    fest = bundle('『마늘한우 축제』행사대행 용역', [DEMAND_FEST], P=327272727, license_='[기타자유업(행사대행업)(9901)]')
    assert G.verdict(read(fest, subject='축제기획및대행서비스')) is line_of(fest, '축제기획')   # DEV-054: 3.27억 ≥ 3억
    assert G.verdict(read(bundle('『마늘한우 축제』행사대행 용역', [DEMAND_FEST], P=2e8), subject='축제기획및대행서비스')) is None
    robot = bundle('로봇플러스 페스티벌 기획·운영 용역', [DEMAND_EVENT], P=5.4e8)
    assert G.verdict(read(robot, subject='기타행사기획및대행서비스')) is None              # DEV-060: cited 행사기획, 10억 cap holds
    assert G.verdict(read(robot, subject='축제기획및대행서비스')) is None                   # same family: the cited item's cap applies


def test_subject_outside_the_list_and_scope_conditions():
    study = bundle('(위탁과제) 알츠하이머 후보소재 비교분석', ['다. 품 명: 농림수산연구조사서비스', DEMAND_EVENT], P=2.27e7, license_='[학술.연구용역(1169)]')
    assert G.verdict(read(study)) is line_of(study, '기타행사기획')                          # DEV-053: certificate 품명 listed, subject not
    trip = bundle('숙박형 현장체험학습 위탁용역', [DEMAND_PLAIN], P=6e8, license_='[종합여행업(1261)]')
    assert G.verdict(read(trip)) is line_of(trip, '해당업종')                                # DEV-049
    assert G.verdict(read(trip, subject='통학운송서비스')) is None
    video = bundle('맞춤형 교육콘텐츠 제작 용역', ['마. 직접생산확인증명서 [동영상제작서비스(8213160301)]를 소지한 업체'], P=3e8)
    assert G.verdict(read(video, subject='동영상제작서비스', scope='조건불충족')) is line_of(video, '동영상제작')
    assert G.verdict(read(video, subject='동영상제작서비스', scope='조건충족')) is None


def test_demand_classes_that_never_fire():
    trip = bundle('숙박형 현장체험학습 위탁용역', [DEMAND_PLAIN], P=6e8)
    for demand in ('선택_또는_대체가능', '계약후_제출서류', '요구하지_않음_명시', '안내문구만'):
        assert G.verdict(read(trip, demand=demand)) is None, demand
    assert G.verdict(read(trip, demand='제출서류_필수')) is line_of(trip, '해당업종')


def test_evidence_line_follows_the_model_then_the_tags():
    trip = bundle('숙박형 현장체험학습 위탁용역', [PENALTY, DEMAND_PLAIN], P=6e8)
    demand_ln = line_of(trip, '해당업종')
    assert G.verdict(read(trip, line=demand_ln.i)) is demand_ln
    assert G.verdict(read(trip, line=-1, evidence='해당업종 및 세부품명의 직접생산확인증명서를 보유한 업체')) is demand_ln
    assert G.verdict(read(trip, line=line_of(trip, '용 역 명').i)) is demand_ln           # a pointed line without the term is ignored


# ---------------------------------------------------------------- request building
class LenEngine:
    max_model_len = 16384

    def __init__(self, per_char=0.5):
        self.per_char, self.calls = per_char, 0

    def token_ids(self, messages, thinking=False):
        self.calls += 1
        return list(range(int(sum(len(m['content']) for m in messages) * self.per_char)))


def test_request_is_built_only_when_the_model_is_needed():
    eng = LenEngine()
    trip = bundle('숙박형 현장체험학습 위탁용역', [DEMAND_PLAIN], P=6e8)
    r = G.request(eng, trip, 3, main.Request)
    assert r is not None and r.rec == 3 and r.fam == G.FAM and r.max_tokens == G.MAX_TOKENS and r.budget == 0
    assert line_of(trip, '해당업종') in r.cands and all(ln in trip.notice.lines for ln in r.cands)
    assert r.schema['properties']['demand']['enum'][-1] == '불명' and G.NONE_ITEM in r.schema['properties']['subject_item']['enum']
    assert len(r.token_ids) + r.max_tokens <= G.PROMPT_TOKEN_LIMIT
    assert G.request(eng, bundle('청소 용역', ['가. 청소 인력 3명 이상']), 0, main.Request) is None             # no candidate
    server = bundle('서버 구매', [DEMAND_PLAIN], work='물품(내자)', codes='컴퓨터서버[4321150102]')
    assert G.request(eng, server, 0, main.Request) is None                                                    # CPU decides


def test_request_shrinks_to_the_token_limit():
    long_lines = [DEMAND_PLAIN] + [f'{k}) 직접생산확인증명서 관련 안내 문장 ' + '가' * 300 for k in range(12)]
    long_docs = [{'type': '과업지시서', 'text': '\n'.join('과업 내용 ' + '나' * 300 for _ in range(30))}]
    trip = bundle('숙박형 현장체험학습 위탁용역', long_lines, P=6e8, docs=long_docs)
    eng = LenEngine(per_char=2.0)
    r = G.request(eng, trip, 0, main.Request)
    assert r is not None and len(r.token_ids) + r.max_tokens <= G.PROMPT_TOKEN_LIMIT and eng.calls > 1
    assert line_of(trip, '해당업종') in r.cands


# ---------------------------------------------------------------- consume
def test_consume_valid_prefixed_invalid_and_mock():
    eng = LenEngine()
    trip = bundle('숙박형 현장체험학습 위탁용역', [DEMAND_PLAIN], P=6e8)
    r = G.request(eng, trip, 0, main.Request)
    good = json.dumps(answer(line=line_of(trip, '해당업종').i), ensure_ascii=False)
    assert G.consume(trip, r.cands, good) is True and getattr(trip, G.FAM)['demand'] == '참가자격_필수'
    assert G.consume(trip, r.cands, '<|channel>생각<channel|>' + good) is True
    assert G.consume(trip, r.cands, 'not json') is False
    assert G.consume(trip, r.cands, json.dumps(dict(answer(), demand='없는값'), ensure_ascii=False)) is False
    assert G.consume(trip, r.cands, json.dumps({'demand': '참가자격_필수'})) is False
    assert G.consume(trip, r.cands, json.dumps(dict(answer(), cited_items='x'))) is False
    mock = MockEngine().generate([(r.token_ids, r.schema, r.max_tokens)])[0][0]
    assert G.consume(trip, r.cands, mock) is True
    reading = getattr(trip, G.FAM)
    assert reading['demand'] == '불명' and reading['subject_item'] == '불명' and reading['demand_line'] == -1
    assert G.verdict(trip) is line_of(trip, '해당업종')          # 불명 falls back to the CPU default (unlisted service, plain demand)


def test_main_consume_routes_to_the_stage(monkeypatch):
    monkeypatch.setattr(switches, 'DEDICATED', ('v12',))
    trip = bundle('숙박형 현장체험학습 위탁용역', [DEMAND_PLAIN], P=6e8)
    r = G.request(LenEngine(), trip, 0, main.Request)
    assert main.consume(trip, r, json.dumps(answer(), ensure_ascii=False)) is True and getattr(trip, G.FAM)['subject_item'] == G.NONE_ITEM


# ---------------------------------------------------------------- verdict without a reading
def test_verdict_without_reading_uses_cpu_defaults():
    fruit = bundle('늘봄학교 간식 구매', [DEMAND_FRUIT], work='물품(내자)', P=3.3e7, codes='과일류[5030990101]')
    assert G.verdict(fruit) is line_of(fruit, '과일류')
    trip = bundle('숙박형 현장체험학습 위탁용역', [DEMAND_PLAIN], P=6e8, license_='[종합여행업(1261)]')
    assert trip.scope.competitive is False and G.verdict(trip) is line_of(trip, '해당업종')
    bus = bundle('초등학교 통학차량 임차용역', ['라. 직접생산확인증명서[통학운송서비스(세부품명번호: 7811189902)]를 소지한 업체이어야 합니다.'],
                 P=1.3e8, license_='[여객자동차운수사업(구역여객자동차운송사업-전세버스)(5805)]')
    assert bus.scope.competitive is True and G.verdict(bus) is None
    assert G.verdict(bundle('학술 연구 용역', [PENALTY])) is None
    assert G.verdict(bundle('크로마토그래피 구매', [ALTERNATIVE], work='물품(내자)', codes='액체크로마토그래피[4111570501]')) is None


def test_judge_takes_the_stage_verdict(monkeypatch):
    monkeypatch.setattr(switches, 'DEDICATED', ('v12',))
    fruit = bundle('늘봄학교 간식 구매', [DEMAND_FRUIT], work='물품(내자)', P=3.3e7, codes='과일류[5030990101]')
    out = judge.judge(fruit)
    assert out['v12'] == (1, DEMAND_FRUIT)
    bus = bundle('초등학교 통학차량 임차용역', ['라. 직접생산확인증명서[통학운송서비스(세부품명번호: 7811189902)]를 소지한 업체이어야 합니다.'],
                 P=1.3e8, license_='[여객자동차운수사업(구역여객자동차운송사업-전세버스)(5805)]')
    assert judge.judge(bus)['v12'] == (0, '')
