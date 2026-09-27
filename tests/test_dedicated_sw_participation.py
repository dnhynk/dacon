"""Dedicated v20 stage pps_c/dedicated/sw_participation.py (BLUEPRINT.md): candidate selection (self_sw, content_sw, the
CPU-compliant statement), the decision rule on hand-written model answers (dev positives and the look-alikes DEV-124, DEV-056,
DEV-17, a stated non-application, a bare regulation name, 상호출자-only), request building against a length-based engine,
consume of valid / invalid / mock outputs, and the verdict without a reading. Runs against the copy under impl/submission."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))

from pps_c import catalog, dedicated, facts, judge, main, switches  # noqa: E402
from pps_c.dedicated import sw_participation as st  # noqa: E402

HEAD = ['입 찰 공 고', '1. 입찰에 부치는 사항']
# dev positives: DEV-24 (1468 + 8111189901), DEV-131 (satellite control SW), DEV-133 (RHEL licence, 세부품명 4323), DEV-134 (§58)
QUAL_1468 = ['2. 입찰참가 자격', '1) 「국가를 당사자로 하는 계약에 관한 법률 시행령」 제12조에 의한 유자격자',
             '2) 국가종합전자조달시스템에 입찰참가자격 등록마감일시까지 아래의 입찰참가자격을 등록한 자',
             '- 소프트웨어사업자(컴퓨터관련서비스사업) (업종코드: 1468)', '3. 공동계약 : 불가']
QUAL_58 = ['5. 입찰참가자격', '④ 「소프트웨어진흥법」제58조에 의한 소프트웨어사업자(컴퓨터관련서비스사업)로 등록되어 있는 업체', '6. 제출 서류']
QUAL_8111 = ['2. 입찰참가자격', 'ㅇ 직접생산확인증명서(세부품명번호 8111219901, 세부품명 : 인터넷지원개발서비스)를 소지한 업체여야 합니다.', '3. 입찰방법']
SPEC_RHEL = ['물자규격서', '『공기업 RHEL 라이선스 갱신 구매(제한경쟁·3억원미만)』', '1. 품명 및 수량', '제1규격 라이선스 RedHat Enterprise Linux for Datacenters 10조']
# compliant statements: DEV-064, DEV-068 (with 상호출자 line), unlabeled D-000419 / D-000415 wordings
STMT_064 = '※ 소프트웨어진흥법 제48조에 따른 소프트웨어사업자의 사업금액별 참여 제한'
STMT_068 = ['마.「소프트웨어 진흥법」제48조 제4항 및 「독점규제 및 공정거래에 관한 법률」제31조에 따라 지정된 상호출자제한기업집단에 속하는 기업은 입찰에 참여할 수 없음',
            '바. 총 사업금액 20억 미만인 사업으로, 「소프트웨어 진흥법」제48조(중소소프트웨어사업자의 사업참여 지원)에 따라, 중소 소프트웨어사업자만 참여 가능']
STMT_HAHAN = '다. 본 입찰은 사업금액이 20억 원 미만인 사업으로서 대기업(소프트웨어 사업자)은 사업금액의 하한에 의거 본 입찰에 참여 불가'
STMT_SPLIT = ['라. 본 사업은 사업금액이 20억원 미만인 사업으로서, 「중소 소프트웨어사업자의 사업 참여 지원에 관한 지침」에 의거 대기업 및 중견기업 소',
              '프트웨어 사업자는 본 입찰에 참여할 수 없습니다.']
# look-alikes that must not be a CPU statement
SOJA_ONLY = '- 「소프트웨어 진흥법」 제48조에 따라 ‘상호출자제한기업집단 소속회사’는 입찰에 참여할 수 없다.'
GENERIC = '소기업·소상공인 대상 입찰로서 중기업 및 대기업은 참여할 수 없습니다.'
RULE_48 = '규격 검토결과 적합자가 1인인 경우에도 「국가를 당사자로 하는 계약에 관한 법률 시행규칙」 제48조제2항에 따라 가격입찰서를 개찰할 수 있습니다.'
DECREE_48 = '「소프트웨어 진흥법」 제51조제6항 및 동 법 시행령 제48조제5항에 따라 하수급인과 공동수급체를 구성하여 참여해야 하며'
CITATION = ['<적용 규정>', '7. 대기업인 소프트웨어사업자가 참여할 수 있는 사업금액의 하한(과학기술정보통신부 고시)', '8. 기타 입찰·계약관련 법령']
# content_sw (no SW사업자 requirement) and boilerplate
CONTENT = ['가. 입찰건명: 기초자치단체 대표홈페이지 고도화 및 유지보수 용역', '나. 사업내용: 정보시스템 개편, 웹 접근성 개선, 콘텐츠관리시스템 구축']
BUS = ['가. 입찰건명: 2026학년도 초등학교 통학차량 임차용역', '나. 전자입찰자는 안전입찰서비스를 이용할 수 있으며 전산시스템 장애 시 연락 바랍니다.']
CALLCENTER = ['가. 입찰건명: 청년수당 서류검증·모니터링 및 전담 콜센터 운영 용역', '나. 주요 과업내용: 콜센터 운영에 필요한 업무공간 및 시스템 구축, 상담 프로그램 개발 및 운영']

SWD, SWM, LIC, ITS, HWS = st.SW_OBJECTS
HW, NONIT = st.NON_SW_OBJECTS
APPLIES, NOTAPP, SOJA, GEN, CITE, ABSENT = st.BAND_CLAUSES[:6]


def bundle_of(*texts, meta=None, attach=None):
    docs = [{'type': '공고문', 'text': '\n'.join(HEAD + list(texts))}]
    if attach:
        docs.append({'type': attach[0], 'text': '\n'.join(attach[1])})
    m = {'업무구분': '일반용역', '계약방법': '제한경쟁'}
    m.update(meta or {})
    return facts.build({'id': 'T', 'meta': m, 'docs': docs}, catalog.load())


def line_with(notice, part):
    return next(ln for ln in notice.lines if part in ln.text)


def answer(obj, clause=ABSENT, quote=''):
    return json.dumps({'object_type': obj, 'band_clause': clause, 'band_clause_quote': quote}, ensure_ascii=False)


class LengthEngine:
    """token_ids by content length, as the mock engine does; max_model_len small enough to force the shrink loop."""
    def __init__(self, max_model_len=16384, per_char=2):
        self.max_model_len, self.per_char = max_model_len, per_char

    def token_ids(self, messages, thinking=False):
        return list(range(sum(len(m['content']) for m in messages) // self.per_char))


# ---------------------------------------------------------------------------------------------- registry and switch
def test_registry_and_default_off():
    assert dedicated.MODULES['v20'] == 'sw_participation' and st.FAM == 'd_sw_participation' and dedicated.stage('v20') is st
    assert st.EXEMPT_SMALL_FIRM_OR_PRIVATE is False and st.UNKNOWN in st.OBJECT_TYPES and st.UNKNOWN in st.BAND_CLAUSES


def test_active_with_switch(monkeypatch):
    monkeypatch.setattr(switches, 'DEDICATED', ('v20',))
    assert [m.FAM for m in dedicated.active()] == ['d_sw_participation'] and dedicated.by_family('d_sw_participation') is st


# ---------------------------------------------------------------------------------------------- candidate selection
def test_self_sw_from_qualification_text_meta_code_and_spec():
    for texts, meta, attach in ((QUAL_1468, None, None), (QUAL_58, None, None), (QUAL_8111, None, None),
                                (['2. 입찰참가자격', '가. 조달청 경쟁입찰참가자격 등록증'], {'면허업종제한목록': '[소프트웨어사업자(컴퓨터관련서비스사업)(1468)]'}, None),
                                (['2. 입찰참가자격', '나. 소프트웨어산업 진흥법 제24조에 의한 소프트웨어 사업자 신고업체'],
                                 {'업무구분': '물품(내자)', '세부품명번호목록': '운영체제[4323300401]'}, ('규격서', SPEC_RHEL))):
        b = bundle_of(*texts, meta=meta, attach=attach)
        assert st.cpu(b) == {'cand': 'self_sw', 'band': 'absent', 'content_hits': st.cpu(b)['content_hits']}, texts


def test_content_sw_needs_two_vocabulary_hits_and_no_self_declaration():
    assert st.cpu(bundle_of(*CONTENT))['cand'] == 'content_sw'
    assert st.cpu(bundle_of(*CALLCENTER))['cand'] == 'content_sw'
    assert st.cpu(bundle_of(*BUS))['cand'] is None                       # one boilerplate hit
    assert st.cpu(bundle_of(*CONTENT, *QUAL_1468))['cand'] == 'self_sw'  # the declaration wins


def test_cpu_statement_is_sw_anchored_with_a_verb():
    for texts in (([STMT_064]), STMT_068, [STMT_HAHAN], STMT_SPLIT):
        b = bundle_of(*QUAL_1468, *texts)
        assert st.cpu(b)['band'] == 'statement', texts
        assert st.request(LengthEngine(), b, 0, main.Request) is None and st.verdict(b) is None
    for texts, band in (([SOJA_ONLY], 'absent'), ([GENERIC], 'absent'), ([RULE_48], 'absent'), ([DECREE_48], 'absent'), (CITATION, 'citation')):
        b = bundle_of(*QUAL_1468, *texts)
        assert st.cpu(b)['band'] == band, texts
        assert st.request(LengthEngine(), b, 0, main.Request) is not None


def test_excerpt_shows_head_qualification_declaration_and_token_lines():
    b = bundle_of(*QUAL_1468, '9. 기타사항', SOJA_ONLY, attach=('과업지시서', ['과업지시서', '1. 과업개요', '가. 사업명: 관제시스템 소프트웨어 설계 및 개발']))
    labels = [label for label, _ in st.parts(b.notice)]
    assert labels[:2] == ['공고문 앞부분', '입찰참가자격 절'] and '소프트웨어사업자 요건 줄' in labels
    assert any(label.startswith('대기업/중견기업') for label in labels) and labels[-1] == '첨부: 과업지시서 앞부분'
    cands = st.select(b.notice)
    assert line_with(b.notice, '1468') in cands and line_with(b.notice, '상호출자') in cands and line_with(b.notice, '관제시스템') in cands
    assert [ln.i for ln in cands] == sorted({ln.i for ln in cands})
    user = st.messages(b)[1]['content']
    assert '업무구분=일반용역' in user and '=== 입찰참가자격 절 ===' in user and '"object_type"' in user


# ---------------------------------------------------------------------------------------------- decision rule
def test_dev_positives_fire_with_blank_evidence():
    for texts, meta, attach, obj in ((QUAL_1468, None, None, ITS), (QUAL_1468, None, ('제안요청서', ['용도개요: 관제 및 전처리시스템 소프트웨어의 설계 및 개발']), SWD),
                                     (QUAL_58, None, None, 'unclear'),
                                     (['2. 입찰참가자격', '나. 소프트웨어 사업자(컴퓨터관련서비스사업, 면허코드 1468) 신고업체'], {'업무구분': '물품(내자)'}, ('규격서', SPEC_RHEL), LIC),
                                     (QUAL_1468, None, ('과업지시서', ['사업명: 전산기기(데스크탑·모니터·노트북) 임차']), HWS)):
        b = bundle_of(*texts, meta=meta, attach=attach)
        cands = st.select(b.notice)
        assert st.consume(b, cands, answer(obj)) and st.verdict(b) is True, obj


def test_look_alikes_with_faithful_answers_do_not_fire():
    cases = [(QUAL_1468 + ['건명: 1~4호선 레일자동살수장치 구매설치'], None, HW, ABSENT),          # DEV-124
             (QUAL_8111 + ['건명: 해운부문 외부사업 운영 지원 및 감축사업 활성화 용역'], None, NONIT, ABSENT),  # DEV-056
             (CALLCENTER, None, NONIT, ABSENT),                                                    # DEV-17 (content_sw)
             (CONTENT, None, 'unclear', ABSENT),                                                   # content_sw unresolved
             (QUAL_1468 + ['본 사업은 소프트웨어 진흥법 제48조제3항제2호의 예외사업으로 대기업 참여제한 하한제도를 적용하지 않음'], None, SWD, NOTAPP),
             (QUAL_1468 + [STMT_HAHAN], None, SWD, APPLIES)]
    for texts, meta, obj, clause in cases:
        b = bundle_of(*texts, meta=meta)
        cands = st.select(b.notice)
        assert cands and st.consume(b, cands, answer(obj, clause)) and st.verdict(b) is None, texts


def test_citation_generic_and_cross_shareholding_readings_still_fire():
    for texts, clause in ((CITATION, CITE), ([GENERIC], GEN), ([SOJA_ONLY], SOJA)):
        b = bundle_of(*QUAL_1468, *texts)
        assert st.consume(b, st.select(b.notice), answer(SWD, clause, texts[-1][:100])) and st.verdict(b) is True, texts
    c = bundle_of(*CONTENT)
    assert st.consume(c, st.select(c.notice), answer(SWM)) and st.verdict(c) is True


def test_exempt_switch_is_a_module_constant(monkeypatch):
    small = bundle_of(*QUAL_1468, meta={'조항호내용': '추정가격 1억원 미만 물품·용역(소기업, 소상공인, 벤처기업, 창업자)'})
    private = bundle_of(*QUAL_1468, meta={'계약방법': '수의계약'})
    plain = bundle_of(*QUAL_1468)
    for b in (small, private, plain):
        st.consume(b, st.select(b.notice), answer(SWM))
        assert st.verdict(b) is True
    monkeypatch.setattr(st, 'EXEMPT_SMALL_FIRM_OR_PRIVATE', True)
    assert st.verdict(small) is None and st.verdict(private) is None and st.verdict(plain) is True


# ---------------------------------------------------------------------------------------------- request building
def test_request_is_none_without_a_candidate_or_with_a_statement():
    assert st.request(LengthEngine(), bundle_of(*BUS), 0, main.Request) is None
    assert st.request(LengthEngine(), bundle_of(*QUAL_1468, STMT_064), 0, main.Request) is None


def test_request_shape_and_token_limit():
    b = bundle_of(*QUAL_1468)
    r = st.request(LengthEngine(), b, 3, main.Request)
    assert isinstance(r, main.Request) and r.fam == 'd_sw_participation' and r.rec == 3 and r.extra == () and r.budget == 0
    assert r.cands == st.select(b.notice) and line_with(b.notice, '1468') in r.cands and r.max_tokens == st.MAX_TOKENS
    assert r.schema == st.schema() and len(r.token_ids) + r.max_tokens <= st.PROMPT_TOKEN_LIMIT
    assert r.schema['properties']['object_type']['enum'] == list(st.OBJECT_TYPES)


def test_request_shrinks_the_excerpt_to_the_engine_limit():
    filler = [f'{k}) 과업 항목 {k}: ' + '가나다라마바사 ' * 20 for k in range(40)]
    b = bundle_of(*QUAL_1468, attach=('과업지시서', ['과업지시서', '1. 과업개요'] + filler))
    full = st.request(LengthEngine(), b, 0, main.Request)
    one = sum(len(m['content']) for m in st.messages(b, 0.1))
    limit = one + 400 + st.MAX_TOKENS + 64
    small = st.request(LengthEngine(max_model_len=limit, per_char=1), b, 0, main.Request)
    assert 0 < len(small.cands) < len(full.cands) and len(small.token_ids) + st.MAX_TOKENS <= limit - 64


# ---------------------------------------------------------------------------------------------- consume
def test_consume_valid_invalid_and_channel_prefix():
    b = bundle_of(*QUAL_1468)
    cands = st.select(b.notice)
    assert st.consume(b, cands, '<channel|>' + answer(SWD)) is True
    assert getattr(b, st.FAM) == {'object_type': SWD, 'band_clause': ABSENT, 'quote': '', 'shown': [ln.i for ln in cands]}
    good = json.loads(answer(SWD))
    for bad in ('not json', '[]', '{"x": 1}', json.dumps(dict(good, object_type='소프트웨어')), json.dumps(dict(good, band_clause='있음')),
                json.dumps({k: v for k, v in good.items() if k != 'band_clause_quote'}), json.dumps(dict(good, extra=1)),
                json.dumps(dict(good, band_clause_quote='x' * (st.QUOTE_MAX + 1))), json.dumps(dict(good, band_clause_quote=None))):
        assert st.consume(b, cands, bad) is False, bad


def test_mock_output_is_valid_and_leaves_the_cpu_rule():
    self_sw, content = bundle_of(*QUAL_1468), bundle_of(*CONTENT)
    mock = json.dumps({'object_type': '불명', 'band_clause': '불명', 'band_clause_quote': '-'}, ensure_ascii=False)
    for b in (self_sw, content):
        cands = st.select(b.notice)
        assert st.consume(b, cands, mock) is True and getattr(b, st.FAM)['shown'] == [ln.i for ln in cands]
    assert st.verdict(self_sw) is True and st.verdict(content) is None


def test_main_consume_routes_to_the_stage(monkeypatch):
    monkeypatch.setattr(switches, 'DEDICATED', ('v20',))
    b = bundle_of(*QUAL_1468, '건명: 레일자동살수장치 구매설치')
    r = main.Request(0, st.FAM, st.select(b.notice), (), [], st.schema(), st.MAX_TOKENS)
    assert main.consume(b, r, answer(SWD)) is True and judge.judge(b)['v20'] == (1, '')
    assert main.consume(b, r, answer(HW)) is True and judge.judge(b)['v20'] == (0, '')


# ---------------------------------------------------------------------------------------------- verdict without a reading
def test_verdict_without_reading_uses_the_cpu_facts():
    b = bundle_of(*QUAL_1468)
    assert not hasattr(b, st.FAM) and st.verdict(b) is True
    assert st.verdict(bundle_of(*QUAL_1468, STMT_064)) is None
    assert st.verdict(bundle_of(*CONTENT)) is None and st.verdict(bundle_of(*BUS)) is None


def test_judge_with_the_switch_and_no_reading(monkeypatch):
    monkeypatch.setattr(switches, 'DEDICATED', ('v20',))
    assert judge.judge(bundle_of(*QUAL_1468))['v20'] == (1, '')
    assert judge.judge(bundle_of(*QUAL_1468, *STMT_068))['v20'] == (0, '')
    assert judge.judge(bundle_of(*CONTENT))['v20'] == (0, '')
