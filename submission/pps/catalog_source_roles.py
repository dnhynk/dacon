"""Compile complete source-role choices without deciding their legal meaning.

Candidates belong only to the current notice. Their source arrays are complete
original ranges, not repaired text, inferred values, or certified scope links.
The model still decides subject relationship, modality, polarity and permission.
"""
from __future__ import annotations

import copy
import hashlib
import json

from . import catalog_condition_review as literal


VERSION = 3
READABLE_VERSIONS = (1, 2, VERSION)


def covering_refs(record, spans, evidence):
    refs = [n for n, span in enumerate(spans, 1)
        if span.doc_index == evidence['doc_index']
        and span.start < evidence['end'] and evidence['start'] < span.end]
    return refs if refs and literal._covers(record, spans, refs, evidence) else []


def _unique(values):
    result = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def build(record, spans, plan, *, version=VERSION):
    from . import catalog_semantics as semantics
    from .catalog_scope import whole_task_witnesses
    from .requirement_frames import containing
    if type(version) is not int or version not in READABLE_VERSIONS:
        raise ValueError('Unknown catalog source-role version')
    literal._original_units(record, spans)
    all_refs = list(range(1, len(spans) + 1))
    properties, unavailable, permissions, fields = [], [], [], []
    tasks = whole_task_witnesses(record, spans, all_refs) + literal._subject_candidates(record)
    task_choices = []
    for task in tasks:
        refs = covering_refs(record, spans, task)
        if len(refs) > 16:
            unavailable.append({'evidence': task, 'reason': 'complete_task_reference_capacity_exceeded'})
        elif refs:
            task_choices.append({'source_units': refs, 'evidence': task,
                                 'whole_purchase_certified': False})
    task_choices = _unique(task_choices)
    for product in plan:
        if not product['program']:
            continue
        names = product['program']['required_fields']
        fields.extend([[product['code'], field] for field in names])
        facts = literal.property_readings(record, product['name'], names)
        observations = [('findings', o) for o in facts['observations']]
        for candidate in semantics.candidates(record, names):
            ev = candidate['evidence']
            if not any(o['field'] == candidate['field'] and o['evidence']['doc_index'] == ev['doc_index']
                       and o['evidence']['start'] == ev['start'] for _, o in observations):
                observations.append(('semantic_readings', candidate))
        for channel, observation in observations:
            ev = observation['evidence']
            refs = covering_refs(record, spans, ev)
            entry = {'code': product['code'], 'field': observation['field'], 'channel': channel,
                     'value_units': refs, 'evidence': ev, 'scope_options': [],
                     'source_issue': observation.get('issue'), 'scope_certified': False}
            if not refs or len(refs) > 16:
                unavailable.append({**entry, 'reason': 'complete_property_reference_capacity_exceeded'
                                     if refs else 'complete_property_not_selected'})
                continue
            anchors = literal._local_task_witnesses(record, spans, {'scope_units': all_refs}, observation)
            required_headers, missing_header = [], False
            for frame in containing(record, ev):
                header = covering_refs(record, spans, frame['heading'])
                missing_header |= not bool(header)
                required_headers.extend(header)
            if missing_header:
                unavailable.append({**entry, 'reason': 'complete_requirement_header_not_selected'})
                continue
            options = [sorted(set(covering_refs(record, spans, anchor) + required_headers)) for anchor in anchors]
            entry['scope_options'] = _unique([option for option in options if option and len(option) <= 16])
            if not entry['scope_options']:
                unavailable.append({**entry, 'reason': 'complete_task_reference_capacity_exceeded'
                                     if options else 'no_complete_local_named_task'})
            else:
                properties.append(entry)
        # Full original condition clauses, including clauses in other documents.
        # Topic relevance or lexical trigger does not certify permission effect.
        from .catalog_permissions import occurrences, line_statement
        source_issues = (occurrences(record, names) if version >= 3 else
            [{'doc_index': di, 'start': m.start(), 'end': m.end()}
             for di, doc in enumerate(record['docs']) for m in semantics._SCOPE_GUARD.finditer(doc['text'])])
        for issue in source_issues:
            full = (semantics.statement if version >= 3 else line_statement)(record, issue)
            refs = covering_refs(record, spans, full)
            condition = {'source_units': refs, 'evidence': full, 'effect_certified': False}
            if refs and len(refs) <= 16:
                permissions.append(condition)
            else:
                unavailable.append({**condition, 'reason': 'complete_permission_reference_capacity_exceeded'
                                     if refs else 'complete_permission_not_selected'})
    properties, permissions, unavailable = map(_unique, (properties, permissions, unavailable))
    contract = {'version': version, 'fields': _unique(fields),
        'properties': [{key: p[key] for key in ('code', 'field', 'channel', 'value_units', 'scope_options')}
                       for p in properties],
        'permission_options': _unique([p['source_units'] for p in permissions]),
        'permission_subject_options': _unique([[], *[t['source_units'] for t in task_choices]])}
    source = [(s.doc_index, s.doc_type, s.start, s.end, s.text) for s in spans]
    return {'version': version, 'generation': contract, 'properties': properties,
        'permissions': permissions, 'tasks': task_choices, 'unavailable': unavailable,
        'source_sha256': hashlib.sha256(json.dumps(source, ensure_ascii=False).encode()).hexdigest(),
        'semantic_truth_certified': False, 'original_source_changed': False}


def constrain(schema, contract, max_units):
    """Use complete, typed role choices in the sampler, with abstention open."""
    from .catalog_condition_facts import _LABELS
    from .catalog_semantics import BOOLEAN_FIELDS
    if type(contract) is not dict or type(contract.get('version')) is not int or contract['version'] not in READABLE_VERSIONS:
        raise ValueError('Unknown catalog source-role contract')
    if set(contract) != {'version', 'fields', 'properties', 'permission_options', 'permission_subject_options'}:
        raise ValueError('Unexpected source-role contract fields')

    def check_refs(refs, *, empty=False):
        if (not isinstance(refs, list) or (not refs and not empty) or len(refs) > 16
                or any(type(n) is not int or not 1 <= n <= max_units for n in refs)
                or len(set(refs)) != len(refs)):
            raise ValueError('Invalid complete source-role references')

    fields = contract['fields']
    if not isinstance(fields, list) or not fields:
        raise ValueError('No declared catalog fields')
    for pair in fields:
        if (not isinstance(pair, list) or len(pair) != 2 or type(pair[0]) is not str
                or len(pair[0]) != 10 or not pair[0].isascii() or not pair[0].isdigit()
                or type(pair[1]) is not str or pair[1] not in _LABELS):
            raise ValueError('Invalid catalog code/field')
    for refs in contract['permission_options']:
        check_refs(refs)
    for refs in contract['permission_subject_options']:
        check_refs(refs, empty=True)
    result = copy.deepcopy(schema)
    branches = {'findings': [], 'semantic_readings': [], 'permissions': []}
    for candidate in contract['properties']:
        if set(candidate) != {'code', 'field', 'channel', 'value_units', 'scope_options'}:
            raise ValueError('Unexpected source-role candidate fields')
        code, field, channel = (candidate[key] for key in ('code', 'field', 'channel'))
        if [code, field] not in fields or channel not in {'findings', 'semantic_readings'}:
            raise ValueError('Undeclared source-role candidate')
        if channel == 'semantic_readings' and field not in BOOLEAN_FIELDS:
            raise ValueError('Nonboolean semantic role')
        check_refs(candidate['value_units'])
        if not candidate['scope_options']:
            raise ValueError('Source property has no named subject option')
        for refs in candidate['scope_options']:
            check_refs(refs)
        branch = copy.deepcopy(result['properties'][channel]['items'])
        props = branch['properties']
        props['code'] = {'const': code}
        props['field'] = {'const': field}
        props['value_units'] = {'type': 'array', 'enum': [candidate['value_units']]}
        props['scope_units'] = {'type': 'array', 'enum': candidate['scope_options']}
        branches[channel].append(branch)
        if contract['permission_options']:
            permission = copy.deepcopy(result['properties']['permissions']['items'])
            pp = permission['properties']
            for key in ('code', 'field', 'value_units', 'scope_units'):
                pp[key] = copy.deepcopy(props[key])
            pp['source_units'] = {'type': 'array', 'enum': contract['permission_options']}
            pp['condition_scope_units'] = {'type': 'array', 'enum': contract['permission_subject_options']}
            if contract['version'] == 1:
                branches['permissions'].append(permission)
            else:
                # Each original property/permission pair has its own slot.
                # Two distinct exception clauses must both remain expressible.
                for refs in contract['permission_options']:
                    slot = copy.deepcopy(permission)
                    slot['properties']['source_units']['enum'] = [refs]
                    branches['permissions'].append(slot)
    for channel, choices in branches.items():
        if contract['version'] >= 2:
            result['properties'][channel] = _slots(choices, result['properties'][channel]['maxItems'])
            continue
        if choices:
            result['properties'][channel]['items'] = {'anyOf': _unique(choices)}
        else:
            result['properties'][channel]['maxItems'] = 0
    unresolved = result['properties']['unresolved_fields']['items']
    choices = []
    for code, field in fields:
        item = copy.deepcopy(unresolved)
        item['properties']['code'], item['properties']['field'] = {'const': code}, {'const': field}
        choices.append(item)
    if contract['version'] >= 2:
        result['properties']['unresolved_fields'] = _slots(choices, result['properties']['unresolved_fields']['maxItems'])
    else:
        result['properties']['unresolved_fields']['items'] = {'anyOf': choices}
    return result


def _slots(choices, limit):
    """A source identity can occur once; null explicitly omits that identity.

    A shorter prefix leaves the remaining slots unaddressed, never negative.
    Do not truncate a source inventory to fit the wire's capacity.
    """
    choices = _unique(choices)
    if len(choices) > limit:
        raise ValueError('Complete source-role slots exceed wire capacity')
    result = {'type': 'array', 'maxItems': len(choices), 'items': False}
    if choices:
        result['prefixItems'] = [{'anyOf': [choice, {'type': 'null'}]} for choice in choices]
    return result


def slot_manifest(schema):
    """Compact prompt instructions for the exact order enforced by the sampler."""
    result = {}
    for channel in ('findings', 'unresolved_fields', 'semantic_readings', 'permissions'):
        result[channel] = []
        for slot in schema['properties'][channel].get('prefixItems', []):
            props = slot['anyOf'][0]['properties']
            item = {key: props[key]['const'] for key in ('code', 'field')}
            for key in ('value_units', 'source_units'):
                if key in props:
                    item[key] = props[key]['enum'][0]
            result[channel].append(item)
    return result


def validate_prepared(record, packet):
    """Recompute the role inventory before execution/consumption; never trust IDs."""
    validate_observed_facts(record, packet)
    shown = packet.get('catalog_conditions', {}).get('source_roles')
    contract = packet.get('generation', {}).get('catalog_roles')
    if shown is None and contract is None:
        return None
    if shown is None or contract is None:
        raise ValueError('Incomplete prepared source-role contract')
    current = build(record, packet['spans'], packet['catalog_conditions']['plan'], version=contract.get('version'))
    if current != shown or current['generation'] != contract:
        raise ValueError('Prepared source roles differ from current original source')
    return contract


def observed_facts(record, spans, plan):
    """Show parsed local values without certifying a delivery requirement.

    This is an optional prompt input, not a new role grammar or a model answer.
    Only complete selected observations are shown. A local must can still live
    under an optional parent, and an observed false is not an absent fact.
    """
    from .catalog_semantics import modality_review
    from .source_units import validate
    validate(spans, record)
    known, unresolved = [], []
    for product in plan:
        if not product['program']:
            continue
        fields = product['program']['required_fields']
        for observation in literal.property_readings(record, product['name'], fields)['observations']:
            ev = observation['evidence']
            refs = covering_refs(record, spans, ev)
            if not refs or len(refs) > 16:
                continue
            entry = {'code': product['code'], 'field': observation['field'],
                'source_units': refs, 'source_range': {key: ev[key] for key in ('doc_index', 'start', 'end')}}
            if observation['issue']:
                unresolved.append({**entry, 'issue': observation['issue']})
                continue
            local = modality_review(observation['field'], ev['text'], 'unclear')
            relations = [{**r, 'start': ev['start'] + r['start'], 'end': ev['start'] + r['end'],
                          **({key: ev['start'] + r[key] for key in ('property_start', 'property_end')}
                             if 'property_start' in r else {})} for r in local['relations']]
            known.append({**entry, 'type': observation['type'], 'value': copy.deepcopy(observation['value']),
                'method': observation.get('source_assertion', 'literal_requirement_suffix'
                    if observation.get('literal_requirement_suffix') else 'typed_original_field'),
                'local_modality_expressions': relations})
    return {'version': 1, 'observations': _unique(known), 'unresolved_values': _unique(unresolved),
        'whole_purchase_certified': False, 'delivery_obligation_certified': False,
        'permissions_resolved': False, 'absence_certified': False}


def validate_observed_facts(record, packet):
    conditions = packet.get('catalog_conditions', {})
    if 'source_observations' not in conditions:
        if 'obligation_questions' in conditions:
            raise ValueError('Obligation questions require source observations')
        return
    shown = conditions['source_observations']
    if packet.get('generation', {}).get('catalog_roles') is None:
        raise ValueError('Observed catalog facts require a prepared source-role contract')
    current = observed_facts(record, packet['spans'], packet['catalog_conditions']['plan'])
    # Python equality alone would allow False == 0 or an integer coordinate == float.
    encode = lambda obj: json.dumps(obj, ensure_ascii=False, sort_keys=True, allow_nan=False)
    if encode(current) != encode(shown):
        raise ValueError('Prepared catalog observations differ from selected original source')
    if 'obligation_questions' in conditions:
        if encode(obligation_questions(current)) != encode(conditions['obligation_questions']):
            raise ValueError('Prepared obligation questions differ from observed source values')


def obligation_questions(observations):
    """Name the operand of a modality decision: this value, not condition truth.

    These are questions, not obligations inferred by the CPU. The constant
    value/range remains source derived; all five modality choices stay open.
    """
    questions = []
    for index, observation in enumerate(observations['observations'], 1):
        questions.append({'key': 'O'+str(index),
            'code': observation['code'], 'field': observation['field'],
            'source_units': copy.deepcopy(observation['source_units']),
            'source_range': copy.deepcopy(observation['source_range']),
            'value_constraint': {'type': observation['type'], 'value': copy.deepcopy(observation['value'])},
            'question': '연결할 납품대상이 이 원문 속성값·범위를 따라야 하는가?',
            'modality_operand': 'value_constraint', 'answer_supplied': False})
    return {'version': 1, 'questions': questions,
        'read_modality_as': {
            'required': '해당 납품대상에 이 관측값·범위 자체가 필수다.',
            'optional': '해당 납품대상에 이 관측값·범위를 선택하거나 대체할 수 있다.',
            'example': '이 관측값·범위는 예시 또는 기존 대상의 설명이다.',
            'negated': '이 관측값·범위를 따를 의무를 원문이 부정한다.',
            'unclear': '이 관측값·범위의 현재 의무를 확정할 수 없다.'},
        'condition_truth_and_obligation_are_separate': True,
        'no_scope_or_permission_decision_supplied': True}
