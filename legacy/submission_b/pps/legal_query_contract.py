"""Bind an optional legal reading to its source and the exact replaced prompt.

This contract changes one legal block. Notice excerpts, rubrics, output schema
and generation parameters stay in the caller's packet. It grants no authority
to classify a notice or to replace saved model answers.
"""
from __future__ import annotations

import copy
import hashlib
import json

from .prompts import token_ids


START = '\n\n[이번 항목의 배포 법령 참고 발췌]\n'
END = '\n\n[이번 호출의 항목별 판단 안내]\n'


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


def split_legal_block(messages):
    if len(messages) != 2 or messages[0]['role'] != 'system' or messages[1]['role'] != 'user':
        raise ValueError('Expected the canonical two-message prompt')
    body = messages[1]['content']
    if body.count(START) != 1 or body.count(END) != 1:
        raise ValueError('Legal block boundaries are missing or ambiguous')
    prefix, tail = body.split(START)
    legal, suffix = tail.split(END)
    return prefix, legal, suffix


def original_source_tokens(context, laws, tokenizer):
    ranges = {}
    for group in context['selected']:
        for source in group['sources']:
            ranges.setdefault(source['alias'], []).extend(source['spans'])
    count = 0
    for alias, spans in sorted(ranges.items()):
        merged = []
        for lo, hi in sorted(spans):
            if merged and lo <= merged[-1][1]:
                merged[-1][1] = max(hi, merged[-1][1])
            else:
                merged.append([lo, hi])
        for lo, hi in merged:
            count += len(tokenizer.encode(laws[alias][lo:hi], add_special_tokens=False))
    return count


def prepare_legal_arm(control, record, knowledge, tokenizer, *, topic=None):
    """None means a fresh, unchanged control; a topic replaces only legal text."""
    packet = copy.deepcopy(control)
    prefix, legal, suffix = split_legal_block(packet['messages'])
    cap = packet['legal_diagnostics']['max_chars']
    baseline = knowledge.legal_context_v2(record, packet['items'], cap, return_metadata=True)
    if legal != baseline['text'] or packet['legal_diagnostics'] != {k: v for k, v in baseline.items() if k != 'text'}:
        raise ValueError('Control does not contain the current v2 legal context')
    budget = original_source_tokens(baseline, knowledge.laws, tokenizer)
    reading = (knowledge.search_legal_dependencies(topic, max_chars=cap,
               tokenizer=tokenizer, max_source_tokens=budget) if topic is not None else None)
    replacement = reading['text'] if reading is not None else legal
    packet['messages'][1]['content'] = prefix + START + replacement + END + suffix
    packet['legal_reading'] = reading
    packet['legal_control'] = {'version': 'legal_query_contract_v1',
        'original_legal_text_sha256': hashlib.sha256(legal.encode()).hexdigest(),
        'unchanged_message_parts_sha256': _digest([packet['messages'][0], prefix, suffix]),
        'source_token_budget': budget, 'max_chars': cap, 'topic': topic}
    packet['token_ids'] = token_ids(tokenizer, packet['messages'], True)
    packet['prompt_sha256'] = _digest(packet['messages'])
    packet['token_ids_sha256'] = _digest(packet['token_ids'])
    return packet


def rebind_questions(control, packet):
    """A declared question rewrite retains the exact original law selection.

V1 promises unchanged nonlegal content. V2 instead binds the new question
plan, retaining the lookup items and source cap of that original V1 reading.
"""
    if control['legal_control']['version'] != 'legal_query_contract_v1':
        raise ValueError('Only an original legal reading may bind new questions')
    before_prefix, before_law, _ = split_legal_block(control['messages'])
    prefix, actual_law, suffix = split_legal_block(packet['messages'])
    if (before_prefix != prefix or before_law != actual_law
            or control['messages'][0] != packet['messages'][0]
            or control['legal_reading'] != packet['legal_reading']):
        raise ValueError('Question planning changed the retained source or legal reading')
    from .source_questions import validate_structure
    validate_structure(packet)
    contract = copy.deepcopy(control['legal_control'])
    contract.update(version='legal_query_contract_v2', lookup_items=control['items'],
        parent_message_parts_sha256=contract['unchanged_message_parts_sha256'],
        question_contract_sha256=packet['source_questions_sha256'],
        unchanged_message_parts_sha256=_digest([packet['messages'][0], prefix, suffix]))
    packet['legal_control'] = contract


def verify_prepared(packet, record, knowledge, tokenizer):
    contract = packet.get('legal_control')
    if contract is None:
        if packet.get('legal_reading') is not None:
            raise ValueError('Legal reading has no source contract')
        return
    version = contract.get('version')
    if version not in {'legal_query_contract_v1', 'legal_query_contract_v2'}:
        raise ValueError('Unknown legal query contract')
    lookup_items = packet['items']
    if version == 'legal_query_contract_v2':
        from .source_questions import validate_structure
        validate_structure(packet)
        lookup_items = contract.get('lookup_items')
        if (lookup_items != list(range(10, 19))
                or contract.get('question_contract_sha256') != packet['source_questions_sha256']):
            raise ValueError('Legal reading does not bind the declared source questions')
    prefix, actual, suffix = split_legal_block(packet['messages'])
    baseline = knowledge.legal_context_v2(record, lookup_items, contract['max_chars'], return_metadata=True)
    if (contract['original_legal_text_sha256'] != hashlib.sha256(baseline['text'].encode()).hexdigest()
            or contract['source_token_budget'] != original_source_tokens(baseline, knowledge.laws, tokenizer)):
        raise ValueError('Original legal context or token budget changed')
    if contract['unchanged_message_parts_sha256'] != _digest([packet['messages'][0], prefix, suffix]):
        raise ValueError('Nonlegal message content changed')
    expected = (knowledge.search_legal_dependencies(contract['topic'], max_chars=contract['max_chars'],
                tokenizer=tokenizer, max_source_tokens=contract['source_token_budget'])
                if contract['topic'] is not None else None)
    if packet.get('legal_reading') != expected:
        raise ValueError('Legal reading differs from supplied source or dependency plan')
    if actual != (expected['text'] if expected is not None else baseline['text']):
        raise ValueError('Rendered legal text differs from the verified reading')
