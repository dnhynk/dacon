"""Streaming executor contracts with a fake engine; no GPU, model or labels."""
import collections
import csv
import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from submission import stream
from submission.prep import PreparationPool
from submission.stream import StreamOptions, StreamingExecutor, TIERS, execute_stream


def read_rows(path):
    with gzip.open(path, 'rt', encoding='utf-8') as handle:
        return [json.loads(line) for line in handle]


def record(rid):
    return {'id': rid, 'meta': {'업무구분': '일반용역'},
            'docs': [{'type': '공고문', 'doc_id': rid + '-notice', 'text': '입찰공고 본문 ' + rid}],
            'input_completeness': {'완전관측': True}, 'dropped_doc_counts': {}}


ITEMS = {'A1': list(range(1, 10)), 'A10': list(range(10, 19)), 'A19': list(range(19, 25)),
         'L19': list(range(19, 25)), 'Q10': list(range(10, 19)), 'S9': [9]}


def packet(rec, profile):
    items = ITEMS[profile]
    family = 'A' if profile == 'S9' else profile[0]
    return {'request_key': f"{family}:{rec['id']}:{items[0]}" + (':S' if profile == 'S9' else ''),
            'record_id': rec['id'], 'family': family, 'batch': profile, 'items': items, 'spans': [],
            'token_ids': [11, 22, 33], 'prompt_sha256': 'prompt-' + profile,
            'token_ids_sha256': 'tokens-' + profile + rec['id'],
            'generation': {'response_format': 'compact', 'thinking_budget': 0, 'max_output_tokens': 64}}


CONFIG = SimpleNamespace(require_positive_evidence=False, max_response_retries=2, rule_checks=False,
                         qualification_checks=False, source_verified_services=False, cross_source_facts=False,
                         max_num_seqs=32)


class SyntheticPipeline:
    def __init__(self, *, q10=(), s9=None):
        self.config = CONFIG
        self.knowledge = None
        self.q10, self.s9 = set(q10), dict(s9 or {})

    def bundle(self, rec):
        packets = [packet(rec, p) for p in ('A1', 'A10', 'A19', 'L19')]
        if rec['id'] in self.q10:
            packets.append(packet(rec, 'Q10'))
        if rec['id'] in self.s9:
            packets.append({**packet(rec, 'S9'),
                            'specification_inventory': {'candidates': [1] * self.s9[rec['id']]}})
        return packets

    def consume(self, rec, pkt, response):
        values = json.loads(response['text'])['v']
        items = [20] if pkt['family'] == 'L' else pkt['items']
        if pkt['family'] == 'L':
            values = [values[1]]
        return ({f'{f}{k}': (v if f == 'v' else '') for k, v in zip(items, values) for f in ('v', 'e')},
                [{'source': 'synthetic_consumer'}])


class Clock:
    def __init__(self, start=1000.):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeRunner:
    """add_request/step semantics of one engine; responses decided per packet and attempt."""

    def __init__(self, responder, clock, *, steps_to_finish=1, seconds_per_step=1.):
        self.config = CONFIG
        self.responder, self.clock = responder, clock
        self.steps_to_finish, self.seconds_per_step = steps_to_finish, seconds_per_step
        self.queue = collections.OrderedDict()
        self.submitted, self.aborted = [], []
        self.load_seconds, self.tokenizer, self.closed = 1., None, False
        self.fail_after = None

    def submit(self, request_id, pkt):
        self.submitted.append(request_id)
        self.queue[request_id] = [pkt, self.steps_to_finish]

    def step(self):
        self.clock.advance(self.seconds_per_step)
        if self.fail_after is not None and len(self.submitted) >= self.fail_after:
            raise RuntimeError('synthetic engine failure')
        finished = []
        for request_id, entry in list(self.queue.items()):
            entry[1] -= 1
            if entry[1] > 0:
                continue
            pkt = self.queue.pop(request_id)[0]
            attempt = int(request_id.rsplit('#', 1)[1])
            text, reason = self.responder(pkt, attempt)
            finished.append(SimpleNamespace(request_id=request_id, finished=True, prompt_token_ids=list(pkt['token_ids']),
                                            num_cached_tokens=1, outputs=[SimpleNamespace(
                                                text=text, token_ids=[7] * 5, finish_reason=reason, stop_reason=None)]))
        return finished

    def unfinished(self):
        return bool(self.queue)

    def abort(self, request_ids):
        for request_id in request_ids:
            self.aborted.append(request_id)
            self.queue.pop(request_id, None)

    def response_from_native(self, output, pkt):
        generated = output.outputs[0]
        return {'text': generated.text, 'finish_reason': generated.finish_reason, 'output_tokens': len(generated.token_ids),
                'cached_input_tokens': output.num_cached_tokens, 'raw_output_sha256': 'synthetic', 'generation_stall': None}

    def close(self):
        self.closed = True


class ClockedPool(PreparationPool):
    """Inline pool whose polling advances the fake clock so idle loops make progress."""

    def __init__(self, pipeline, clock):
        super().__init__('unused', 'unused', workers=0, inline_pipeline=pipeline)
        self.clock = clock

    def poll(self, timeout=0.):
        self.clock.advance(.5)
        return super().poll(0.)


def valid(pkt, value=1):
    n = len(pkt['items'])
    return json.dumps({'v': [value] * n, 'e': [0] * n}), 'stop'


def answer(pkt, attempt):
    """Responder: every request answers a valid all-positive judgment."""
    return valid(pkt, 1)


def run(tmp_path, recs, pipeline, responder, *, options=None, clock=None, runner=None):
    clock = clock or Clock()
    runner = runner or FakeRunner(responder, clock)
    pool = ClockedPool(pipeline, clock)
    options = options or StreamOptions(total_runtime_seconds=100000, tier_ceiling=0)
    report = execute_stream('unused', 'unused', tmp_path / 'output', tokenizer_dir='unused',
                            runner_factory=lambda config, journal: runner, options=options,
                            records_override=recs, pool=pool, pipeline=pipeline, clock=clock, started_at=clock())
    return report, runner


def csv_rows(path):
    with open(path, encoding='utf-8', newline='') as handle:
        return {row['id']: row for row in csv.DictReader(handle)}


def test_records_stream_in_order_with_adjacent_prefix_requests_and_gated_reviews(tmp_path):
    recs = [record('r1'), record('r2'), record('r3')]
    pipeline = SyntheticPipeline(q10={'r2'}, s9={'r1': 0, 'r3': 2})
    report, runner = run(tmp_path, recs, pipeline, answer)
    assert report['records'] == 3 and report['records_without_model_call'] == 0
    assert report['tier_counts'] == {'canonical': 3}
    first = [key.split('#')[0].split(':')[0] + ':' + key.split(':')[1] for key in runner.submitted]
    # Every record's A1, A19, A10 enter together, in input order, before the next record.
    assert first[:4] == ['A:r1', 'A:r1', 'A:r1', 'L:r1']
    assert [k.split('#')[0].split(':')[2] for k in runner.submitted[:4]] == ['1', '19', '10', '19']
    assert runner.submitted[4:9] == ['A:r2:1#0', 'A:r2:19#0', 'A:r2:10#0', 'Q:r2:10#0', 'L:r2:19#0']
    # S9 for r1 is gate-skipped (v9 from the model is 1 here, so it runs), r3 runs by candidate count.
    assert 'A:r1:9:S#0' in runner.submitted and 'A:r3:9:S#0' in runner.submitted
    rows = csv_rows(tmp_path / 'output/submission.csv')
    assert set(rows) == {'r1', 'r2', 'r3'}
    assert all(rows[rid][f'v{k}'] == '1' for rid in rows for k in range(1, 25))
    assert (tmp_path / 'output/B3.csv').is_file()
    native = read_rows(tmp_path / 'output/stream_native_00000.jsonl.gz')
    assert len(native) == report['responses'] == report['requests_submitted']
    assert all(row['native']['prompt_tokens_match'] for row in native)
    records = {r['id']: r for r in read_rows(tmp_path / 'output/stream_records_00000.jsonl.gz')}
    assert set(records) == {'r1', 'r2', 'r3'} and list(rows) == ['r1', 'r2', 'r3']
    assert records['r1']['sources']['S9'] == 'model_response' and records['r2']['sources']['Q10'] == 'model_response'
    assert runner.closed and report['source_unchanged'] is True
    timing = report['timing']
    assert timing['first_submit_elapsed'] is not None and timing['generation_window_seconds'] >= timing['engine_busy_seconds'] - 1e-6
    assert report['preparation']['inline'] is True


def test_s9_gate_skip_without_candidates_or_positive_v9(tmp_path):
    recs = [record('r1')]
    pipeline = SyntheticPipeline(s9={'r1': 0})
    report, runner = run(tmp_path, recs, pipeline, lambda pkt, attempt: valid(pkt, 0))
    assert 'A:r1:9:S#0' not in runner.submitted and report['s9_gate_skips'] == 1


def test_format_failures_retry_immediately_then_fall_back_to_source_rules(tmp_path):
    recs = [record('r1'), record('r2')]
    pipeline = SyntheticPipeline()

    def responder(pkt, attempt):
        if pkt['record_id'] == 'r1' and pkt['batch'] == 'A1' and attempt < 2:
            return '{', 'stop'
        if pkt['record_id'] == 'r2' and pkt['batch'] == 'A10':
            return '{', 'stop'
        return valid(pkt)

    report, runner = run(tmp_path, recs, pipeline, responder)
    assert report['parse_retries'] == 4 and report['unresolved_requests'] == 1
    assert report['source_rule_fallbacks'] == 1
    order = runner.submitted.index
    assert order('L:r1:19#0') < order('A:r1:1#1') < order('A:r1:1#2')  # recovery follows the failed answer at once
    rows = csv_rows(tmp_path / 'output/submission.csv')
    assert all(rows['r1'][f'v{k}'] == '1' for k in range(1, 10))
    assert all(rows['r2'][f'v{k}'] == '0' for k in range(10, 19)) and rows['r2']['v1'] == '1'
    records = {r['id']: r for r in read_rows(tmp_path / 'output/stream_records_00000.jsonl.gz')}
    assert records['r1']['sources']['A1'] == 'format_recovery_attempt_2'
    assert records['r2']['sources']['A10'].startswith('source_rules_fallback')
    consumed = read_rows(tmp_path / 'output/stream_consumed_00000.jsonl.gz')
    assert sum(1 for row in consumed if row.get('error')) == 5


def test_submission_deadline_completes_the_csv_from_source_rules(tmp_path):
    recs = [record(f'r{i}') for i in range(1, 5)]
    pipeline = SyntheticPipeline()
    clock = Clock()
    runner = FakeRunner(answer, clock, steps_to_finish=2, seconds_per_step=30.)
    options = StreamOptions(total_runtime_seconds=300, margin_seconds=180, abort_grace_seconds=60, inflight_requests=2, tier_ceiling=0)
    report, runner = run(tmp_path, recs, pipeline, answer, options=options, clock=clock, runner=runner)
    assert report['records'] == 4 and report['records_after_submission_deadline'] >= 1
    assert report['records_without_model_call'] == report['records_after_submission_deadline']
    assert report['deadline']['submissions_closed_early'] is True
    rows = csv_rows(tmp_path / 'output/submission.csv')
    assert set(rows) == {'r1', 'r2', 'r3', 'r4'}
    records = {r['id']: r for r in read_rows(tmp_path / 'output/stream_records_00000.jsonl.gz')}
    late = [rid for rid, r in records.items() if r['tier'] == stream.FALLBACK_TIER]
    assert late and all(rows[rid][f'v{k}'] == '0' for rid in late for k in range(1, 25))
    assert (tmp_path / 'output/submission_deadline.json').is_file()


def test_abort_deadline_finalizes_inflight_records(tmp_path):
    recs = [record('r1'), record('r2')]
    pipeline = SyntheticPipeline()
    clock = Clock()
    runner = FakeRunner(answer, clock, steps_to_finish=1000, seconds_per_step=20.)
    options = StreamOptions(total_runtime_seconds=300, margin_seconds=180, abort_grace_seconds=60, tier_ceiling=0)
    report, runner = run(tmp_path, recs, pipeline, answer, options=options, clock=clock, runner=runner)
    assert report['aborted_requests'] == 8 and runner.aborted
    assert report['deadline']['aborted'] is True
    rows = csv_rows(tmp_path / 'output/submission.csv')
    assert set(rows) == {'r1', 'r2'} and all(v in {'0', '1'} for row in rows.values() for k, v in row.items() if k.startswith('v'))


def test_engine_failure_still_writes_a_complete_csv_before_raising(tmp_path):
    recs = [record('r1'), record('r2'), record('r3')]
    pipeline = SyntheticPipeline()
    clock = Clock()
    runner = FakeRunner(answer, clock)
    runner.fail_after = 8
    with pytest.raises(RuntimeError, match='synthetic engine failure'):
        run(tmp_path, recs, pipeline, answer, clock=clock, runner=runner)
    assert (tmp_path / 'output/failure.json').is_file()
    rows = csv_rows(tmp_path / 'output/submission.csv')
    assert set(rows) == {'r1', 'r2', 'r3'}
    assert runner.closed


def test_code_only_a10_skips_the_engine(tmp_path, monkeypatch):
    recs = [record('r1')]
    pipeline = SyntheticPipeline()

    def prepared(pipe, rec):
        packets = pipe.bundle(rec)
        key = next(p['request_key'] for p in packets if p['batch'] == 'A10')
        return {'packets': packets, 'fallback_rows': {}, 'code_only': {key: {
            'row': {f'{f}{k}': (1 if f == 'v' else '') for k in range(10, 19) for f in ('v', 'e')},
            'decision': {'rule': 'all_A10_items_source_fixed', 'model_called': False}}}}
    import submission.prep as prep
    monkeypatch.setattr(prep, 'prepare_record', prepared)
    report, runner = run(tmp_path, recs, pipeline, answer)
    assert report['code_only_skips'] == 1 and 'A:r1:10#0' not in runner.submitted
    rows = csv_rows(tmp_path / 'output/submission.csv')
    assert all(rows['r1'][f'v{k}'] == '1' for k in range(10, 19))


def test_lower_tiers_replace_model_rows_with_source_rules(tmp_path):
    recs = [record('r1'), record('r2')]
    pipeline = SyntheticPipeline(q10={'r1', 'r2'}, s9={'r1': 3})
    options = StreamOptions(total_runtime_seconds=100000, tier_ceiling=2, tier_floor=2)
    report, runner = run(tmp_path, recs, pipeline, answer, options=options)
    assert report['tier_counts'] == {TIERS[2]['name']: 2}
    profiles = sorted({k.split('#')[0].split(':')[0] + k.split('#')[0].split(':')[2] for k in runner.submitted})
    assert profiles == ['A1', 'A19', 'Q10']
    rows = csv_rows(tmp_path / 'output/submission.csv')
    # Q10 (model, value1) overrides the source-only A10 zeros for items10..18; v20 keeps A19.
    assert all(rows['r1'][f'v{k}'] == '1' for k in range(10, 19)) and rows['r1']['v20'] == '1'
    assert report['source_rule_rows_for_unplanned_profiles'] == 2


def test_stream_options_reject_unsafe_budgets():
    with pytest.raises(ValueError):
        StreamOptions(total_runtime_seconds=200, margin_seconds=180, abort_grace_seconds=60)
    with pytest.raises(ValueError):
        StreamOptions(tier_ceiling=3, tier_floor=1)
    with pytest.raises(ValueError):
        StreamOptions(projection_records=0)


def test_pre_engine_hook_runs_after_prefeed_and_before_the_engine(tmp_path):
    recs = [record('r1'), record('r2')]
    pipeline = SyntheticPipeline()
    clock = Clock()
    runner = FakeRunner(answer, clock)
    pool = ClockedPool(pipeline, clock)
    order = []

    def hook(journal):
        order.append(('hook', len(pool._inline_results)))   # prepare results already waiting

    def factory(config, journal):
        order.append(('engine', None))
        return runner
    report = execute_stream('unused', 'unused', tmp_path / 'output', tokenizer_dir='unused', runner_factory=factory,
                            options=StreamOptions(total_runtime_seconds=100000, tier_ceiling=0), records_override=recs,
                            pool=pool, pipeline=pipeline, clock=clock, started_at=clock(), pre_engine=hook)
    assert [kind for kind, _ in order] == ['hook', 'engine'] and order[0][1] == 2   # both records prepared ahead
    assert report['records'] == 2

