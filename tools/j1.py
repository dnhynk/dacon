"""J1 (per-item model judgment, OR on v9 and v1) tools for pipeline C.

    python tools/j1.py law                      # write submission/pps_c/assets/j1_law.json from the law package
    python tools/j1.py replay RUN INPUT OUT.csv [--thresholds v9=0.9,v1=0.95]
    python tools/j1.py roc RUN [--corpus RUN]   # dev ROC of a record-only run (+ corpus exposure, never labelled)
    python tools/j1.py budget                   # J1 calls, prompt tokens and server seconds (CPU only)
    python tools/j1.py v9cov                    # candidate coverage of v9 positives (dev) and planted misses (replica)
    python tools/j1.py kit STAMP JOB [JOB ...] [-- <script.py args>]

RUN is a directory with journal.jsonl.gz (family readings), j1.jsonl.gz (J1 records) and submission.csv. Replica
output is aggregate counts only. Analysis outputs go to runs/rebuild_c/j1_20260925/ under this checkout.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import importlib.util
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / 'submission'))
from pps_c import catalog, csvout, facts, families as F, j1, judge, record  # noqa: E402

MAIN = Path('D:/repos/dacon')
DATA = MAIN / 'data_open'
OUT = HERE / 'runs/rebuild_c/j1_20260925'
LAW = DATA / 'data/법령패키지/법령'
DECREE = '국가를 당사자로 하는 계약에 관한 법률 시행령.txt'
RULES = '(계약예규) 정부 입찰·계약 집행기준.txt'
# (item, file, article, start marker, inclusive end marker or None for the start sentence alone)
LAW_CUTS = [
    ('v1', DECREE, '국가계약법 시행령 제21조 제1항',
     '①법 제7조제1항 단서에 따라 경쟁참가자의 자격을 제한할 수 있는 경우와 그 제한사항은 다음 각 호와 같다.', None),
    ('v1', RULES, '정부 입찰·계약 집행기준 제5조 제4항',
     '④ 계약담당공무원은 시행령 제21조제1항에 의하여 제한경쟁입찰에 참가할 자의 자격을 제한하는 경우에',
     '다음 각호와 같이 경쟁참가자의 자격을 제한하여서는 아니 된다.'),
    ('v9', RULES, '정부 입찰·계약 집행기준 제5조 제4항',
     '④ 계약담당공무원은 시행령 제21조제1항에 의하여 제한경쟁입찰에 참가할 자의 자격을 제한하는 경우에',
     '다음 각호와 같이 경쟁참가자의 자격을 제한하여서는 아니 된다.'),
    ('v9', RULES, '정부 입찰·계약 집행기준 제5조 제4항 제5호',
     '5. 물품의 제조ㆍ구매입찰시 부당하게 특정상표 또는 특정규격 또는 모델을 지정하여', '동등이상인 국산품목의 납품을 거부)'),
]
ITEMS = [f'v{k}' for k in range(1, 25)]
INPUTS = {'dev': DATA / 'dev.jsonl.gz', 'replica': MAIN / 'runs/replica_20260922/set_v2_1853/replica_input.jsonl.gz',
          'corpus': MAIN / 'runs/rebuild_c/inputs/corpus1250.jsonl.gz'}
JOURNALS = {'dev': MAIN / 'runs/rebuild_c/gpu/mix_C2/dev', 'replica': MAIN / 'runs/rebuild_c/gpu/mix_C2/replica',
            'corpus': MAIN / 'runs/rebuild_c/gpu/mix_C2/corpus'}


def law_cut(text, start, end):
    s = text.index(start)
    return start if end is None else text[s:text.index(end, s) + len(end)]


def cmd_law(_):
    out = {}
    for item, fname, label, start, end in LAW_CUTS:
        text = (LAW / fname).read_text(encoding='utf-8')
        out.setdefault(item, []).append({'source': fname, 'article': label, 'text': law_cut(text, start, end)})
    dest = HERE / 'submission/pps_c/assets/j1_law.json'
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1) + '\n', encoding='utf-8', newline='\n')
    print(json.dumps({k: [len(x['text']) for x in v] for k, v in out.items()}), dest)


# ---------------------------------------------------------------- replay
def load_journal(run):
    jr = defaultdict(list)
    with gzip.open(Path(run) / 'journal.jsonl.gz', 'rt', encoding='utf-8') as fh:
        for line in fh:
            j = json.loads(line)
            jr[j['id']].append(j)
    return jr


def load_j1(run):
    p = Path(run) / 'j1.jsonl.gz'
    recs = defaultdict(dict)
    if p.is_file():
        with gzip.open(p, 'rt', encoding='utf-8') as fh:
            for line in fh:
                r = json.loads(line)
                recs[r['id']][r['item']] = r
    return recs


def rebuild(rec, cat, journal):
    """The bundle of one record with the journal's model readings applied (as runs/rebuild_c/tools/replay.py)."""
    b = facts.build(rec, cat)
    for j in journal:
        fam = F.FAMILIES[j['fam']]
        cands = [b.notice.lines[i] for i in j['lines']]
        extra = F.top_fields(j['fam'])
        for ln in cands:
            b.readings.setdefault(j['fam'], {}).setdefault(ln.i, fam.default(b.notice, ln))
        if j['fam'] not in b.cands:
            b.cands[j['fam']] = cands
        else:
            known = {ln.i for ln in b.cands[j['fam']]}
            b.cands[j['fam']] = sorted(b.cands[j['fam']] + [ln for ln in cands if ln.i not in known], key=lambda ln: ln.i)
        lines, top = F.parse(j['text'], fam, cands, extra)
        facts.apply_model(b, j['fam'], lines, top)
    return b


def parse_thresholds(s):
    out = {}
    for part in filter(None, (s or '').split(',')):
        k, v = part.split('=')
        out[k.strip()] = float(v)
    return out


def replay(run, inp, thresholds=None):
    """[(record, verdicts)] of a run re-judged on CPU; J1 thresholds applied when given."""
    jr, recs1 = load_journal(run), load_j1(run)
    cat = catalog.load(str(DATA / 'data'))
    rows = []
    for rec in record.iter_records(inp):
        b = rebuild(rec, cat, jr.get(rec['id'], []))
        v = judge.judge(b)
        if thresholds:
            v = j1.apply(b, v, recs1.get(rec['id'], {}), thresholds)
        rows.append((rec, v))
    return rows


def write_rows(rows, out):
    csvout.write([csvout.row(rec['id'], v, '\n'.join(d['text'] for d in rec['docs'])) for rec, v in rows], out)


def cmd_replay(a):
    rows = replay(a.run, a.input, parse_thresholds(a.thresholds))
    write_rows(rows, a.out)
    print('wrote', a.out, len(rows))


# ---------------------------------------------------------------- ROC
def read_csv(p):
    return {r['id']: r for r in csv.DictReader(open(p, encoding='utf-8-sig', newline=''))}


GRID = (0.05, 0.1, 0.2, 0.3, 0.5, 0.7, 0.8, 0.9, 0.95, 0.98, 0.99, 0.995, 0.999)


def quantiles(xs):
    xs = sorted(xs)
    if not xs:
        return 'n=0'
    q = lambda f: xs[min(len(xs) - 1, int(f * (len(xs) - 1) + 0.5))]  # noqa: E731
    return f'n={len(xs)} min {xs[0]:.3f} q25 {q(.25):.3f} med {q(.5):.3f} q75 {q(.75):.3f} max {xs[-1]:.3f}'


def cmd_roc(a):
    """Dev is the ROC: organizer labels, which the judge never saw. For each threshold, the dev TP and FP a v9/v1 OR adds
    to the run's own CPU verdicts (few positives: counts, not rates). The corpus is exposure only: how many firings a
    threshold adds on natural notices, never counted as right or wrong."""
    lab = read_csv(DATA / 'dev_labels.csv')
    recs = load_j1(a.run)
    base = read_csv(Path(a.run) / 'submission.csv')
    report = {'run': str(a.run), 'items': {}}
    n_rec = sum(len(v) for v in recs.values())
    miss = sum(r.get('p1') is None for v in recs.values() for r in v.values())
    bound = sum(bool(r.get('bound')) for v in recs.values() for r in v.values())
    lines = [f'J1 records {n_rec}; p1 missing {miss}; one digit outside the top-{j1.TOP_K} (bounded p1) {bound}']
    crecs = load_j1(a.corpus) if a.corpus else None
    cbase = read_csv(Path(a.corpus) / 'submission.csv') if a.corpus else None
    for item in j1.TARGETS:
        rs = {i: rr[item] for i, rr in recs.items() if item in rr and i in lab}
        npos = sum(lab[i][item] == '1' for i in lab)
        tp0 = sum(lab[i][item] == '1' and base[i][item] == '1' for i in lab)
        fp0 = sum(lab[i][item] != '1' and base[i][item] == '1' for i in lab)
        fn_judged = [r['p1'] for i, r in rs.items() if lab[i][item] == '1' and base[i][item] == '0' and r.get('p1') is not None]
        f0 = 2 * tp0 / (tp0 + fp0 + npos) if tp0 + fp0 + npos else 1.0
        lines.append(f'\n{item} (OR on family {j1.TARGETS[item]}): dev positives {npos}; CPU TP {tp0} FP {fp0} FN {npos - tp0} '
                     f'(F1 {f0:.3f}); judged notices {len(rs)}; CPU misses judged {len(fn_judged)} — few positives, counts only')
        lines.append('  p1 of dev positives: ' + quantiles([r['p1'] for i, r in rs.items() if lab[i][item] == '1' and r.get('p1') is not None]))
        lines.append('  p1 of dev negatives: ' + quantiles([r['p1'] for i, r in rs.items() if lab[i][item] != '1' and r.get('p1') is not None]))
        lines.append('  p1 of CPU misses (dev positives with CPU 0): ' + ' '.join(f'{p:.3f}' for p in sorted(fn_judged)))
        grid = []
        for th in GRID:
            add = [i for i, r in rs.items() if base[i][item] == '0' and r.get('p1') is not None and r['p1'] >= th]
            d_tp = sum(lab[i][item] == '1' for i in add)
            d_fp = len(add) - d_tp
            tp, fp = tp0 + d_tp, fp0 + d_fp
            f1 = 2 * tp / (tp + fp + npos) if tp + fp + npos else 1.0
            row = {'threshold': th, 'added_tp': d_tp, 'added_fp': d_fp, 'dev_f1': round(f1, 4), 'dev_f1_delta': round(f1 - f0, 4)}
            if crecs is not None:
                row['corpus_added'] = sum(1 for i, rr in crecs.items() if item in rr and cbase[i][item] == '0'
                                          and rr[item].get('p1') is not None and rr[item]['p1'] >= th)
            grid.append(row)
            lines.append('  th {:<6} added TP {:>2} FP {:>3}  dev F1 {:.3f} ({:+.3f}){}'.format(
                th, d_tp, d_fp, row['dev_f1'], row['dev_f1_delta'],
                f"  corpus added {row['corpus_added']} of {len(cbase)} (exposure)" if crecs is not None else ''))
        report['items'][item] = {'dev_positives': npos, 'cpu': [tp0, fp0, npos - tp0], 'judged': len(rs),
                                 'p1_cpu_misses': sorted(fn_judged), 'grid': grid}
    OUT.mkdir(parents=True, exist_ok=True)
    tag = Path(a.run).parent.name + '_' + Path(a.run).name
    (OUT / f'roc_{tag}.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    (OUT / f'roc_{tag}.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('\n'.join(lines))


# ---------------------------------------------------------------- budget
def chunk_costs(run):
    """[(seconds, prompt tokens, output tokens, requests)] per logged chunk of a GPU run (journal order = queue order)."""
    js = [json.loads(line) for line in gzip.open(Path(run) / 'journal.jsonl.gz', 'rt', encoding='utf-8')]
    marks = [(int(m.group(1)), float(m.group(2))) for m in
             re.finditer(r'(\d+)/\d+ requests, \d+s elapsed, chunk ([\d.]+)s', (Path(run) / 'log.txt').read_text(encoding='utf-8'))]
    out, prev = [], 0
    for done, sec in marks:
        part = js[prev:done]
        out.append((sec, sum(j['in'] for j in part), sum(j['out'] for j in part), len(part)))
        prev = done
    return out


def fit_costs(chunks):
    """Least squares seconds = a · prompt tokens + b · output tokens over chunks after the first (warm-up)."""
    import numpy as np
    rows = chunks[1:] if len(chunks) > 2 else chunks
    X = np.array([[c[1], c[2]] for c in rows], dtype=float)
    y = np.array([c[0] for c in rows], dtype=float)
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    return float(coef[0]), float(coef[1]), float(np.sqrt(np.mean((X @ coef - y) ** 2)))


def token_ratio(run, inp, limit=200):
    """Prompt tokens per prompt character, from a run's journal ('in' = tokens of the family prompt)."""
    jr = load_journal(run)
    cat = catalog.load(str(DATA / 'data'))
    chars = toks = 0
    for k, rec in enumerate(record.iter_records(inp)):
        if k >= limit:
            break
        b = facts.build(rec, cat)
        title = (b.titles[0] if b.titles else '')[:80]
        for j in jr.get(rec['id'], []):
            fam = F.FAMILIES[j['fam']]
            cands = [b.notice.lines[i] for i in j['lines']]
            chars += sum(len(m['content']) for m in F.messages(b.notice, fam, cands, F.top_fields(j['fam']), title))
            toks += j['in']
    return toks / chars


def cmd_budget(_):
    ratio = token_ratio(JOURNALS['dev'], INPUTS['dev'])
    runs = [MAIN / 'runs/rebuild_c/gpu/c9_0924i/dev', MAIN / 'runs/rebuild_c/gpu/fp2a_0924/dev',
            MAIN / 'runs/rebuild_c/gpu/fp2a_0924/replica']
    fits = {}
    for run in runs:
        if (run / 'log.txt').is_file():
            ch = chunk_costs(run)
            a_in, b_out, rmse = fit_costs(ch)
            fits[str(run)] = {'chunks': len(ch), 'sec_per_prompt_token': a_in, 'sec_per_output_token': b_out, 'rmse_s': rmse,
                              'total_s': round(sum(c[0] for c in ch), 1), 'prompt_tokens': sum(c[1] for c in ch),
                              'output_tokens': sum(c[2] for c in ch), 'requests': sum(c[3] for c in ch)}
    report = {'tokens_per_char': round(ratio, 4), 'cost_fits': fits, 'inputs': {}}
    cat = catalog.load(str(DATA / 'data'))
    for name in ('dev', 'replica', 'corpus'):
        jr = load_journal(JOURNALS[name])
        n, calls, toks = 0, Counter(), Counter()
        for rec in record.iter_records(INPUTS[name]):
            n += 1
            b = rebuild(rec, cat, jr.get(rec['id'], []))
            v = judge.judge(b)
            for mode in ('record', 'apply'):
                for item in j1.wanted(b, v, mode, j1.TARGETS):
                    t = int(sum(len(m['content']) for m in j1.messages(b, item, j1.candidates(b, item))) * ratio) + 12
                    calls[(mode, item)] += 1
                    toks[(mode, item)] += t
        row = {'notices': n}
        for mode in ('record', 'apply'):
            for item in j1.TARGETS:
                c, t = calls[(mode, item)], toks[(mode, item)]
                row[f'{mode}_{item}'] = {'calls': c, 'prompt_tokens': t, 'tokens_per_call': round(t / max(1, c))}
            row[f'{mode}_prompt_tokens'] = sum(toks[(mode, it)] for it in j1.TARGETS)
            row[f'{mode}_calls'] = sum(calls[(mode, it)] for it in j1.TARGETS)
        report['inputs'][name] = row
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'budget.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=1))


# ---------------------------------------------------------------- v9 candidate coverage
LATIN_ANY = F.LATIN_MODEL
MODEL_WORD_WIDE = re.compile(F.MODEL_WORD.pattern + r'|메이커|제작\s*사|제조\s*업체|생산\s*업체|품\s*번|P\s*/\s*N|Part\s*No|\b[Mm]odel\b|\bMODEL\b')


def widened(notice, word, latin_anywhere):
    """C's model selection with a wider cue word and/or Latin model tokens accepted in every document."""
    out = []
    for ln in notice.lines:
        t = ln.text
        if not t.strip() or ln.sec in ('EVAL', 'DOCS') or (F.CREDIT.search(t) and '등급' in t):
            continue
        if word.search(t):
            out.append(ln)
            continue
        if latin_anywhere or ln.doc_type in ('규격서', '과업지시서', '제안요청서') or ln.sec in ('OVERVIEW', 'TOP'):
            if [m.group(0) for m in LATIN_ANY.finditer(t) if not F.SPEC_NOISE.match(m.group(0))]:
                out.append(ln)
    return out


def cmd_v9cov(_):
    """Aggregates only: dev v9 positives and replica planted-v9 misses (P3a replay) with and without a model-family
    candidate line, and what a wider selection would add, with its exposure on dev and corpus."""
    import numpy as np
    cat = catalog.load(str(DATA / 'data'))
    lab = read_csv(DATA / 'dev_labels.csv')
    sel = {'C selection': lambda b: b.cands.get('model', []),
           'wider cue words': lambda b: widened(b.notice, MODEL_WORD_WIDE, False),
           'Latin tokens in every document': lambda b: widened(b.notice, F.MODEL_WORD, True),
           'both': lambda b: widened(b.notice, MODEL_WORD_WIDE, True)}
    counts = {k: Counter() for k in sel}
    dev_pos = {i for i, r in lab.items() if r['v9'] == '1'}
    for rec in record.iter_records(INPUTS['dev']):
        b = facts.build(rec, cat)
        for k, f in sel.items():
            got = f(b)
            counts[k]['dev_notices_with_lines'] += bool(got)
            counts[k]['dev_positives_with_lines'] += rec['id'] in dev_pos and bool(got)
    h = np.load(MAIN / 'runs/rebuild_20260924/headroom/hyps_hosts_final.npz', allow_pickle=False)
    rids = list(h['rids'])
    col = ITEMS.index('v9')
    planted = h['edited_prob'][:, col] >= .5
    pred = read_csv(MAIN / 'runs/rebuild_c/lb_replays/P3a_replica.csv')
    missed = {rids[k] for k in range(len(rids)) if planted[k] and pred[rids[k]]['v9'] != '1'}
    for rec in record.iter_records(INPUTS['replica']):
        if rec['id'] in missed:
            b = facts.build(rec, cat)
            for k, f in sel.items():
                counts[k]['replica_missed_with_lines'] += bool(f(b))
    for rec in record.iter_records(INPUTS['corpus']):
        b = facts.build(rec, cat)
        for k, f in sel.items():
            got = f(b)
            counts[k]['corpus_notices_with_lines'] += bool(got)
            counts[k]['corpus_lines'] += len(got)
    res = {'dev_positives': len(dev_pos), 'replica_planted_v9': int(planted.sum()), 'replica_missed_by_P3a': len(missed),
           'selections': {k: dict(v) for k, v in counts.items()}}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'v9_coverage.json').write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(res, ensure_ascii=False, indent=1))


# ---------------------------------------------------------------- Colab kit
def cmd_kit(a, extra):
    """runs/rebuild_c/colab/make_colab.py with its code payload read from this checkout's submission/ and its notebook
    written under this checkout's artifacts/ (the shared kit file is not edited)."""
    import hashlib
    spec = importlib.util.spec_from_file_location('make_colab', MAIN / 'runs/rebuild_c/colab/make_colab.py')
    mk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mk)
    src = HERE / 'submission'

    def code_payload():
        entries = {name: (mk.KIT / name).read_bytes() for name in ('runner.py', 'uv_exact.py', 'requirements-gpu.lock', 'download_model.py')}
        entries['submission/script.py'] = (src / 'script.py').read_bytes()
        h = hashlib.sha256()
        for p in sorted((src / 'pps_c').rglob('*')):
            if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc':
                rel = 'submission/' + p.relative_to(src).as_posix()
                entries[rel] = p.read_bytes()
                h.update(rel.encode())
                h.update(entries[rel])
        manifest = {'code_fingerprint': h.hexdigest(), 'files': len(entries)}
        entries['payload_manifest.json'] = json.dumps(manifest, indent=1).encode()
        return mk.zip_bytes(entries), manifest

    mk.code_payload = code_payload
    mk.OUT = HERE / 'artifacts/rebuild_c/colab'
    sys.argv = ['make_colab.py', a.stamp, *a.jobs] + (['--', *extra] if extra else [])
    mk.main()


def main():
    argv = sys.argv[1:]
    extra = argv[argv.index('--') + 1:] if '--' in argv else []
    argv = argv[:argv.index('--')] if '--' in argv else argv
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('law')
    p = sub.add_parser('replay')
    p.add_argument('run')
    p.add_argument('input')
    p.add_argument('out')
    p.add_argument('--thresholds', default='')
    p = sub.add_parser('roc')
    p.add_argument('run')
    p.add_argument('--corpus', default=None)
    sub.add_parser('budget')
    sub.add_parser('v9cov')
    p = sub.add_parser('kit')
    p.add_argument('stamp')
    p.add_argument('jobs', nargs='+')
    a = ap.parse_args(argv)
    if a.cmd == 'kit':
        return cmd_kit(a, extra)
    return {'law': cmd_law, 'replay': cmd_replay, 'roc': cmd_roc, 'budget': cmd_budget, 'v9cov': cmd_v9cov}[a.cmd](a)


if __name__ == '__main__':
    main()
