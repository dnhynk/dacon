"""Entry: facts → family reading calls → CPU judgment → submission.csv.

    python script.py                       # evaluation server (PPS_DATA_DIR, PPS_MODEL_DIR, PPS_OUTPUT_DIR)
    python script.py --mode cpu --input X  # no model: CPU default readings only
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import catalog, csvout, facts, families as F, j1, judge, record, switches
from .runner import Engine, MockEngine, log

# Family order after the first pass: cheap, high-value families first so a deadline cut loses the least.
ORDER = ('size', 'dp', 'region', 'perf', 'pledge', 'brief', 'sw', 'model')
# The 'values' family (stated 예산·추정가격 for v24) is not called: on the organizer sample the model left every amount
# empty under the digit grammar, so it adds time without a reading.
PROMPT_TOKEN_LIMIT = 12000


@dataclass
class Request:
    rec: int
    fam: str
    cands: list
    extra: tuple
    token_ids: list
    schema: dict
    max_tokens: int
    budget: int = 0


@dataclass
class JudgeRequest:
    rec: int
    item: str
    lines: list
    cpu: int
    token_ids: list


# J1 seconds per request before any J1 chunk is measured (prefill only, one answer token); only the deadline check uses it.
J1_PRIOR_SECONDS = 0.4

THINKING = {'families': (), 'budget': 0}


def make_request(engine, b, k, name):
    fam = F.FAMILIES[name]
    cands = list(b.cands.get(name, []))
    extra = F.top_fields(name)
    if not cands and not extra:
        return None
    title = (b.titles[0] if b.titles else '')[:80]
    think = name in THINKING['families']
    budget = THINKING['budget'] if think else 0
    while True:
        msgs = F.messages(b.notice, fam, cands, extra, title)
        ids = engine.token_ids(msgs, thinking=think)
        if len(ids) + F.max_tokens(fam, cands, extra) + budget <= min(PROMPT_TOKEN_LIMIT, engine.max_model_len - 64) or len(cands) <= 1:
            break
        cands = cands[:max(1, int(len(cands) * 0.75))]
    return Request(k, name, cands, extra, ids, F.schema(fam, cands, extra), F.max_tokens(fam, cands, extra), budget)


def first_pass_request(engine, b, k):
    """Every notice gets one normal model call; the qualification-requirements family serves it."""
    req = make_request(engine, b, k, 'inst')
    if req is not None:
        return req
    lines = [ln for ln in b.notice.lines if ln.doc_type == '공고문' and ln.text.strip()][:12]
    b.cands['inst'] = lines
    b.readings['inst'] = {ln.i: F.INST.default(b.notice, ln) for ln in lines}
    return make_request(engine, b, k, 'inst')


def j1_request(engine, b, k, item, verdicts, thinking):
    lines = j1.candidates(b, item)
    while True:
        ids = engine.token_ids(j1.messages(b, item, lines), thinking=thinking)
        if len(ids) + j1.ANSWER_TOKENS <= min(j1.PROMPT_TOKEN_LIMIT, engine.max_model_len - 64) or len(lines) <= 1:
            break
        lines = lines[:max(1, int(len(lines) * 0.75))]
    return JudgeRequest(k, item, lines, verdicts[item][0], ids)


def run_j1(engine, bundles, verdicts, mode, thresholds, args, t0, budget, stats, journal):
    """Per-item judgments after the family readings; a deadline cut leaves the remaining cells to the CPU verdicts."""
    thinking = args.j1_thinking_budget > 0
    queue = [j1_request(engine, b, k, item, v, thinking) for k, (b, v) in enumerate(zip(bundles, verdicts))
             for item in j1.wanted(b, v, mode, thresholds)]
    queue.sort(key=lambda r: (r.item, r.rec))    # one item's requests together: its shared prompt head stays cached
    st = stats['j1'] = {'mode': mode, 'requests': len(queue), 'judged': 0, 'p1_missing': 0, 'skipped_deadline': 0,
                        'seconds': 0.0}
    recs, per_req, j0 = {}, None, time.time()
    for s in range(0, len(queue), args.chunk):
        chunk = queue[s:s + args.chunk]
        elapsed = time.time() - t0
        if elapsed + (per_req or J1_PRIOR_SECONDS) * len(chunk) * 1.2 > budget:
            st['skipped_deadline'] += len(queue) - s
            log(f'deadline: {len(queue) - s} J1 requests left to CPU verdicts at {elapsed:.0f}s')
            break
        c0 = time.time()
        outs = engine.judge([(r.token_ids, j1.ANSWER_TOKENS, args.j1_thinking_budget) for r in chunk], top_k=j1.TOP_K)
        rate = (time.time() - c0) / max(1, len(chunk))
        per_req = rate if per_req is None else 0.7 * per_req + 0.3 * rate
        for r, (text, finish, ntok, positions) in zip(chunk, outs):
            ans = j1.answer_probability(positions, after_reasoning=thinking)
            b = bundles[r.rec]
            rec = {'id': b.notice.id, 'item': r.item, 'cpu': r.cpu, 'lines': [ln.i for ln in r.lines],
                   'in': len(r.token_ids), 'out': ntok, 'finish': finish, 'text': text, **ans}
            recs.setdefault(b.notice.id, {})[r.item] = rec
            st['judged'] += 1
            st['p1_missing'] += ans['p1'] is None
            if journal is not None:
                journal.append(rec)
        log(f'J1 {min(s + args.chunk, len(queue))}/{len(queue)} requests, {time.time() - t0:.0f}s elapsed')
    st['seconds'] = round(time.time() - j0, 1)
    return recs


def run(args):
    t0 = time.time()
    budget = args.runtime_seconds - args.margin_seconds
    input_path = args.input or str(Path(args.data_dir) / 'test.jsonl.gz')
    recs = list(record.iter_records(input_path, limit=args.limit))
    log(f'{len(recs)} notices from {input_path}')
    cat = catalog.load(args.data_dir)
    bundles = [facts.build(r, cat) for r in recs]
    log(f'CPU facts in {time.time() - t0:.1f}s')
    journal = []
    stats = {'requests': 0, 'answered': 0, 'parse_failed': 0, 'skipped_deadline': 0, 'by_family': {}}
    engine = None
    j1_mode = args.j1 or ('apply' if switches.J1_THRESHOLDS else 'off')
    if args.mode != 'cpu':
        THINKING['families'] = tuple(args.thinking_families or ())
        THINKING['budget'] = args.thinking_budget
        engine = MockEngine() if args.mode == 'mock' else Engine(
            args.model_dir, max_num_seqs=args.max_num_seqs,
            thinking=bool(THINKING['families']) or (j1_mode != 'off' and args.j1_thinking_budget > 0))
        first = [first_pass_request(engine, b, k) for k, b in enumerate(bundles)]
        rest = []
        for name in ORDER:
            if args.families and name not in args.families:
                continue
            for k, b in enumerate(bundles):
                if name in b.cands:
                    r = make_request(engine, b, k, name)
                    if r is not None:
                        rest.append(r)
        queue = [r for r in first if r is not None] + rest
        stats['requests'] = len(queue)
        log(f'{len(queue)} requests ({len(first)} first pass); prompt build {time.time() - t0:.1f}s')
        per_req, done, retry = None, 0, []
        for s in range(0, len(queue), args.chunk):
            chunk = queue[s:s + args.chunk]
            elapsed = time.time() - t0
            if s >= len(first) and per_req is not None and elapsed + per_req * len(chunk) * 1.2 > budget:
                stats['skipped_deadline'] += len(queue) - s
                log(f'deadline: {len(queue) - s} requests left to CPU defaults at {elapsed:.0f}s')
                break
            c0 = time.time()
            outs = engine.generate([(r.token_ids, r.schema, r.max_tokens, r.budget) for r in chunk])
            dt_chunk = time.time() - c0
            rate = dt_chunk / max(1, len(chunk))
            per_req = rate if per_req is None else 0.7 * per_req + 0.3 * rate
            for r, (text, finish, ntok) in zip(chunk, outs):
                ok = consume(bundles[r.rec], r, text)
                stats['by_family'].setdefault(r.fam, [0, 0])
                stats['by_family'][r.fam][0] += 1
                if ok:
                    stats['answered'] += 1
                    stats['by_family'][r.fam][1] += 1
                else:
                    retry.append(r)
                if args.journal:
                    journal.append({'id': bundles[r.rec].notice.id, 'fam': r.fam, 'lines': [ln.i for ln in r.cands],
                                    'in': len(r.token_ids), 'out': ntok, 'finish': finish, 'text': text})
            done += len(chunk)
            log(f'{done}/{len(queue)} requests, {time.time() - t0:.0f}s elapsed, chunk {dt_chunk:.1f}s')
        if retry and time.time() - t0 + (per_req or 1) * len(retry) * 1.2 < budget:
            outs = engine.generate([(r.token_ids, r.schema, r.max_tokens, r.budget) for r in retry])
            for r, (text, finish, ntok) in zip(retry, outs):
                if consume(bundles[r.rec], r, text):
                    stats['answered'] += 1
                else:
                    stats['parse_failed'] += 1
        else:
            stats['parse_failed'] += len(retry)
    verdicts = [judge.judge(b) for b in bundles]
    j1_journal = [] if args.j1_journal or args.journal else None
    if engine is not None and j1_mode != 'off':
        thresholds = dict(switches.J1_THRESHOLDS) if j1_mode == 'apply' else {}
        by_id = run_j1(engine, bundles, verdicts, j1_mode, thresholds, args, t0, budget, stats, j1_journal)
        if thresholds:
            verdicts = [j1.apply(b, v, by_id.get(b.notice.id, {}), thresholds) for b, v in zip(bundles, verdicts)]
    rows = []
    for rec, v in zip(recs, verdicts):
        rows.append(csvout.row(rec['id'], v, '\n'.join(d['text'] for d in rec['docs'])))
    out_path = str(Path(args.output_dir) / 'submission.csv')
    csvout.write(rows, out_path)
    errs = csvout.validate(out_path, [r['id'] for r in recs])
    stats.update({'notices': len(recs), 'seconds': round(time.time() - t0, 1), 'self_check': errs or 'PASS'})
    log(json.dumps(stats, ensure_ascii=False))
    if args.journal:
        with gzip.open(args.journal, 'wt', encoding='utf-8') as fh:
            for j in journal:
                fh.write(json.dumps(j, ensure_ascii=False) + '\n')
        Path(str(args.journal) + '.stats.json').write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding='utf-8')
    if j1_journal:
        dest = args.j1_journal or str(Path(args.journal).with_name('j1.jsonl.gz'))
        with gzip.open(dest, 'wt', encoding='utf-8') as fh:
            for j in j1_journal:
                fh.write(json.dumps(j, ensure_ascii=False) + '\n')
    return 0 if not errs else 1


def consume(b, r, text):
    lines, top = F.parse(text, F.FAMILIES[r.fam], r.cands, r.extra)
    return facts.apply_model(b, r.fam, lines, top)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--data-dir', default=os.environ.get('PPS_DATA_DIR', './data'))
    ap.add_argument('--output-dir', default=os.environ.get('PPS_OUTPUT_DIR', './output'))
    ap.add_argument('--model-dir', default=os.environ.get('PPS_MODEL_DIR', '/opt/models/gemma-4-26B-A4B-it'))
    ap.add_argument('--input', default=None)
    ap.add_argument('--mode', choices=('llm', 'cpu', 'mock'), default='llm')
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--chunk', type=int, default=384)
    ap.add_argument('--max-num-seqs', type=int, default=48)
    ap.add_argument('--runtime-seconds', type=float, default=7200)
    ap.add_argument('--margin-seconds', type=float, default=600)
    ap.add_argument('--families', nargs='*', default=None)
    ap.add_argument('--journal', default=os.environ.get('PPS_C_JOURNAL'))
    ap.add_argument('--thinking-families', nargs='*', default=None, help='families read with the reasoning channel on')
    ap.add_argument('--thinking-budget', type=int, default=256)
    ap.add_argument('--j1', choices=('off', 'record', 'apply'), default=None,
                    help='per-item judgment (v9, v1 OR): record = journal p1 only; apply = switches.J1_THRESHOLDS '
                         '(the default when thresholds are set, else off)')
    ap.add_argument('--j1-journal', default=None, help='J1 records (default: j1.jsonl.gz beside --journal)')
    ap.add_argument('--j1-thinking-budget', type=int, default=0, help='>0: J1 reads with the reasoning channel on')
    args = ap.parse_args(argv)
    return run(args)


if __name__ == '__main__':
    sys.exit(main())
