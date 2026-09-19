"""Deadline projection chooses tiers from measured engine costs, never from labels.

The fixture is the per-request timing of the 2026-09-19 A100-40GB stream runs
(tests/fixtures/stream_gpu_verify_20260919.json): profile, submit/finish
seconds, prompt/cached/decode tokens. No prompts, responses or labels.
"""
import json
from pathlib import Path

import pytest

from submission.stream import CostModel, ProfileStats, StreamOptions, TIERS, TierPolicy

FIXTURE = Path(__file__).resolve().parent / 'fixtures/stream_gpu_verify_20260919.json'
A100 = {'a': 157e-6, 'b': .35e-3}


class Clock:
    def __init__(self, start=0.):
        self.now = start

    def __call__(self):
        return self.now


def make(*, ceiling=0, floor=len(TIERS) - 1, projection=1853, records=200, seconds_left=6600., **overrides):
    clock = Clock()
    options = StreamOptions(total_runtime_seconds=7200, tier_ceiling=ceiling, tier_floor=floor,
                            projection_records=projection, **overrides)
    stats = ProfileStats()
    model = CostModel(options.prior_prefill_seconds_per_token, options.prior_decode_seconds_per_token)
    policy = TierPolicy(options, stats, model, total_records=records, clock=clock, submit_deadline=seconds_left)
    return policy, clock, stats, model


def replay(model, stats, requests, clock=None, *, kind='engine'):
    """Feed a GPU log through the cost model the way the executor does."""
    inflight = 0
    events = sorted([(r['submitted'], 1, r) for r in requests] + [(r['finished'], -1, r) for r in requests],
                    key=lambda e: (e[0], e[1]))
    busy = False
    for t, kind_, r in events:
        model.tick(t, busy)
        if clock is not None:
            clock.now = t
        if kind_ == 1:
            inflight += 1
        else:
            inflight -= 1
            new_prefill = r['prompt'] - min(r['prompt'], r['cached'])
            model.observe(new_prefill, r['decode'])
            stats.observe_request(r['profile'], new_prefill, r['decode'], r['cached'] / r['prompt'])
        busy = inflight > 0


def test_tier_ladder_costs_are_monotone_with_prior_costs():
    stats = ProfileStats()
    costs = [stats.seconds_per_record(t, A100['a'], A100['b']) for t in range(len(TIERS))]
    assert costs == sorted(costs, reverse=True)
    assert 6.2 < costs[0] < 7.6 and 3.2 < costs[2] < 3.9 and 2.4 < costs[3] < 2.9 and 1.6 < costs[4] < 2.0


@pytest.mark.skipif(not FIXTURE.is_file(), reason='GPU timing fixture missing')
def test_cost_model_recovers_the_a100_canonical_cost():
    fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))['canonical']
    options = StreamOptions()
    model = CostModel(options.prior_prefill_seconds_per_token * 2, options.prior_decode_seconds_per_token * 2)
    stats = ProfileStats()
    replay(model, stats, fixture['requests'])
    a, b, windows = model.parameters()
    assert windows >= 15 and model.ready(options)
    # Doubled priors must be pulled back to the measured engine within 15%.
    assert abs(a - A100['a']) / A100['a'] < .15
    per_record = stats.seconds_per_record(0, a, b)
    generation = fixture['seconds'] - fixture['engine_load_seconds']
    assert abs(per_record - generation / fixture['records']) / (generation / fixture['records']) < .15
    assert stats.snapshot(a, b)['profiles']['A10']['cached_fraction'] > .6


@pytest.mark.skipif(not FIXTURE.is_file(), reason='GPU timing fixture missing')
def test_prefill_heavy_mix_does_not_read_as_a_slower_engine():
    """The old single-weight model collapsed to a1_only on the projection run."""
    fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
    canonical = CostModel(A100['a'], A100['b'])
    replay(canonical, ProfileStats(), fixture['canonical']['requests'])
    a1, b1, _ = canonical.parameters()
    projection = CostModel(A100['a'], A100['b'])
    replay(projection, ProfileStats(), fixture['projection']['requests'])
    a2, b2, _ = projection.parameters()
    stats = ProfileStats()
    tier2_canonical = stats.seconds_per_record(2, a1, b1)
    tier2_projection = stats.seconds_per_record(2, a2, b2)
    assert abs(tier2_canonical - tier2_projection) / tier2_canonical < .25


@pytest.mark.skipif(not FIXTURE.is_file(), reason='GPU timing fixture missing')
def test_a100_projection_settles_on_the_catalog_tier_not_a1_only():
    fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))['canonical']
    # Process-relative submit deadline of a 7,200s budget; the fixture clock is process-relative too.
    policy, clock, stats, model = make(seconds_left=7200.)
    assert policy.decide(0) == 3       # prior: tier2 needs about 6,500s and misses the 10% margin
    replay(model, stats, fixture['requests'], clock)
    view = policy.projection(200)
    assert view['ready'] and view['need'][4] < view['need'][3] < view['need'][2] < view['need'][0]
    assert view['need'][3] <= view['time_left'] * .9 < view['need'][2]
    decisions = [policy.decide(n) for n in range(200, 260)]
    assert set(decisions) == {3} and TIERS[3]['name'] == 'source_rules_a10_a19' 


def test_prior_picks_the_richest_affordable_tier_and_respects_bounds():
    fast, _, _, _ = make(prior_prefill_seconds_per_token=1e-9, prior_decode_seconds_per_token=1e-9)
    assert fast.decide(0) == 0
    slow, _, _, _ = make(prior_prefill_seconds_per_token=1., prior_decode_seconds_per_token=1.)
    assert slow.decide(0) == len(TIERS) - 1
    bounded, _, _, _ = make(ceiling=2, floor=3, prior_prefill_seconds_per_token=1e-9, prior_decode_seconds_per_token=1e-9)
    assert bounded.decide(0) == 2
    bounded, _, _, _ = make(ceiling=2, floor=3, prior_prefill_seconds_per_token=1., prior_decode_seconds_per_token=1.)
    assert bounded.decide(0) == 3


def synthetic_stream(count, *, prefill, decode, seconds_each, start=0.):
    requests, t = [], start
    for _ in range(count):
        requests.append({'profile': 'A1', 'submitted': t, 'finished': t + seconds_each, 'prompt': prefill,
                         'cached': 0, 'decode': decode})
        t += seconds_each
    return requests


def test_measured_slow_engine_descends_one_tier_at_a_time_with_dwell():
    # Prior ten times too optimistic; the engine then measures 360us per prefill token.
    policy, clock, stats, model = make(prior_prefill_seconds_per_token=36e-6, prior_decode_seconds_per_token=.035e-3)
    assert policy.decide(0) == 0
    replay(model, stats, synthetic_stream(60, prefill=11000, decode=256, seconds_each=4.), clock)
    assert model.ready(policy.options)
    path = [policy.decide(n) for n in range(60, 120)]
    assert path[0] == 1 and max(path) == 4 and all(b - a in (0, 1) for a, b in zip(path, path[1:]))
    steps = [i for i, (a, b) in enumerate(zip(path, path[1:])) if a != b]
    assert all(later - earlier >= 3 for earlier, later in zip(steps, steps[1:]))
    assert policy.timeline[-1]['basis'] == 'measured' and policy.timeline[-1]['a_us_per_prefill_token'] > 250


def test_measured_fast_engine_ascends_after_dwell_and_extra_slack():
    # Prior three times slower than the A100; the engine then measures twice the A100 speed.
    policy, clock, stats, model = make(prior_prefill_seconds_per_token=471e-6, prior_decode_seconds_per_token=1.05e-3)
    assert policy.decide(0) == len(TIERS) - 1
    stream, t = [], 0.
    for i in range(2000):
        if i % 2:
            stream.append({'profile': 'A10', 'submitted': t, 'finished': t + .46, 'prompt': 3400, 'cached': 0, 'decode': 1062})
            t += .46
        else:
            stream.append({'profile': 'A1', 'submitted': t, 'finished': t + .86, 'prompt': 11000, 'cached': 0, 'decode': 256})
            t += .86
    replay(model, stats, stream, clock)
    assert model.ready(policy.options)
    a, b, windows = model.parameters()
    # A fixed request mix cannot separate a from b; the observed mix cost must still be right.
    assert windows >= 15 and abs(a * (11000 + 3400) + b * (256 + 1062) - 1.32) / 1.32 < .1
    path = [policy.decide(n) for n in range(200, 400)]   # timing evidence, not records submitted
    assert path[0] == len(TIERS) - 1 and path[-1] < len(TIERS) - 1
    assert all(a_ - b_ in (0, 1) for a_, b_ in zip(path, path[1:]))
    steps = [i for i, (a_, b_) in enumerate(zip(path, path[1:])) if a_ != b_]
    assert steps and all(later - earlier >= policy.options.ascent_dwell_records for earlier, later in zip(steps, steps[1:]))
    view = policy.projection(400)
    affordable = next(t for t in range(len(TIERS)) if view['need'][t] <= view['time_left'] * .8)
    assert path[-1] == affordable


def test_latency_reserve_shrinks_the_usable_window():
    policy, _, _, _ = make()
    assert policy.effective_submit_deadline() == policy.submit_deadline
    for _ in range(50):
        policy.observe_latency(400.)
    assert policy.effective_submit_deadline() < policy.submit_deadline - 300


def test_presence_and_cache_observations_change_projected_cost():
    stats = ProfileStats()
    before = stats.seconds_per_record(0, A100['a'], A100['b'])
    for _ in range(100):
        stats.observe_presence('Q10', False)
        stats.observe_presence('S9', False)
    assert stats.seconds_per_record(0, A100['a'], A100['b']) < before
    for _ in range(20):
        stats.observe_request('A19', 12000, 290, 0.)
    assert stats.snapshot()['profiles']['A19']['cached_fraction'] < .1
    assert stats.seconds_per_record(2, A100['a'], A100['b']) > stats.seconds_per_record(3, A100['a'], A100['b'])


def test_prior_choice_is_made_once_and_held_until_measured():
    policy, clock, stats, model = make()
    first = policy.decide(0)
    assert first == 3
    # Presence drift before any measurement must not move the tier.
    for _ in range(60):
        stats.observe_presence('Q10', True)
        stats.observe_presence('S9', True)
    clock.now += 100.
    assert all(policy.decide(n) == first for n in range(1, 40))
    assert len(policy.timeline) == 1 and policy.timeline[0]['basis'] == 'prior'

