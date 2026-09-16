"""Freeze a source-matched SW relation-format contrast on exposed review cases."""
import argparse
import dataclasses
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.prepare_retrieval_contrast import read_rows, write_rows
from tools.compare_notice_facets import digest
from submission.b4_entry import digest as value_digest
from submission.pps.data import records
from submission.pps.knowledge import Knowledge
from submission.pps.prompts import Config, build_prompt, output_schema
from submission.pps.notice_search import merge_ranges
from submission.runtime import source_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--retrieval', type=Path, required=True)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--control-format', choices=['factored', 'software_facts'], default='factored')
    parser.add_argument('--candidate-format', choices=['software_facts', 'software_refs'], default='software_facts')
    args = parser.parse_args()
    if args.control_format == args.candidate_format:
        parser.error('Control and candidate formats must differ')
    prior = json.loads((args.retrieval/'preregistered.json').read_text(encoding='utf-8'))
    assert digest(args.input) == prior['input_sha256']
    assert digest(ROOT/'models/gemma-tokenizer/tokenizer.json') == prior['tokenizer_sha256']
    cases = [c for c in prior['cases'] if c['items'] == [20]]
    wanted = {c['record_id'] for c in cases}
    recs = {r['id']: r for r in records(args.input) if r['id'] in wanted}
    arms = ('A_current', 'A_lexical_context', 'C_hybrid_context')
    sources = {(r['measurement']['task_id'], r['measurement']['arm']): r['retrieval']
        for r in read_rows(args.retrieval/'retrieval_results.jsonl.gz') if r['measurement']['budget'] == 4096}
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(ROOT/'models/gemma-tokenizer', local_files_only=True, trust_remote_code=False)
    knowledge = Knowledge(ROOT/'data_open/data')
    base = dataclasses.replace(Config.load(ROOT/'submission/model/config.json'),
        shared_prefix=False, sme_facts=False, product_facts=False, cross_source_facts=False,
        response_format='factored', thinking_items=(), thinking_token_budget=0)
    packets = []
    for ci, case in enumerate(cases):
        rec = recs[case['record_id']]
        for ai, arm in enumerate(arms[ci % 3:] + arms[:ci % 3]):
            controls = []
            formats = (args.control_format, args.candidate_format)
            if (ci+ai) % 2:
                formats = formats[::-1]
            for fmt in formats:
                config = dataclasses.replace(base, response_format=fmt)
                selection = sources[(case['task_id'], arm)]
                prompt = build_prompt(rec, knowledge, config, tokenizer, (20,), source_selection=selection)
                spans = [dataclasses.asdict(s) for s in prompt['spans']]
                controls.append(merge_ranges([(s['doc_index'], s['start'], s['end']) for s in spans], rec['docs']))
                packets.append({'request_key': f"software:{case['task_id']}:{arm}:{fmt}",
                    'record_id': rec['id'], 'case': case['task_id'], 'arm': arm+':'+fmt, 'family': 'L',
                    'items': [20], 'messages': prompt['messages'], 'token_ids': prompt['token_ids'],
                    'prompt_sha256': value_digest(prompt['messages']), 'token_ids_sha256': value_digest(prompt['token_ids']),
                    'source_sha256': value_digest(spans), 'spans': spans, 'source_search': selection,
                    'source_unitization': prompt.get('source_unitization'),
                    'comparison_facts': None, 'coverage': prompt['coverage'],
                    'generation': {'response_format': fmt, 'thinking_budget': 0, 'max_output_tokens': 2048},
                    'schema_sha256': value_digest(output_schema(fmt, len(spans), (20,)))})
            assert controls[0] == controls[1]
    assert len(packets) == 36 and len(recs) == 6
    args.output.mkdir(parents=True, exist_ok=False)
    write_rows(args.output/'contrast_packets.jsonl.gz', packets)
    write_rows(args.output/'contrast_inputs.jsonl.gz', list(recs.values()))
    freeze = {'source_sha256': source_manifest(), 'config': dataclasses.asdict(base),
        'packets_sha256': digest(args.output/'contrast_packets.jsonl.gz'),
        'inputs_sha256': digest(args.output/'contrast_inputs.jsonl.gz'),
        'call_plan': [{'number': n//18, 'request_keys': [p['request_key'] for p in packets[n:n+18]]}
                      for n in range(0, len(packets), 18)],
        'cases': len(cases), 'notices': len(recs), 'primary_requests': len(packets),
        'kind': 'fresh_software_relation_contract_diagnostic', 'classification_labels_read': False,
        'source_token_budget': 4096, 'source_selection_unchanged_between_formats': True,
        'formats': [args.control_format, args.candidate_format],
        'unit_boundaries_may_differ': args.candidate_format == 'software_refs',
        'control': 'Same original source, metadata, factual hints and fixed Gemma. Output task/schema and typed-v20 consumer are the independent variable.',
        'reading_limit': 'Partial typed applicability is not full-source absence. Unknown decisions and deterministic full-source rules are recorded separately.',
        'new_full160_score': False, 'official_macro_f1': None,
        'input_tokens_total': sum(len(p['token_ids']) for p in packets),
        'input_tokens_max': max(len(p['token_ids']) for p in packets),
        'per_format': {fmt: {'input_tokens': sum(len(p['token_ids']) for p in packets if p['generation']['response_format'] == fmt),
                            'source_units': sum(len(p['spans']) for p in packets if p['generation']['response_format'] == fmt)}
                       for fmt in (args.control_format, args.candidate_format)}}
    (args.output/'contrast_freeze.json').write_text(json.dumps(freeze, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k:v for k,v in freeze.items() if k not in {'source_sha256','config','call_plan'}}, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
