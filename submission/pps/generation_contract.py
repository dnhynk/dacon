"""CPU grammar checks and the narrower grammar supported by the fixed engine.

The response validator remains authoritative. A sampler need not enforce every
semantic constraint, but an unsupported schema must fail before model loading.
"""
from __future__ import annotations

import hashlib
import json

from .prompts import output_schema


def engine_options(enable_thinking):
    # vLLM0.26 GuidanceBackend reads these engine options, not the similarly
    # named per-request fields. Pin the backend tested by CPU preflight.
    options = {'backend': 'guidance', 'disable_any_whitespace': True}
    if enable_thinking:
        options.update(reasoning_parser='gemma4', enable_in_reasoning=False)
    return options


def json_whitespace_stall(text):
    """Recognize a long trailing whitespace run outside a JSON string.

    It is a generation failure observation, never a completed JSON repair or
    a legal judgment. Short whitespace and quoted source content are retained.
    """
    if not text.lstrip().startswith(('{', '[')):
        return None
    tail = len(text) - len(text.rstrip(' \t\r\n'))
    if tail < 128:
        return None
    quoted = escaped = False
    for char in text[:-tail]:
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
    return None if quoted else {'kind': 'outside_json_string_whitespace_loop', 'trailing_characters': tail}


def progress_preflight(tokenizer, *, schema_order=None, response_format='catalog_scope'):
    """Exercise the actual token mask without loading weights or allocating GPU."""
    import llguidance
    import llguidance.hf
    ll_tokenizer = llguidance.hf.from_tokenizer(tokenizer)
    if response_format not in {'catalog_scope', 'catalog_conditions', 'catalog_semantics'}:
        raise ValueError('No token-progress probe for this response format')
    schema = generation_schema(response_format, 70, tuple(range(10, 19)), schema_order=schema_order)
    if response_format in {'catalog_conditions', 'catalog_semantics'}:
        prefix = '{"findings":[{"code":"4321150102","field":"cpu_architecture","value_units":['
    else:
        prefix = ('{"task_summary":"전체 과업","purchase_kind":"service","whole_task_units":['
                  if schema_order else '{"purchase_kind":"service","whole_task_units":[')
    encode = lambda text: tokenizer.encode(text, add_special_tokens=False)
    whitespace = encode('    \n' * 20)
    counts = {}
    effective = None
    for flexible in (True, False):
        grammar = llguidance.LLMatcher.grammar_from_json_schema(schema,
            defaults={'whitespace_flexible': flexible})
        matcher = llguidance.LLMatcher(ll_tokenizer, grammar)
        if not matcher.consume_tokens(encode(prefix)):
            raise ValueError('JSON progress preflight rejected its array prefix')
        counts[flexible] = matcher.validate_tokens(whitespace)
        if not flexible:
            effective = grammar
            one = encode('1')
            if matcher.validate_tokens(one) != len(one) or counts[False] != 0:
                raise ValueError('Effective JSON grammar does not force array progress')
    examples = [
        {'purchase_kind': 'service', 'whole_task_units': [], 'task_summary': '전체 과업 불명확',
         'catalog_relation': 'unknown', 'relationships': [], 'unresolved_scope': True},
        {'purchase_kind': 'service', 'whole_task_units': [1, 70], 'task_summary': '원문 공백과 \\"인용\\" 보존',
         'catalog_relation': 'listed_category', 'relationships': [
             {'code': '8014190201', 'role': 'whole', 'source_units': [1, 70]}], 'unresolved_scope': False},
    ]
    if response_format in {'catalog_conditions', 'catalog_semantics'}:
        examples = [{'findings': [], 'unresolved_fields': []},
            {'findings': [{'code': '4321150102', 'field': 'cpu_architecture',
                'value_units': [1, 70], 'scope_units': [1], 'condition_units': [],
                'scope': 'whole_named_purchase', 'modality': 'required',
                'reason': '원문 속성의 대상과 필수 조건'}],
             'unresolved_fields': [{'code': '4321150102', 'field': 'cpu_count',
                                    'reason': '속성이 없거나 해석 불명확'}]}]
    if response_format == 'catalog_semantics':
        for example in examples:
            example.update(semantic_readings=[], permissions=[])
        examples[1]['semantic_readings'] = [{'code': '8213160301',
            'field': 'commissioning_public_agency_identified', 'value_units': [1],
            'scope_units': [1], 'condition_units': [], 'scope': 'whole_named_purchase',
            'modality': 'required', 'quantifier': 'all_named_targets',
            'reason': '원문에서 실제 대상과 조건을 확인', 'polarity': 'affirmed'}]
    for example in examples:
        matcher = llguidance.LLMatcher(ll_tokenizer, effective)
        example = {key: example[key] for key in schema['properties']}
        ids = encode(json.dumps(example, ensure_ascii=False, separators=(',', ':')))
        if not matcher.consume_tokens(ids) or not matcher.is_accepting():
            raise ValueError('Effective grammar rejects a complete valid reference object')
    return {'status': 'PASS', 'llguidance': llguidance.__version__,
        'schema_order': schema_order, 'response_format': response_format,
        'model_loaded': False, 'gpu_allocated': False,
        'unbounded_whitespace_tokens_accepted_control': counts[True],
        'whitespace_tokens_accepted_effective': counts[False],
        'complete_objects_accepted': len(examples), 'tokenizer_vocab_size': len(tokenizer),
        'effective_engine_options': engine_options(True)}


def generation_schema(response_format, max_evidence, items, *, schema_order=None, catalog_roles=None, catalog_fields=None,
                      specification_inventory=None):
    if specification_inventory is not None and response_format != 'specification_candidates':
        raise ValueError('Specification inventory requires its candidate review format')
    if catalog_roles is not None and response_format != 'catalog_semantics':
        raise ValueError('Source-role constraints require catalog_semantics')
    if catalog_fields is not None and response_format not in {'catalog_conditions', 'catalog_semantics'}:
        raise ValueError('Catalog fields require a condition response format')
    if schema_order is not None and (response_format != 'catalog_scope' or schema_order != 'catalog_facts_first'):
        raise ValueError('Unsupported generation schema order for this response format')
    if response_format == 'specification_candidates':
        if specification_inventory is None:
            raise ValueError('Candidate generation requires the complete prepared inventory')
        from .specification_candidate_review import schema as candidate_schema, NAME
        result = candidate_schema(max_evidence, items, plan=specification_inventory)
        # Each occurrence still requires its own C key and complete answer.
        # Repeating the identical relational schema inline makes llguidance
        # expand its lexer once per candidate and overflow on ordinary lists.
        # Shared definitions preserve every numeric bound and allOf relation;
        # candidates with a missing source value keep their distinct schema.
        reviews = result['properties'][NAME]['properties']
        definitions, identities = {}, {}
        for key, answer in reviews.items():
            identity = json.dumps(answer, ensure_ascii=False, sort_keys=True)
            if identity not in identities:
                name = 'candidate_answer_' + str(len(definitions) + 1)
                identities[identity] = name
                definitions[name] = answer
            reviews[key] = {'$ref': '#/$defs/' + identities[identity]}
        if definitions:
            result['$defs'] = definitions
        return result
    schema = output_schema(response_format, max_evidence, items)
    if response_format == 'catalog_semantics':
        from .catalog_semantics import generation_schema as semantic_schema
        return semantic_schema(max_evidence, items, source_roles=catalog_roles, field_contract=catalog_fields)
    if response_format == 'catalog_conditions':
        from .catalog_condition_review import schema as condition_schema
        result = condition_schema(max_evidence, items, wire=True)
        if catalog_fields is not None:
            from .catalog_field_contract import constrain
            result = constrain(result, catalog_fields)
        return result
    if response_format in {'catalog_scope', 'goods_scope'}:
        # llguidance1.7.6 cannot compile uniqueItems. Keep it in output_schema,
        # so the canonical validator stays strict. The explicit wire adapter
        # may losslessly canonicalize repeated valid source addresses.
        properties = schema['properties']
        if response_format == 'goods_scope':
            properties['whole_purchase_units'].pop('uniqueItems', None)
            properties['item_relations']['items']['properties']['source_units'].pop('uniqueItems', None)
        else:
            properties['whole_task_units'].pop('uniqueItems', None)
            properties['relationships']['items']['properties']['source_units'].pop('uniqueItems', None)
        if schema_order == 'catalog_facts_first':
            # Guidance fixes object field order. This opt-in diagnostic writes
            # task facts and relationships before committing to a category.
            order = ('task_summary', 'purchase_kind', 'whole_task_units',
                     'relationships', 'catalog_relation', 'unresolved_scope')
            schema['properties'] = {key: properties[key] for key in order}
            schema['required'] = list(order)
    return schema


def validate_grammar(schema):
    """Use the same CPU compiler as vLLM guidance, without importing vLLM."""
    import llguidance
    # vLLM validates with flexible whitespace, then compiles for generation
    # with the configured whitespace option. Check both paths.
    for flexible in (True, False):
        grammar = llguidance.LLMatcher.grammar_from_json_schema(
            schema, defaults={'whitespace_flexible': flexible})
        error = llguidance.LLMatcher.validate_grammar(grammar)
        if error:
            raise ValueError('Generation grammar rejected before model loading: ' + error)


def prepared_preflight(packets):
    """Compile the exact per-request schema, including each source inventory.

    A generic schema with every possible optional candidate key has a different
    language and can exceed compiler limits. It is never a substitute for the
    required keys and reference bounds the sampler will actually receive.
    """
    if not packets:
        raise ValueError('Prepared grammar preflight requires requests')
    cases, checked, identities = [], set(), set()
    for packet in packets:
        key = packet.get('request_key')
        if type(key) is not str or not key or key in identities:
            raise ValueError('Prepared grammar requests require unique identities')
        identities.add(key)
        generation = packet['generation']
        schema = generation_schema(generation['response_format'], len(packet['spans']), packet['items'],
            schema_order=generation.get('schema_order'), catalog_roles=generation.get('catalog_roles'),
            catalog_fields=generation.get('catalog_fields'), specification_inventory=generation.get('specification_inventory'))
        fingerprint = hashlib.sha256(json.dumps(schema, ensure_ascii=False).encode()).hexdigest()
        if packet.get('generation_schema_sha256', fingerprint) != fingerprint:
            raise ValueError('Prepared effective generation schema digest changed')
        if fingerprint not in checked:
            validate_grammar(schema)
            checked.add(fingerprint)
        cases.append({'request_key': key, 'format': generation['response_format'],
            'source_units': len(packet['spans']), 'generation_schema_sha256': fingerprint, 'status': 'PASS'})
    return {'status': 'PASS', 'requests': len(cases), 'unique_effective_schemas': len(checked), 'cases': cases,
            'original_source_verification_separate': True, 'model_loaded': False, 'gpu_allocated': False}


def preflight(specifications=None):
    import llguidance
    if specifications is None:
        specifications = [(form, count, tuple(range(1, 25)))
            for form in ('compact', 'reasoned', 'factored', 'fact_compact') for count in (1, 512)]
        specifications += [(form, count, (20,)) for form in ('software_facts', 'software_refs')
                           for count in (1, 512)]
        specifications += [(form, count, tuple(range(10, 19)))
                           for form in ('catalog_scope', 'goods_scope') for count in (1, 512)]
        specifications += [('catalog_conditions', count, tuple(range(10, 19))) for count in (1, 512)]
        specifications += [('catalog_semantics', count, tuple(range(10, 19))) for count in (1, 512)]
    cases = []
    normalized = set()
    for specification in specifications:
        if len(specification) not in (3, 4):
            raise ValueError('Grammar preflight requires format, unit count, items and optional order')
        form, count, items = specification[:3]
        order = specification[3] if len(specification) == 4 else None
        normalized.add((form, count, tuple(items), order))
    for form, count, items, order in sorted(normalized, key=lambda s: (s[0], s[1], s[2], s[3] or '')):
        schema = generation_schema(form, count, items, schema_order=order)
        validate_grammar(schema)
        encode = lambda obj: json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
        cases.append({'format': form, 'source_units': count, 'items': list(items),
            'schema_order': order,
            'generation_schema_sha256': hashlib.sha256(encode(schema)).hexdigest(),
            'generation_schema_ordered_sha256': hashlib.sha256(json.dumps(
                schema, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest(),
            'validation_schema_sha256': hashlib.sha256(encode(output_schema(form, count, items))).hexdigest()})
    return {'llguidance': llguidance.__version__, 'cases': cases, 'model_loaded': False,
            'gpu_allocated': False, 'status': 'PASS',
            'source': 'https://github.com/vllm-project/vllm/blob/v0.26.0/vllm/v1/structured_output/backend_guidance.py'}
