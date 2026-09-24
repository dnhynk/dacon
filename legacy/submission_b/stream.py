"""Record-major streaming execution against the one fixed engine.

Contract: every input record ends with 24 predictions in the CSV, in time;
a run in which the model never answered writes no CSV.
Records enter the engine in input order, each record's shared-prefix requests
adjacent so the prefix cache is still warm; format failures are retried at
once; and each record's judgment tier comes from a plan fixed before the first
submission, the richest the budget affords, with measured costs deciding only
once the run falls behind that plan. Prompts, schemas and consumers are the canonical
ones. This module decides only which prepared requests run and when, and it
records every such decision.
"""
from __future__ import annotations

import collections
import dataclasses
import gzip
import hashlib
import json
import os
import time
import traceback
from pathlib import Path

from .b4_entry import PROFILES
from .engine import serial
from .pps import corpus_lines, precision_gates
from .pps.data import ABSENCE, missing_evidence_items, write_csv
from .prep import PROFILE_ITEMS
from .runtime import Journal, format_recovery_packet, sha256, source_manifest

# Cheaper tiers drop, in order, the work measured least valuable per second of
# engine time on the development cohort (frozen-response replay, A100 costs):
# L19+S9 (-0.003 F1, 2.2s), the A10 model call (+0.011, 0.9s), the A19 model
# call (-0.018, 0.9s), then the catalog review (-0.075, 0.9s). A1 is never
# dropped while the engine can be reached; a record without any model call is
# a deadline failure.
TIERS = (
    {'name': 'canonical', 'profiles': ('A1', 'A10', 'A19', 'L19', 'Q10', 'W20', 'S9')},
    {'name': 'no_optional_reviews', 'profiles': ('A1', 'A10', 'A19', 'Q10')},
    {'name': 'source_rules_a10', 'profiles': ('A1', 'A19', 'Q10')},
    {'name': 'source_rules_a10_a19', 'profiles': ('A1', 'Q10')},
    {'name': 'a1_only', 'profiles': ('A1',)},
)
FALLBACK_TIER = 'deadline_source_rules'
# A19 follows A1 directly: it shares A1's prefix and finishes quickly, so the
# prefix is still resident; the long-decoding A10 comes third.
SUBMIT_ORDER = ('A1', 'A19', 'A10', 'Q10', 'L19', 'W20')
A_PROFILES = ('A1', 'A10', 'A19')
RULE_PROFILE = {k: profile for profile, items in PROFILE_ITEMS.items() for k in items}   # item -> source-rule row
# Observed on the A100-40GB development200 stream run (2026-09-19): tokens per
# executed request beyond the prefix cache, decode tokens, the fraction of
# records that carry the packet or pass its gate, and the cached share.
PRIOR_TOKENS = {'A1': (10944, 256), 'A10': (3439, 1062), 'A19': (4500, 286), 'L19': (11848, 291),
                'Q10': (9371, 118), 'S9': (9794, 236), 'W20': (12000, 300)}
PRIOR_PRESENCE = {'A1': 1., 'A10': 1., 'A19': 1., 'L19': 1., 'Q10': .585, 'S9': .188, 'W20': 0.}
PRIOR_CACHED = {'A1': .11, 'A10': .71, 'A19': .66, 'L19': .01, 'Q10': 0., 'S9': .13, 'W20': 0.}
# The fixed tier plan's only machine inputs: engine seconds per new prefill and
# per decode token on the official L40S image, and the engine-ready time the plan
# assumes. Update this one set from a measured official-image run; provenance in
# docs/RUNTIME_STREAMING.md.
PLAN_PRIORS = {'prefill_seconds_per_token': 133.8e-6, 'decode_seconds_per_token': 1.314e-3,
               'engine_ready_seconds': 320.}
# config a10_attach: a record the ladder puts at this tier runs at the tier above, whose only
# extra request is A10 (runs/rebuild_20260924/DESIGN.md 2-4).
A10_BASE_TIER = 2
assert set(TIERS[A10_BASE_TIER - 1]['profiles']) == {*TIERS[A10_BASE_TIER]['profiles'], 'A10'}
# Attachment waits for this many fitted cost windows: the ridge prior then weighs under 3% (about
# 1/n^2) of the evidence. Earlier fits lean on the prior, so a slower engine would attach A10 that the
# ladder then pays for with A19; a later start loses no attachment, the unspent time stays in the budget.
A10_ATTACH_MIN_WINDOWS = 6
# A request reading fewer cached tokens than its fixed prefix minus this recomputed it; the
# engine reports cache hits in 32-token steps.
FIXED_PREFIX_TOLERANCE = 64


@dataclasses.dataclass
class StreamOptions:
    total_runtime_seconds: int = 7200
    margin_seconds: int = 180           # no new records after deadline - margin
    abort_grace_seconds: int = 60       # in-flight requests are aborted this long after submissions stop
    tier_ceiling: int = 2               # tiers 0/1 cost more and scored lower than tier2 on fresh dev200 runs
    tier_floor: int = len(TIERS) - 1
    projection_records: int | None = None
    # 'fixed': FixedTierPlan, tiers decided per record before the first submission;
    # 'adaptive': TierPolicy decides every record from measured costs.
    tier_plan: str = 'fixed'
    plan_prefill_seconds_per_token: float = PLAN_PRIORS['prefill_seconds_per_token']
    plan_decode_seconds_per_token: float = PLAN_PRIORS['decode_seconds_per_token']
    plan_engine_ready_seconds: float = PLAN_PRIORS['engine_ready_seconds']
    guard_reserve_fraction: float = .5  # a fixed plan may lag by this share of its reserve before TierPolicy takes over
    guard_min_slack_seconds: float = 120.
    # Engine cost priors: A100-40GB, int8 weight-only MoE with the default
    # Triton config, fitted on the 2026-09-19 stream runs (prefill dominates).
    prior_prefill_seconds_per_token: float = 157e-6
    prior_decode_seconds_per_token: float = .35e-3
    inflight_requests: int = 8          # queue depth; KV capacity, not this, bounds concurrency
    prep_lookahead_records: int = 48
    steady_completions: int = 32        # measured decisions need this many finished requests
    steady_seconds: float = 120.        # and this much engine-busy time
    descent_dwell_records: int = 8
    ascent_dwell_records: int = 16
    margin_fraction: float = .10
    upgrade_margin_fraction: float = .10
    progress_every_records: int = 25

    def __post_init__(self):
        if not 0 <= self.tier_ceiling <= self.tier_floor < len(TIERS):
            raise ValueError('Tier ceiling/floor must satisfy 0 <= ceiling <= floor < number of tiers')
        if self.total_runtime_seconds <= self.margin_seconds + self.abort_grace_seconds:
            raise ValueError('Runtime budget must exceed its own safety margins')
        if self.prior_prefill_seconds_per_token <= 0 or self.prior_decode_seconds_per_token <= 0:
            raise ValueError('Cost priors must be positive')
        if min(self.inflight_requests, self.prep_lookahead_records, self.descent_dwell_records,
               self.ascent_dwell_records, self.steady_completions) < 1 or self.steady_seconds <= 0:
            raise ValueError('Lookahead, dwell and steady-state sizes must be positive')
        if self.projection_records is not None and self.projection_records < 1:
            raise ValueError('Projection record count must be positive')
        if self.tier_plan not in ('fixed', 'adaptive'):
            raise ValueError("Tier plan must be 'fixed' or 'adaptive'")
        if (min(self.plan_prefill_seconds_per_token, self.plan_decode_seconds_per_token) <= 0
                or self.plan_engine_ready_seconds < 0):
            raise ValueError('Plan cost priors must be positive and the planned engine-ready time nonnegative')
        if not 0 < self.guard_reserve_fraction < 1 or self.guard_min_slack_seconds < 0:
            raise ValueError('The guard must keep part of the plan reserve')


class ProfileStats:
    """Prior-seeded moving averages of what each profile costs and how often it runs."""

    def __init__(self, alpha=.15, presence_alpha=.05):
        self.alpha, self.presence_alpha = alpha, presence_alpha
        self.prefill = {p: float(t[0]) for p, t in PRIOR_TOKENS.items()}
        self.decode = {p: float(t[1]) for p, t in PRIOR_TOKENS.items()}
        self.presence = dict(PRIOR_PRESENCE)
        self.cached = dict(PRIOR_CACHED)
        self.observations = collections.Counter()

    def observe_request(self, profile, new_prefill, decode, cached_fraction=None):
        if profile not in self.prefill:
            self.prefill[profile], self.decode[profile], self.presence[profile], self.cached[profile] = new_prefill, decode, 1., 0.
        a = self.alpha
        self.prefill[profile] += a * (new_prefill - self.prefill[profile])
        self.decode[profile] += a * (decode - self.decode[profile])
        if cached_fraction is not None:
            self.cached[profile] += a * (cached_fraction - self.cached[profile])
        self.observations[profile] += 1

    def observe_presence(self, profile, present):
        self.presence[profile] = self.presence.get(profile, 0.) + self.presence_alpha * ((1. if present else 0.) - self.presence.get(profile, 0.))

    def seconds_per_record(self, tier, a, b):
        return sum(self.presence.get(p, 0.) * (a * self.prefill[p] + b * self.decode[p])
                   for p in TIERS[tier]['profiles'] if p in self.prefill)

    def snapshot(self, a=None, b=None):
        result = {'profiles': {p: {'observations': self.observations[p], 'mean_new_prefill': round(self.prefill[p]),
                                   'mean_decode': round(self.decode[p], 1), 'presence': round(self.presence.get(p, 0.), 3),
                                   'cached_fraction': round(self.cached.get(p, 0.), 3)} for p in self.prefill}}
        if a is not None and b is not None:
            result['seconds_per_record_by_tier'] = {TIERS[t]['name']: round(self.seconds_per_record(t, a, b), 3)
                                                    for t in range(len(TIERS))}
        return result


class CostModel:
    """Seconds per new prefill token (a) and per decode token (b), fitted online.

    Windows of engine-busy time accumulate the tokens of the requests that
    finished in them. A ridge fit toward the prior keeps the a/b split sane
    while one request mix dominates; the mix-independent total is what the
    deadline projection needs, and it is measured, not assumed.
    """

    def __init__(self, a0, b0, *, window_seconds=60., prior_windows=1.):
        self.a0, self.b0 = a0, b0
        self.window_seconds, self.prior_windows = window_seconds, prior_windows
        self.windows = []            # (busy seconds, new prefill tokens, decode tokens)
        self.current = None          # [busy seconds at window start, prefill, decode]
        self.busy_seconds = 0.
        self.last_tick = None
        self.completions = 0
        self._fit = None

    def tick(self, now, busy):
        if self.last_tick is not None and busy:
            self.busy_seconds += max(0., now - self.last_tick)
        self.last_tick = now
        if self.current is None:
            self.current = [self.busy_seconds, 0., 0.]
        elif self.busy_seconds - self.current[0] >= self.window_seconds:
            span = self.busy_seconds - self.current[0]
            if self.current[1] + self.current[2] > 0:
                self.windows.append((span, self.current[1], self.current[2]))
                self._fit = None
            self.current = [self.busy_seconds, 0., 0.]

    def observe(self, new_prefill, decode):
        if self.current is None:
            self.current = [self.busy_seconds, 0., 0.]
        self.current[1] += new_prefill
        self.current[2] += decode
        self.completions += 1

    def ready(self, options):
        return (self.completions >= options.steady_completions and self.busy_seconds >= options.steady_seconds
                and len(self.windows) >= 2)

    def parameters(self):
        """Ridge fit of scale factors x, y on the priors: a = x*a0, b = y*b0.

        Window seconds are modelled as x*a0*P + y*b0*D. The penalty
        prior_windows*window_seconds^2 * ((x-1)^2 + (y-1)^2) is worth that many
        windows of evidence, so an absurd prior is overruled quickly, and when
        one request mix dominates (P and D collinear) the residual lands mostly
        on the token type that already carries most of the time.
        """
        if self._fit is None:
            # The first window still contains the pipeline fill; skip it.
            fitted = self.windows[1:]
            # The prior starts worth prior_windows windows of the observed
            # evidence and fades as windows accumulate, so a badly wrong prior
            # is overruled; with one fixed request mix it still fixes the a/b
            # split (the point on the fitted line nearest the prior).
            scale = (sum((self.a0 * p) ** 2 + (self.b0 * d) ** 2 for _, p, d in fitted) / len(fitted)) if fitted else 0.
            lam = self.prior_windows * scale / max(1, len(fitted))
            suu = lam + sum((self.a0 * p) ** 2 for _, p, _ in fitted)
            svv = lam + sum((self.b0 * d) ** 2 for _, _, d in fitted)
            suv = sum(self.a0 * p * self.b0 * d for _, p, d in fitted)
            su = lam + sum(self.a0 * p * t for t, p, _ in fitted)
            sv = lam + sum(self.b0 * d * t for t, _, d in fitted)
            det = suu * svv - suv * suv
            if det <= 0:
                x = y = 1.
            else:
                x, y = (su * svv - sv * suv) / det, (suu * sv - suv * su) / det
            x, y = min(max(x, .02), 50.), min(max(y, .02), 50.)
            self._fit = (x * self.a0, y * self.b0, len(fitted))
        return self._fit

    def snapshot(self):
        a, b, n = self.parameters()
        return {'a_seconds_per_prefill_token': a, 'b_seconds_per_decode_token': b, 'fitted_windows': n,
                'busy_seconds': round(self.busy_seconds, 1), 'completions': self.completions,
                'prior': {'a': self.a0, 'b': self.b0}, 'window_seconds': self.window_seconds}


class TierPolicy:
    """Choose the richest tier whose projected cost fits the remaining budget.

    Before the engine reaches a steady state the prior costs choose a starting
    tier. Afterwards the fitted costs decide: moving down happens one tier at a
    time as soon as the projection misses (a large miss shortens the dwell),
    moving up needs extra slack and a longer dwell.
    """

    def __init__(self, options, stats, model, *, total_records, clock, submit_deadline):
        self.options, self.stats, self.model = options, stats, model
        self.total = max(total_records, options.projection_records or 0)
        self.clock = clock
        self.submit_deadline = submit_deadline
        self.current = options.tier_ceiling
        self.latency = [4., 240.]                 # prior weight, prior total seconds per record
        self.since_change = 10 ** 9
        self.timeline = []
        self.measured_decisions = 0

    def observe_latency(self, seconds):
        self.latency[0] += 1
        self.latency[1] += seconds

    def expected_latency(self):
        return self.latency[1] / self.latency[0]

    def effective_submit_deadline(self):
        """Submitting later than this risks the record being aborted unfinished."""
        return self.submit_deadline - max(0., self.expected_latency() - self.options.abort_grace_seconds)

    def projection(self, submitted, now=None):
        now = self.clock() if now is None else now
        ready = self.model.ready(self.options)
        a, b, windows = self.model.parameters() if ready else (self.model.a0, self.model.b0, 0)
        remaining = self.total - submitted
        time_left = self.effective_submit_deadline() - now
        need = {t: remaining * self.stats.seconds_per_record(t, a, b)
                for t in range(self.options.tier_ceiling, self.options.tier_floor + 1)}
        return {'ready': ready, 'a': a, 'b': b, 'windows': windows, 'remaining': remaining,
                'time_left': time_left, 'need': need}

    def _affordable(self, need, time_left, extra):
        for tier in range(self.options.tier_ceiling, self.options.tier_floor + 1):
            if need[tier] <= time_left * (1 - self.options.margin_fraction - extra):
                return tier
        return self.options.tier_floor

    def decide(self, submitted):
        now = self.clock()
        if self.total - submitted <= 0:
            return self.current
        view = self.projection(submitted, now)
        need, time_left = view['need'], view['time_left']
        if not view['ready']:
            # One prior-based choice; presence drift before any measurement is
            # not evidence, so hold that tier until the engine is measured.
            chosen = self._affordable(need, time_left, 0.) if not self.timeline and self.since_change >= 10 ** 9 else self.current
            basis = 'prior'
        else:
            self.measured_decisions += 1
            basis = 'measured'
            target = self._affordable(need, time_left, 0.)
            if target < self.current:
                richer = self._affordable(need, time_left, self.options.upgrade_margin_fraction)
                chosen = (self.current - 1 if richer < self.current
                          and self.since_change >= self.options.ascent_dwell_records else self.current)
            elif target > self.current:
                shortfall = need[self.current] / max(1., time_left)
                dwell = self.options.descent_dwell_records if shortfall <= 1.25 else 3
                chosen = self.current + 1 if self.since_change >= dwell else self.current
            else:
                chosen = self.current
        if chosen != self.current:
            self.timeline.append({'submitted': submitted, 'basis': basis, 'seconds_left': round(time_left, 1),
                                  'from': TIERS[self.current]['name'], 'to': TIERS[chosen]['name'],
                                  'a_us_per_prefill_token': round(view['a'] * 1e6, 1),
                                  'b_ms_per_decode_token': round(view['b'] * 1e3, 3),
                                  'fitted_windows': view['windows'], 'remaining_projection': view['remaining'],
                                  'need_seconds': {TIERS[t]['name']: round(v) for t, v in need.items()}})
            self.current, self.since_change = chosen, 1
        else:
            self.since_change += 1
        return chosen

    def receipt(self):
        return {'mode': 'adaptive'}


class FixedTierPlan(TierPolicy):
    """Per-record tiers fixed before the first submission: the same input gets the same requests.

    The plan reads the record count (projection_records when larger) and priors
    only: PRIOR_TOKENS/PRIOR_PRESENCE for each tier's work per record, PLAN_PRIORS
    for the engine. It is the richest mix of two adjacent tiers whose planned work
    meets TierPolicy's budget rule, work <= time left x (1 - margin_fraction), with
    time counted from the planned engine-ready time; the cheaper tier's records
    are spread evenly by input index. Nothing measured moves a tier while the run
    keeps to the plan's schedule. Guard: when a submission starts later than its
    planned start by more than max(guard_min_slack_seconds, guard_reserve_fraction
    x the planned reserve), TierPolicy decides that record and every later one.
    """

    def __init__(self, options, stats, model, *, total_records, clock, submit_deadline, started_at):
        super().__init__(options, stats, model, total_records=total_records, clock=clock,
                         submit_deadline=submit_deadline)
        self.records, self.started_at = total_records, started_at
        prior = ProfileStats()
        self.cost = {t: prior.seconds_per_record(t, options.plan_prefill_seconds_per_token,
                                                 options.plan_decode_seconds_per_token)
                     for t in range(options.tier_ceiling, options.tier_floor + 1)}
        # TierPolicy's first-decision view from options alone (prior latency reserve), so no clock enters.
        self.time_left = (options.total_runtime_seconds - options.margin_seconds - options.plan_engine_ready_seconds
                          - max(0., self.expected_latency() - options.abort_grace_seconds))
        budget, n = self.time_left * (1 - options.margin_fraction), self.total
        self.rich = self.cheap = options.tier_floor
        rich_count = n
        for tier in range(options.tier_ceiling, options.tier_floor + 1):
            if n * self.cost[tier] <= budget:
                if tier == options.tier_ceiling:
                    self.rich = self.cheap = tier
                else:
                    self.rich, self.cheap = tier - 1, tier
                    rich_count = int((budget - n * self.cost[tier]) // (self.cost[tier - 1] - self.cost[tier]))
                break
        cheap_count = n - rich_count
        # Bresenham spreading: record i is cheap where the running cheap quota crosses an integer.
        self.tiers = [self.cheap if (i + 1) * cheap_count // n > i * cheap_count // n else self.rich
                      for i in range(n)]
        self.starts, elapsed = [], options.plan_engine_ready_seconds
        for tier in self.tiers:
            self.starts.append(elapsed)      # planned start of each record, seconds after started_at
            elapsed += self.cost[tier]
        self.work = elapsed - options.plan_engine_ready_seconds
        self.reserve = self.time_left - self.work
        self.slack = max(options.guard_min_slack_seconds, options.guard_reserve_fraction * self.reserve)
        self.max_lag = self.switch_index = self.switch_elapsed = self.switch_lag = None
        if self.tiers:
            self.current = self.tiers[0]

    def decide(self, submitted):
        if self.switch_index is None:
            elapsed = self.clock() - self.started_at
            lag = elapsed - self.starts[submitted]
            self.max_lag = lag if self.max_lag is None else max(self.max_lag, lag)
            if lag <= self.slack:
                self.current = self.tiers[submitted]
                return self.current
            self.switch_index, self.switch_elapsed, self.switch_lag = submitted, round(elapsed, 1), round(lag, 1)
            self.since_change = 10 ** 9     # TierPolicy starts as at run start, from the tier last planned
        return super().decide(submitted)

    def receipt(self):
        return {'mode': 'fixed', 'records': self.records, 'plan_records': self.total,
                'planned_counts': dict(collections.Counter(TIERS[t]['name'] for t in self.tiers[:self.records])),
                'plan_sha256': hashlib.sha256(bytes(self.tiers)).hexdigest(),
                'seconds_per_record': {TIERS[t]['name']: round(c, 4) for t, c in self.cost.items()},
                'priors': {'prefill_seconds_per_token': self.options.plan_prefill_seconds_per_token,
                           'decode_seconds_per_token': self.options.plan_decode_seconds_per_token,
                           'engine_ready_seconds': self.options.plan_engine_ready_seconds},
                'time_left_seconds': round(self.time_left, 1), 'planned_work_seconds': round(self.work, 1),
                'reserve_seconds': round(self.reserve, 1), 'guard_slack_seconds': round(self.slack, 1),
                'max_lag_seconds': None if self.max_lag is None else round(self.max_lag, 1),
                'guard_triggered': self.switch_index is not None, 'switch_record_index': self.switch_index,
                'switch_elapsed': self.switch_elapsed, 'switch_lag_seconds': self.switch_lag}


def journaling():
    """Notice-text journals are development evidence only; the official run leaves the CSV and aggregates."""
    return os.environ.get('PPS_STREAM_JOURNAL') == '1'


class Appender:
    """Rotating gzip JSONL writer; each row is flushed so a crash keeps evidence. Writes only when journaling()."""

    def __init__(self, root, prefix, rotate=200):
        self.root, self.prefix, self.rotate = Path(root), prefix, rotate
        self.stream, self.files, self.rows = None, [], 0
        self.enabled = journaling()

    def write(self, row):
        if not self.enabled:
            return
        if self.stream is None or self.rows >= self.rotate:
            self._open()
        self.stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        self.stream.flush()
        self.rows += 1

    def _open(self):
        self.close()
        path = self.root / f'{self.prefix}_{len(self.files):05d}.jsonl.gz'
        self.stream = gzip.open(path, 'wt', encoding='utf-8')
        self.files.append(path.name)
        self.rows = 0

    def close(self):
        if self.stream is not None:
            self.stream.close()
            self.stream = None


class RecordState:
    def __init__(self, index, record):
        self.index, self.record = index, record
        self.packets = {}          # profile -> packet
        self.fallback_rows = {}    # profile -> (row, details)
        self.code_only = {}
        self.tier = None
        self.planned = []          # profiles planned for the engine
        self.rows = {}             # profile -> row or None
        self.sources = {}          # profile -> how the row was produced
        self.pending = {}          # request_id -> (profile, attempt, packet, submitted_time)
        self.s9_decided = False
        self.submitted_at = None
        self.prepared = False
        self.rules_requested = False
        self.finalized = False
        self.model_calls = 0
        self.error = None
        self.final = None
        self.b3 = None
        self.details = {}
        self.focused_decided = False
        self.focused_preparing = False
        self.focused_proofs = {}
        self.a10_premise = None    # source-only premise score, prepared when config a10_attach is on


class StreamingExecutor:
    def __init__(self, records, pipeline, runner, pool, journal, options, *, started_at, clock=time.monotonic,
                 source_code=None):
        self.records = list(records)
        self.pipeline, self.runner, self.pool, self.journal = pipeline, runner, pool, journal
        self.options = options
        self.clock = clock
        self.started_at = started_at
        self.source_code = source_manifest() if source_code is None else source_code
        self.deadline = started_at + options.total_runtime_seconds
        self.submit_deadline = self.deadline - options.margin_seconds
        self.abort_deadline = self.submit_deadline + options.abort_grace_seconds
        self.stats = ProfileStats()
        self.model = CostModel(options.prior_prefill_seconds_per_token, options.prior_decode_seconds_per_token)
        if options.tier_plan == 'fixed':
            self.policy = FixedTierPlan(options, self.stats, self.model, total_records=len(self.records), clock=clock,
                                        submit_deadline=self.submit_deadline, started_at=started_at)
        else:
            self.policy = TierPolicy(options, self.stats, self.model, total_records=len(self.records), clock=clock,
                                     submit_deadline=self.submit_deadline)
        self.states = [RecordState(i, r) for i, r in enumerate(self.records)]
        self.ready = {}
        self.prep_cursor = self.submit_cursor = self.rules_cursor = 0
        self.finalized = 0
        self.inflight = {}          # request_id -> state
        self.native_out = Appender(journal.root, 'stream_native')
        self.consumed_out = Appender(journal.root, 'stream_consumed')
        self.packets_out = Appender(journal.root, 'stream_packets', rotate=64)
        self.records_out = Appender(journal.root, 'stream_records')
        self.counts = collections.Counter()
        self.tier_counts = collections.Counter()
        self.packet_hash = hashlib.sha256()
        self.consume_ids = 0
        self.consume_tasks = {}     # task id -> (state, profile, attempt, packet, response)
        self.last_progress = clock()
        self.aborted = False
        self.submissions_closed = False
        self.busy_since_tick = False
        self.engine_ready_at = None
        self.first_submit_at = None
        self.focused_tasks = set()
        self.focused_reserved_seconds = 0.
        self.focused_budget_seconds = (len(self.records) * getattr(pipeline.config, 'focused_verify_seconds_per_record', .1619)
                                       if getattr(pipeline.config, 'focused_verify', False) else 0.)
        # Loaded here, before the engine: corpus_seen_filter without its index file fails at once.
        self.corpus_index = corpus_lines.load_for(pipeline)
        self.corpus_filter_items = tuple(getattr(pipeline.config, 'corpus_seen_filter_items', ()) or ())
        self.source_rule_items = tuple(getattr(pipeline.config, 'source_rule_items', ()) or ())
        self.precision_gates = tuple(getattr(pipeline.config, 'precision_gates', ()) or ())
        self.a10_attach = bool(getattr(pipeline.config, 'a10_attach', False))
        self.cache_totals = {}      # profile -> exact prompt and cached token totals
        self.fixed_prefix_ready = {}    # fixed prefix sha256 -> when its first request finished

    # ---- preparation -----------------------------------------------------
    def prefeed(self):
        """Queue the first records for preparation before the engine exists."""
        self._feed_pool()

    def _feed_pool(self):
        capacity = self.options.prep_lookahead_records
        while (self.prep_cursor < len(self.states) and self.prep_cursor - self.submit_cursor < capacity
               and self.pool.outstanding < max(4, capacity)):
            state = self.states[self.prep_cursor]
            self.pool.submit({'kind': 'prepare', 'id': state.index, 'record': state.record})
            self.prep_cursor += 1

    def _feed_rules(self):
        """After submissions close, unprepared records still get source-only rows."""
        while self.rules_cursor < len(self.states) and self.pool.outstanding < 64:
            state = self.states[self.rules_cursor]
            self.rules_cursor += 1
            if state.prepared or state.finalized or state.rules_requested:
                continue
            state.rules_requested = True
            from .prep import PROFILE_ITEMS
            for profile, items in PROFILE_ITEMS.items():
                self.pool.submit({'kind': 'rules', 'id': [state.index, profile], 'record': state.record,
                                  'items': list(items)})

    def _absorb(self, result):
        kind = result.get('kind')
        if kind == 'prepare':
            state = self.states[result['id']]
            if result.get('error'):
                state.error = result['error']
                self.journal.save(f'prepare_failure_{state.index:05d}.json',
                                  {'record_id': state.record['id'], 'error': result['error'],
                                   'traceback': result.get('traceback')})
            else:
                for packet in result['packets']:
                    state.packets[packet['batch']] = packet
                    self.packet_hash.update(packet['token_ids_sha256'].encode())
                    self.packets_out.write(packet)
                state.fallback_rows = result['fallback_rows']
                state.code_only = result.get('code_only', {})
                state.a10_premise = result.get('a10_premise')
                for profile in ('Q10', 'W20'):
                    self.stats.observe_presence(profile, profile in state.packets)
            state.prepared = True
            self.ready[state.index] = state
        elif kind == 'rules':
            index, profile = result['id']
            state = self.states[index]
            if not result.get('error') and not state.finalized:
                state.fallback_rows[profile] = (result['row'], result['details'])
        elif kind == 'consume':
            entry = self.consume_tasks.pop(result['id'])
            self._consumed(entry, result)
        elif kind == 'focused_prepare':
            state = self.states[result['id']]
            self.focused_tasks.discard(state.index)
            state.focused_preparing = False
            if state.finalized:
                return
            if result.get('error'):
                state.sources['FV'] = 'preparation_failed: ' + result['error']
            else:
                self._submit_focused(state, result['packets'])
            self._maybe_finalize(state)
        else:
            raise RuntimeError('Unexpected preparation result kind: ' + str(kind))

    # ---- submission ------------------------------------------------------
    def _submit_ready(self):
        while (self.submit_cursor in self.ready and len(self.inflight) < self.options.inflight_requests
               and self.clock() < self.policy.effective_submit_deadline()):
            state = self.ready.pop(self.submit_cursor)
            self.submit_cursor += 1
            self._submit_record(state)

    def _submit_record(self, state):
        state.submitted_at = self.clock()
        if self.first_submit_at is None:
            self.first_submit_at = state.submitted_at
        if state.error or 'A1' not in state.packets:
            state.tier = FALLBACK_TIER
            state.sources['_record'] = 'preparation_failed' if state.error else 'missing_primary_packet'
            self._finalize(state)
            return
        tier = self.policy.decide(state.index)
        if tier == A10_BASE_TIER and self.a10_attach and 'A10' in state.packets:
            self.counts['a10_attach_decisions'] += 1
            if self._attach_a10(state.index):
                tier -= 1
                self.counts['a10_attached'] += 1
                self.counts['a10_attached_premise_zero'] += state.a10_premise == 0
        state.tier = TIERS[tier]['name']
        profiles = TIERS[tier]['profiles']
        state.planned = []
        for p in SUBMIT_ORDER:
            if p == 'A10' and p in profiles:
                # Budget profiles (config a10_budget_profiles) stand in for the one A10 packet.
                state.planned += sorted((k for k in state.packets if k.startswith('A10_t')), key=lambda k: int(k[5:]))
            if p in profiles and p in state.packets:
                state.planned.append(p)
        if 'S9' in profiles and 'S9' in state.packets:
            state.planned.append('S9')
        for profile in state.planned:
            if profile == 'S9':
                continue  # gated on A1's consumed row
            packet = state.packets[profile]
            if profile == 'A10' and packet['request_key'] in state.code_only:
                decision = state.code_only[packet['request_key']]
                state.rows[profile] = decision['row']
                state.sources[profile] = 'source_fixed_code_only'
                self.counts['code_only_skips'] += 1
                self.consumed_out.write({'request_key': packet['request_key'], 'record_id': state.record['id'],
                                         'attempt': None, 'row': decision['row'], 'details': [
                                             {'source': 'frozen_execution_skip', 'decision': decision['decision']}]})
                continue
            self._submit_request(state, profile, packet, 0)
        self._maybe_finalize(state)

    def _attach_a10(self, index):
        """A10 for this record when every remaining record at the base tier plus this record's A10 fits
        the ladder's ascent rule, work <= time left x (1 - reserve), reserve = margin_fraction +
        upgrade_margin_fraction: attaching A10 lifts this one record a tier. The reserve is a share of the
        time left, so of the remaining planned work (review round 04). The ladder descends on a noisy
        per-record cost estimate at margin_fraction; the upgrade margin keeps the attachments out of that
        band (runs/rebuild_20260924/stage1/precheck/deadline_01). A record whose source-only A10 premise
        scores 0 needs twice the reserve (review round 05; precheck/premise_attach_01.json).
        Only well-measured engine costs attach."""
        view = self.policy.projection(index)
        if not view['ready'] or view['windows'] < A10_ATTACH_MIN_WINDOWS or A10_BASE_TIER not in view['need']:
            return False
        a, b = view['a'], view['b']
        extra = (self.stats.seconds_per_record(A10_BASE_TIER - 1, a, b)
                 - self.stats.seconds_per_record(A10_BASE_TIER, a, b))
        reserve = self.options.margin_fraction + self.options.upgrade_margin_fraction
        share = 1 - reserve * (2 if self.states[index].a10_premise == 0 else 1)
        return view['need'][A10_BASE_TIER] + extra <= view['time_left'] * share

    def _observe_cache(self, profile, packet, prompt_tokens, cached, submitted):
        """Exact cache totals per profile. A request reading less than its fixed prefix from the cache
        recomputed it, unless no request with that prefix had finished when it was submitted (cold)."""
        entry = self.cache_totals.setdefault(profile, {'requests': 0, 'prompt_tokens': 0, 'cached_tokens': 0})
        entry['requests'] += 1
        entry['prompt_tokens'] += prompt_tokens
        entry['cached_tokens'] += cached
        fixed, key = packet.get('fixed_prefix_tokens'), packet.get('fixed_prefix_sha256')
        if not fixed or not key:
            return
        ready = self.fixed_prefix_ready.get(key)
        if cached < fixed - FIXED_PREFIX_TOLERANCE:
            self.counts['fixed_prefix_recomputed' if ready is not None and submitted > ready
                        else 'fixed_prefix_cold'] += 1
        if ready is None:
            self.fixed_prefix_ready[key] = self.clock()

    def _preemptions(self):
        label, counts = getattr(self.runner, 'preemption_counter', None), getattr(self.runner, 'preemptions', None)
        if counts is None:
            return None if label is None else {'counter': label, 'total': None}
        return {'counter': label, 'total': sum(counts.values()), 'requests': len(counts),
                'request_ids': sorted(counts)[:100]}

    def _submit_request(self, state, profile, packet, attempt):
        request_id = f"{packet['request_key']}#{attempt}"
        self.runner.submit(request_id, packet)
        state.pending[request_id] = (profile, attempt, packet, self.clock())
        self.inflight[request_id] = state
        self.counts['requests_submitted'] += 1
        if attempt:
            self.counts['parse_retries'] += 1

    # ---- engine outputs --------------------------------------------------
    def _finish(self, output):
        state = self.inflight.pop(output.request_id, None)
        if state is None:
            return
        profile, attempt, packet, submitted = state.pending.pop(output.request_id)
        now = self.clock()
        candidates = list(getattr(output, 'outputs', None) or [])
        generated = candidates[0] if candidates else None
        prompt_ids = getattr(output, 'prompt_token_ids', None)
        native = {'request_id': output.request_id, 'raw_text': generated.text if generated else None,
                  'output_token_ids': list(generated.token_ids) if generated else [],
                  'finish_reason': generated.finish_reason if generated else None,
                  'stop_reason': getattr(generated, 'stop_reason', None),
                  'cached_input_tokens': getattr(output, 'num_cached_tokens', None),
                  'prompt_tokens_match': None if prompt_ids is None else list(prompt_ids) == list(packet['token_ids']),
                  'num_outputs': len(candidates)}
        self.native_out.write({'request_key': packet['request_key'], 'record_id': state.record['id'],
                               'profile': profile, 'attempt': attempt,
                               'prompt_sha256': packet['prompt_sha256'], 'token_ids_sha256': packet['token_ids_sha256'],
                               'submitted_at': submitted - self.started_at, 'finished_at': now - self.started_at,
                               'native': native})
        self.counts['responses'] += 1
        state.model_calls += 1
        if native['prompt_tokens_match'] is False:
            self._resolve_failure(state, profile, attempt, packet, 'native prompt tokens differ from the packet')
            return
        try:
            response = self.runner.response_from_native(output, packet)
        except Exception as exc:
            self._resolve_failure(state, profile, attempt, packet, 'native parse: ' + type(exc).__name__ + ': ' + str(exc))
            return
        prompt_tokens = len(packet['token_ids'])
        cached = min(prompt_tokens, native['cached_input_tokens'] or 0)
        new_prefill = prompt_tokens - cached
        decode = response.get('output_tokens', 0)
        self._observe_cache(profile, packet, prompt_tokens, cached, submitted)
        self.stats.observe_request('FV' if profile.startswith('FV:') else profile,
                                   new_prefill, decode, cached / prompt_tokens if prompt_tokens else None)
        self.model.observe(new_prefill, decode)
        if response.get('generation_stall'):
            self._resolve_failure(state, profile, attempt, packet, 'JSON generation stalled', response=response)
            return
        task_id = self.consume_ids
        self.consume_ids += 1
        self.consume_tasks[task_id] = (state, profile, attempt, packet, response)
        self.pool.submit({'kind': 'consume', 'id': task_id, 'record': state.record, 'packet': packet,
                          'response': response})

    def _consumed(self, entry, result):
        state, profile, attempt, packet, response = entry
        if state.finalized:
            return
        if result.get('error'):
            self._resolve_failure(state, profile, attempt, packet, 'worker: ' + result['error'], response=response)
            return
        if result.get('parse_error'):
            self._resolve_failure(state, profile, attempt, packet, result['parse_error'], response=response,
                                  retryable=True)
            return
        if result.get('cpu_error'):
            self._resolve_failure(state, profile, attempt, packet, result['cpu_error'], response=response)
            return
        state.rows[profile] = result['row']
        if getattr(self.pipeline.config, 'focused_verify', False):
            state.details[profile] = result['details']
            if profile.startswith('FV:'):
                state.focused_proofs[profile] = result['details']
        state.sources[profile] = 'model_response' if attempt == 0 else f'format_recovery_attempt_{attempt}'
        self.consumed_out.write({'request_key': packet['request_key'], 'record_id': state.record['id'],
                                 'attempt': attempt, 'row': result['row'], 'details': result['details'],
                                 'response': response})
        if profile == 'A1':
            self._gate_s9(state)
        self._maybe_finalize(state)

    def _resolve_failure(self, state, profile, attempt, packet, error, *, response=None, retryable=False):
        retries = getattr(self.pipeline.config, 'max_response_retries', 0)
        if profile.startswith('FV:'):
            retries = 0  # Optional calls cannot spend the envelope on format recovery.
        self.consumed_out.write({'request_key': packet['request_key'], 'record_id': state.record['id'],
                                 'attempt': attempt, 'row': None, 'details': None, 'error': error,
                                 'retryable': retryable, 'response': response})
        if retryable and attempt < retries and not self.aborted and self.clock() < self.submit_deadline:
            self._submit_request(state, profile, format_recovery_packet(packet, attempt + 1), attempt + 1)
            return
        self.counts['unresolved_requests'] += 1
        self._fallback(state, profile, 'unresolved: ' + error)
        if profile == 'A1':
            self._gate_s9(state)
        self._maybe_finalize(state)

    def _fallback(self, state, profile, reason):
        if profile in A_PROFILES:
            state.rows[profile] = state.fallback_rows[profile][0] if profile in state.fallback_rows else None
            state.sources[profile] = 'source_rules_fallback: ' + reason
            self.counts['source_rule_fallbacks'] += 1
        else:
            state.rows[profile] = None
            state.sources[profile] = 'dropped: ' + reason

    def _gate_s9(self, state):
        if state.s9_decided or 'S9' not in state.planned:
            return
        state.s9_decided = True
        packet = state.packets['S9']
        a1 = state.rows.get('A1')
        value = a1.get('v9') if a1 is not None else None
        count = len(packet['specification_inventory']['candidates'])
        needed = value == 1 or bool(count)
        self.stats.observe_presence('S9', needed)
        if not needed:
            state.rows['S9'] = {}
            state.sources['S9'] = 'gate_skip'
            self.counts['s9_gate_skips'] += 1
        elif not self.aborted and self.clock() < self.submit_deadline:
            self._submit_request(state, 'S9', packet, 0)
        else:
            self._fallback(state, 'S9', 'submissions closed')

    # ---- finalization ----------------------------------------------------
    def _gate_focused(self, state):
        state.focused_decided = True
        if (not getattr(self.pipeline.config, 'focused_verify', False) or self.submissions_closed or self.aborted
                or state.tier != TIERS[self.options.tier_ceiling]['name']
                or self.policy.current != self.options.tier_ceiling
                or self.focused_budget_seconds - self.focused_reserved_seconds < 1024 * .35e-3
                or 'A19' not in state.planned or self.clock() >= self.policy.effective_submit_deadline()):
            return
        state.focused_preparing = True
        self.focused_tasks.add(state.index)
        details = [state.fallback_rows[p][1] for p in A_PROFILES if p in state.fallback_rows]
        details.extend(state.details[p] for p in ('A1', 'A10', 'A19', 'Q10') if p in state.details)
        self.pool.submit({'kind': 'focused_prepare', 'id': state.index, 'record': state.record,
                          'baseline': self._assemble(state, count=False), 'details': details})

    def _submit_focused(self, state, packets):
        """Reserve worst-case decode tokens; drop FV before any existing tier."""
        for packet in packets:
            view = self.policy.projection(self.submit_cursor)
            a, b = max(150e-6, view['a']), max(.35e-3, view['b'])
            cost = a * len(packet['token_ids']) + b * packet['generation']['max_output_tokens']
            base_need = view['need'][self.options.tier_ceiling]
            affordable = (base_need + cost <= view['time_left'] * (1-self.options.margin_fraction))
            if (self.submissions_closed or self.aborted or self.policy.current != self.options.tier_ceiling
                    or self.clock() >= self.policy.effective_submit_deadline() or not affordable
                    or self.focused_reserved_seconds + cost > self.focused_budget_seconds):
                self.counts['focused_budget_drops'] += 1
                continue
            self.focused_reserved_seconds += cost
            profile = packet['batch']
            state.packets[profile] = packet
            state.planned.append(profile)
            self.packet_hash.update(packet['token_ids_sha256'].encode())
            self.packets_out.write(packet)
            self._submit_request(state, profile, packet, 0)
            self.counts['focused_requests'] += 1

    def _maybe_finalize(self, state):
        if state.finalized or state.pending:
            return
        if 'S9' in state.planned and not state.s9_decided:
            return
        if any(p not in state.rows for p in state.planned):
            return
        if not state.focused_decided:
            self._gate_focused(state)
        if state.focused_preparing:
            return
        self._finalize(state)

    def _finalize(self, state):
        state.finalized = True
        self.finalized += 1
        if state.tier is None:
            state.tier = FALLBACK_TIER
        self.tier_counts[state.tier] += 1
        row = self._assemble(state)
        state.final = row
        now = self.clock()
        if state.submitted_at is not None and state.model_calls:
            self.policy.observe_latency(now - state.submitted_at)
        self.records_out.write({'index': state.index, 'id': state.record['id'], 'tier': state.tier,
                                'planned': state.planned, 'sources': state.sources, 'model_calls': state.model_calls,
                                'seconds': None if state.submitted_at is None else round(now - state.submitted_at, 3),
                                'row': row})
        if state.model_calls == 0:
            self.counts['records_without_model_call'] += 1
        state.packets = {}
        state.fallback_rows = {}
        state.pending = {}
        self._progress()

    def _assemble(self, state, *, count=True):
        result = {'id': state.record['id'], **{f'v{k}': None for k in range(1, 25)}, **{f'e{k}': '' for k in range(1, 25)}}
        for profile in A_PROFILES:
            row = state.rows.get(profile)
            if row is None:
                row = state.fallback_rows[profile][0] if profile in state.fallback_rows else None
                if row is not None:
                    state.sources.setdefault(profile, 'source_rules_not_planned')
                    if count:
                        self.counts['source_rule_rows'] += 1
            if row is not None:
                result.update(row)
        for profile in ('Q10', 'S9'):
            row = state.rows.get(profile)
            if row:
                result.update(row)
        b3 = dict(result)
        for profile in ('L19', 'W20'):
            row = state.rows.get(profile)
            if row:
                result.update({k: row[k] for k in ('v20', 'e20') if k in row})
        # Source-only judgment for the configured items: there the model verdict adds
        # firing on untouched notices, not injected edits.
        for k in getattr(self, 'source_rule_items', ()):
            source = state.fallback_rows.get(RULE_PROFILE[k])
            if source is not None:
                result[f'v{k}'], result[f'e{k}'] = source[0][f'v{k}'], source[0][f'e{k}']
        # Resolve all focused answers together: conflicting routes for the same
        # cited clause abstain, and existing positives/evidence are preserved.
        clauses = collections.defaultdict(set)
        for proof in state.focused_proofs.values():
            if proof.get('reason') == 'accepted':
                s = proof['citation']
                clauses[(proof['family'], s['doc_index'], s['start'], s['end'])].add(proof['item'])
        for proof in state.focused_proofs.values():
            if proof.get('reason') != 'accepted':
                continue
            s, item = proof['citation'], proof['item']
            key = (proof['family'], s['doc_index'], s['start'], s['end'])
            if len(clauses[key]) == 1 and not result[f'v{item}']:
                result[f'v{item}'], result[f'e{item}'] = 1, proof['evidence']
        filled = [k for k in range(1, 25) if result[f'v{k}'] is None]
        for k in filled:
            result[f'v{k}'], result[f'e{k}'] = 0, ''
        if filled:
            state.sources['_filled_zero_items'] = filled
            if count:
                self.counts['filled_zero_cells'] += len(filled)
        index = getattr(self, 'corpus_index', None)
        if index is not None:
            dropped = corpus_lines.apply(state.record, result, index, getattr(self, 'corpus_filter_items', ()))
            if dropped:
                state.sources['_corpus_seen_dropped'] = dropped
        gates = getattr(self, 'precision_gates', ())
        if gates:
            cleared = precision_gates.apply(state.record, result, gates)
            if cleared:
                state.sources['_precision_gates'] = cleared
        state.b3 = b3
        return result

    # ---- deadline --------------------------------------------------------
    def _close_submissions(self):
        if self.submissions_closed:
            return
        self.submissions_closed = True
        self.counts['records_after_submission_deadline'] = len(self.states) - self.submit_cursor
        self.rules_cursor = self.submit_cursor
        for index in range(self.submit_cursor, len(self.states)):
            state = self.states[index]
            state.tier = FALLBACK_TIER
            state.sources['_record'] = 'submissions_closed_before_record'
        self.journal.save('submission_deadline.json', {'elapsed': self.clock() - self.started_at,
            'submitted_records': self.submit_cursor, 'prepared_records': self.prep_cursor,
            'inflight_requests': len(self.inflight)})

    def _finalize_idle(self, force=False):
        for index in range(self.submit_cursor, len(self.states)):
            state = self.states[index]
            if state.finalized:
                continue
            ready = state.prepared or all(p in state.fallback_rows for p in A_PROFILES)
            if ready or force:
                self._finalize(state)

    def _abort_inflight(self):
        if self.aborted:
            return
        self.aborted = True
        ids = list(self.inflight)
        if ids:
            try:
                self.runner.abort(ids)
            except Exception as exc:
                self.journal.save('abort_failure.json', {'error': repr(exc)})
        for request_id in ids:
            state = self.inflight.pop(request_id)
            profile, attempt, packet, _ = state.pending.pop(request_id)
            self._fallback(state, profile, 'aborted at deadline')
        self.counts['aborted_requests'] = len(ids)
        self.consume_tasks.clear()
        for state in self.states:
            if not state.finalized:
                for profile in state.planned:
                    if profile not in state.rows:
                        self._fallback(state, profile, 'unfinished at deadline')
                self._finalize(state)

    def _progress(self, force=False):
        now = self.clock()
        if not force and now - self.last_progress < 10 and self.finalized % self.options.progress_every_records:
            return
        self.last_progress = now
        a, b, windows = self.model.parameters()
        self.journal.progress(phase='stream', finalized=self.finalized, submitted=self.submit_cursor,
                              prepared=self.prep_cursor, inflight=len(self.inflight),
                              tier=TIERS[self.policy.current]['name'],
                              prefill_us_per_token=round(a * 1e6, 1), decode_ms_per_token=round(b * 1e3, 3),
                              fitted_windows=windows, measured=self.model.ready(self.options),
                              elapsed=round(now - self.started_at, 1),
                              seconds_to_submit_deadline=round(self.submit_deadline - now, 1))

    # ---- main loop -------------------------------------------------------
    def run(self):
        journal = self.journal
        journal.save('stream_options.json', dataclasses.asdict(self.options))
        journal.progress(phase='stream_start', records=len(self.states))
        try:
            while self.finalized < len(self.states):
                now = self.clock()
                self.model.tick(now, self.busy_since_tick)
                if now >= self.submit_deadline:
                    self._close_submissions()
                if self.submissions_closed:
                    self._feed_rules()
                else:
                    self._feed_pool()
                busy = bool(self.inflight)
                for result in self.pool.poll(timeout=0. if busy else .02):
                    self._absorb(result)
                if not self.submissions_closed:
                    self._submit_ready()
                finished = []
                if self.inflight:
                    try:
                        finished = self.runner.step()
                    except Exception:
                        journal.save('engine_failure.json', {'traceback': traceback.format_exc(),
                                                             'inflight': len(self.inflight)})
                        raise
                    for output in finished:
                        self._finish(output)
                # Engine-busy time for the cost model: something ran in this iteration.
                self.busy_since_tick = bool(self.inflight) or bool(finished)
                if self.submissions_closed:
                    self._finalize_idle()
                if now >= self.abort_deadline and not self.aborted:
                    self._abort_inflight()
                elif (self.submit_cursor >= len(self.states) and not self.inflight and not self.consume_tasks and not self.focused_tasks
                      and self.finalized < len(self.states)):
                    # Nothing can arrive any more: complete every record from what it has.
                    for state in self.states:
                        if not state.finalized:
                            for profile in state.planned:
                                if profile not in state.rows:
                                    self._fallback(state, profile, 'no pending work')
                            self._finalize(state)
            self._progress(force=True)
            return self._complete()
        except BaseException as exc:
            journal.save('failure.json', {'type': type(exc).__name__, 'message': str(exc),
                                          'traceback': traceback.format_exc(), 'finalized': self.finalized,
                                          'records': len(self.states)})
            self._emergency_csv()
            raise
        finally:
            for appender in (self.native_out, self.consumed_out, self.packets_out, self.records_out):
                appender.close()

    def _emergency_csv(self):
        """A complete CSV from whatever finished plus source-only rows, once the model has answered."""
        try:
            for state in self.states:
                if not state.finalized:
                    state.tier = FALLBACK_TIER
                    state.sources['_record'] = 'emergency_completion'
                    for profile in state.planned:
                        if profile not in state.rows:
                            self._fallback(state, profile, 'emergency completion')
                    state.finalized = True
                    state.final = self._assemble(state)
            self._write_csv(emergency=True)
        except BaseException:
            self.journal.save('emergency_csv_failure.json', {'traceback': traceback.format_exc()})

    def _write_csv(self, *, emergency=False):
        if not self.counts['responses']:
            # The competition requires at least one answered LLM call; a rules-only file is never submitted.
            raise RuntimeError('No LLM response arrived; refusing a rules-only submission')
        rows = [self.states[i].final for i in range(len(self.states))]
        config = self.pipeline.config
        path = self.journal.root / 'submission.csv'
        try:
            write_csv(path, rows, recs=self.records, require_positive_evidence=config.require_positive_evidence)
        except ValueError as exc:
            # Labels are already 0/1; the only repairable contract failure is a
            # quote that is not an exact source substring. Drop such quotes,
            # never the file.
            self.journal.save('csv_repair.json', {'error': str(exc), 'emergency': emergency})
            for row, rec in zip(rows, self.records):
                for k in range(1, 25):
                    quote = row[f'e{k}']
                    if quote and (not row[f'v{k}'] or k in ABSENCE or not any(quote in d['text'] for d in rec['docs'])):
                        row[f'e{k}'] = ''
            write_csv(path, rows, recs=self.records, require_positive_evidence=False)
        return path

    def _complete(self):
        self._write_csv()
        b3_rows = [state.b3 for state in self.states]
        if journaling() and all(r is not None and all(r[f'v{k}'] is not None for k in range(1, 25)) for r in b3_rows):
            try:
                write_csv(self.journal.root / 'B3.csv', b3_rows, recs=self.records,
                          require_positive_evidence=self.pipeline.config.require_positive_evidence)
            except ValueError as exc:
                self.journal.save('b3_csv_skipped.json', {'error': str(exc)})
        now = self.clock()
        a, b, _ = self.model.parameters()
        report = {'records': len(self.states), 'executor': 'stream',
                  'requests_submitted': self.counts['requests_submitted'], 'responses': self.counts['responses'],
                  'parse_retries': self.counts['parse_retries'], 'unresolved_requests': self.counts['unresolved_requests'],
                  'source_rule_fallbacks': self.counts['source_rule_fallbacks'],
                  'source_rule_rows_for_unplanned_profiles': self.counts['source_rule_rows'],
                  'filled_zero_cells': self.counts['filled_zero_cells'],
                  'records_without_model_call': self.counts['records_without_model_call'],
                  'records_after_submission_deadline': self.counts['records_after_submission_deadline'],
                  'aborted_requests': self.counts['aborted_requests'],
                  's9_gate_skips': self.counts['s9_gate_skips'], 'code_only_skips': self.counts['code_only_skips'],
                  'tier_counts': dict(self.tier_counts), 'tier_plan': self.policy.receipt(),
                  'tier_timeline': self.policy.timeline,
                  'a10_attach': {'enabled': self.a10_attach, 'decisions': self.counts['a10_attach_decisions'],
                                 'attached': self.counts['a10_attached'],
                                 'attached_premise_zero': self.counts['a10_attached_premise_zero'],
                                 'reserve_fraction_of_time_left': round(self.options.margin_fraction
                                                                        + self.options.upgrade_margin_fraction, 3)},
                  'prefix_cache': {'by_profile': {p: {**v, 'cached_fraction': round(v['cached_tokens'] / v['prompt_tokens'], 4)
                                                      if v['prompt_tokens'] else None}
                                                  for p, v in sorted(self.cache_totals.items())},
                                   'fixed_prefix_variants': len(self.fixed_prefix_ready),
                                   'fixed_prefix_recomputed': self.counts['fixed_prefix_recomputed'],
                                   'fixed_prefix_cold': self.counts['fixed_prefix_cold']},
                  'engine_preemptions': self._preemptions(),
                  'final_tier': TIERS[self.policy.current]['name'],
                  'measured_tier_decisions': self.policy.measured_decisions,
                  'cost_model': self.model.snapshot(),
                  'profile_stats': self.stats.snapshot(a, b),
                  'focused_verify': {'enabled': getattr(self.pipeline.config, 'focused_verify', False),
                                     'requests': self.counts['focused_requests'],
                                     'budget_drops': self.counts['focused_budget_drops'],
                                     'reserved_seconds': self.focused_reserved_seconds,
                                     'budget_seconds': self.focused_budget_seconds},
                  'expected_record_latency_seconds': self.policy.expected_latency(),
                  'seconds': now - self.started_at, 'engine_load_seconds': getattr(self.runner, 'load_seconds', None),
                  'timing': {'engine_ready_elapsed': None if self.engine_ready_at is None else round(self.engine_ready_at - self.started_at, 1),
                             'first_submit_elapsed': None if self.first_submit_at is None else round(self.first_submit_at - self.started_at, 1),
                             'generation_window_seconds': None if self.first_submit_at is None else round(now - self.first_submit_at, 1),
                             'engine_busy_seconds': round(self.model.busy_seconds, 1),
                             'engine_idle_seconds': None if self.first_submit_at is None else round(max(0., now - self.first_submit_at - self.model.busy_seconds), 1)},
                  'deadline': {'total_runtime_seconds': self.options.total_runtime_seconds,
                               'submit_deadline_elapsed': self.submit_deadline - self.started_at,
                               'abort_deadline_elapsed': self.abort_deadline - self.started_at,
                               'submissions_closed_early': self.submissions_closed, 'aborted': self.aborted},
                  'preparation': self.pool.receipt(live=True), 'packets_sha256': self.packet_hash.hexdigest(),
                  'journal_files': {'native': self.native_out.files, 'consumed': self.consumed_out.files,
                                    'packets': self.packets_out.files, 'records': self.records_out.files},
                  'missing_required_evidence': sum(len(missing_evidence_items(s.final)) for s in self.states),
                  'labels_read': False, 'official_score': None, 'l40s_time_verified': False,
                  'source_unchanged': source_manifest() == self.source_code}
        self.journal.save('run_report.json', report)
        self.journal.save('prediction_freeze.json', {'epoch': time.time(), 'labels_read': False,
            'files': {p.name: sha256(p) for p in sorted(self.journal.root.glob('*.csv'))}})
        self.journal.progress(phase='complete', finalized=self.finalized, records=len(self.states))
        return report


def execute_stream(input_path, data_dir, output_dir, *, tokenizer_dir, runner_factory, options,
                   source_options=None, limit=None, started_at=None, pool=None, records_override=None,
                   clock=time.monotonic, pipeline=None, engine_overrides=None, pre_engine=None):
    """Prepare in workers, load the engine once, stream every record, write the CSV once the model answered."""
    from .pps.data import records as read_records
    from .pps.input_contract import diagnostics, VERSION as INPUT_CONTRACT_VERSION
    journal = Journal(output_dir)
    started = clock() if started_at is None else started_at
    source_options = dict(source_options or {})
    code = source_manifest()
    runner = None
    try:
        journal.progress(phase='read_inputs')
        original_input_sha256 = sha256(input_path) if Path(input_path).is_file() else None
        recs = list(records_override) if records_override is not None else list(read_records(input_path, limit))
        if journaling():
            journal.save('input_contract.json', {'version': INPUT_CONTRACT_VERSION,
                'original_input_sha256': original_input_sha256, 'records': [diagnostics(r) for r in recs],
                'prediction_features_added': False})
        journal.save('input_freeze.json', {'epoch': time.time(), 'labels_read': False, 'source_sha256': code,
            'original_input_sha256': original_input_sha256, 'records': len(recs), 'executor': 'stream'})
        if pipeline is None:
            from transformers import AutoTokenizer
            from .b4_entry import B4Pipeline
            tokenizer = AutoTokenizer.from_pretrained(tokenizer_dir, local_files_only=True, trust_remote_code=False)
            pipeline = B4Pipeline(data_dir, tokenizer, **source_options)
        if engine_overrides:
            # Scheduler-only engine arguments; prompts, schemas and consumers are untouched.
            pipeline.config = dataclasses.replace(pipeline.config, **engine_overrides)
            journal.save('engine_overrides.json', dict(engine_overrides))
        journal.save('execution_config.json', serial(pipeline.config))
        if pool is None:
            from .prep import PreparationPool
            pool = PreparationPool(data_dir, tokenizer_dir, source_options)
        pool.start()
        executor = StreamingExecutor(recs, pipeline, None, pool, journal, options, started_at=started,
                                     clock=clock, source_code=code)
        # The workers prepare the first records while the engine loads, so the
        # first submission follows the load immediately.
        executor.prefeed()
        if pre_engine is not None:
            # GPU-side preparation that must finish before the engine owns the
            # device (kernel tuning); it may not raise and it changes no input.
            journal.progress(phase='pre_engine')
            pre_engine(journal)
        journal.progress(phase='engine_load', prepared_ahead=executor.prep_cursor)
        runner = runner_factory(pipeline.config, journal)
        if runner.config != pipeline.config:
            raise ValueError('The preserved consumer/engine configuration must be retained')
        executor.runner = runner
        executor.engine_ready_at = clock()
        journal.save('preparation_pool.json', pool.receipt())
        return executor.run()
    except BaseException as exc:
        if not (journal.root / 'failure.json').is_file():
            journal.save('failure.json', {'type': type(exc).__name__, 'message': str(exc),
                                          'traceback': traceback.format_exc()})
        raise
    finally:
        if pool is not None:
            try:
                pool.close()
            except Exception:
                pass
        if runner is not None:
            try:
                runner.close()
                journal.save('engine_shutdown.json', {'epoch': time.time(), 'status': 'shutdown_returned'})
            except Exception as exc:
                journal.save('engine_shutdown.json', {'epoch': time.time(), 'status': 'shutdown_error', 'error': str(exc)})
