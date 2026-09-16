"""Measure source-table hypotheses separately from accepted relationships."""
import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from submission.pps.table_structure import table_structures,reading_order_candidates,numeric_invariant
from submission.runtime import source_manifest


def benchmark():
    # Known structured rows are serialized independently of the parser. Missing
    # values are removed from the observations, never retained as inferred gold.
    labels=[('name','품명'),('specification','규격'),('unit','단위'),
            ('quantity','수량'),('unit_price','단가(원)'),('amount','금액(원)')]
    base=[['온도계','10cm','개','2','100','200'],
          ['압력계','20cm','개','3','200','600'],
          ['온도계','30cm','개','4','300','1200']]
    cases=[]
    for layout in ('pipe','row_major','column_major'):
        for missing in (None,'quantity','unit_price','amount'):
            parts=[];offset=0;observed={}
            def emit(value,role=None,row=None):
                nonlocal offset
                if role and value:observed[row,role]=(offset,offset+len(value))
                parts.append(value);offset+=len(value)
            emit((' | ' if layout=='pipe' else '\n').join(label for _,label in labels)+'\n')
            if layout=='pipe':
                for row,values in enumerate(base):
                    for c,(role,_) in enumerate(labels):
                        if c:emit(' | ')
                        emit('' if role==missing else values[c],role,row)
                    emit('\n')
            else:
                order=[(r,c) for r in range(len(base)) for c in range(len(labels))]
                if layout=='column_major':order.sort(key=lambda rc:(rc[1],rc[0]))
                for r,c in order:
                    role=labels[c][0]
                    if role==missing:continue
                    emit(base[r][c],role,r);emit('\n')
            text=''.join(parts)
            gold={(observed[r,'name'],role,observed[r,role])
                  for r in range(len(base)) for role,_ in labels[1:] if (r,role) in observed}
            accepted=set();proposed=set();decidable=0;conditional=0
            def edges(name,mapped,target):
                for role,cell in mapped.items():
                    if role not in dict(labels) or role=='name' or cell is None:continue
                    target.add(((name['start'],name['end']),role,(cell['start'],cell['end'])))
            for table in table_structures(text):
                for row in table['rows']:
                    if row['literal_column_alignment']:
                        for candidate in row['candidates']:edges(candidate['name'],candidate,accepted)
                    else:
                        for name in row['name_candidates']:
                            for numeric in row['numeric_alternatives']:
                                edges(name,{**numeric,'unit':row['unit']},proposed)
                    answer=numeric_invariant(row,'amount',upper=1000)
                    if answer['status']=='invariant':
                        decidable+=row['literal_column_alignment'];conditional+=not row['literal_column_alignment']
                for layout_candidate in table['complete_layout_candidates']:
                    for mapped in layout_candidate['rows']:edges(mapped['name'],mapped,proposed)
            cases.append(dict(layout=layout,missing_column=missing,gold_observed_relations=len(gold),
                accepted_correct=len(accepted&gold),accepted_wrong=len(accepted-gold),
                candidate_correct=len(proposed&gold),candidate_wrong=len(proposed-gold),
                gold_reached=len((accepted|proposed)&gold),
                rows_with_observed_amount_band=decidable,rows_with_conditional_amount_band=conditional,
                source_sha256=hashlib.sha256(text.encode()).hexdigest(),text=text))
    return cases


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    code=source_manifest();input_hash=sha(args.input)
    with gzip.open(args.input,'rt',encoding='utf8') as stream:records=list(map(json.loads,stream))
    observations=[]
    for record in records:
        docs=[dict(doc_index=i,doc_type=d['type'],source_sha256=hashlib.sha256(d['text'].encode()).hexdigest(),
                   tables=table_structures(d['text']),reading_order_candidates=reading_order_candidates(d['text']))
              for i,d in enumerate(record['docs'])]
        observations.append(dict(id=record['id'],docs=docs))
    cases=benchmark()
    tables=[t for o in observations for d in o['docs'] for t in d['tables']]
    rows=[r for t in tables for r in t['rows']]
    report=dict(kind='source_structure_hypotheses_audit',source_sha256=code,input_sha256=input_hash,
        labels_read=False,new_model_calls=0,gpu_used=False,records=len(records),
        source_tables=len(tables),table_layouts=dict(Counter(t['layout'] for t in tables)),
        local_rows=len(rows),literal_pipe_rows=sum(r['literal_column_alignment'] for r in rows),
        candidate_rows_with_names=sum(bool(r['name_candidates']) for r in rows if not r['literal_column_alignment']),
        tables_with_unresolved_ranges=sum(bool(t['unresolved_source_ranges']) for t in tables),
        truncated_tables=sum(t['candidate_search_truncated'] for t in tables),
        arithmetic=dict(Counter(r['arithmetic']['status'] for r in rows)),
        synthetic_cases=[{k:v for k,v in c.items() if k!='text'} for c in cases],
        synthetic_totals={k:sum(c[k] for c in cases) for k in ('gold_observed_relations','accepted_correct',
            'accepted_wrong','candidate_correct','candidate_wrong','gold_reached',
            'rows_with_observed_amount_band','rows_with_conditional_amount_band')},
        actual_final_judgments_newly_certified=0,
        limitation='Synthetic relation accuracy is not unseen performance. Vertical candidates are conditional on modeled layouts; no item identity, absence or final verdict is certified.')
    with gzip.open(args.output/'observations.jsonl.gz','wt',encoding='utf8') as stream:
        for row in observations:stream.write(json.dumps(row,ensure_ascii=False)+'\n')
    with (args.output/'synthetic_cases.json').open('x',encoding='utf8') as stream:json.dump(cases,stream,ensure_ascii=False,indent=2)
    assert source_manifest()==code and sha(args.input)==input_hash
    with (args.output/'report.json').open('x',encoding='utf8') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in report.items() if k not in {'source_sha256','synthetic_cases'}},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
