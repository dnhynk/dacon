"""Dedicated v9 stage pps_c/dedicated/model_name.py (BLUEPRINT.md): candidate selection, the decision rule on hand-written
model answers, request building against a length-based engine, consume of valid / invalid / mock outputs, and the verdict
without a reading. Runs against the copy under impl/submission."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))

from pps_c import catalog, dedicated, facts, judge, main, record, switches  # noqa: E402
from pps_c.dedicated import model_name as st  # noqa: E402

SPEC = ['1. 품 명', '소방용 관창', '2. 규격', '- 제조사·모델명 : 아크론브라스(Akron Brass) 터보젯(TurboJet)', '- 유량 : 490 LPM',
        '- 사이즈 : 65mm(F) x 40mm(M*2)', '- 시험압력 : 2MPa', '상기 사항과 동등 또는 그 이상의 조건을 충족하는 제품으로 납품 가능함']
PC = ['1. 규격', '1) Windows 11 Pro (한글판)', '2) CPU: Intel Core Ultra 9 285K', '3) GPU: NVIDIA RTX Pro 4000 Blackwell 24GB GDDR7',
      '4) RAM: 128GB DDR5-5600MT/s ECC', '5) 3 × USB 3.2 Gen2x2 Type-C']


def notice_of(*texts, doc_type='규격서', with_notice=True):
    docs = [{'type': '공고문', 'text': '1. 입찰에 부치는 사항\n가. 입찰건명: 물품 구매'}] if with_notice else []
    docs.append({'type': doc_type, 'text': '\n'.join(texts)})
    return record.build({'id': 'T', 'meta': {'업무구분': '물품(내자)'}, 'docs': docs})


def bundle_of(*texts, doc_type='규격서'):
    docs = [{'type': '공고문', 'text': '1. 입찰에 부치는 사항\n가. 입찰건명: 물품 구매'}, {'type': doc_type, 'text': '\n'.join(texts)}]
    return facts.build({'id': 'T', 'meta': {'업무구분': '물품(내자)'}, 'docs': docs}, catalog.load())


def line_with(notice, part):
    return next(ln for ln in notice.lines if part in ln.text)


class LengthEngine:
    """token_ids by content length, as the mock engine does; max_model_len small enough to force the shrink loop."""
    def __init__(self, max_model_len=16384, per_char=2):
        self.max_model_len, self.per_char = max_model_len, per_char

    def token_ids(self, messages, thinking=False):
        return list(range(sum(len(m['content']) for m in messages) // self.per_char))


def answer(*items):
    return json.dumps({'designations': [{'줄': i, '표현': e, '종류': k, '역할': r} for i, e, k, r in items]}, ensure_ascii=False)


# ---------------------------------------------------------------------------------------------- registry and switch
def test_registry_and_default_off():
    assert dedicated.MODULES['v9'] == 'model_name' and st.FAM == 'd_model_name'
    assert dedicated.stage('v9') is st


def test_active_with_switch(monkeypatch):
    monkeypatch.setattr(switches, 'DEDICATED', ('v9',))
    assert [m.FAM for m in dedicated.active()] == ['d_model_name'] and dedicated.by_family('d_model_name') is st


# ---------------------------------------------------------------------------------------------- candidate selection
def test_explicit_label_line_is_the_strongest_candidate_and_gates():
    n = notice_of(*SPEC)
    info = st.analyse(n)
    ln = line_with(n, '제조사·모델명')
    assert info[ln.i]['score'] >= 8 and st.gated(n)
    assert st.select(n)[0] is ln or ln in st.select(n)


def test_units_standards_and_boilerplate_do_not_gate():
    n = notice_of('- 샤프트 : STS440c 또는 동등 이상', '- 사용전력 : 37kw 2p 60HZ', 'KS F 2357 (아스팔트 혼합물용 골재)에 적합할 것',
                  '요구사항 고유번호 | PLR-001 | 요구사항 명칭', '나이스평가정보㈜ | 서울 ( , NICE평가정보(주)) | |', 'A1, A2+, A20, A2-, A3+, A30')
    assert not st.gated(n)


def test_table_rows_under_a_maker_header_become_candidates_with_the_header():
    n = notice_of('3. 구입내역', '제품명 | 제조사명 | 유효성분 | 규격 | 비고', '펙사론 | 팜아그로텍 | 클로란트라닐리프롤 5.4 | 500ml |',
                  '올크린 | 팜아그로텍 | 오리사트로빈 12 | 1L |')
    info = st.analyse(n)
    row = line_with(n, '펙사론')
    assert info[row.i]['score'] >= st.GATE_SCORE and info[row.i]['text'].startswith('[표] 제품명 | 제조사명')
    assert row in st.select(n)


def test_short_code_line_shows_its_neighbours_and_equivalence_clause_is_appended():
    n = notice_of('EFP 카메라(1)', 'HDC-3500', '해상도: Full HD (60p)', '3.1 제품군: Dell 및 HP 서버군 등',
                  '※ 또는 동급 이상의 성능을 갖춘 타사 제품군 포함', doc_type='제안요청서')
    info = st.analyse(n)
    code = line_with(n, 'HDC-3500')
    assert 'EFP 카메라(1) ‖ HDC-3500 ‖ 해상도' in info[code.i]['text']
    dell = line_with(n, 'Dell 및 HP')
    assert '/ ※ 또는 동급 이상' in info[dell.i]['text']


def test_section_hint_marks_inventory_tables():
    n = notice_of('2. 유지보수 대상 장비 현황', '구분 | 모델 | 도입연도', '저장분배 서버 | Lenovo, System X3650 M5 | 2019', doc_type='과업지시서')
    info = st.analyse(n)
    row = line_with(n, 'Lenovo')
    assert info[row.i]['text'].startswith('[§구분 | 모델 | 도입연도]')      # the nearest header-like line above the row
    n2 = notice_of('2. 유지보수 대상 장비 현황', '저장분배 서버 | Lenovo, System X3650 M5 | 2019', doc_type='과업지시서')
    assert st.analyse(n2)[line_with(n2, 'Lenovo').i]['text'].startswith('[§2. 유지보수 대상 장비 현황]')


def test_cap_keeps_the_highest_scores_in_document_order():
    n = notice_of(*(['- 형식 : RF%03d-R 롤러체인' % k for k in range(40)] + ['- 제조사·모델명 : 아크론브라스(Akron Brass) 터보젯(TurboJet)']))
    cands = st.select(n, cap=5)
    assert len(cands) == 5 and line_with(n, '제조사·모델명') in cands
    assert [ln.i for ln in cands] == sorted(ln.i for ln in cands)


# ---------------------------------------------------------------------------------------------- decision rule
def test_item_designation_fires_with_the_original_line_as_evidence():
    b = bundle_of(*SPEC)
    cands = st.select(b.notice)
    ln = line_with(b.notice, '제조사·모델명')
    assert st.consume(b, cands, answer((ln.i, '아크론브라스(Akron Brass) 터보젯(TurboJet)', 'model_or_product_name', 'supplied_item')))
    assert st.verdict(b) is ln


def test_component_and_performance_equipment_fire():
    b = bundle_of('B.감속기 (Gear Reducer)', '- 제작사 : KGM 또는 동급 이상', '(모델 WKA-167 )')
    cands = st.select(b.notice)
    ln = line_with(b.notice, 'KGM')
    assert st.consume(b, cands, answer((ln.i, 'KGM', 'maker_or_brand', 'supplied_component')))
    assert st.verdict(b) is ln
    s = bundle_of('EFP 카메라(1)', 'HDC-3500', '해상도: Full HD (60p)', doc_type='제안요청서')
    code = line_with(s.notice, 'HDC-3500')
    assert st.consume(s, st.select(s.notice), answer((code.i, 'HDC-3500', 'model_or_product_name', 'performance_equipment')))
    assert st.verdict(s) is code


def test_look_alike_roles_and_kinds_do_not_fire():
    b = bundle_of(*PC)
    cands = st.select(b.notice)
    gpu = line_with(b.notice, 'NVIDIA')
    win = line_with(b.notice, 'Windows')
    assert st.consume(b, cands, answer((gpu.i, 'NVIDIA RTX Pro 4000', 'model_or_product_name', 'computer_component'),
                                       (win.i, 'Windows 11 Pro', 'model_or_product_name', 'computer_component')))
    assert st.verdict(b) is None
    cases = [
        ('※ 기술원 보유장비는 RQ-30(오스트리아)장비이며, 연계가능 장비는 동등이상의 장비 필요', 'RQ-30', 'model_or_product_name', 'existing_equipment'),
        ('제안업체는 비행체의 형상, 모델명 및 일련번호를 제안서에 명시하여야 하며', '모델명', 'generic_or_unit', 'bidder_form_or_instruction'),
        ('- 샤프트 : STS440c 또는 동등 이상', 'STS440c', 'material_grade_or_standard', 'supplied_component'),
        ('요구사항 고유번호 | PLR-001 | 요구사항 명칭', 'PLR-001', 'requirement_id_or_code', 'supplied_item'),
        ('7. Excel, AutoCAD, Google Earth 등의 외부 소프트웨어에서 결과 값을 바로 확인', 'AutoCAD', 'model_or_product_name', 'environment_or_tool'),
        ('제품군은 예: 삼성전자, LG전자 등 국내 제조사 제품', '삼성전자', 'maker_or_brand', 'example_only'),
    ]
    for text, expr, kind, role in cases:
        b = bundle_of('1. 규격', text)
        ln = line_with(b.notice, expr)
        assert st.consume(b, [ln], answer((ln.i, expr, kind, role)))
        assert st.verdict(b) is None, text


def test_computer_parts_switch_is_a_module_constant(monkeypatch):
    b = bundle_of(*PC)
    gpu = line_with(b.notice, 'NVIDIA')
    st.consume(b, [gpu], answer((gpu.i, 'NVIDIA RTX Pro 4000', 'model_or_product_name', 'computer_component')))
    assert st.verdict(b) is None
    monkeypatch.setattr(st, 'COMPUTER_PARTS_FIRE', True)
    assert st.verdict(b) is gpu


def test_expression_not_written_in_the_shown_line_is_dropped():
    b = bundle_of(*SPEC)
    ln = line_with(b.notice, '유량')
    assert st.consume(b, [ln], answer((ln.i, 'Bosch GWS-2000', 'model_or_product_name', 'supplied_item')))
    assert getattr(b, st.FAM)['designations'] == [] and st.verdict(b) is None


def test_line_outside_the_excerpt_is_dropped_and_context_line_is_located():
    b = bundle_of('EFP 카메라(1)', 'HDC-3500', '해상도: Full HD (60p)', doc_type='제안요청서')
    code = line_with(b.notice, 'HDC-3500')
    other = line_with(b.notice, '입찰건명')
    assert st.consume(b, [code], answer((other.i, '입찰건명', 'maker_or_brand', 'supplied_item')))
    assert st.verdict(b) is None
    head = line_with(b.notice, 'EFP 카메라')
    assert st.consume(b, [code], answer((code.i, 'EFP 카메라(1)', 'model_or_product_name', 'performance_equipment')))
    assert st.verdict(b) is head        # the expression sits in the neighbour shown as context


# ---------------------------------------------------------------------------------------------- request building
def test_request_is_none_without_a_gated_candidate():
    b = bundle_of('- 사용전력 : 37kw 2p 60HZ', 'KS F 2357에 적합할 것')
    assert st.request(LengthEngine(), b, 0, main.Request) is None


def test_request_shape_and_token_limit():
    b = bundle_of(*SPEC)
    r = st.request(LengthEngine(), b, 3, main.Request)
    assert isinstance(r, main.Request) and r.fam == 'd_model_name' and r.rec == 3 and r.extra == () and r.budget == 0
    assert r.cands and all(ln in b.notice.lines for ln in r.cands) and r.max_tokens == st.MAX_TOKENS
    assert r.schema == st.schema() and len(r.token_ids) + r.max_tokens <= st.PROMPT_TOKEN_LIMIT
    msgs = st.messages(b, r.cands)
    assert msgs[0]['role'] == 'system' and f'L{line_with(b.notice, "제조사·모델명").i}:' in msgs[1]['content']


def test_request_shrinks_the_excerpt_to_the_engine_limit():
    lines = ['- 모델명 : AP%04d 규격 %d' % (k, k) for k in range(60)]
    b = bundle_of('1. 규격', *lines)
    full = st.request(LengthEngine(), b, 0, main.Request)
    assert len(full.cands) == st.MAX_EXCERPTS
    # an engine whose limit fits the prompt with about four excerpts, so the 75% shrink loop must run
    one = sum(len(m['content']) for m in st.messages(b, full.cands[:1]))
    limit = one + 3 * 40 + st.MAX_TOKENS + 64
    small = st.request(LengthEngine(max_model_len=limit, per_char=1), b, 0, main.Request)
    assert 0 < len(small.cands) < len(full.cands) and len(small.token_ids) + st.MAX_TOKENS <= limit - 64


# ---------------------------------------------------------------------------------------------- consume
def test_consume_valid_invalid_and_channel_prefix():
    b = bundle_of(*SPEC)
    ln = line_with(b.notice, '제조사·모델명')
    good = answer((ln.i, 'TurboJet', 'model_or_product_name', 'supplied_item'))
    assert st.consume(b, [ln], '<channel|>' + good) is True and st.verdict(b) is ln
    for bad in ('not json', '[]', '{"x": 1}', json.dumps({'designations': 'x'}),
                json.dumps({'designations': [{'줄': '3', '표현': 'a', '종류': 'maker_or_brand', '역할': 'supplied_item'}]}),
                json.dumps({'designations': [{'줄': 3, '표현': 'a', '종류': '불명', '역할': 'supplied_item'}]}),
                json.dumps({'designations': [{'줄': 3, '표현': 'a', '종류': 'maker_or_brand', '역할': '불명'}]}),
                json.dumps({'designations': [{'줄': 3, '표현': 'a', '종류': 'maker_or_brand'}]}),
                json.dumps({'designations': [{'줄': 1, '표현': 'a', '종류': 'maker_or_brand', '역할': 'supplied_item'}] * 13})):
        assert st.consume(b, [ln], bad) is False, bad


def test_mock_output_is_valid_and_gives_no_verdict():
    b = bundle_of(*SPEC)
    ln = line_with(b.notice, '제조사·모델명')
    assert st.consume(b, [ln], json.dumps({'designations': []})) is True
    assert getattr(b, st.FAM) == {'designations': [], 'shown': [ln.i]} and st.verdict(b) is None


def test_main_consume_routes_to_the_stage(monkeypatch):
    monkeypatch.setattr(switches, 'DEDICATED', ('v9',))
    b = bundle_of(*SPEC)
    ln = line_with(b.notice, '제조사·모델명')
    r = main.Request(0, st.FAM, [ln], (), [], st.schema(), st.MAX_TOKENS)
    assert main.consume(b, r, answer((ln.i, 'TurboJet', 'model_or_product_name', 'supplied_item'))) is True
    assert judge.judge(b)['v9'] == (1, ln.text.strip())


# ---------------------------------------------------------------------------------------------- verdict without a reading
def test_verdict_without_reading_uses_the_explicit_label_fallback():
    b = bundle_of(*SPEC)
    assert not hasattr(b, st.FAM)
    assert st.verdict(b) is line_with(b.notice, '제조사·모델명')
    coded = bundle_of('∘ 모델명 : AP5114', '- 유량 : 490 LPM')
    assert st.verdict(coded) is line_with(coded.notice, 'AP5114')
    assert st.verdict(bundle_of(*PC)) is None
    assert st.verdict(bundle_of('제품 및 모델명(제품생산번호), 상세규격이 명시된 내역서 제출')) is None
    assert st.verdict(bundle_of('2. 사용 시 제품명: 사용량: kg/월')) is None
    assert st.verdict(bundle_of('ㅇ 엔진모델 : 4기통 4-CYCLE(경유) 이상')) is None


def test_judge_with_the_switch_and_no_reading(monkeypatch):
    monkeypatch.setattr(switches, 'DEDICATED', ('v9',))
    out = judge.judge(bundle_of(*SPEC))
    assert out['v9'] == (1, '- 제조사·모델명 : 아크론브라스(Akron Brass) 터보젯(TurboJet)')
    assert judge.judge(bundle_of(*PC))['v9'] == (0, '')
