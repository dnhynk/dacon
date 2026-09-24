"""Score every frozen whole-cohort policy and its fixed paired contrasts.

Prediction and reference identities are checked before opening exposed labels.
Incomplete policies remain in the report and never become smaller-cohort scores.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.pps.data import read_csv
from tools.evaluate import compare, evaluate


def read(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def save(path, value):
    with path.open('x', encoding='utf8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def validate(consumed, prepared, baseline_manifest):
    frozen = read(consumed / 'prediction_freeze.json')
    expected = read(prepared / 'input_freeze.json')
    baseline = read(baseline_manifest)
    if (frozen['prepared_freeze_sha256'] != sha(prepared / 'input_freeze.json')
            or (frozen.get('prepared_source_sha256',frozen['source_sha256']) != expected['source_sha256'])
            or (not frozen.get('current_cpu_replay') and frozen['source_sha256'] != expected['source_sha256'])
            or frozen['applied_recipes_sha256'] != sha(consumed / 'applied_recipes.json')
            or frozen['labels_read']
            or bool(frozen['saved_old_model_responses_used']) != bool(frozen.get('current_cpu_replay'))):
        raise ValueError('Not the declared frozen fresh/replay prediction population')
    if sha(prepared / 'current_inputs.jsonl.gz') != expected['files']['current_inputs.jsonl.gz']:
        raise ValueError('Original cohort changed')
    with gzip.open(prepared / 'current_inputs.jsonl.gz', 'rt', encoding='utf8') as stream:
        ids = [json.loads(line)['id'] for line in stream]
    if len(ids) != expected['records'] or len(set(ids)) != len(ids):
        raise ValueError('Incomplete original cohort')
    policies = read(prepared / 'preregistered.json')['source_policies']
    names = {policy + suffix for policy in policies for suffix in (
        '', '+law', '+v9', '+law+v9', '+gated_v9', '+law+gated_v9', '+no_L', '+law+no_L')}
    if set(frozen['predictions']) != names:
        raise ValueError('A predeclared policy is missing or a new policy was added')
    predictions, incomplete = {}, {}
    def check_csv(path, digest):
        if sha(path) != digest:
            raise ValueError('Frozen CSV changed: ' + str(path))
        actual = [row['id'] for row in read_csv(path)]
        if len(actual) != len(ids) or len(set(actual)) != len(actual) or set(actual) != set(ids):
            raise ValueError('Predictions must contain the entire original cohort')
    for name, entry in frozen['predictions'].items():
        if entry['status'] == 'complete':
            if Path(entry['path']).name != entry['path'] or entry['records'] != len(ids):
                raise ValueError('Invalid complete prediction entry')
            path = consumed / entry['path']
            check_csv(path, entry['sha256'])
            predictions[name] = path
        elif entry['status'] == 'incomplete_no_csv':
            if not entry['unresolved_logical_keys'] or (consumed / (name + '.csv')).exists():
                raise ValueError('Incomplete policy has an unexplained or partial CSV')
            incomplete[name] = entry
        else:
            raise ValueError('Unrecognized policy completion state')
    references = {}
    for reference in baseline['references']:
        path = ROOT / reference['prediction']
        if reference['id'] in references:
            raise ValueError('Duplicate declared reference')
        check_csv(path, reference['sha256'])
        references[reference['id']] = path
    return frozen, baseline, policies, predictions, incomplete, references


def compact(metric):
    return dict(macro_f1=metric['macro_f1'], records=metric['records'],
                fp=sum(item['fp'] for item in metric['per_item'].values()),
                fn=sum(item['fn'] for item in metric['per_item'].values()))


def score(consumed, prepared, baseline_manifest, labels, output):
    if output.exists():
        raise ValueError('Preserve prior scores; choose a new output directory')
    frozen, baseline, policies, predictions, incomplete, references = validate(
        consumed, prepared, baseline_manifest)
    # The first label access is after all complete CSVs and all omissions passed.
    if sha(labels) != baseline['exposed_development_labels_sha256']:
        raise ValueError('Only the declared exposed development labels are allowed')
    output.mkdir(parents=True, exist_ok=False)
    scores, summaries = {}, {}
    for name, path in predictions.items():
        result = compare(labels, path, list(references.values()))
        result.update(measurement=frozen['measurement'],
                      deployment_call_order_verified=False, official_score=None,
                      exposed_development_only=True, old_response_join=False,
                      current_cpu_replay=frozen.get('current_cpu_replay',False))
        save(output / (name + '_metrics.json'), result)
        scores[name] = result['candidate']
        summaries[name] = dict(**compact(result['candidate']),
            prediction=str(path), prediction_sha256=sha(path),
            derived_conditional_execution=frozen['predictions'][name]['derived_conditional_execution'],
            three_call_B3_control=frozen['predictions'][name].get('three_call_B3_control',False),
            logical_calls=frozen['predictions'][name]['logical_calls'],
            unique_native_calls=frozen['predictions'][name]['unique_native_calls'],
            references={rid: {k: v for k, v in result['comparisons'][str(ref)].items()
                              if k not in ('changes', 'per_item_delta')}
                        for rid, ref in references.items()})
    edges = set()
    for policy in policies:
        for suffix in ('', '+law', '+v9', '+law+v9', '+gated_v9', '+law+gated_v9'):
            if policy != 'current':
                edges.add(('source', 'current' + suffix, policy + suffix))
        for law in ('', '+law'):
            edges.add(('fourth_L_call', policy + law + '+no_L', policy + law))
            for review in ('+v9', '+gated_v9'):
                edges.add(('review', policy + law, policy + law + review))
        for review in ('', '+v9', '+gated_v9'):
            edges.add(('law', policy + review, policy + '+law' + review))
    contrasts = []
    for intervention, control, candidate in sorted(edges):
        if control not in predictions or candidate not in predictions:
            contrasts.append(dict(intervention=intervention, control=control,
                candidate=candidate, status='incomplete_no_contrast'))
            continue
        delta = compare(labels, predictions[candidate], [predictions[control]])['comparisons'][str(predictions[control])]
        contrasts.append(dict(intervention=intervention, control=control,
            candidate=candidate, status='complete', **delta))
    save(output / 'paired_contrasts.json', contrasts)
    report = dict(kind=('saved_response_current_cpu_policy_replay' if frozen.get('current_cpu_replay')
                       else 'whole_cohort_fresh_prepared_input_policy_comparison'),
        consumed_freeze_sha256=sha(consumed / 'prediction_freeze.json'),
        baseline_manifest_sha256=sha(baseline_manifest),
        source_sha256=frozen['source_sha256'],
        prepared_source_sha256=frozen.get('prepared_source_sha256',frozen['source_sha256']),
        current_cpu_replay=frozen.get('current_cpu_replay',False),
        labels_sha256=sha(labels), measurements=summaries, incomplete_policies=incomplete,
        all_declared_combinations_complete=not incomplete,
        references={rid: dict(**compact(evaluate(labels, path)), path=str(path))
                    for rid, path in references.items()},
        no_per_notice_or_per_item_policy_selection=True,
        deployment_call_order_verified=False, official_score=None,
        limitations='Exposed development160; all complete policies reported, incomplete policies unscored. '
            'Fixed shared-engine comparison with same-notice exact-input aliases. '
            'Derived gating is not an executed adaptive deployment. '
            'Selection among these development policies is not held-out validation.')
    save(output / 'report.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('consumed', 'prepared', 'baseline-manifest', 'labels', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    report = score(args.consumed, args.prepared, args.baseline_manifest, args.labels, args.output)
    print(json.dumps({'measurements': {k: {x: v[x] for x in ('macro_f1', 'fp', 'fn')}
                         for k, v in report['measurements'].items()},
                      'incomplete_policies': list(report['incomplete_policies'])}, indent=2))
