"""Compare source-addressed supply candidates at each actual A source budget.

The candidate count is a syntactic discovery measure. Neither a printed quantity
nor retrieval similarity certifies a unique product, purchase role or violation.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import gzip
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.pps.notice_search import NoticeSearch, factual_queries, merge_ranges
from submission.pps.qualification import qualification_facts, inventory
from submission.pps.specification_candidate_review import NAME, schema
from submission.runtime import Journal, source_manifest, sha256
from tools.audit_named_supply_lists import candidates
from tools.audit_specification_candidates import unknown_response
from tools.source_coverage import covered


def observations(record, search):
    found = []
    for di, doc in enumerate(record['docs']):
        for value in candidates(doc['text']):
            source = {'doc_index': di, **value['source']}
            context = search._context(SimpleNamespace(**source))
            found.append({**value, 'doc_index': di, 'context': context})
    return found


def choose_reserve(search, values, cap):
    """Bounded coverage per marginal token, never truncate a source witness.

    Every distinct occurrence remains in the reported inventory. Oversized
    contexts are explicitly left unread, rather than being treated as absent.
    """
    selected, pending = [], list(range(len(values)))
    while pending:
        proposals = []
        base = search.token_cost(selected)
        for index in pending:
            proposed = merge_ranges([*selected, *values[index]['context']], search.rec['docs'])
            cost = search.token_cost(proposed)
            if cost <= cap:
                gained = sum(all(any(di == v['doc_index'] and lo <= a and b <= hi
                    for di, lo, hi in proposed) for _, a, b in v['context'])
                    and not all(any(di == v['doc_index'] and lo <= a and b <= hi
                    for di, lo, hi in selected) for _, a, b in v['context']) for v in values)
                proposals.append((gained / max(1, cost-base), -cost, -index, proposed))
        if not proposals:
            break
        best = max(proposals, key=lambda p: p[:3])
        selected = best[3]
        pending.remove(-best[2])
    return selected


def reading(record, result, values, review):
    def hit(di, ref):
        return covered(record, result['spans'], {'doc_index': di, **ref})
    found = []
    for value in values:
        di = value['doc_index']
        found.append({'doc_index': di, 'source': value['source'],
            'candidate_read': hit(di, value['source']),
            'context_read': all(hit(d, {'start': lo, 'end': hi}) for d, lo, hi in value['context']),
            'decision_bundle_certified': False})
    sections = qualification_facts(record, inventory(record))['eligibility_sections']
    human = []
    for case in review.get('cases', []):
        if case['record_id'] != record['id'] or case['items'] == [20]:
            continue  # This tool changes only A source; L is an unchanged control.
        hits = {}
        for ref in case['required_bundle']:
            import hashlib
            doc = record['docs'][ref['doc_index']]
            if hashlib.sha256(doc['text'].encode()).hexdigest() != ref['doc_sha256']:
                raise ValueError('Reviewed original document changed')
            hits[ref['evidence_id']] = hit(ref['doc_index'], ref)
        human.append({'case': case['task_id'], 'related_sentence': any(hits[ref['evidence_id']]
            for ref in case['required_bundle'] if ref['role'] == 'support'),
            'known_bundle': bool(hits) and all(hits.values()),
            'resolved_bundle': case['resolution'] == 'resolved' and bool(hits) and all(hits.values())})
    return {'candidate_observations': found, 'human': human,
        'eligibility_sections': len(sections), 'eligibility_read': sum(covered(record, result['spans'], s['evidence']) for s in sections),
        'absence_verified': False}


def capacity(tokenizer, count):
    """A valid all-unknown witness, not a maximum-length guarantee."""
    import jsonschema
    plan = {'unit_count': 1, 'candidates': [{'key': f'C{i}', 'value_source': {'text': 'observed'}} for i in range(1, count+1)]}
    answer = unknown_response(plan)
    for value in answer[NAME].values():
        value.update(permission_attribute='unknown', permission_effect='unknown')
    answer.update(unresolved='미확인', judgment={'reason': '미확인', 'v': 0, 'e': 0})
    jsonschema.validate(answer, schema(1, plan=plan))
    raw = json.dumps(answer, ensure_ascii=False, separators=(',', ':'))
    short = {'C': {key: list(value.values()) for key, value in answer[NAME].items()},
             'unresolved': answer['unresolved'], 'judgment': answer['judgment']}
    return {'candidates': count, 'valid_verbose_unknown_tokens': len(tokenizer.encode(raw, add_special_tokens=False)),
        'positional_design_only_tokens': len(tokenizer.encode(json.dumps(short, ensure_ascii=False, separators=(',', ':')), add_special_tokens=False)),
        'positional_wire_implemented_or_validated': False, 'all_possible_answers_fit_certified': False}


def compare(prepared, review_path, output, *, dense=False):
    from transformers import AutoTokenizer
    journal = Journal(output)
    code, began = source_manifest(), time.monotonic()
    def rows(name):
        with gzip.open(prepared/name, 'rt', encoding='utf8') as stream:
            return list(map(json.loads, stream))
    records = rows('current_inputs.jsonl.gz')
    packets = {p['record_id']: p for p in rows('primary_packets.jsonl.gz') if p['batch'] == 'A1'}
    tokenizer = AutoTokenizer.from_pretrained(ROOT/'models/gemma-tokenizer', local_files_only=True, trust_remote_code=False)
    encoder = None
    if dense:
        from submission.pps.embeddings import BGEDenseEncoder
        encoder = BGEDenseEncoder(ROOT/'models/bge-m3')
    review = json.loads(review_path.read_text(encoding='utf8'))
    journal.save('preregistered.json', {'source_sha256': code, 'tool_sha256': sha256(__file__),
        'discovery_tool_sha256': sha256(ROOT/'tools/audit_named_supply_lists.py'),
        'input_sha256': sha256(prepared/'current_inputs.jsonl.gz'), 'review_manifest_sha256': sha256(review_path),
        'source_budget': 'same notice original A raw-source tokens; no other token subsidy',
        'cohort': 'all160; source changes only for observed alphanumeric counted supply syntax',
        'arms': ['canonical_A', 'factual_all', 'supply_group_all', 'supply_group_v9', 'supply_reserve_half_all', 'supply_reserve_v9'],
        'selection_policy': 'evidence_cover', 'method': 'hybrid' if dense else 'lexical',
        'reserve_selection': 'deterministic newly covered syntax contexts per marginal raw token; no partial witness',
        'classification_labels_read': False, 'new_model_calls': 0, 'GPU_used': False})
    results, capacities = [], []
    for record in records:
        search = NoticeSearch(record, tokenizer, encoder)
        values = observations(record, search)
        packet = packets[record['id']]
        cap = sum(len(tokenizer.encode(s['text'], add_special_tokens=False)) for s in packet['spans'])
        original = {'record_id': record['id'], 'arm': 'canonical_A', 'spans': packet['spans'], 'source_tokens': cap, 'token_budget': cap}
        original['reading'] = reading(record, original, values, review)
        results.append(original)
        if not values:
            continue
        capacities.append({'record_id': record['id'], **capacity(tokenizer, len(values))})
        names = tuple(dict.fromkeys(v['value_source']['text'] for v in values))
        groups = {'specification': factual_queries((9,)),
                  'counted_source_names': tuple(n+' 실제 구매 대상과 구성품, 기존 장비의 관계 및 대체품 허용 조건' for n in names)}
        all_groups = {'other_conditions': factual_queries(tuple(range(1,9))+tuple(range(10,25))), **groups}
        for arm, items, queries, group, required in (
            ('factual_all', tuple(range(1,25)), factual_queries(range(1,25)), None, ()),
            ('supply_group_all', tuple(range(1,25)), None, all_groups, ()),
            ('supply_group_v9', (9,), None, groups, ()),
            ('supply_reserve_half_all', tuple(range(1,25)), None, all_groups, choose_reserve(search, values, cap//2)),
            ('supply_reserve_v9', (9,), None, groups, choose_reserve(search, values, cap))):
            result = search.search(items, token_budget=cap, method='hybrid' if dense else 'lexical',
                queries=queries, query_groups=group, required_ranges=required, selection_policy='evidence_cover')
            result.update(record_id=record['id'], arm=arm)
            for span in result['spans']:
                assert record['docs'][span['doc_index']]['text'][span['start']:span['end']] == span['text']
            assert result['source_tokens'] <= cap
            result['reading'] = reading(record, result, values, review)
            results.append(result)
        print(json.dumps({'record_id': record['id'], 'candidates': len(values), 'records_finished': len(capacities)}), flush=True)
    if code != source_manifest():
        raise ValueError('Runtime source changed during comparison')
    journal.rows('results.jsonl.gz', results)
    measurements = []
    totals = defaultdict(lambda: defaultdict(int))
    for result in results:
        reading_ = result['reading']; values = reading_['candidate_observations']
        row = {'record_id': result['record_id'], 'arm': result['arm'], 'source_tokens': result['source_tokens'],
            'candidates': len(values), 'candidate_read': sum(v['candidate_read'] for v in values),
            'syntax_context_read': sum(v['context_read'] for v in values),
            'eligibility_sections': reading_['eligibility_sections'], 'eligibility_read': reading_['eligibility_read'],
            'human_cases': len(reading_['human']), 'human_related_sentence': sum(h['related_sentence'] for h in reading_['human']),
            'human_known_bundle': sum(h['known_bundle'] for h in reading_['human']),
            'human_resolved_bundle': sum(h['resolved_bundle'] for h in reading_['human'])}
        measurements.append(row)
        if values:
            for key, value in row.items():
                if isinstance(value, int):
                    totals[result['arm']][key] += value
    report = {'records': len(records), 'notices_with_candidates': len(capacities),
        'changed_cohort_measurements': dict(totals), 'measurements': measurements, 'response_capacity': capacities,
        'seconds': time.monotonic()-began, 'source_changed': False, 'classification_labels_read': False,
        'new_model_calls': 0, 'scope': 'Syntax context and human anchors separate. No unique-name, legal decision, full absence coverage or F1 certification.'}
    journal.save('report.json', report)
    return {k: v for k, v in report.items() if k not in {'measurements', 'response_capacity'}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', type=Path, required=True)
    parser.add_argument('--review-manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--dense', action='store_true')
    args = parser.parse_args()
    print(json.dumps(compare(args.prepared, args.review_manifest, args.output, dense=args.dense), ensure_ascii=False, indent=2))
