"""Optional source-bound positive overlay; the normal pipeline remains authoritative."""
from __future__ import annotations

import dataclasses
import json
import re
import jsonschema

from . import focused_prompt as prompt
from .response_contract import loads

FAMILIES = {'region': (5, 6, 7), 'size': (13, 14, 15, 17), 'briefing': (22,)}
SMALL = {'소기업', '소상공인', '소기업_소상공인', '중기업_이상_제외'}
BROAD = {'중소기업', '대기업_제외'}


def same_span(a, b):
    return bool(a and b and a['doc_index'] == b['doc_index']
                and a['start'] < b['end'] and b['start'] < a['end'])


def parsed_spans(record):
    """Current participation parsers, independent of the legal item decision."""
    from .qualification import inventory, qualification_facts
    from .performance import performance_facts
    from .other_checks import briefing_check
    q = qualification_facts(record, inventory(record))
    sizes = [e['evidence'] for e in q['active_size'] + q['entity_qualification_obligations']]
    if q['ordinary_commercial_size_bound']:
        sizes += q['ordinary_commercial_size_bound']['evidence']
    return dict(size=sizes,
        region=[e['evidence'] for e in performance_facts(record, consumer=True)['operative_regions']],
        briefing=[e['evidence'] for e in briefing_check(record)['facts']['events'] if e['restricts_eligibility']])


def cpu_facts(details):
    """Keep source-linked vetoes and the final Q10 product assignment."""
    result = dict(zeros=[], qualification_spans=[], product_status=None)
    def add(value):
        if isinstance(value, list):
            for x in value:
                add(x)
        elif isinstance(value, dict):
            if value.get('product'):
                result['product_status'] = value['product'].get('status')
            result['zeros'].extend(dict(d, item=int(k[1:])) for k, d in value.get('decisions', {}).items()
                                   if d.get('value') == 0)
            if value.get('item') and value.get('value') == 0:
                result['zeros'].append(value)
            result['qualification_spans'].extend(e['evidence'] for e in value.get('qualification', {}).get('inventory', [])
                                                 if e.get('size') and e.get('evidence'))
            if 'facts' in value:
                add(value['facts'])
    for detail in details:
        add(detail)
    return result


def number(value):
    try:
        return float(str(value).replace(',', ''))
    except (TypeError, ValueError):
        return None


def applicable(family, premises, cpu):
    if family == 'briefing':
        return '협상' in str(premises.get('낙찰방법'))
    if number(premises.get('입찰추정가격')) is None or '수의' in str(premises.get('계약방법')):
        return False
    if family == 'size':
        return cpu.get('product_status') in ('general', 'competition')
    bounds = premises.get('CPU_지역제한상한_meta만') or {}
    price = number(premises.get('입찰추정가격'))
    low, high = number(bounds.get('below_ceiling')), number(bounds.get('above_ceiling'))
    return (low is not None and price < low) or (high is not None and price >= high)


def eligibility_ranges(record):
    from .retrieval import _source_units, _eligibility_units
    ranges = []
    for di, doc in enumerate(record['docs']):
        units = _source_units(doc['text'])
        ranges.extend(dict(doc_index=di, start=units[a][0], end=units[b][1])
                      for a, b in _eligibility_units(doc['text'], units))
    return ranges


def gate_job(job, parsed, baseline, cpu, *, lexical=True, eligibility=False, ranges=()):
    family = job['family']
    if family not in FAMILIES:
        return 'family'
    if any(int(baseline.get(f'v{i}', baseline.get(i, 0)) or 0) for i in FAMILIES[family]):
        return 'already_positive'
    if any(same_span(job['candidate'], e) for e in parsed[family]):
        return 'parsed_participation'
    if not applicable(family, job['premises'], cpu):
        return 'inapplicable_or_unknown'
    if lexical and not job['selection']['lexical_score']:
        return 'no_lexical_cue'
    if eligibility and not any(same_span(job['candidate'], e) for e in ranges):
        return 'outside_eligibility'
    return None


def candidate_jobs(record, config):
    """V2D lexical ranking; no semantic backfill without a family cue."""
    from .retrieval import split_spans
    spans = split_spans(record, overlap=config.span_overlap)
    premises = prompt.premises(record)
    result = []
    for family in FAMILIES:
        scores = [sum(c in re.sub(r'\s+', '', s.text) for c in prompt.CUES[family].split()) for s in spans]
        # V2D cap4 interleaves lexical 1,2 / semantic 1 / lexical 3.
        # Keeping these three cue anchors avoids unmeasured semantic backfill.
        selected = sorted((i for i, value in enumerate(scores) if value), key=lambda i: (-scores[i], i))[:3]
        for rank, i in enumerate(selected, 1):
            s = spans[i]
            context = [prompt.span_row(record, spans[j], j+1)
                       for j in range(max(0, i-config.focused_verify_neighbors), min(len(spans), i+config.focused_verify_neighbors+1))
                       if spans[j].doc_index == s.doc_index]
            result.append(dict(record_id=record['id'], family=family,
                candidate_id=f"{record['id']}:{family}:{s.doc_index}:{s.start}:{s.end}",
                candidate=prompt.span_row(record, s, i+1), context=context, premises=premises,
                selection=dict(index=i, rank=rank, lexical_score=scores[i], selected_by='lexical')))
    return result


def prepare(pipe, record, baseline, details):
    if not pipe.config.focused_verify:
        return []
    parsed, cpu = parsed_spans(record), cpu_facts(details)
    ranges = eligibility_ranges(record)
    counts, packets = {}, []
    for job in candidate_jobs(record, pipe.config):
        family = job['family']
        if gate_job(job, parsed, baseline, cpu, eligibility=pipe.config.focused_verify_eligibility, ranges=ranges):
            continue
        if counts.get(family, 0) >= pipe.config.focused_verify_top_k:
            continue
        messages, ids, schema, max_tokens = prompt.prompt(job, 'judge_think', None, pipe.tokenizer)
        if len(ids) > pipe.config.focused_verify_max_input_tokens:
            continue
        counts[family] = counts.get(family, 0) + 1
        from submission.b4_entry import digest
        packet = dict(record_id=record['id'], request_key='FV:'+job['candidate_id'],
            batch=f'FV:{family}:{counts[family]}', family='FV', items=list(FAMILIES[family]),
            spans=[{k: s[k] for k in ('doc_index','doc_type','start','end','text')} for s in job['context']],
            messages=messages, token_ids=ids, input_tokens=len(ids),
            prompt_sha256=digest(messages), token_ids_sha256=digest(ids), schema_sha256=digest(schema),
            generation=dict(response_format='focused_verify', thinking_budget=384, max_output_tokens=max_tokens, schema=schema),
            focused_job=job, focused_cpu=cpu)
        packets.append(packet)
    return packets


def decode(text, job):
    answer = loads(text)
    jsonschema.validate(answer, prompt.COMBINED[job['family']])
    sources = {s['s']: s for s in job['context']}
    if any(s is not None and s not in sources for s in [answer['read']['s'], *(v['s'] for v in answer['items'].values())]):
        raise ValueError('Invalid focused citation')
    read = answer['read']
    if read['s'] is not None:
        literals = [v for k, v in read.items() if (k.endswith('_text') or k == 'quote') and v] + read.get('tokens', [])
        if any(t not in sources[read['s']]['text'] for t in literals):
            raise ValueError('Nonverbatim focused extraction')
    elif read['quote']:
        raise ValueError('Quote without focused source')
    return answer


def route(job, read, cpu):
    family, p = job['family'], job['premises']
    price = number(p.get('입찰추정가격'))
    quote = '수의' in str(p.get('계약방법'))
    if family == 'region':
        if read['whose_location'] != 'bidder' or not read['mandatory'] or price is None or quote:
            return None
        bounds = p.get('CPU_지역제한상한_meta만') or {}
        lo, hi = number(bounds.get('below_ceiling')), number(bounds.get('above_ceiling'))
        if hi is not None and price >= hi:
            return 5
        if lo is not None and price < lo:
            if read['level'] == '기초':
                return 6
            if read['level'] == '광역' and len(set(read['tokens'])) >= 2:
                return 7
    elif family == 'size':
        if read['role'] != 'participation_requirement' or price is None or quote:
            return None
        restriction = read['restriction']
        if cpu.get('product_status') == 'competition':
            return 13 if restriction in SMALL else None
        if cpu.get('product_status') == 'general':
            if price >= 230000000 and restriction in SMALL | BROAD:
                return 14
            if 100000000 <= price < 230000000 and restriction in SMALL:
                return 15
            if price < 100000000 and restriction in BROAD:
                return 17
    elif family == 'briefing':
        if (read['event'] in ('pre_bid_briefing', 'site_visit') and read['held']
                and read['attendance'] == 'required_for_participation' and '협상' in str(p.get('낙찰방법'))):
            return 22
    return None


def lawful_veto(job, item, source, cpu):
    for decision in cpu.get('zeros', []):
        if decision.get('item') != item:
            continue
        reason = decision.get('reason', '')
        if reason == 'model_witness_has_only_scoring_or_form_purpose':
            for occurrence in decision.get('occurrences', []):
                for purpose in occurrence.get('purposes', []):
                    if purpose.get('status') in ('scoring', 'forms_or_submission') and same_span(source, purpose.get('evidence')):
                        return reason
        if reason in ('documented_small_quote_with_standard_size_eligibility', 'identified_purchase_in_conditional_catalog',
                      'identified_purchase_outside_conditional_catalog'):
            if any(same_span(source, s) for s in cpu.get('qualification_spans', [])):
                return reason
    return None


def decide(job, answer, cpu):
    if answer is None:
        return None, 'invalid_or_missing'
    read = answer['read']
    source = next((s for s in job['context'] if s['s'] == read['s']), None)
    if not read['quote'].strip() or source is None:
        return None, 'no_grounded_read'
    item = route(job, read, cpu)
    if item is None:
        return None, 'cpu_route_unknown_or_lawful'
    verdict = answer['items'][f'v{item}']
    if verdict['v'] != 1:
        return None, 'judge_nonpositive'
    # route embodies the V2D read-direction predicate for these three families.
    if read['s'] != verdict['s']:
        return None, 'read_direction_or_citation_disagreement'
    if sum(v['v'] for v in answer['items'].values()) != 1:
        return None, 'multiple_positive_items'
    veto = lawful_veto(job, item, source, cpu)
    if veto:
        return None, 'cpu_lawful:' + veto
    return item, 'accepted'


def consume(record, packet, response):
    job, cpu = packet['focused_job'], packet['focused_cpu']
    answer = decode(response['text'], job)
    item, reason = decide(job, answer, cpu)
    details = dict(source='focused_verify', reason=reason, family=job['family'], candidate_id=job['candidate_id'],
                   read=answer['read'], items=answer['items'])
    if item is None:
        return {}, details
    source = next(s for s in job['context'] if s['s'] == answer['read']['s'])
    original = record['docs'][source['doc_index']]['text'][source['start']:source['end']]
    if original != source['text'] or answer['read']['quote'] not in original:
        raise ValueError('Focused evidence differs from original source')
    evidence = answer['read']['quote'][:500]
    details.update(item=item, citation=source, evidence=evidence)
    return {f'v{item}': 1, f'e{item}': evidence}, details
