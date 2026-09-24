"""Compare source-role candidates and saved selections on CPU without labels."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import jsonschema
from submission.b4_entry import restored
from submission.pps import catalog_scope, catalog_semantics, catalog_source_roles
from submission.pps.retrieval import Span
from submission.runtime import source_manifest
from tools.run_retrieval_contrast import read_rows


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def old_witness_function(archive):
    """Only the frozen role functions; not a second active submission runtime."""
    with zipfile.ZipFile(archive) as z:
        products = z.read('submission/pps/products.py').decode('utf-8')
        scope = z.read('submission/pps/catalog_scope.py').decode('utf-8')
        identity = z.read('submission/pps/service_identity.py')
    if identity != (ROOT / 'submission/pps/service_identity.py').read_bytes():
        raise ValueError('Shared whole-contract scanner differs; isolate the full historical dependency first')
    namespace = {'__name__': 'frozen_products_role_audit'}
    exec(compile(products, str(archive) + ':products.py', 'exec'), namespace)
    functions = [node for node in ast.parse(scope).body
                 if isinstance(node, ast.FunctionDef) and node.name == 'whole_task_witnesses']
    if len(functions) != 1:
        raise ValueError('Historical task witness function unavailable')
    namespace['whole_contract_scope'] = catalog_scope.whole_contract_scope
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(archive) + ':whole_task_witnesses', 'exec'), namespace)
    return namespace['whole_task_witnesses']


def audit(input_path, archive, packets_path, responses_path, output):
    if output.exists():
        raise FileExistsError('Preserve completed audits; choose a new output directory')
    inputs = read_rows(input_path)
    records = {r['id']: r for r in inputs}
    if len(records) != len(inputs):
        raise ValueError('Duplicate notice IDs')
    previous = old_witness_function(archive)
    changes = []
    counts = {'before': 0, 'after': 0, 'removed': 0, 'added': 0}
    for rec in inputs:
        spans = [Span(i, d['type'], 0, len(d['text']), d['text']) for i, d in enumerate(rec['docs']) if d['text']]
        refs = list(range(1, len(spans) + 1))
        before, after = previous(rec, spans, refs), catalog_scope.whole_task_witnesses(rec, spans, refs)
        key = lambda e: (e['doc_index'], e['start'], e['end'])
        old, new = {key(e): e for e in before}, {key(e): e for e in after}
        removed, added = [old[k] for k in old.keys() - new.keys()], [new[k] for k in new.keys() - old.keys()]
        counts['before'] += len(old); counts['after'] += len(new)
        counts['removed'] += len(removed); counts['added'] += len(added)
        if removed or added:
            changes.append({'record_id': rec['id'], 'removed': sorted(removed, key=key), 'added': sorted(added, key=key)})
    packets = {p['request_key']: restored(p) for p in read_rows(packets_path)}
    selections = []
    for native in read_rows(responses_path):
        packet = packets[native['request_key']]
        if packet['generation']['response_format'] != catalog_semantics.FORMAT:
            continue
        rec = records[packet['record_id']]
        inventory = catalog_source_roles.build(rec, packet['spans'], packet['catalog_conditions']['plan'])
        schema = catalog_semantics.generation_schema(len(packet['spans']), source_roles=inventory['generation'])
        payload = json.loads(native['response']['text'])
        errors = list(jsonschema.Draft202012Validator(schema).iter_errors(payload))
        prior_valid = jsonschema.Draft202012Validator(catalog_semantics.generation_schema(
            len(packet['spans']))).is_valid(payload)
        selections.append({'record_id': rec['id'], 'request_key': packet['request_key'],
            'old_raw_object_meets_new_roles': not errors,
            'old_raw_object_meets_prior_boolean_mask': prior_valid,
            'rejected_channels': sorted({list(e.path)[0] for e in errors if e.path}),
            'inventory': inventory, 'old_raw_object_unchanged': payload,
            'new_model_calls': 0, 'semantic_truth_certified': False})
    summary = {'source_sha256': source_manifest(), 'records': len(inputs),
        'task_witnesses': counts, 'notices_with_changed_task_witnesses': len(changes),
        'saved_semantic_responses': len(selections),
        'saved_objects_outside_new_role_contract': sum(not s['old_raw_object_meets_new_roles'] for s in selections),
        'saved_objects_outside_prior_boolean_mask': sum(not s['old_raw_object_meets_prior_boolean_mask'] for s in selections),
        'additional_saved_objects_blocked_by_roles': sum(s['old_raw_object_meets_prior_boolean_mask']
            and not s['old_raw_object_meets_new_roles'] for s in selections),
        'labels_read': False, 'gpu_used': False, 'new_model_calls': 0,
        'input_sha256': {p.as_posix(): sha(p) for p in (input_path, archive, packets_path, responses_path)},
        'scope': 'Source-role inventory and existing JSON validity only. Not human gold, semantic accuracy or fresh inference.'}
    output.mkdir(parents=True, exist_ok=False)
    for name, value in [('summary.json', summary), ('task_changes.json', changes), ('saved_selections.json', selections)]:
        with (output / name).open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
    return {k: v for k, v in summary.items() if k not in {'source_sha256', 'input_sha256'}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('input', 'baseline-zip', 'packets', 'responses', 'output'):
        parser.add_argument('--' + key, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.input, args.baseline_zip, args.packets, args.responses, args.output), ensure_ascii=False))


if __name__ == '__main__':
    main()
