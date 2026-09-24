"""Fixed tier plan: the same input gets the same requests; a lagging run hands over to TierPolicy.

No GPU, model or labels. CostEngine finishes one request at a time after
a x new prefill + b x decode seconds of fake clock, so a slower engine is a
larger a or b; preparation costs no engine time.
"""
from types import SimpleNamespace

import pytest

from submission.prep import PreparationPool
from submission.stream import (PLAN_PRIORS, PRIOR_CACHED, PRIOR_TOKENS, TIERS, CostModel, FixedTierPlan, ProfileStats,
                               StreamOptions, TierPolicy, StreamingExecutor, execute_stream)
from test_stream_executor import (Clock, ClockedPool, FakeRunner, SyntheticPipeline, answer, read_rows, record, run,
                                  valid)

L40S = {'plan_prefill_seconds_per_token': 162.8e-6, 'plan_decode_seconds_per_token': .352e-3,
        'plan_engine_ready_seconds': 320.}
TIER2, TIER3 = TIERS[2]['name'], TIERS[3]['name']


def plan(records=1853, started_at=0., **overrides):
    options = StreamOptions(**{**L40S, **overrides})
    return FixedTierPlan(options, ProfileStats(), CostModel(options.prior_prefill_seconds_per_token,
                                                            options.prior_decode_seconds_per_token),
                         total_records=records, clock=Clock(started_at), started_at=started_at,
                         submit_deadline=started_at + options.total_runtime_seconds - options.margin_seconds)


def spread(count, n):
    """Indices that receive `count` of `n` evenly, as the plan spreads its cheaper tier."""
    return {i for i in range(n) if (i + 1) * count // n > i * count // n}


class TokenPipeline(SyntheticPipeline):
    """Synthetic packets carrying each profile's prior work: new prefill, cached prefix and decode tokens."""

    def bundle(self, rec):
        packets = super().bundle(rec)
        for pkt in packets:
            new, decode = PRIOR_TOKENS[pkt['batch']]
            cached = round(new * PRIOR_CACHED[pkt['batch']] / (1 - PRIOR_CACHED[pkt['batch']]))
            pkt['token_ids'] = range(new + cached)
            pkt['work'] = {'new_prefill': new, 'cached': cached, 'decode': decode}
        return packets


class CostEngine(FakeRunner):
    """One serial engine: each step finishes the oldest request after seconds(packet) of fake clock."""

    def __init__(self, clock, seconds):
        super().__init__(lambda pkt, attempt: valid(pkt, 0), clock)
        self.seconds = seconds

    def step(self):
        if not self.queue:
            return []
        request_id, (pkt, _) = self.queue.popitem(last=False)
        self.clock.advance(self.seconds(pkt))
        text, reason = self.responder(pkt, 0)
        return [SimpleNamespace(request_id=request_id, finished=True, prompt_token_ids=None,
                                num_cached_tokens=pkt['work']['cached'],
                                outputs=[SimpleNamespace(text=text, token_ids=(), finish_reason=reason, stop_reason=None)])]

    def response_from_native(self, output, pkt):
        return {**super().response_from_native(output, pkt), 'output_tokens': pkt['work']['decode']}


class QuietPool(ClockedPool):
    """The fake clock moves only while the loop idles; preparation runs beside the engine."""

    def poll(self, timeout=0.):
        self.clock.advance(timeout)
        return PreparationPool.poll(self, 0.)


def simulate(tmp_path, records, *, a, b, load=304., q10_share=.585, **options):
    """Stream `records` synthetic notices through an engine costing a/b seconds per new prefill/decode token."""
    recs = [record(f'r{i:05d}') for i in range(records)]
    q10 = {recs[i]['id'] for i in spread(round(records * q10_share), records)}
    pipeline, clock = TokenPipeline(q10=q10), Clock(0.)
    engine = CostEngine(clock, lambda pkt: a * pkt['work']['new_prefill'] + b * pkt['work']['decode'])

    def factory(config, journal):
        clock.advance(load)
        return engine
    report = execute_stream('unused', 'unused', tmp_path / 'output', tokenizer_dir='unused', runner_factory=factory,
                            options=StreamOptions(**options), records_override=recs, pool=QuietPool(pipeline, clock),
                            pipeline=pipeline, clock=clock, started_at=clock())
    return report, engine


def test_default_options_plan_from_the_named_priors():
    options = StreamOptions()
    assert options.tier_plan == 'fixed' and options.tier_ceiling == 2
    assert (options.plan_prefill_seconds_per_token, options.plan_decode_seconds_per_token,
            options.plan_engine_ready_seconds) == (PLAN_PRIORS['prefill_seconds_per_token'],
                                                   PLAN_PRIORS['decode_seconds_per_token'],
                                                   PLAN_PRIORS['engine_ready_seconds'])
    for bad in ({'tier_plan': 'measured'}, {'plan_decode_seconds_per_token': 0.}, {'plan_engine_ready_seconds': -1.},
                {'guard_reserve_fraction': 1.}, {'guard_reserve_fraction': 0.}, {'guard_min_slack_seconds': -1.}):
        with pytest.raises(ValueError):
            StreamOptions(**bad)


def test_plan_for_1853_records_with_the_l40s_priors():
    fixed = plan()
    receipt = fixed.receipt()
    assert receipt['planned_counts'] == {TIER2: 1035, TIER3: 818} and receipt['plan_records'] == 1853
    # TierPolicy's budget rule from the planned engine-ready time: (7200 - 180 - 320) x 0.9 seconds of work,
    # and one more tier2 record would exceed it.
    assert receipt['time_left_seconds'] == 6700.
    assert fixed.work <= 6700 * .9 < fixed.work + fixed.cost[2] - fixed.cost[3]
    assert receipt['seconds_per_record'] == {TIER2: pytest.approx(3.6218, abs=1e-4), TIER3: pytest.approx(2.7886, abs=1e-4),
                                             TIERS[4]['name']: pytest.approx(1.8718, abs=1e-4)}
    assert fixed.reserve >= 670 and receipt['guard_slack_seconds'] == pytest.approx(fixed.reserve / 2, abs=.1)
    # The cheaper tier is spread by input index: every prefix holds its share within one record.
    assert {i for i, t in enumerate(fixed.tiers) if t == 3} == spread(818, 1853)
    cheap = 0
    for i, tier in enumerate(fixed.tiers):
        cheap += tier == 3
        assert abs(cheap - (i + 1) * 818 / 1853) < 1
    # Only the record count and the options enter: another start time gives the same plan.
    again = plan(started_at=98765.4321)
    assert again.tiers == fixed.tiers and again.receipt()['plan_sha256'] == receipt['plan_sha256']


def test_plan_takes_the_ceiling_when_it_fits_the_floor_when_nothing_does_and_the_projection_prefix():
    assert set(plan(records=200).tiers) == {2}
    assert set(plan(records=3000).tiers) == {3, 4}        # tier3 no longer fits: the mix moves down the ladder
    assert set(plan(records=5000).tiers) == {4}           # nothing fits: the floor, like TierPolicy
    assert set(plan(records=5000, tier_floor=3).tiers) == {3}
    projected = plan(records=200, projection_records=1853)
    assert projected.tiers == plan().tiers
    counts = projected.receipt()['planned_counts']
    assert sum(counts.values()) == 200 and counts == {TIER2: 200 - len(spread(818, 1853) & set(range(200))),
                                                      TIER3: len(spread(818, 1853) & set(range(200)))}


def test_same_input_gets_the_same_tiers_and_requests_at_different_engine_speeds(tmp_path, monkeypatch):
    monkeypatch.setenv('PPS_STREAM_JOURNAL', '1')
    recs = [record(f'r{i:02d}') for i in range(24)]
    q10 = {r['id'] for i, r in enumerate(recs) if i % 5 in (0, 2, 3)}
    options = StreamOptions(projection_records=1853, **L40S)        # the 1853-record plan mixes tier2 and tier3
    runs = []
    for seconds in (.5, 20.):
        clock = Clock()
        engine = FakeRunner(answer, clock, seconds_per_step=seconds)
        report, engine = run(tmp_path / str(seconds), recs, SyntheticPipeline(q10=q10), answer, options=options,
                             clock=clock, runner=engine)
        tiers = {r['id']: r['tier'] for r in read_rows(tmp_path / str(seconds) / 'output/stream_records_00000.jsonl.gz')}
        runs.append((report, set(engine.submitted), tiers))
    (fast, fast_requests, fast_tiers), (slow, slow_requests, slow_tiers) = runs
    assert slow['seconds'] > 10 * fast['seconds']
    assert fast_requests == slow_requests and fast_tiers == slow_tiers
    assert {rid for rid, tier in fast_tiers.items() if tier == TIER3} == {recs[i]['id'] for i in spread(818, 1853) if i < 24}
    for rec in recs:     # Q10 follows its packet, A19 follows the planned tier
        rid = rec['id']
        assert (f'Q:{rid}:10#0' in fast_requests) == (rid in q10)
        assert (f'A:{rid}:19#0' in fast_requests) == (fast_tiers[rid] == TIER2)
    for report in (fast, slow):
        receipt = report['tier_plan']
        assert receipt['mode'] == 'fixed' and receipt['guard_triggered'] is False and receipt['switch_record_index'] is None
        assert report['tier_counts'] == receipt['planned_counts'] and report['tier_timeline'] == []
        assert report['records_without_model_call'] == 0
    assert fast['tier_plan']['plan_sha256'] == slow['tier_plan']['plan_sha256']


def test_l40s_engine_keeps_the_plan_and_finishes_with_the_planned_reserve(tmp_path):
    report, engine = simulate(tmp_path, 1853, a=162.8e-6, b=.352e-3, **L40S)
    receipt = report['tier_plan']
    assert receipt['guard_triggered'] is False and report['tier_counts'] == receipt['planned_counts']
    assert report['tier_counts'] == {TIER2: 1035, TIER3: 818} and report['tier_timeline'] == []
    assert report['records_without_model_call'] == 0 and report['deadline']['submissions_closed_early'] is False
    assert receipt['max_lag_seconds'] < 0 and report['seconds'] < 7020 - 600


def test_guard_hands_a_slow_run_to_the_measured_policy_and_it_finishes_in_time(tmp_path):
    report, engine = simulate(tmp_path, 1853, a=1.2 * 162.8e-6, b=1.2 * .352e-3, **L40S)
    receipt = report['tier_plan']
    assert receipt['guard_triggered'] is True and receipt['switch_lag_seconds'] > receipt['guard_slack_seconds']
    switch = receipt['switch_record_index']
    assert 0 < switch < 1853 and receipt['max_lag_seconds'] == receipt['switch_lag_seconds']
    # Up to the switch the records ran the plan; afterwards TierPolicy's measured decisions.
    requests = {key.split('#')[0] for key in engine.submitted}
    planned = plan().tiers
    assert all((f'A:r{i:05d}:19' in requests) == (planned[i] == 2) for i in range(switch))
    assert report['measured_tier_decisions'] > 0 and report['tier_timeline']
    assert all(entry['submitted'] >= switch for entry in report['tier_timeline'])
    assert report['records_without_model_call'] == 0 and report['records_after_submission_deadline'] == 0
    assert report['deadline']['submissions_closed_early'] is False and report['seconds'] < 7020


def test_adaptive_mode_runs_the_measured_policy_for_every_record(tmp_path):
    executors = {mode: StreamingExecutor([record('r1')], SyntheticPipeline(), None, None, SimpleNamespace(root=tmp_path),
                                         StreamOptions(tier_plan=mode), started_at=0., clock=Clock(0.), source_code='x')
                 for mode in ('adaptive', 'fixed')}
    assert type(executors['adaptive'].policy) is TierPolicy and type(executors['fixed'].policy) is FixedTierPlan
    report, engine = simulate(tmp_path, 1853, a=162.8e-6, b=.352e-3, tier_plan='adaptive')
    assert report['tier_plan'] == {'mode': 'adaptive'}
    # TierPolicy's own first decision: the A100 prior finds tier2 unaffordable for 1853 records and starts at tier3.
    assert report['tier_timeline'][0]['basis'] == 'prior' and report['tier_timeline'][0]['to'] == TIER3
    assert report['records_without_model_call'] == 0
