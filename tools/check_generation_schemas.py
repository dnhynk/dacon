"""Compile every prepared effective generation schema on CPU before GPU work."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.pps.generation_contract import preflight, prepared_preflight, progress_preflight, generation_schema, validate_grammar
from submission.runtime import source_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packets', type=Path)
    parser.add_argument('--tokenizer-dir', type=Path, help='Also verify actual token-mask progress on CPU')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Preserve existing evidence; choose a new output file')
    specifications = None
    if args.packets:
        with gzip.open(args.packets, 'rt', encoding='utf-8') as stream:
            packets = list(map(json.loads, stream))
        specifications = [(p['generation']['response_format'], len(p['spans']), tuple(p['items']),
                           p['generation'].get('schema_order')) for p in packets]
        if not packets:
            raise ValueError('No prepared generation requests')
    report = preflight([s for s in specifications if s[0] != 'specification_candidates'] if specifications is not None else None)
    if args.packets:
        report['prepared_effective_schemas'] = prepared_preflight(packets)
        report['source_role_schemas'] = []
        report['condition_field_schemas'] = []
        report['specification_candidate_schemas'] = []
        for packet in packets:
            roles = packet['generation'].get('catalog_roles')
            fields = packet['generation'].get('catalog_fields')
            inventory = packet['generation'].get('specification_inventory')
            if roles is None and fields is None and inventory is None:
                continue
            from submission.pps.catalog_field_contract import validate_prepared
            validate_prepared(packet)
            schema = generation_schema(packet['generation']['response_format'], len(packet['spans']),
                packet['items'], catalog_roles=roles, catalog_fields=fields, specification_inventory=inventory)
            validate_grammar(schema)
            entry = {'request_key': packet['request_key'],
                'generation_schema_sha256': hashlib.sha256(json.dumps(schema, ensure_ascii=False).encode()).hexdigest(),
                'status': 'PASS'}
            if roles is not None:
                report['source_role_schemas'].append(entry)
            if fields is not None:
                report['condition_field_schemas'].append(entry)
            if inventory is not None:
                report['specification_candidate_schemas'].append(entry)
    if args.tokenizer_dir:
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(args.tokenizer_dir, local_files_only=True, trust_remote_code=False)
        report['token_progress'] = progress_preflight(tokenizer)
        if any(case['format'] == 'catalog_conditions' for case in report['cases']):
            report['condition_token_progress'] = progress_preflight(tokenizer,
                response_format='catalog_conditions')
        if any(case['format'] == 'catalog_semantics' for case in report['cases']):
            report['semantic_token_progress'] = progress_preflight(tokenizer, response_format='catalog_semantics')
        orders = {case['schema_order'] for case in report['cases'] if case['schema_order'] is not None}
        report['ordered_token_progress'] = {order: progress_preflight(tokenizer, schema_order=order)
                                            for order in sorted(orders)}
    report['source_sha256'] = source_manifest()
    if args.packets:
        report.update(packets=args.packets.as_posix(), requests=len(packets),
            packets_sha256=hashlib.sha256(args.packets.read_bytes()).hexdigest())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in report.items() if k not in ('cases', 'source_sha256')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
