import gzip
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'submission'))
sys.path.insert(0, str(ROOT / 'tools'))

from pps_c import catalog, j1, judge, record, switches  # noqa: E402

MAIN = Path('D:/repos/dacon')
DATA = MAIN / 'data_open'
DEV_JOURNAL = MAIN / 'runs/rebuild_c/gpu/mix_C2/dev'
REPLICA_JOURNAL = MAIN / 'runs/rebuild_c/gpu/mix_C2/replica'
P3A_DEV = MAIN / 'runs/rebuild_c/transfer_20260925/dev_preds/dev_P3a_size_private.csv'
P3A_REPLICA = MAIN / 'runs/rebuild_c/lb_replays/P3a_replica.csv'
needs_dev = pytest.mark.skipif(not (DATA / 'dev.jsonl.gz').is_file() or not DEV_JOURNAL.is_dir(), reason='dev data absent')


def tools():
    import j1 as t
    return t


def dev_bundle(k=7):
    t = tools()
    cat = catalog.load(str(DATA / 'data'))
    jr = t.load_journal(DEV_JOURNAL)
    rec = next(r for n, r in enumerate(record.iter_records(DATA / 'dev.jsonl.gz')) if n == k)
    return t.rebuild(rec, cat, jr.get(rec['id'], []))


def test_default_is_off():
    assert switches.J1_THRESHOLDS == {}
    assert set(j1.TARGETS) == {'v9', 'v1'}


@pytest.mark.skipif(not (DATA / 'data/법령패키지/법령').is_dir(), reason='law package absent')
def test_law_excerpts_are_verbatim():
    for item in j1.TARGETS:
        assert j1.law_excerpts(item)
        for x in j1.law_excerpts(item):
            assert x['text'] in (DATA / 'data/법령패키지/법령' / x['source']).read_text(encoding='utf-8')


@needs_dev
def test_prompt_assembly():
    b = dev_bundle()
    v = judge.judge(b)
    items = j1.wanted(b, v, 'record', {})
    assert items
    for item in items:
        lines = j1.candidates(b, item)
        assert 0 < len(lines) <= j1.MAX_LINES[item]
        system, user = j1.messages(b, item, lines)
        assert system['role'] == 'system' and '숫자 한 글자' in system['content']
        text = user['content']
        assert text.startswith(f'[항목] {item} {j1.ITEM_ROWS[item]["항목명"]}')
        assert j1.DEFINITION[item] in text and j1.NEEDS[item] in text
        assert all(x['text'].replace(']]>', '') in text for x in j1.law_excerpts(item))
        assert f'추정가격: {j1.won(b.meta.P)}' in text
        assert all(f'L{ln.i}:' in text for ln in lines)
        assert text.rstrip().endswith('숫자 한 글자만 답한다.')      # the question closes the prompt


@needs_dev
def test_wanted_modes():
    b = dev_bundle()
    v = judge.judge(b)
    rec = j1.wanted(b, v, 'record', {})
    assert rec == [it for it in j1.TARGETS if j1.candidates(b, it)]
    assert j1.wanted(b, v, 'apply', {}) == []
    fired = dict(v)
    fired['v1'] = (1, 'x')
    assert 'v1' not in j1.wanted(b, fired, 'apply', {'v1': 0.9, 'v9': 0.9})


def test_answer_probability():
    pos = [('1', [('1', -0.1), (' 1', -0.05), ('0', -2.5), ('**', -4.0)])]
    a = j1.answer_probability(pos)
    assert a['pos'] == 0 and not a['bound']
    assert a['lp1'] == -0.05 and a['lp0'] == -2.5          # best logprob per digit
    assert abs(a['p1'] - 1 / (1 + pytest.importorskip('math').exp(-2.5 + 0.05))) < 1e-9
    one = j1.answer_probability([('0', [('0', -0.01), ('A', -6.0), ('B', -9.0)])])
    assert one['bound'] and one['lp1'] == -9.0 and one['p1'] < 0.001    # '1' outside the top-K: bounded by the last
    none = j1.answer_probability([('답', [('답', -0.2), ('**', -1.9)])])
    assert none['p1'] is None and none['pos'] is None
    later = j1.answer_probability([('\n', [('\n', -0.3), ('**', -1.5)]), ('1', [('1', -0.2), ('0', -1.8)])])
    assert later['pos'] == 1 and later['p1'] > 0.5
    think = [('<|channel>', [('<|channel>', -0.1)]), ('0', [('0', -0.1), ('1', -3.0)]), ('<channel|>', [('<channel|>', -0.1)]),
             ('1', [('1', -0.4), ('0', -1.2)])]
    t = j1.answer_probability(think, after_reasoning=True)
    assert t['pos'] == 3 and t['p1'] > 0.5
    assert j1.answer_probability(think[:2], after_reasoning=True)['p1'] is None


@needs_dev
def test_apply_or():
    b = dev_bundle()
    v = judge.judge(b)
    item = next(it for it in j1.wanted(b, v, 'record', {}) if v[it][0] == 0)
    lines = j1.candidates(b, item)
    rec = {'lines': [ln.i for ln in lines], 'p1': 0.97}
    got = j1.apply(b, v, {item: rec}, {item: 0.95})
    assert got[item] == (1, judge.evidence(lines[0].text))
    assert j1.apply(b, v, {item: rec}, {item: 0.99}) == v
    assert j1.apply(b, v, {item: dict(rec, p1=None)}, {item: 0.5}) == v
    assert j1.apply(b, v, {}, {item: 0.5}) == v
    on = dict(v)
    on[item] = (1, 'cpu line')
    assert j1.apply(b, on, {item: dict(rec, p1=0.0)}, {item: 0.5})[item] == (1, 'cpu line')   # OR never removes


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


@needs_dev
def test_threshold_replay(tmp_path):
    """tools/j1.py replay applies thresholds from j1.jsonl.gz and changes exactly the recorded cells."""
    t = tools()
    run = tmp_path / 'run'
    run.mkdir()
    shutil.copy(DEV_JOURNAL / 'journal.jsonl.gz', run / 'journal.jsonl.gz')
    base = t.replay(run, DATA / 'dev.jsonl.gz')
    picks = [(rec['id'], it) for rec, v in base for it in j1.TARGETS if v[it][0] == 0][:3]
    cat = catalog.load(str(DATA / 'data'))
    jr = t.load_journal(run)
    recs = {r['id']: r for r, _ in base}
    rows = []
    for i, it in picks:
        b = t.rebuild(recs[i], cat, jr.get(i, []))
        rows.append({'id': i, 'item': it, 'cpu': 0, 'lines': [ln.i for ln in j1.candidates(b, it)], 'p1': 0.99})
    with gzip.open(run / 'j1.jsonl.gz', 'wt', encoding='utf-8') as fh:
        for r in rows:
            fh.write(json.dumps(r) + '\n')
    th = {'v9': 0.9, 'v1': 0.9}
    got = t.replay(run, DATA / 'dev.jsonl.gz', th)
    changed = {(rec['id'], it) for (rec, v0), (_, v1) in zip(base, got) for it in v0 if v0[it] != v1[it]}
    assert len(picks) == 3 and changed == set(picks)
    assert all(v1[it][0] == 1 for (rec, v1) in got for i, it in picks if rec['id'] == i)


@needs_dev
def test_j1_off_identity_dev(tmp_path):
    """Thresholds off: the dev replay of C2's journal with this code equals P3a's dev replay byte for byte."""
    if not P3A_DEV.is_file():
        pytest.skip('P3a dev replay absent')
    t = tools()
    out = tmp_path / 'dev.csv'
    t.write_rows(t.replay(DEV_JOURNAL, DATA / 'dev.jsonl.gz'), out)
    assert sha(out) == sha(P3A_DEV)


@pytest.mark.skipif(os.environ.get('PPS_SLOW') != '1' or not REPLICA_JOURNAL.is_dir() or not P3A_REPLICA.is_file(),
                    reason='replica replay (a few minutes): set PPS_SLOW=1')
def test_j1_off_identity_replica(tmp_path):
    t = tools()
    out = tmp_path / 'replica.csv'
    t.write_rows(t.replay(REPLICA_JOURNAL, MAIN / 'runs/replica_20260922/set_v2_1853/replica_input.jsonl.gz'), out)
    assert sha(out) == sha(P3A_REPLICA)
