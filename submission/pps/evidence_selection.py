"""Choose source contexts by their joint, budgeted reading value.

This is a retrieval objective, not an estimate of factual or legal certainty.
Only complete original lines receive credit. Overlapping index chunks never
multiply that credit; all headings and conditions still consume source tokens.
"""
from bisect import bisect_left, bisect_right
from decimal import Context, Decimal, ROUND_HALF_EVEN
from functools import lru_cache
import math


def _portable_log1p(value):
    """Platform-independent logarithm for the deterministic source objective.

    Native libm log1p differed by one ULP between Windows and Linux even with
    identical facet masses. Use explicitly rounded decimal arithmetic before
    converting to binary64. Preserve small positive masses during 1+x too.
    """
    number=Decimal.from_float(value)
    if not number.is_finite() or number<0:
        raise ValueError('Evidence mass must be finite and nonnegative')
    precision=40+max(0,-number.adjusted()) if number else 40
    context=Context(prec=precision,rounding=ROUND_HALF_EVEN)
    return float(context.ln(context.add(Decimal(1),number)))


class EvidenceObjective:
    def __init__(self, search, families, order):
        self.starts, self.ends, self.offsets, self.weights = [], [], [], []
        self.units = []
        for di, units in enumerate(search.units):
            self.offsets.append(len(self.units))
            self.starts.append([lo for lo, hi in units])
            self.ends.append([hi for lo, hi in units])
            self.units.extend((di, lo, hi) for lo, hi in units)
        self.support = [{} for _ in self.units]
        # Mathematical intermediate values live only within this notice's
        # objective. No document features or statistics cross notice calls.
        self._log1p=lru_cache(maxsize=8192)(_portable_log1p)
        allowed = set(order)
        for family in families:
            for ranking in family:
                facet = len(self.weights)
                self.weights.append(1. / (len(families) * max(1, len(family))))
                for rank, i in enumerate(ranking, 1):
                    if i not in allowed:
                        continue
                    s = search.chunks[i]
                    first = bisect_right(self.ends[s.doc_index], s.start)
                    last = bisect_left(self.starts[s.doc_index], s.end)
                    for j in range(first, last):
                        unit = self.offsets[s.doc_index] + j
                        _, lo, hi = self.units[unit]
                        # One rank-one chunk supplies at most one unit of mass
                        # across its original lines. Use max across overlaps.
                        value = (min(hi, s.end) - max(lo, s.start)) / ((s.end - s.start) * rank)
                        self.support[unit][facet] = max(self.support[unit].get(facet, 0.), value)

    def covered(self, ranges):
        mask = 0
        for di, lo, hi in ranges:
            first = bisect_left(self.starts[di], lo)
            last = bisect_right(self.ends[di], hi)
            if first < last:
                mask |= ((1 << last) - (1 << first)) << self.offsets[di]
        return mask

    def masses(self, mask):
        values = [[] for _ in self.weights]
        while mask:
            bit = mask & -mask
            for j, support in self.support[bit.bit_length() - 1].items():
                values[j].append(support)
            mask ^= bit
        return tuple(math.fsum(v) for v in values)

    def value(self, mask):
        # A second distinct passage for the same question remains valuable;
        # it has diminishing return, rather than a one-hit saturation rule.
        return math.fsum(w * self._log1p(v) for w, v in zip(self.weights, self.masses(mask)))


def pack_evidence(search, families, order, required, budget, expand, *, refill=False):
    from .notice_search import merge_ranges

    objective = EvidenceObjective(search, families, order)
    contexts, aliases = {}, {}
    for i in order:
        s = search.chunks[i]
        context = search._contexts[i] if expand else ((s.doc_index, s.start, s.end),)
        if context in aliases:
            aliases[context].append(i)
        else:
            contexts[i] = context
            aliases[context] = [i]
    candidates = tuple(sorted(contexts))
    evaluations = 0

    @lru_cache(maxsize=2048)
    def evaluate_ranges(ranges):
        nonlocal evaluations
        evaluations += 1
        mask = objective.covered(ranges)
        return ranges, search.token_cost(ranges), objective.value(mask), mask

    def evaluate(chosen):
        return evaluate_ranges(merge_ranges(
            [*required, *(r for i in chosen for r in contexts[i])], search.rec['docs']))

    trace = []

    def event(kind, before, after, **extra):
        return {'action': kind, 'source_tokens_before': before[1], 'source_tokens_after': after[1],
            'added_source_tokens': after[1] - before[1], 'utility_before': before[2], 'utility_after': after[2],
            'new_complete_source_units': (after[3] & ~before[3]).bit_count(),
            'removed_complete_source_units': (before[3] & ~after[3]).bit_count(), **extra}

    def fill(chosen, *, forbidden=(), evaluation_limit=None):
        chosen = tuple(sorted(chosen))
        current = evaluate(chosen)
        events, attempted = [], 0
        while True:
            remaining = [i for i in candidates if i not in chosen and i not in forbidden]
            # A step compares every remaining candidate. Do not let the work
            # limit bias it toward candidates with lower source indices.
            if evaluation_limit is not None and attempted + len(remaining) > evaluation_limit:
                return chosen, current, events, attempted, True
            best = None
            for i in remaining:
                proposal = evaluate(tuple(sorted((*chosen, i))))
                attempted += 1
                gain = proposal[2] - current[2]
                if proposal[1] > budget or gain <= 1e-12:
                    continue
                key = (gain / max(1, proposal[1] - current[1]), gain, -proposal[1], -i)
                if best is None or key > best[0]:
                    best = key, i, proposal
            if best is None:
                return chosen, current, events, attempted, False
            _, i, proposal = best
            events.append(event('add', current, proposal, candidate=i))
            chosen, current = tuple(sorted((*chosen, i))), proposal

    chosen, current, events, _, _ = fill(())
    trace.extend(events)
    # Guard against a density-only construction losing to one valuable bundle.
    singles = [(i, evaluate((i,))) for i in candidates]
    feasible = [(i, p) for i, p in singles if p[1] <= budget]
    if feasible:
        i, single = max(feasible, key=lambda p: (p[1][2], -p[1][1], -p[0]))
        if single[2] > current[2] + 1e-12:
            trace.append(event('restart_from_single_bundle', current, single, candidate=i))
            chosen, current, events, _, _ = fill((i,))
            trace.extend(events)

    # One bounded best exchange recomputes the full union, including shared
    # dependencies and mandatory witnesses. Never subtract stand-alone costs.
    best, exchange_evaluations = None, 0
    for removed in chosen:
        retained = tuple(i for i in chosen if i != removed)
        for added in candidates:
            if added in chosen:
                continue
            proposal = evaluate(tuple(sorted((*retained, added))))
            exchange_evaluations += 1
            gain = proposal[2] - current[2]
            better = gain > 1e-12 or (abs(gain) <= 1e-12 and proposal[1] < current[1])
            if proposal[1] <= budget and better:
                key = (proposal[2], -proposal[1], -added, -removed)
                if best is None or key > best[0]:
                    best = key, removed, added, proposal
    if best is not None:
        _, removed, added, proposal = best
        trace.append(event('exchange', current, proposal, removed_candidate=removed, added_candidate=added))
        chosen, current, events, _, _ = fill(tuple(i for i in chosen if i != removed) + (added,))
        trace.extend(events)

    # A valuable large context can beat every individual replacement while
    # losing to several complementary smaller contexts. Explore one removal
    # followed by a complete greedy refill, without re-adding that context.
    # The incumbent is always retained until a feasible improvement is found.
    # Work is bounded by a deterministic count, never wall time or model output.
    refill_trials, refill_evaluations, refill_limit = [], 0, 4096
    if refill and chosen:
        best = None
        per_trial_limit = refill_limit // len(chosen)
        for removed in chosen:
            retained = tuple(i for i in chosen if i != removed)
            alternative, proposal, events, attempted, exhausted = fill(
                retained, forbidden=(removed,), evaluation_limit=per_trial_limit)
            refill_evaluations += attempted
            refill_trials.append({'removed_candidate': removed, 'evaluations': attempted,
                'work_limit_reached': exhausted, 'source_tokens': proposal[1], 'utility': proposal[2]})
            gain = proposal[2] - current[2]
            better = gain > 1e-12 or (abs(gain) <= 1e-12 and proposal[1] < current[1])
            if proposal[1] <= budget and better:
                key = (proposal[2], -proposal[1], tuple(-i for i in alternative), -removed)
                if best is None or key > best[0]:
                    best = key, removed, alternative, proposal, events
        if best is not None:
            _, removed, alternative, proposal, events = best
            trace.append(event('refill', current, proposal, removed_candidate=removed,
                added_candidates=sorted(set(alternative) - set(chosen)), construction_trace=events))
            chosen, current = alternative, proposal

    skipped, statuses = [], []
    for i in candidates:
        proposal = evaluate(tuple(sorted(set((*chosen, i)))))
        if i in chosen:
            status = 'selected'
        elif proposal[0] == current[0]:
            status = 'context_already_returned'
        elif proposal[1] > budget:
            status = 'over_budget'
            skipped.extend(aliases[contexts[i]])
        else:
            status = 'no_positive_marginal_utility'
        statuses.append({'candidate': i, 'aliases': aliases[contexts[i]], 'status': status,
            'added_source_tokens': proposal[1] - current[1], 'marginal_utility': proposal[2] - current[2]})
    return current[0], list(chosen), sorted(skipped), {
        'objective': 'weighted log1p of distinct complete source-line rank mass',
        'utility_is_legal_confidence': False, 'utility': current[2],
        'facet_mass': list(objective.masses(current[3])), 'facet_weights': objective.weights,
        'complete_source_units_returned': current[3].bit_count(),
        'unique_candidate_contexts': len(candidates), 'range_evaluations': evaluations,
        'exchange_evaluations': exchange_evaluations, 'exchange_passes': 1,
        **({'refill_evaluations': refill_evaluations, 'refill_evaluation_limit': refill_limit,
            'refill_passes': 1, 'refill_trials': refill_trials} if refill else {}),
        'selection_trace': trace, 'candidate_status': statuses,
        'optimality_certified': False, 'retrieval_completeness_certified': False}
