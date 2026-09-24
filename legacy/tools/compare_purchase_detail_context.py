"""Test literal summary/detail context links at the actual canonical source cap.

This is an offline source-selection comparison, not purchase interpretation.
Classification labels and prior predictions never participate in selection.
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.pps.notice_search import NoticeSearch, factual_queries, FACT_QUERIES, merge_ranges
from submission.pps.purchase_details import detail_links
from submission.pps.qualification import qualification_facts, inventory
from submission.pps.table_structure import table_structures
from submission.runtime import Journal, source_manifest, sha256
from tools.source_coverage import covered


class LinkedDetailSearch(NoticeSearch):
    """Extend retrieval candidates, retaining all ambiguous literal targets."""
    def __init__(self, record, tokenizer, encoder=None):
        self.detail_inventory = [detail_links(doc['text']) for doc in record['docs']]
        super().__init__(record, tokenizer, encoder)

    def _context(self, span):
        ranges = list(super()._context(span))
        local = self.detail_inventory[span.doc_index]
        cards = {card['key']: card for card in local['cards']}
        for link in local['links']:
            row, card = link['summary_row'], cards[link['card']]
            witnesses = (row, card['source'])
            if any(span.start < ref['end'] and ref['start'] < span.end for ref in witnesses):
                refs = [link['summary_header'], row, card['source']]
                if card['preceding_caption']:
                    refs.append(card['preceding_caption'])
                ranges.extend((span.doc_index, ref['start'], ref['end']) for ref in refs)
        return merge_ranges(ranges, self.rec['docs'])


def reading(record, result, inventories):
    values = []
    for di, local in enumerate(inventories):
        cards = {card['key']: card for card in local['cards']}
        def hit(ref):
            return covered(record, result['spans'], {'doc_index': di, **ref})
        for link in local['links']:
            card = cards[link['card']]
            fields = [hit(field['body']) for field in card['fields']]
            values.append({'doc_index': di, 'card': card['key'], 'name': link['summary_name']['text'],
                'summary_row': hit(link['summary_row']), 'summary_header': hit(link['summary_header']),
                'detail_name': hit(link['detail_name']), 'all_detail_fields': bool(fields) and all(fields),
                'linked_bundle': hit(link['summary_row']) and hit(link['summary_header']) and hit(card['table_source'])
                    and bool(fields) and all(fields),
                'unique_literal_target': link['unique_literal_target'],
                'same_purchase_item_certified': False})
    sections = qualification_facts(record, inventory(record))['eligibility_sections']
    return {'detail_links': values, 'eligibility_sections_detected': len(sections),
        'eligibility_sections_read': sum(covered(record, result['spans'], section['evidence']) for section in sections),
        'absence_verified': False, 'classification_certified': False}


def query_groups(record, inventories, items):
    groups = {}
    topics = ('specification', 'eligibility', 'assurance', 'software', 'comparison')
    for topic in topics:
        group = tuple(q for q in factual_queries(items) if q in FACT_QUERIES[topic])
        if group:
            groups[topic] = group
    assigned = {q for group in groups.values() for q in group}
    rest = tuple(q for q in factual_queries(items) if q not in assigned)
    if rest:
        groups['other_conditions'] = rest
    names = tuple(dict.fromkeys(link['summary_name']['text'] for local in inventories for link in local['links']))
    if names:
        groups['literal_purchase_names'] = tuple(
            name + ' 물품의 실제 용도와 재질, 규격, 구성품, 동등품 및 예외 조건' for name in names)
    return groups


def summary_ranges(record,inventories):
    """Preserve all rows of a source table, including names with no detail link."""
    ranges=[]
    for di,local in enumerate(inventories):
        for table in table_structures(record['docs'][di]['text']):
            if any(table['start']<=link['summary_row']['start']<table['end'] for link in local['links']):
                ranges.append((di,table['start'],table['end']))
    return merge_ranges(ranges,record['docs'])


def compare(prepared, output, *, dense=False, grouped=False, preserve_summary=False, items=tuple(range(1,25))):
    from transformers import AutoTokenizer
    journal = Journal(output)
    code, began = source_manifest(), time.monotonic()
    def load(name):
        with gzip.open(prepared / name, 'rt', encoding='utf8') as stream:
            return list(map(json.loads, stream))
    records = load('current_inputs.jsonl.gz')
    primary = {p['record_id']: p for p in load('primary_packets.jsonl.gz') if p['batch'] == 'A1'}
    tokenizer = AutoTokenizer.from_pretrained(ROOT / 'models/gemma-tokenizer', local_files_only=True, trust_remote_code=False)
    encoder = None
    if dense:
        from submission.pps.embeddings import BGEDenseEncoder
        encoder = BGEDenseEncoder(ROOT / 'models/bge-m3')
    journal.save('preregistered.json', {'source_sha256': code, 'tool_sha256': sha256(__file__),
        'input_sha256': sha256(prepared / 'current_inputs.jsonl.gz'), 'source_notices': len(records),
        'source_cohort_rule': 'all records; context changes only where literal summary/detail links exist',
        'source_budget': 'sum of original canonical A1 source token counts for the same notice',
        'items': list(items), 'queries': factual_queries(items),
        'query_strategy': 'equal_topic_groups_and_literal_item_names' if grouped else 'flat',
        'preserve_full_linked_summary_tables': preserve_summary,
        'context_policies': ['local', 'literal_linked_details'],
        'selection_policies': ['rrf', 'evidence_cover', 'evidence_refill'],
        'method': 'hybrid' if dense else 'lexical', 'labels_read': False, 'new_gemma_calls': 0, 'gpu_allocated': False})
    results = []
    unchanged = []
    for record in records:
        inventories = [detail_links(doc['text']) for doc in record['docs']]
        if not any(local['links'] for local in inventories):
            unchanged.append(record['id'])
            continue
        packet = primary[record['id']]
        cap = sum(len(tokenizer.encode(span['text'], add_special_tokens=False)) for span in packet['spans'])
        original = {'record_id': record['id'], 'arm': 'canonical_A', 'spans': packet['spans'],
                    'source_tokens': cap, 'token_budget': cap}
        original['reading'] = reading(record, original, inventories)
        results.append(original)
        local = NoticeSearch(record, tokenizer, encoder)
        linked = LinkedDetailSearch(record, tokenizer, encoder)
        queries = {'query_groups': query_groups(record,inventories,items)} if grouped else {'queries': factual_queries(items)}
        required = summary_ranges(record,inventories) if preserve_summary else ()
        for tool, context in ((local, 'local'), (linked, 'literal_linked_details')):
            if tool is linked and dense:
                tool.vectors, tool._queries = local.vectors, dict(local._queries)
            for selection in ('rrf', 'evidence_cover', 'evidence_refill'):
                started = time.monotonic()
                result = tool.search(items, token_budget=cap, method='hybrid' if dense else 'lexical',
                    **queries, required_ranges=required, selection_policy=selection)
                result.update(record_id=record['id'], arm=context + '/' + selection, seconds=time.monotonic()-started)
                result['reading'] = reading(record, result, inventories)
                assert result['source_tokens'] <= cap
                for span in result['spans']:
                    assert record['docs'][span['doc_index']]['text'][span['start']:span['end']] == span['text']
                results.append(result)
    if source_manifest() != code:
        raise ValueError('Source changed during context comparison')
    journal.rows('results.jsonl.gz', results)
    summary = [{'record_id': result['record_id'], 'arm': result['arm'], 'source_tokens': result['source_tokens'],
                'summary_rows': sum(link['summary_row'] for link in result['reading']['detail_links']),
                'all_detail_fields': sum(link['all_detail_fields'] for link in result['reading']['detail_links']),
                'linked_bundles': sum(link['linked_bundle'] for link in result['reading']['detail_links']),
                'eligibility_sections_read': result['reading']['eligibility_sections_read'],
                'seconds': result.get('seconds')} for result in results]
    report = {'source_notices': len(records), 'notices_with_literal_links': len(records)-len(unchanged),
        'unchanged_context_notices': unchanged, 'measurements': summary, 'seconds': time.monotonic()-began,
        'source_changed': False, 'labels_read': False, 'new_gemma_calls': 0, 'gpu_allocated': False,
        'scope': 'Source-addressed reading comparison; relation correctness and F1 not measured.'}
    journal.save('report.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--dense', action='store_true')
    parser.add_argument('--grouped', action='store_true')
    parser.add_argument('--purchase-issue', action='store_true')
    parser.add_argument('--preserve-summary', action='store_true')
    args = parser.parse_args()
    print(json.dumps(compare(args.prepared, args.output, dense=args.dense, grouped=args.grouped,
        preserve_summary=args.preserve_summary,
        items=(9,10,11,18) if args.purchase_issue else tuple(range(1,25))), ensure_ascii=False, indent=2))
