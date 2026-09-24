"""Score all preregistered policies after normal execution and native recovery.

Only the actual normal run is a standalone inference measurement. The other
policies are diagnostic joins of fresh responses from the same frozen round.
All prediction and baseline identities are checked before exposed labels open.
"""
from __future__ import annotations

import argparse
import gzip
import itertools
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.pps.data import read_csv
from tools.evaluate import compare, evaluate
from tools.score_integrated_comparison import read, sha, save, compact


def validate(consumed, prepared, baseline_manifest, *, cpu_replay=False):
    frozen = read(consumed / 'prediction_freeze.json')
    expected = read(prepared / 'input_freeze.json')
    baseline = read(baseline_manifest)
    registration = read(prepared / 'preregistered.json')
    for name, digest in expected['files'].items():
        if Path(name).name != name or sha(prepared / name) != digest:
            raise ValueError('Prepared input identity changed: ' + name)
    normal = registration['normal_policy']
    source_valid = (frozen.get('kind') == 'saved_response_current_cpu_replay'
        and frozen.get('generation_source_sha256') == expected['source_sha256']
        and frozen.get('new_model_calls') == 0) if cpu_replay else frozen['source_sha256'] == expected['source_sha256']
    if (frozen['prepared_sha256'] != sha(prepared / 'input_freeze.json')
            or not source_valid
            or frozen['recipes_sha256'] != sha(prepared / 'recipes.json')
            or baseline['recipes_sha256'] != frozen['recipes_sha256']
            or baseline['normal_policy'] != normal
            or frozen['labels_read'] is not False
            or (not cpu_replay and frozen['normal_reproduced_exactly'] is not True)
            or frozen['source_and_cpu_frozen_before_generation'] is not (not cpu_replay)
            or frozen['only_normal_policy_is_standalone_execution'] is not (not cpu_replay)):
        raise ValueError('Not the frozen normal/probe prediction population')
    declared = read(prepared / 'recipes.json')
    if set(frozen['predictions']) != set(declared) or normal not in declared:
        raise ValueError('A predeclared policy was removed or added')
    with gzip.open(prepared / 'current_inputs.jsonl.gz', 'rt', encoding='utf8') as stream:
        ids = [json.loads(line)['id'] for line in stream]
    if len(ids) != expected['records'] or len(set(ids)) != len(ids) or not ids:
        raise ValueError('Incomplete or duplicated original cohort')

    def check_csv(path, digest):
        if sha(path) != digest:
            raise ValueError('Frozen CSV changed: ' + str(path))
        observed = [row['id'] for row in read_csv(path)]
        if len(observed) != len(ids) or len(set(observed)) != len(observed) or set(observed) != set(ids):
            raise ValueError('Prediction must contain the entire original cohort')

    predictions, incomplete = {}, {}
    for name, entry in frozen['predictions'].items():
        if entry['status'] == 'complete':
            if (entry['path'] != name + '.csv' or Path(entry['path']).name != entry['path']
                    or entry['records'] != len(ids)
                    or entry['standalone_normal_execution'] is not (name == normal and not cpu_replay)):
                raise ValueError('Invalid complete policy entry')
            path = consumed / entry['path']
            check_csv(path, entry['sha256'])
            predictions[name] = path
        elif entry['status'] == 'incomplete_no_csv':
            if not entry['missing'] or (consumed / (name + '.csv')).exists() or name == normal:
                raise ValueError('Incomplete policy has unexplained omissions or a partial CSV')
            incomplete[name] = entry
        else:
            raise ValueError('Unknown completion state')
    if not cpu_replay and sha(predictions[normal]) != frozen['normal_prediction_sha256']:
        raise ValueError('Actual normal execution output differs from replay')
    references = {}
    for reference in baseline['references']:
        if reference['id'] in references:
            raise ValueError('Duplicate reference')
        path = ROOT / reference['prediction']
        check_csv(path, reference['sha256'])
        references[reference['id']] = path
    return frozen, baseline, normal, predictions, incomplete, references


def contrasts(names):
    """Global one-setting interventions, never an item-dependent policy."""
    edges = set()
    def name(thinking, catalog, software, v9):
        return f'thinking{thinking}_catalog_{catalog}_software_{software}_v9_{v9}'
    for thinking, catalog, software, v9 in itertools.product(
            (0, 768), ('none', 'control', 'explicit'), ('none', 'relations'), ('none', 'gated')):
        here = name(thinking, catalog, software, v9)
        if thinking == 768:
            edges.add(('thinking_budget_0', here, name(0, catalog, software, v9)))
        if catalog == 'explicit':
            for before in ('none', 'control'):
                edges.add(('catalog_' + before + '_to_explicit', name(thinking, before, software, v9), here))
        if software == 'relations':
            edges.add(('software_relations', name(thinking, catalog, 'none', v9), here))
        if v9 == 'gated':
            edges.add(('gated_specification_review', name(thinking, catalog, software, 'none'), here))
    for value in names:
        if value.endswith('_no_L'):
            edges.add(('fourth_L_call', value, value[:-5]))
    return sorted(e for e in edges if e[1] in names and e[2] in names)


def score(consumed, prepared, baseline_manifest, labels, output, *, cpu_replay=False):
    if output.exists():
        raise ValueError('Preserve old scores; choose a new directory')
    frozen, baseline, normal, predictions, incomplete, references = validate(consumed, prepared, baseline_manifest,cpu_replay=cpu_replay)
    # First access to labels occurs only after all complete and missing policies
    # and all comparison baselines have been checked together.
    if sha(labels) != baseline['exposed_development_labels_sha256']:
        raise ValueError('Only the declared exposed development labels are allowed')
    output.mkdir(parents=True, exist_ok=False)
    summaries = {}
    for name, path in predictions.items():
        result = compare(labels, path, list(references.values()))
        standalone = name == normal and not cpu_replay
        result.update(measurement=('saved_response_current_cpu_replay' if cpu_replay else
                                   'fresh_model_inference' if standalone else 'fresh_whole_cohort_policy_comparison'),
                      deployment_call_order_verified=standalone, official_score=None,
                      exposed_development_only=True, source_and_cpu_frozen_before_generation=not cpu_replay,
                      valid_response_rerolls=0, per_notice_or_item_policy_selection=False)
        save(output / (name + '_metrics.json'), result)
        summaries[name] = dict(**compact(result['candidate']), prediction=str(path), prediction_sha256=sha(path),
            standalone_normal_execution=standalone,
            executed_calls=frozen['predictions'][name]['executed_calls'],
            references={rid: {k: v for k, v in result['comparisons'][str(ref)].items()
                              if k not in ('changes', 'per_item_delta')} for rid, ref in references.items()})
    paired = []
    declared_edges=(read(prepared/'contrasts.json') if (prepared/'contrasts.json').is_file()
                    else contrasts(frozen['predictions']))
    for intervention, control, candidate in declared_edges:
        if control not in predictions or candidate not in predictions:
            paired.append(dict(intervention=intervention, control=control, candidate=candidate,
                               status='incomplete_no_contrast'))
            continue
        delta = compare(labels, predictions[candidate], [predictions[control]])['comparisons'][str(predictions[control])]
        paired.append(dict(intervention=intervention, control=control, candidate=candidate, status='complete', **delta))
    save(output / 'paired_contrasts.json', paired)
    report = dict(kind='saved_response_current_cpu_replay' if cpu_replay else 'normal_runtime_with_fresh_fixed_probes', normal_policy=normal,
        consumed_freeze_sha256=sha(consumed / 'prediction_freeze.json'),
        baseline_manifest_sha256=sha(baseline_manifest), source_sha256=frozen['source_sha256'],
        labels_sha256=sha(labels), measurements=summaries, incomplete_policies=incomplete,
        all_declared_combinations_complete=not incomplete,
        references={rid: dict(**compact(evaluate(labels, path)), path=str(path)) for rid, path in references.items()},
        only_normal_policy_is_standalone_execution=not cpu_replay, no_per_notice_or_per_item_policy_selection=True,
        official_score=None, limitations=('Current CPU on stored responses; no fresh inference effect. ' if cpu_replay else '') +
        'Exposed development160; selection among policies is not held-out validation. '
        'Only the preregistered normal policy ran end-to-end. Other policies join fresh responses from fixed later probes. '
        'Probe order and engine state can affect numerical nondeterminism, so thinking-budget contrasts are not a proof of that factor alone. '
        'A100 wall time does not certify L40S full evaluation cost.')
    save(output / 'report.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ('consumed', 'prepared', 'baseline-manifest', 'labels', 'output'):
        parser.add_argument('--' + field, type=Path, required=True)
    parser.add_argument('--cpu-replay', action='store_true')
    args = parser.parse_args()
    report = score(args.consumed, args.prepared, args.baseline_manifest, args.labels, args.output,cpu_replay=args.cpu_replay)
    print(json.dumps({name: {k: value[k] for k in ('macro_f1', 'fp', 'fn', 'standalone_normal_execution')}
                      for name, value in report['measurements'].items()}, indent=2))
