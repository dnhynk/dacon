"""Rebuild stage 1 (runs/rebuild_20260924/DESIGN.md): fixed-prefix layout, A10 budget profiles, A10 attachment.

No GPU, model or labels. The layout tests use a character tokenizer and a stub knowledge; the
packet tests use the local data package and fixed tokenizer when present.
"""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from submission.pps.prompts import (Config, FIXED_PREFIX_ORDER_LINE, ORDER_LINE, QUESTION_HEADER,
                                    build_shared_prompts)
from submission.stream import TIERS
from test_stream_executor import CONFIG
from test_w4_tier_plan import TokenPipeline, simulate

ROOT = Path(__file__).resolve().parents[1]
GROUPS = (tuple(range(1, 10)), tuple(range(10, 19)), tuple(range(19, 25)))
TIER1 = TIERS[1]['name']


class Tokenizer:
    def apply_chat_template(self, messages, **kwargs):
        return list(map(ord, json.dumps(messages, ensure_ascii=False, separators=(',', ':'))))

    def encode(self, text, add_special_tokens=False):
        return list(map(ord, text))


class Knowledge:
    table = {f'v{i}': {'항목명': f'항목{i}'} for i in range(1, 25)}
    laws = {}
    alternatives = ['national']

    def legal_context_v2(self, rec, items, max_chars, return_metadata=False):
        text = f'[법령 v{items[0]}~v{items[-1]}]'
        metadata = {'selected': [], 'max_chars': max_chars, 'scope': {'alternatives': self.alternatives}}
        return {'text': text, **metadata} if return_metadata else text

    def search_legal_dependencies(self, topic, *, max_chars, tokenizer, max_source_tokens):
        return {'topic': topic, 'text': '[직접생산 법령 발췌]', 'max_source_tokens': max_source_tokens}

    def product_matches(self, rec):
        return []


def fixture(text='2. 입찰참가자격\n가. 연구시설을 보유한 기관.\n3. 계약조건\n서류 제출.', **overrides):
    rec = {'id': 'layout-fixture', 'meta': {'적용계약법': '국가계약법'}, 'docs': [
        {'doc_id': 'D0', 'type': '공고문', 'text': text}]}
    cfg = Config(shared_prefix=True, rubric_version='v6', response_format='fact_compact', judgment_groups=GROUPS,
                 legal_context_version='v2', **overrides)
    return rec, Knowledge(), cfg, Tokenizer()


def split_current(prompt):
    """Current layout: [common][law block][header][instructions]\n[question]."""
    user = prompt['messages'][1]['content']
    law_at = user.find('\n\n[이번 항목의 배포 법령 참고 발췌]\n')
    common, tail = user[:law_at], user[law_at:]
    law, rest = tail.split('\n\n[이번 호출의 항목별 판단 안내]\n')
    instructions, question = rest.split('\n이번 호출에서 검토할 항목', 1)
    return common, law.split('\n', 3)[-1], instructions, '이번 호출에서 검토할 항목' + question


def test_rebuild_options_are_off_by_default_and_in_the_packaged_config():
    for config in (Config(), Config.load(ROOT / 'submission/model/config.json')):
        assert (config.prompt_layout, config.a10_budget_profiles, config.a10_attach) in (('current', (), False),
                                                                                         ('current', [], False))
    for bad in ({'prompt_layout': 'lean'}, {'a10_budget_profiles': (0, 0)}, {'a10_budget_profiles': (4096,)},
                {'a10_attach': 1}, {'a10_attach': True, 'a10_budget_profiles': (0,)}):
        with pytest.raises(ValueError):
            fixture(enable_thinking=True, thinking_token_budget=0, max_output_tokens=2048, **bad)
    with pytest.raises(ValueError):
        Config(prompt_layout='fixed_prefix', legal_context_version='v1')
    with pytest.raises(ValueError):
        Config(engine_max_model_len=8192)
    assert Config().engine_context() == 16384 and Config(engine_max_model_len=24576).engine_context() == 24576


def test_fixed_prefix_moves_the_same_strings_ahead_of_the_notice():
    rec, knowledge, cfg, tokenizer = fixture()
    old = build_shared_prompts(rec, knowledge, cfg, tokenizer, GROUPS)
    new = build_shared_prompts(rec, knowledge, replace(cfg, prompt_layout='fixed_prefix'), tokenizer, GROUPS)
    assert new[0]['messages'][0]['content'] == old[0]['messages'][0]['content'].replace(ORDER_LINE, FIXED_PREFIX_ORDER_LINE)
    fixed = new[0]['messages'][1]['content'].split('[입력정보]', 1)[0]
    for items, before, after in zip(GROUPS, old, new):
        common, law, instructions, question = split_current(before)
        user = after['messages'][1]['content']
        label = f'v{items[0]}~v{items[-1]}'
        assert f'[{label} 배포 법령 참고 발췌]\n{law}\n\n[{label} 항목별 판단 안내]\n{instructions}\n\n' in fixed
        assert user.startswith(fixed + common) and user.endswith(QUESTION_HEADER + question)
        assert after['spans'] == before['spans'] and after['items'] == before['items']
        assert after['fixed_prefix_tokens'] == new[0]['fixed_prefix_tokens'] > len(fixed)
        assert after['shared_prefix_tokens'] >= after['fixed_prefix_tokens']
    assert len({p['fixed_prefix_sha256'] for p in new}) == 1 and 'fixed_prefix_tokens' not in old[0]


def test_fixed_prefix_sizes_the_source_as_the_current_layout_does():
    # A long notice the current layout must shrink: the fixed-prefix order keeps exactly that source and only
    # checks the moved requests against the engine context (DESIGN.md 2-1, decision 1).
    text = '\n'.join(f'{n}. 입찰참가자격 조건 {n}: 연구시설과 인력을 보유한 기관만 참여할 수 있다.' for n in range(1, 400))
    rec, knowledge, cfg, tokenizer = fixture(text=text, max_model_len=12000, max_output_tokens=640, document_chars=14000)
    old = build_shared_prompts(rec, knowledge, cfg, tokenizer, GROUPS)
    assert old[0]['document_budget'] < 14000
    new = build_shared_prompts(rec, knowledge, replace(cfg, prompt_layout='fixed_prefix', engine_max_model_len=20000),
                               tokenizer, GROUPS)
    assert [p['spans'] for p in new] == [p['spans'] for p in old]
    assert new[0]['document_budget'] == old[0]['document_budget']
    assert max(len(p['token_ids']) for p in new) + 640 + 32 > 12000      # the moved requests need the engine context
    # Without that room the source shrinks further until the moved requests fit; preparation does not fail.
    tight = build_shared_prompts(rec, knowledge, replace(cfg, prompt_layout='fixed_prefix'), tokenizer, GROUPS)
    assert tight[0]['document_budget'] < old[0]['document_budget']
    assert max(len(p['token_ids']) for p in tight) + 640 + 32 <= 12000 and 'fixed_prefix_sha256' in tight[0]


def test_unresolved_governing_law_keeps_the_current_layout():
    rec, knowledge, cfg, tokenizer = fixture()
    knowledge.alternatives = ['national', 'local']
    old = build_shared_prompts(rec, knowledge, cfg, tokenizer, GROUPS)
    new = build_shared_prompts(rec, knowledge, replace(cfg, prompt_layout='fixed_prefix'), tokenizer, GROUPS)
    assert [p['messages'] for p in new] == [p['messages'] for p in old]
    assert not any('fixed_prefix_sha256' in p for p in new)


def test_lean_prefix_leaves_the_law_out_and_is_one_prefix_for_every_notice():
    from submission.pps.prompts import FIXED_PREFIX_LEAN_ORDER_LINE
    rec, knowledge, cfg, tokenizer = fixture(prompt_layout='fixed_prefix_lean')
    old = build_shared_prompts(rec, knowledge, replace(cfg, prompt_layout='current'), tokenizer, GROUPS)
    lean = build_shared_prompts(rec, knowledge, cfg, tokenizer, GROUPS)
    assert lean[0]['messages'][0]['content'] == old[0]['messages'][0]['content'].replace(ORDER_LINE, FIXED_PREFIX_LEAN_ORDER_LINE)
    fixed = lean[0]['messages'][1]['content'].split('[입력정보]', 1)[0]
    assert '[v1~v9 항목별 판단 안내]' in fixed and all('[법령 v' not in p['messages'][1]['content'] for p in lean)
    for before, after in zip(old, lean):
        _, _, instructions, question = split_current(before)
        assert instructions in fixed and after['messages'][1]['content'].endswith(QUESTION_HEADER + question)
    knowledge.alternatives = ['national', 'local']        # an unresolved law does not split the lean prefix
    assert build_shared_prompts(rec, knowledge, cfg, tokenizer, GROUPS)[0]['fixed_prefix_sha256'] == lean[0]['fixed_prefix_sha256']


def test_rubric_variant_changes_only_the_shared_table():
    from submission.pps.prompts import RUBRIC_PART_REVISIONS
    rec, knowledge, cfg, tokenizer = fixture(prompt_layout='fixed_prefix')
    base = build_shared_prompts(rec, knowledge, cfg, tokenizer, GROUPS)
    variant = build_shared_prompts(rec, knowledge, replace(cfg, rubric_variant='parts:v1b'), tokenizer, GROUPS)
    heads = {p['messages'][1]['content'].split('[입력정보]', 1)[0] for p in variant}
    revised = RUBRIC_PART_REVISIONS['v1b'][1]
    assert len(heads) == 1 and f'v1 항목1: {revised}' in heads.pop()
    assert revised not in base[0]['messages'][1]['content']
    for before, after in zip(base, variant):
        tail = lambda p: p['messages'][1]['content'].split('[입력정보]', 1)[1]
        assert tail(before) == tail(after) and after['spans'] == before['spans']
        assert after['messages'][1]['content'] != before['messages'][1]['content']


def test_a10_dependency_law_is_read_in_the_fixed_prefix():
    rec, knowledge, cfg, tokenizer = fixture(prompt_layout='fixed_prefix', legal_source_policy='direct_production',
                                             input_strategy='audited')
    prompts = build_shared_prompts(rec, knowledge, cfg, tokenizer, GROUPS)
    fixed = prompts[0]['messages'][1]['content'].split('[입력정보]', 1)[0]
    assert '[v10~v18 배포 법령 참고 발췌]\n[직접생산 법령 발췌]\n' in fixed and '[법령 v10~v18]' not in fixed
    assert [p.get('legal_reading', {}).get('topic') for p in prompts] == [None, 'direct_production', None]


needs_data = pytest.mark.skipif(not (ROOT / 'models/gemma-tokenizer').is_dir() or not (ROOT / 'data_open/data').is_dir(),
                                reason='Local data package and fixed tokenizer required')


@pytest.fixture(scope='module')
def real():
    from transformers import AutoTokenizer
    from submission.b4_entry import B4Pipeline
    tokenizer = AutoTokenizer.from_pretrained(ROOT / 'models/gemma-tokenizer', local_files_only=True)
    rec = {'id': 'rebuild-synthetic', 'meta': {'적용계약법': '지방계약법', '입찰추정가격': 150000000},
           'docs': [{'doc_id': 'N1', 'type': '공고문', 'text': '입찰 공고\n사업명: 시험장비 구매\n추정가격: 1억5천만원\n'
                     '입찰참가자격: 소기업 또는 소상공인에 한함\n품명: 시험장비'}]}
    make = lambda **options: B4Pipeline(ROOT / 'data_open/data', tokenizer, catalog_review='current', **options)
    return rec, make, tokenizer


@needs_data
def test_fixed_prefix_a10_law_equals_the_current_substitution(real):
    from submission.pps.legal_query_contract import split_legal_block
    rec, make, _ = real
    old = {p['batch']: p for p in make().bundle(rec)}
    new = {p['batch']: p for p in make(prompt_layout='fixed_prefix', engine_max_model_len=24576).bundle(rec)}
    fixed = new['A1']['messages'][1]['content'].split('[입력정보]', 1)[0]
    for profile, label in (('A1', 'v1~v9'), ('A10', 'v10~v18'), ('A19', 'v19~v24')):
        _, law, _ = split_legal_block(old[profile]['messages'])
        assert f'[{label} 배포 법령 참고 발췌]\n{law}\n\n' in fixed
    assert new['A10']['legal_reading'] == old['A10']['legal_reading']
    assert {new[p]['fixed_prefix_sha256'] for p in ('A1', 'A10', 'A19')} == {new['A1']['fixed_prefix_sha256']}
    assert new['A1']['spans'] == old['A1']['spans']
    for profile in ('S9', 'L19'):       # sized as before: the same add-on packets
        assert (profile in new) == (profile in old)
        if profile in new:
            assert new[profile]['token_ids_sha256'] == old[profile]['token_ids_sha256']
            assert 'fixed_prefix_tokens' not in new[profile]


@needs_data
def test_a10_premise_scores_source_facts_only(real):
    rec, make, _ = real
    pipe = make()
    # Only the price band holds: 1.5억 is inside 1억~2.3억. The one-line eligibility text is not read as an operative
    # size restriction, and there is no listed code or certificate.
    assert pipe.a10_premise(rec) == 1
    from submission.prep import prepare_record
    assert 'a10_premise' not in prepare_record(pipe, rec)
    assert prepare_record(make(a10_attach=True), rec)['a10_premise'] == 1


@needs_data
def test_budget_profiles_send_the_same_a10_prompt_per_budget(real):
    rec, make, _ = real
    packets = make(a10_budget_profiles=(0, 256, 768)).bundle(rec)
    a10 = [p for p in packets if p['batch'].startswith('A10')]
    assert [p['batch'] for p in a10] == ['A10_t0', 'A10_t256', 'A10_t768']
    assert [p['generation']['thinking_budget'] for p in a10] == [0, 256, 768]
    assert len({p['token_ids_sha256'] for p in a10}) == 1 and len({p['request_key'] for p in a10}) == 3


@needs_data
def test_service_catalog_precedes_metadata_only_when_asked(real):
    from submission.pps.catalog_scope import prompt, service_catalog
    from submission.pps.specialist_packets import selection
    rec, make, tokenizer = real
    pipe = make()
    control = next(p for p in pipe.bundle(rec) if p['batch'] == 'A1')
    shared = selection(rec, control, tokenizer)
    before = prompt(rec, shared, tokenizer, pipe.knowledge.products)
    after = prompt(rec, shared, tokenizer, pipe.knowledge.products, catalog_first=True)
    user = after['messages'][1]['content']
    assert user.startswith('제공 고시 전체 서비스 목록:\n') and after['spans'] == before['spans']
    assert sorted(user.split('\n')) == sorted(before['messages'][1]['content'].split('\n'))
    with pytest.raises(ValueError):
        prompt(rec, shared, tokenizer, pipe.knowledge.products, q10_variant='purchase_roles', catalog_first=True)


class BudgetPipeline(TokenPipeline):
    """A10 replaced by two budget profiles carrying A10's prior work."""

    def bundle(self, rec):
        packets = []
        for pkt in super().bundle(rec):
            if pkt['batch'] == 'A10':
                packets += [{**pkt, 'batch': f'A10_t{b}', 'request_key': pkt['request_key'] + f':t{b}'} for b in (0, 768)]
            else:
                packets.append(pkt)
        return packets


def test_a_tier_with_a10_sends_every_budget_profile(tmp_path, monkeypatch):
    import test_w4_tier_plan
    monkeypatch.setattr(test_w4_tier_plan, 'TokenPipeline', BudgetPipeline)
    report, engine = simulate(tmp_path, 20, a=150e-6, b=1e-3, tier_plan='adaptive', tier_ceiling=1, tier_floor=1,
                              total_runtime_seconds=100000)
    keys = [r.rsplit('#', 1)[0] for r in engine.submitted]
    assert sum(k.endswith(':t0') for k in keys) == sum(k.endswith(':t768') for k in keys) == 20
    assert not any(k.startswith('A:') and k.endswith(':10') for k in keys)
    assert report['tier_counts'] == {TIER1: 20}
    assert set(report['profile_stats']['profiles']) >= {'A10_t0', 'A10_t768'}


def attach_run(tmp_path, monkeypatch, *, attach, scale=1., records=1853, load=300.):
    """Prior-token work at a = 100 us and b = 1 ms: tier 2 costs 2.70 s and A10 1.41 s per record.

    The cost priors are the nominal engine's, so the first decision is tier 2; `scale` slows the engine.
    """
    # The synthetic pipeline and engine share CONFIG; the executor reads a10_attach from it.
    monkeypatch.setattr(CONFIG, 'a10_attach', attach, raising=False)
    monkeypatch.setattr(TokenPipeline, 'a10_premise', lambda self, rec: 1, raising=False)
    return simulate(tmp_path, records, a=scale * 100e-6, b=scale * 1e-3, load=load, tier_plan='adaptive',
                    prior_prefill_seconds_per_token=100e-6, prior_decode_seconds_per_token=1e-3)


def attached_indices(engine):
    return sorted(int(r.split(':')[1][1:]) for r in engine.submitted if r.startswith('A:') and r.endswith(':10#0'))


def test_a10_attachment_rule_is_the_ladder_budget_with_this_a10():
    """need(tier 2) + A10 <= (1 - margin_fraction - upgrade_margin_fraction) x time left, measured costs only."""
    from submission.stream import A10_ATTACH_MIN_WINDOWS, StreamingExecutor, StreamOptions

    class Stats:
        def seconds_per_record(self, tier, a, b):
            return {1: 4.0, 2: 2.5}[tier]            # A10 adds 1.5 s

    def decide(time_left, need2, ready=True, windows=A10_ATTACH_MIN_WINDOWS, premise=1):
        executor = StreamingExecutor.__new__(StreamingExecutor)
        executor.options, executor.stats = StreamOptions(), Stats()
        executor.states = [SimpleNamespace(a10_premise=premise)]
        view = {'ready': ready, 'windows': windows, 'a': 1., 'b': 1., 'time_left': time_left, 'need': {2: need2}}
        executor.policy = SimpleNamespace(projection=lambda index: view)
        return executor._attach_a10(0)
    assert decide(1000., 798.5) and not decide(1000., 798.6)
    assert decide(1000., 798.5, premise=None) and decide(1000., 798.5, premise=4)
    # A record with no source-only A10 premise needs twice the reserve (review round 05).
    assert decide(1000., 598.5, premise=0) and not decide(1000., 598.6, premise=0)
    assert not decide(1000., 100., ready=False) and not decide(1000., 100., windows=A10_ATTACH_MIN_WINDOWS - 1)


def test_a10_attachment_keeps_every_record_answered(tmp_path, monkeypatch):
    off, _ = attach_run(tmp_path / 'off', monkeypatch, attach=False)
    on, engine = attach_run(tmp_path / 'on', monkeypatch, attach=True)
    assert TIER1 not in off['tier_counts'] and off['a10_attach'] == {
        'enabled': False, 'decisions': 0, 'attached': 0, 'attached_premise_zero': 0, 'reserve_fraction_of_time_left': .2}
    attach = on['a10_attach']
    assert attach['enabled'] and attach['attached'] == on['tier_counts'][TIER1] == len(attached_indices(engine)) > 0
    assert on['records_without_model_call'] == 0 and on['records_after_submission_deadline'] == 0
    # Attachment waits for six fitted cost windows (about 130 records at 2.7 s).
    assert attached_indices(engine)[0] > 100
    assert on['prefix_cache']['by_profile']['A1']['requests'] == 1853 and on['engine_preemptions'] is None


def test_replay_keeps_one_budget_profile_as_the_a10_group():
    import sys
    sys.path.insert(0, str(ROOT / 'tools'))
    from cell_reconsume import replay_record
    rec = {'id': 'r1'}
    rows = {'A10_t0': {'v10': 1, 'e10': ''}, 'A10_t768': {'v10': 0, 'e10': ''}}
    responses = [({'batch': profile, 'items': list(range(10, 19))}, {'text': profile}) for profile in rows]
    consume = lambda pipe, record, packet, response: {'row': rows[packet['batch']]}
    rules = lambda pipe, record, items: ({f'{f}{k}': 0 if f == 'v' else '' for k in items for f in 've'}, [])
    saved = {'row': {c: 0 if c[0] == 'v' else '' for c in [f'{f}{k}' for k in range(1, 25) for f in 've']}}
    kept = replay_record(None, rec, saved, responses, consume, rules, drop=('A10_t768',))
    assert kept['row']['v10'] == 1
    with pytest.raises(ValueError):
        replay_record(None, rec, saved, responses, consume, rules)
