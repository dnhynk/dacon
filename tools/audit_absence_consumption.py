"""Trace preserved model positives through absence guards without reading labels."""
from __future__ import annotations

import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission.pps.pipeline import parse_output
from submission.pps.retrieval import Span
from submission.pps.response_contract import loads
from submission.runtime import source_manifest

ITEMS = (10, 11, 16, 18, 20)


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def read_rows(path):
    return list(map(json.loads, gzip.open(path, 'rt', encoding='utf-8')))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def section_coverage(record, sections, spans):
    """Character coverage of known source sections, not an absence proof."""
    result = []
    for section in sections:
        ev = section['evidence']; di, lo, hi = ev['doc_index'], ev['start'], ev['end']
        text = record['docs'][di]['text']
        cursor, gaps = lo, []
        for a, b in sorted((max(lo, s.start), min(hi, s.end)) for s in spans
                          if s.doc_index == di and s.start < hi and lo < s.end):
            if a > cursor and text[cursor:a].strip():
                gaps.append([cursor, a])
            cursor = max(cursor, b)
        if cursor < hi and text[cursor:hi].strip():
            gaps.append([cursor, hi])
        result.append(dict(doc_index=di, start=lo, end=hi, closed=section['closed'],
            all_nonwhitespace_selected=not gaps, unselected_ranges=gaps))
    return result


def trace(source_run, consumer_run, output):
    code = source_manifest()
    freeze = read(consumer_run/'freeze.json')
    if freeze['source_sha256'] != code:
        raise ValueError('Consumer must be frozen on the current source; do not relabel historical results')
    paths = [source_run/'current_inputs.jsonl.gz', source_run/'current_packets.jsonl.gz',
             consumer_run/'freeze.json', consumer_run/'decisions.jsonl.gz']
    resolved = source_run/'resolved_responses.jsonl.gz'
    response_paths = [resolved] if resolved.exists() else sorted(source_run.glob('first_part_*.jsonl.gz'))
    paths += response_paths
    hashes = {str(p.resolve()): sha(p) for p in paths}
    if any(sha(Path(p)) != h for p, h in freeze['input_sha256'].items()):
        raise ValueError('Preserved consumer inputs changed')
    records = {r['id']: r for r in read_rows(paths[0])}
    packets = {p['request_key']: p for p in read_rows(paths[1])}
    decisions = {r['request_key']: r for r in read_rows(consumer_run/'decisions.jsonl.gz')}
    responses = {r['request_key']: r['response'] for p in response_paths for r in read_rows(p)}
    if not (packets.keys() == responses.keys() == decisions.keys()):
        raise ValueError('Packets, responses and consumed rows differ')
    observations = []
    for key, packet in packets.items():
        selected = set(packet['items']) & set(ITEMS)
        # The canonical B4 keeps A10 and replaces A20 with the uniform L20.
        if packet['family'] == 'A':
            selected.discard(20)
        elif packet['family'] == 'L':
            selected &= {20}
        else:
            selected.clear()
        if not selected:
            continue
        record, response = records[packet['record_id']], responses[key]
        spans = [Span(**s) for s in packet['spans']]
        raw, _ = parse_output(response['text'], spans, tuple(packet['items']), rec=record)
        details = decisions[key]['details']; final = decisions[key]['row']
        native = loads(response['text'])
        qualification = next((d['facts'] for d in details if d.get('source') == 'supplied_catalog_qualification_v2'), None)
        sw = next((d['decision'] for d in details if d.get('source') == 'canonical_SW_rule_on_L_response'), None)
        for item in sorted(selected):
            field = f'v{item}'
            blockers, authoritative = [], None
            observation = dict(id=record['id'], request_key=key, item=item, raw_value=raw[item-1],
                final_value=int(final[field]), provided_completeness=record.get('input_completeness'),
                dropped_docs=record.get('dropped_doc_counts'), selected_coverage=packet.get('coverage'),
                model_facts=native.get('facts', {}), source_consumer_details=details)
            if qualification is not None:
                q = qualification['qualification']; product = qualification['product']
                authoritative = qualification['decisions'].get(field)
                if not q['complete']:
                    blockers.append('provided_documents_incomplete')
                if not q['closed_eligibility']:
                    blockers.append('qualification_section_not_closed')
                if not q['reference_coverage']['qualification_deferrals_resolved']:
                    blockers.append('explicit_qualification_reference_unavailable')
                predicate = 'no_direct' if item == 10 else 'no_size'
                if not q[predicate]:
                    blockers.append(predicate+'_not_proven')
                observation.update(source_absence_predicate={predicate:q[predicate]},
                    product_status=product['status'], product_uncertainty=product['uncertainty'],
                    source_qualification=q, qualification_section_input_coverage=section_coverage(record,q['eligibility_sections'],spans),
                    deferred=qualification.get('deferred_decisions',{}).get(field))
            elif sw is not None:
                authoritative = sw if sw['value'] is not None else None
                if sw['value'] is None:
                    blockers.append(sw['reason'])
                observation['software_source_check'] = sw
            else:
                raise ValueError('Required current consumer diagnostics missing: '+key)
            observation.update(source_rule_decision=authoritative, guard_blockers=blockers,
                retained_positive_after_abstention=(raw[item-1]==1 and int(final[field])==1 and authoritative is None),
                classification='trace_only_no_error_or_normality_certificate')
            observations.append(observation)
    assert len(observations) == len(records)*len(ITEMS)
    if source_manifest() != code or any(sha(Path(p)) != h for p,h in hashes.items()):
        raise ValueError('Source/evidence changed during audit')
    output.mkdir(parents=True, exist_ok=False)
    with gzip.open(output/'observations.jsonl.gz', 'wt', encoding='utf-8') as stream:
        for row in observations:
            stream.write(json.dumps(row,ensure_ascii=False)+'\n')
    summary = dict(kind='saved_response_absence_consumption_trace', source_sha256=code, input_sha256=hashes,
        records=len(records), items=list(ITEMS), labels_read=False, new_model_calls=0,
        by_item={str(k): dict(raw_positive=sum(o['raw_value']==1 for o in observations if o['item']==k),
            final_positive=sum(o['final_value']==1 for o in observations if o['item']==k),
            retained_after_abstention=sum(o['retained_positive_after_abstention'] for o in observations if o['item']==k)) for k in ITEMS},
        retained_guard_reasons=dict(Counter(reason for o in observations if o['retained_positive_after_abstention'] for reason in o['guard_blockers'])),
        limitation='Source abstention is not a model error; missing source coverage is not legal normality. No new inference or scoring.')
    (output/'report.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if not k.endswith('sha256')},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-run',type=Path,required=True)
    parser.add_argument('--consumer-run',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    trace(args.source_run,args.consumer_run,args.output)
