"""Dedicated v19 stage pps_c/dedicated/pledge.py (BLUEPRINT.md): candidate selection, the decision rule on hand-written model
answers (dev positives and the look-alikes), request building against a length-based engine, consume of valid / invalid / mock
outputs, and the verdict without a reading. Runs against the copy under impl/submission."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))

from pps_c import catalog, dedicated, facts, judge, main, record, switches  # noqa: E402
from pps_c.dedicated import pledge as st  # noqa: E402

HEAD = ['1. 입찰에 부치는 사항', '가. 입찰건명: 냉난방기 구매 설치']
# dev positives (PPS-DEV-035, 033, 036, 037) and look-alike negatives (088 계약 시, 051 bidder's own form, 178 ability, 096 anonymous list)
HOLD = '마. 납품(공급)업체의 경우 전자입찰서 제출 마감일 전일까지 제조사로부터 당해 물품공급 및 무상지원(A/S) 확약서를 발급후 보유 및 제출하여야 합니다.'
BID_LIST = ['다. 제출서류', '1) 입찰관련서류', '1-1) 나라장터에서 출력한 “경쟁입찰참가자격등록증” 1부', '1-2) 사업자등록증 1부',
            '1-3) 등기사항전부증명서[현재유효한사항] 1부', '1-4) 제조회사 공급 증명원 및 A/S 확약서 각 1부', '2) 제안서']
SPLIT = ['○ 공급업체의 경우 당해 물품 제조사의 정품인증서 및 A/S확약서를 전자입찰서 제출 마감일', '전일까지 보유하고, 계약체결 후 수요기관에 제출하여야 합니다.']
AWARD = '3.2.1. 공급업체는 당해 물품 제조사의 물품공급 및 기술지원(A/S) 확약서를 전자입찰서 제출 마감일 전일까지 보유하여야 하며, 낙찰통보 이전에 반드시 제출하여야 합니다.(미제출시 낙찰대상자 제외)'
APPRAISAL = ['※ 적격심사 시, 아래 서류를 반드시 제출해주시기 바랍니다(미제출시 적격심사 대상에서 제외)', '1) 사업자등록증 사본 1부',
             '6) 제조업체가 아닐 경우 제조업체로부터 받은 [납품 및 사후 A/S확약서(보증서)]']
CONTRACT = '나. 계약 시 각 물품 제조·기술지원사의 “물품공급(공급자증명원) 및 기술지원(A/S) 확약서”를 발급받아 제출하여야 합니다.'
SELF_FORM = ['[첨부 3]', '장비별 공급 및 기술지원 확약서', '1. 위 사업 관련하여 당사는 의료원에게 진료에 차질이 없도록 공급 및 기술지원을 원활히 제공하도록 한다.']
ABLE = '◦ 물품제조사의 제품공급 및 기술지원(A/S) 확약서 제출 가능한 업체'
ANON_LIST = ['2) 입찰참가자격 확인용 서류', '- 입찰참가신청서 1부', '- 정품공급 확약서 1부.']
SECRECY = '○ 과업수행자는 각종 자료를 전부 납품 후 파기하여야 하며, 인쇄업체로부터 사업관련 복사본 자료를 보유하고 있지 않다는 대표명의의 확약서를 제출하여야 한다.'
BOILER = ['가. 입찰보증금은 전자입찰참가신청서식에 따라 납부확약 내용이 명기된 전자입찰서의 납부이행각서 제출로 갈음합니다.',
          '다만 전자입찰의 경우 입찰서 제출 시 전자입찰서에 서약서의 내용을 포함하고 있으므로 전자입찰서 제출로 확약서 제출을 갈음함',
          '위의 입찰금액으로 준공(납품‧용역수행)기한 내에 물품(공사‧용역)을 완성(제조‧납품)할 것을 확약하며 (규격)입찰서를 제출합니다.',
          '5.2. 계약업체는 납품지시와 동시에 자료 및 기밀누설 방지, 보안관련 제반 규정의 준수를 확약 한다는 대표자 명의의 보안서약서를 제출하고',
          '5) 계약상대자는 제안서 제출 시 근로조건 이행 확약서에 동의한 경우에는 동의한 사항을 준수하여야 한다.',
          '당사는 위탁급식 공급업체 선정 입찰 및 계약에 참여함에 있어 관계공무원에게 금품을 제공하지 않을 것을 확약하며 청렴계약 이행서약서를 제출합니다.']

S, O = 'SUPPLY_OR_TECH_SUPPORT', 'OTHER'
T, B, U = 'THIRD_PARTY_MAKER_OR_SUPPLIER', 'BIDDER_ITSELF', 'UNSPECIFIED'
BID, AWD, AFT, UNS = 'AT_OR_BEFORE_BID', 'BEFORE_AWARD', 'AFTER_AWARD', 'UNSPECIFIED'


def notice_of(*texts, doc_type='공고문', attach=None):
    docs = [{'type': '공고문', 'text': '\n'.join(HEAD + list(texts))}] if doc_type == '공고문' else \
           [{'type': '공고문', 'text': '\n'.join(HEAD)}, {'type': doc_type, 'text': '\n'.join(texts)}]
    if attach:
        docs.append({'type': attach[0], 'text': '\n'.join(attach[1])})
    return record.build({'id': 'T', 'meta': {'업무구분': '물품(내자)'}, 'docs': docs})


def bundle_of(*texts, doc_type='공고문', attach=None):
    n = notice_of(*texts, doc_type=doc_type, attach=attach)
    return facts.build({'id': 'T', 'meta': {'업무구분': '물품(내자)'}, 'docs': n.docs}, catalog.load())


def line_with(notice, part):
    return next(ln for ln in notice.lines if part in ln.text)


def answer(*items):
    return json.dumps({'snippets': [{'idx': i, 'pledge_kind': k, 'pledger': p, 'required_from_bidder': r, 'timing': t,
                                     'timing_basis': 'EXPLICIT_IN_LINE'} for i, k, p, r, t in items]}, ensure_ascii=False)


class LengthEngine:
    """token_ids by content length, as the mock engine does; max_model_len small enough to force the shrink loop."""
    def __init__(self, max_model_len=16384, per_char=2):
        self.max_model_len, self.per_char = max_model_len, per_char

    def token_ids(self, messages, thinking=False):
        return list(range(sum(len(m['content']) for m in messages) // self.per_char))


# ---------------------------------------------------------------------------------------------- registry and switch
def test_registry_and_default_off():
    assert dedicated.MODULES['v19'] == 'pledge' and st.FAM == 'd_pledge' and dedicated.stage('v19') is st
    assert st.FIRE_TIMINGS == ('AT_OR_BEFORE_BID', 'BEFORE_AWARD') and st.FIRE_PLEDGERS == ('THIRD_PARTY_MAKER_OR_SUPPLIER',)


def test_active_with_switch(monkeypatch):
    monkeypatch.setattr(switches, 'DEDICATED', ('v19',))
    assert [m.FAM for m in dedicated.active()] == ['d_pledge'] and dedicated.by_family('d_pledge') is st


# ---------------------------------------------------------------------------------------------- candidate selection
def test_pledge_lines_are_anchors_and_boilerplate_is_not():
    for text in (HOLD, AWARD, CONTRACT, ABLE, BID_LIST[5], SPLIT[0], APPRAISAL[2], ANON_LIST[2], SELF_FORM[1]):
        assert st.is_anchor_line(text), text
    for text in BOILER:
        assert not st.is_anchor_line(text), text
    n = notice_of(*BOILER, HOLD)
    assert st.anchors(n) == [line_with(n, '무상지원')]


def test_snippet_carries_the_list_heading_and_the_continuation_line():
    n = notice_of(*BID_LIST)
    [s] = st.snippets(n, st.anchors(n))
    shown = [ln.text for ln, _ in s['rows']]
    assert '1) 입찰관련서류' in shown and '다. 제출서류' in shown and shown[-1] == '2) 제안서'
    assert [ln.text for ln, a in s['rows'] if a] == [BID_LIST[5]]
    n2 = notice_of(*SPLIT)
    [s2] = st.snippets(n2, st.anchors(n2))
    assert [(ln.text, a) for ln, a in s2['rows']][-2:] == [(SPLIT[0], True), (SPLIT[1], False)]


def test_adjacent_anchors_merge_and_far_ones_split():
    n = notice_of('11. 공급확약서 : 1 EA', '12. 기술지원확약서 : 1 EA', *([''] * 8), HOLD)
    snips = st.snippets(n, st.anchors(n))
    assert [len(s['anchors']) for s in snips] == [2, 1]
    rendered = st.render(1, snips[0])
    assert rendered.startswith('[발췌 1 | 문서: 공고문]') and rendered.count('\n>> ') == 2


def test_select_puts_the_notice_first_and_caps_by_rank():
    spec = ['4.3 제출 서류', '가. 정품 공급확약서 (물품 납품 전)']
    n = notice_of(HOLD, attach=('규격서', spec))
    cands = st.select(n)
    assert [ln.doc_type for ln in cands] == ['공고문', '규격서'] and cands[0] is line_with(n, '무상지원')
    many = notice_of(*[f'{k}) 제조사 정품공급 확약서 제출 {k}' + '\n' * 5 for k in range(10)])    # spaced beyond MERGE_GAP
    assert len(st.snippets(many, st.anchors(many))) == 10
    assert len(st.select(many, cap=3)) == 3 and len(st.select(many)) == st.MAX_SNIPPETS


# ---------------------------------------------------------------------------------------------- decision rule
def test_bid_deadline_possession_fires_with_the_original_line_as_evidence():
    b = bundle_of(HOLD)
    cands = st.select(b.notice)
    assert st.consume(b, cands, answer((1, S, T, 'YES', BID)))
    assert st.verdict(b) is line_with(b.notice, '무상지원')


def test_list_item_and_split_line_and_pre_award_fire():
    for texts, part in ((BID_LIST, '제조회사 공급 증명원'), (SPLIT, '정품인증서'), ([AWARD], '낙찰통보'), (APPRAISAL, '제조업체로부터')):
        b = bundle_of(*texts)
        cands = st.select(b.notice)
        timing = AWD if texts is APPRAISAL else BID
        assert st.consume(b, cands, answer((1, S, T, 'YES', timing)))
        assert st.verdict(b) is line_with(b.notice, part), part


def test_look_alikes_with_faithful_answers_do_not_fire():
    cases = [([CONTRACT], (S, T, 'YES', AFT)),           # 계약 시 제출: after the award
             (SELF_FORM, (S, B, 'NO', UNS)),             # the bidder's own pledge form
             ([ABLE], (S, T, 'NO', UNS)),                # ability statement in the qualification
             (ANON_LIST, (S, U, 'YES', BID)),            # anonymous 공급확약서 in a bid list
             ([SECRECY], (O, T, 'YES', AFT))]            # a 보안 pledge that reaches the model (DEV-30): OTHER kind
    for texts, (k, p, r, t) in cases:
        b = bundle_of(*texts)
        cands = st.select(b.notice)
        assert cands, texts
        assert st.consume(b, cands, answer((1, k, p, r, t)))
        assert st.verdict(b) is None, texts


def test_fire_switches_are_module_constants(monkeypatch):
    b = bundle_of(*APPRAISAL)
    st.consume(b, st.select(b.notice), answer((1, S, T, 'YES', AWD)))
    assert st.verdict(b) is line_with(b.notice, '제조업체로부터')
    monkeypatch.setattr(st, 'FIRE_TIMINGS', ('AT_OR_BEFORE_BID',))
    assert st.verdict(b) is None
    a = bundle_of(*ANON_LIST)
    st.consume(a, st.select(a.notice), answer((1, S, U, 'YES', BID)))
    assert st.verdict(a) is None
    monkeypatch.setattr(st, 'FIRE_PLEDGERS', ('THIRD_PARTY_MAKER_OR_SUPPLIER', 'UNSPECIFIED'))
    assert st.verdict(a) is line_with(a.notice, '정품공급 확약서')


def test_evidence_prefers_the_notice_and_the_bid_stage():
    b = bundle_of(*APPRAISAL, attach=('규격서', [HOLD]))
    cands = st.select(b.notice)
    assert [ln.doc_type for ln in cands] == ['공고문', '규격서']
    assert st.consume(b, cands, answer((1, S, T, 'YES', AWD), (2, S, T, 'YES', BID)))
    assert st.verdict(b) is line_with(b.notice, '제조업체로부터')       # 공고문 first
    assert st.consume(b, cands, answer((1, S, T, 'NO', UNS), (2, S, T, 'YES', BID)))
    assert st.verdict(b) is line_with(b.notice, '무상지원')


# ---------------------------------------------------------------------------------------------- request building
def test_request_is_none_without_a_candidate():
    assert st.request(LengthEngine(), bundle_of(*BOILER), 0, main.Request) is None


def test_request_shape_and_token_limit():
    b = bundle_of(*BID_LIST)
    r = st.request(LengthEngine(), b, 3, main.Request)
    assert isinstance(r, main.Request) and r.fam == 'd_pledge' and r.rec == 3 and r.extra == () and r.budget == 0
    assert r.cands == [line_with(b.notice, '제조회사 공급 증명원')] and r.max_tokens == st.MAX_TOKENS
    assert r.schema == st.schema() and len(r.token_ids) + r.max_tokens <= st.PROMPT_TOKEN_LIMIT
    msgs = st.messages(b, r.cands)
    assert msgs[0]['role'] == 'system' and '>> 1-4) 제조회사 공급 증명원 및 A/S 확약서 각 1부' in msgs[1]['content']
    assert '   1) 입찰관련서류' in msgs[1]['content'] and '"snippets"' in msgs[1]['content']


def test_request_shrinks_the_packet_to_the_engine_limit():
    lines = [f'{k}) 제조사 정품공급 확약서 제출 항목 {k}\n{k}-a) 규격 {k}\n\n\n\n\n' for k in range(12)]
    b = bundle_of(*lines)
    full = st.request(LengthEngine(), b, 0, main.Request)
    assert len(full.cands) == st.MAX_SNIPPETS
    one = sum(len(m['content']) for m in st.messages(b, full.cands[:1]))
    limit = one + 3 * 60 + st.MAX_TOKENS + 64
    small = st.request(LengthEngine(max_model_len=limit, per_char=1), b, 0, main.Request)
    assert 0 < len(small.cands) < len(full.cands) and len(small.token_ids) + st.MAX_TOKENS <= limit - 64


# ---------------------------------------------------------------------------------------------- consume
def test_consume_valid_invalid_and_channel_prefix():
    b = bundle_of(HOLD)
    cands = st.select(b.notice)
    assert st.consume(b, cands, '<channel|>' + answer((1, S, T, 'YES', BID))) is True and st.verdict(b) is cands[0]
    item = {'idx': 1, 'pledge_kind': S, 'pledger': T, 'required_from_bidder': 'YES', 'timing': BID, 'timing_basis': 'NONE'}
    for bad in ('not json', '[]', '{"x": 1}', json.dumps({'snippets': 'x'}), json.dumps({'snippets': [dict(item, idx='1')]}),
                json.dumps({'snippets': [dict(item, pledge_kind='불명')]}), json.dumps({'snippets': [dict(item, timing='입찰 전')]}),
                json.dumps({'snippets': [{k: v for k, v in item.items() if k != 'timing'}]}), json.dumps({'snippets': [item] * 7})):
        assert st.consume(b, cands, bad) is False, bad
    assert st.consume(b, cands, json.dumps({'snippets': [dict(item, idx=9)]})) is True      # names no shown snippet: dropped
    assert getattr(b, st.FAM)['items'] == [] and st.verdict(b) is None


def test_mock_output_is_valid_and_gives_no_verdict():
    b = bundle_of(HOLD)
    cands = st.select(b.notice)
    assert st.consume(b, cands, json.dumps({'snippets': []})) is True
    reading = getattr(b, st.FAM)
    assert reading['items'] == [] and reading['shown'] == [cands[0].i] and st.verdict(b) is None


def test_main_consume_routes_to_the_stage(monkeypatch):
    monkeypatch.setattr(switches, 'DEDICATED', ('v19',))
    b = bundle_of(HOLD)
    ln = line_with(b.notice, '무상지원')
    r = main.Request(0, st.FAM, [ln], (), [], st.schema(), st.MAX_TOKENS)
    assert main.consume(b, r, answer((1, S, T, 'YES', BID))) is True
    assert judge.judge(b)['v19'] == (1, ln.text.strip())
    assert main.consume(b, r, answer((1, S, T, 'YES', AFT))) is True and judge.judge(b)['v19'] == (0, '')


# ---------------------------------------------------------------------------------------------- verdict without a reading
def test_verdict_without_reading_uses_the_keyword_standin():
    b = bundle_of(HOLD)
    assert not hasattr(b, st.FAM) and st.verdict(b) is line_with(b.notice, '무상지원')
    lst, app = bundle_of(*BID_LIST), bundle_of(*APPRAISAL)
    assert st.verdict(lst) is line_with(lst.notice, '제조회사 공급 증명원')
    assert st.verdict(app) is line_with(app.notice, '제조업체로부터')
    for texts in ([CONTRACT], SELF_FORM, [ABLE], ANON_LIST, BOILER):
        assert st.verdict(bundle_of(*texts)) is None, texts


def test_judge_with_the_switch_and_no_reading(monkeypatch):
    monkeypatch.setattr(switches, 'DEDICATED', ('v19',))
    assert judge.judge(bundle_of(HOLD))['v19'] == (1, HOLD)
    assert judge.judge(bundle_of(CONTRACT))['v19'] == (0, '')
