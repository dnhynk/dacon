"""Fallible whole-goods review over a source-certified purchase inventory.

Structure code proves which item names form the complete current purchase.
Hybrid retrieval proposes catalog rows to inspect, while a compact directory
keeps every supplied goods name visible so an omitted retrieval hit cannot be
treated as proof of absence.  Gemma resolves item identity; deterministic code
checks source ownership, completeness, catalog rows and designation conditions.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re

import jsonschema

from .catalog_candidates import CatalogCandidates
from .response_contract import loads
from .source_units import render, unitize


ITEMS = tuple(range(10, 19))
ALLOWED_UNCERTAINTY = {
    'explicit_multiple_items_catalog_identity_unresolved',
    'unlisted_code_is_not_proof_of_general_purchase',
}


def goods_catalog(products, encoder=None):
    rows = [row for row in products.values() if not row['대분류'].endswith('서비스')]
    return CatalogCandidates(rows, encoder=encoder)


def inventory(source_product):
    lists = [value for value in source_product.get('purchase_item_lists', ())
             if value.get('whole_purchase_certified')
             and not value.get('catalog_identity_complete')]
    if len(lists) != 1:
        return None
    result = lists[0]
    if (type(result.get('observed_item_count')) is not int
            or not 2 <= result['observed_item_count'] <= 64
            or len(result.get('rows', ())) != result['observed_item_count']
            or any(not row.get('name', {}).get('text') for row in result['rows'])):
        return None
    return result


def eligible(record, source_product):
    return (record.get('meta', {}).get('업무구분') == '물품(내자)'
            and source_product.get('status') == 'unknown'
            and bool(source_product.get('uncertainty'))
            and set(source_product['uncertainty']) <= ALLOWED_UNCERTAINTY
            and inventory(source_product) is not None)


def source_review_blocker(record, source):
    if not eligible(record, source['product']):
        return 'source_purchase_inventory_not_eligible'
    if not source['qualification']['complete']:
        return 'incomplete_qualification_source'
    return None


def schema(max_units, items=ITEMS):
    if tuple(items) != ITEMS or type(max_units) is not int or max_units < 1:
        raise ValueError('Goods scope review needs items10..18 and original source units')
    refs = {'type': 'array', 'maxItems': 16, 'uniqueItems': True,
            'items': {'type': 'integer', 'minimum': 1, 'maximum': max_units}}
    relation = {'type': 'object', 'additionalProperties': False,
        'required': ['item_id', 'relation', 'catalog_row_id', 'source_units'],
        'properties': {
            'item_id': {'type': 'integer', 'minimum': 1, 'maximum': 64},
            'relation': {'type': 'string',
                         'enum': ['listed_category', 'outside_supplied_goods_catalog', 'unknown']},
            'catalog_row_id': {'type': 'integer', 'minimum': 0, 'maximum': 1000},
            'source_units': refs}}
    return {'type': 'object', 'additionalProperties': False,
        'required': ['purchase_kind', 'whole_purchase_units', 'item_relations', 'unresolved_scope'],
        'properties': {
            'purchase_kind': {'type': 'string', 'enum': ['goods', 'mixed', 'unknown']},
            'whole_purchase_units': refs,
            'item_relations': {'type': 'array', 'minItems': 1, 'maxItems': 64,
                               'items': relation},
            'unresolved_scope': {'type': 'boolean'}}}


def decode_response(text, spans):
    obj = loads(text)
    wire = schema(len(spans))
    wire['properties']['whole_purchase_units'].pop('uniqueItems', None)
    wire['properties']['item_relations']['items']['properties']['source_units'].pop(
        'uniqueItems', None)
    jsonschema.validate(obj, wire)
    changes = []
    for path, refs in [('whole_purchase_units', obj['whole_purchase_units']), *[
            (f'item_relations.{i}.source_units', relation['source_units'])
            for i, relation in enumerate(obj['item_relations'])]]:
        if any(type(number) is not int for number in refs):
            raise ValueError('Source unit IDs must be exact integers')
        unique = list(dict.fromkeys(refs))
        if unique != refs:
            changes.append({'path': path, 'original': refs[:], 'canonical': unique})
            refs[:] = unique
    jsonschema.validate(obj, schema(len(spans)))
    return obj, {'kind': 'idempotent_source_reference_set', 'changes': changes,
        'raw_response_sha256': hashlib.sha256(text.encode()).hexdigest(),
        'semantic_fields_changed': False}


def _ranges(witness, source_product):
    refs = [witness['scope_evidence'], witness['header_evidence']]
    refs.extend(count['scope_evidence'] for count in source_product['purchase_item_counts']
                if count.get('item_list_index') is not None)
    refs.extend(row['block_range'] for row in witness['rows'])
    return [(value['doc_index'], value['start'], value['end']) for value in refs]


def select_source(record, tokenizer, encoder, source_product, *, token_budget, method='hybrid', tool=None):
    from .notice_search import NoticeSearch
    witness = inventory(source_product)
    if witness is None:
        raise ValueError('A source-certified goods inventory is required')
    tool = tool or NoticeSearch(record, tokenizer, encoder if method == 'hybrid' else None)
    required = _ranges(witness, source_product)
    if tool.token_cost(required) > token_budget:
        raise ValueError('Goods inventory source reserve exceeds context budget')
    queries = [row['name']['text'] + ' 실제 용도 재질 규격 구성품' for row in witness['rows']]
    selected = tool.search(ITEMS, token_budget=token_budget, method=method,
        queries=queries, required_ranges=required, selection_policy='evidence_cover')
    selected['diagnostics']['goods_scope_source'] = {
        'method': method, 'whole_purchase_certified': True,
        'observed_item_count': witness['observed_item_count'],
        'required_original_ranges': required,
        'retrieval_is_not_catalog_absence_proof': True}
    return selected


def _identity_query(row, tokenizer, max_tokens=192):
    """Keep source identity context in a short catalog-retrieval query.

    A flattened PDF name such as ``약달력`` or ``보행매트`` is often a lexical
    neighbour of a different catalog item.  The certified detail block already
    contains the intended use and occasionally material/form facts.  Preserve
    those facts for retrieval without turning them into an identity judgment.
    """
    name = re.sub(r'\s+', ' ', row['name']['text']).strip()
    detail = row.get('detail_range', row.get('block_range', {})).get('text', '')
    lines = [re.sub(r'^[\s○●□■ㆍ·-]+|\s+$', '', value)
             for value in re.split(r'[\r\n]+', detail)]
    lines = [re.sub(r'\s+', ' ', value) for value in lines if value.strip()]
    purpose, attributes = [], []
    in_purpose = False
    for line in lines:
        compact = re.sub(r'\s+', '', line)
        if re.match(r'^[①1.)]*용도', compact):
            in_purpose = True
            tail = re.sub(r'^[①1.)]*\s*용\s*도\s*[:：]?', '', line).strip()
            if tail:
                purpose.append(tail)
            continue
        if in_purpose and re.match(r'^[②2.)]*규격', compact):
            in_purpose = False
            continue
        if in_purpose:
            purpose.append(line)
            continue
        if re.search(r'(?:소재|재질|형태|구성품|주요\s*기능|사용\s*대상)\s*[:：]', line):
            attributes.append(line)
    chunks = [name]
    if purpose:
        chunks.append('실제 용도: ' + ' '.join(purpose))
    if attributes:
        chunks.append('형태·재질·구성: ' + ' '.join(attributes[:3]))
    accepted = []
    for chunk in chunks:
        candidate = ' '.join([*accepted, chunk])
        if len(tokenizer.encode(candidate, add_special_tokens=False)) > max_tokens:
            break
        accepted.append(chunk)
    return ' '.join(accepted) if accepted else name


def catalog_candidates(catalog, witness, tokenizer, *, method='hybrid',
                       required_codes=()):
    lexical_queries = [row['name']['text'] for row in witness['rows']]
    dense_queries = [_identity_query(row, tokenizer) for row in witness['rows']]
    return catalog.search(lexical_queries, tokenizer, token_budget=3072, method=method,
        required_codes=required_codes, max_candidates=40,
        selection_policy='semantic_frontier', dense_queries=dense_queries)


def _retrieval_hints(candidate, frontier_depths):
    """Compactly bind fallible catalog suggestions to source inventory rows."""
    hints = []
    frontier_depths = frontier_depths or {}
    for item_id, ranks in enumerate(candidate.get('query_ranks', ()), 1):
        values = []
        lexical = ranks.get('lexical')
        if lexical is not None and lexical <= frontier_depths.get('lexical', 0):
            values.append('L' + str(lexical) + ('E' if ranks.get('lexical_exact') else ''))
        dense = ranks.get('dense')
        if dense is not None and dense <= frontier_depths.get('dense', 0):
            values.append('D' + str(dense))
        if values:
            hints.append('I' + str(item_id) + ':' + ','.join(values))
    return hints


def _unit_numbers(units, evidence):
    return [number for number, unit in enumerate(units, 1)
            if unit.doc_index == evidence['doc_index']
            and unit.start < evidence['end'] and evidence['start'] < unit.end]


def _prepared_inventory(units, witness):
    prepared = []
    rows = witness['rows']
    for index, row in enumerate(rows):
        name_units = _unit_numbers(units, row['name'])
        detail = row.get('detail_range', row['block_range'])
        context_units = _unit_numbers(units, detail)
        if not name_units or not set(name_units) <= set(context_units):
            raise ValueError('Selected goods source omitted a certified item name')
        prepared.append({'item_id': index + 1, 'name': row['name']['text'],
                         'name_source_units': name_units,
                         'context_source_units': context_units,
                         'source_evidence': row['name']})
    return prepared


def prompt(record, selection, tokenizer, catalog, candidates, source_product):
    from .prompts import token_ids, verified_search_spans
    selected = verified_search_spans(record, selection, tokenizer)
    units = unitize(selected)
    witness = inventory(source_product)
    if witness is None:
        raise ValueError('Goods prompt needs one complete observed inventory')
    prepared = _prepared_inventory(units, witness)
    whole = sorted(set(_unit_numbers(units, witness['scope_evidence'])
                       + _unit_numbers(units, witness['header_evidence'])))
    if not whole:
        raise ValueError('Selected goods source omitted the whole-purchase witness')
    directory = [{'row_id': row['row_id'], 'code': row['code'], 'name': row['name']}
                 for row in catalog.rows]
    directory_text = '\n'.join(f"R{row['row_id']}\t{row['name']}" for row in directory)
    frontier_depths = candidates.get('candidate_frontier_depths')
    candidate_rows = [{**{key: row[key] for key in
                       ('row_id', 'code', 'category', 'parent', 'name', 'condition')},
                       'retrieval_hints': _retrieval_hints(row, frontier_depths)}
                      for row in candidates['candidates']]
    system = '''현재 계약에서 구매하는 모든 물품을 제공된 중소기업자간 경쟁제품 고시와 대조한다. 법적 위반 여부는 출력하지 않는다.
원문에서 확인된 I항목은 전체 구매목록이다. 각 I항목을 빠짐없이 한 번씩 판정한다. 크기·성별·포장 단위만 다른 같은 세부품명은 같은 고시 행일 수 있지만, 이름 일부가 겹쳐도 실제 용도·재질·형태가 다르면 같은 물품으로 확정하지 않는다.
전체 명칭 디렉터리는 제공 고시의 물품 행을 하나도 빼지 않은 목록이다. 검색 후보 상세는 비교를 돕는 후보일 뿐이며 검색 순위나 누락을 동일성·부재의 근거로 쓰지 않는다. 후보 밖이라도 전체 디렉터리에서 같은 물품을 찾으면 그 R번호를 사용한다. retrieval_hints의 I는 원문 구매항목, L/D는 어휘/의미 검색 순위, E는 명칭 문자열 포함을 뜻할 뿐 동일성이나 확률이 아니다.
원문 등록코드 정확조회는 그 코드가 제공 고시에 있는지만 보여준다. 미등재 코드는 전체 묶음이 고시 밖이라는 증명은 아니지만, 비슷한 이름의 다른 고시 행으로 그 코드의 물품을 바꾸는 근거도 아니다.
후보 상세의 category와 parent를 실제 용도·재질·형태와 함께 비교한다. 의료·복지용 흡수제품과 일반 의류, 약 보관용 주머니와 인쇄 일정표, 신체 보호대와 의류 액세서리, 실내용 생활매트와 도로 건설자재처럼 표면어만 겹치고 제품 영역이 다른 경우에는 같은 세부품명으로 확정하지 않는다.
purchase_kind=goods는 물품의 배송·설치·교육·검수·보안·하자보수 같은 통상 이행의무를 포함한다. mixed는 물품과 별도로 계약대상이 되는 독립된 용역을 원문에서 확인한 경우에만 쓴다. 서식에서 납품 업무를 용역이라고 부르거나 인력이 투입된다는 이유만으로 mixed를 쓰지 않는다. 계약대상 자체를 해결하지 못한 경우에만 unknown을 쓴다.
relation=listed_category이면 실제 같은 세부품명인 R번호를 catalog_row_id에 쓴다. outside_supplied_goods_catalog이면 catalog_row_id=0, unknown이면 catalog_row_id=0으로 쓴다. 비슷한 후보가 있으나 규격 문맥이 부족하면 unknown이다.
source_units에는 해당 I항목의 이름과 동일성 판단에 사용한 원문 S번호를 쓴다. 모든 항목이 해결된 경우에만 unresolved_scope=false이다. 지정된 JSON 객체 하나만 출력한다.'''
    inventory_text = '\n'.join(
        f"I{item['item_id']} name={item['name']} name_source={item['name_source_units']} "
        f"available_context={item['context_source_units']}" for item in prepared)
    user = ('등록 정보(원문을 대체하지 않음):\n'
            + json.dumps(record.get('meta', {}), ensure_ascii=False, separators=(',', ':'))
            + '\n원문에서 제약으로 확인된 전체 구매목록:\n' + inventory_text
            + '\n원문 등록코드의 제공 고시 정확조회(전체 묶음 판정이 아님):\n'
            + json.dumps(candidates.get('required_lookups', []),
                         ensure_ascii=False, separators=(',', ':'))
            + '\n제공 고시 전체 물품 명칭 디렉터리(' + str(len(directory)) + '행 모두):\n'
            + directory_text
            + '\n혼합 검색의 상세 비교 후보(전체 디렉터리를 대체하지 않음):\n'
            + json.dumps(candidate_rows, ensure_ascii=False, separators=(',', ':'))
            + '\n현재 공고 원문:\n' + render(units)
            + '\nJSON Schema:\n'
            + json.dumps(schema(len(units)), ensure_ascii=False, separators=(',', ':')))
    messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]
    directory_sha = hashlib.sha256(json.dumps(directory, ensure_ascii=False,
        separators=(',', ':')).encode()).hexdigest()
    return {'items': list(ITEMS), 'messages': messages,
        'token_ids': token_ids(tokenizer, messages, True), 'spans': units,
        'coverage': selection.get('coverage'), 'goods_scope': {
            'directory': directory, 'directory_sha256': directory_sha,
            'all_supplied_goods_rows_present': True,
            'prepared_inventory': prepared, 'whole_source_units': whole,
            'purchase_witness': witness, 'catalog_candidates': candidates,
            'retrieval_is_not_absence_proof': True},
        'source_unitization': {'method': 'source_units_v1',
                               'original_source_tokens': selection['source_tokens']}}


def review(record, response, packet, knowledge, baseline=None):
    from .prices import project_prices
    from .qualification import infer, catalog_condition, software_catalog_prices
    spans = packet['spans']
    obj, normalization = decode_response(response['text'], spans)
    baseline = baseline or {f'{prefix}{number}': '0' if prefix == 'v' else ''
                            for number in ITEMS for prefix in ('v', 'e')}
    _, source = knowledge.qualification_decisions(record, baseline)
    original = source['product']
    log = {'model_fact_is_fallible': True, 'model_scope': obj,
           'source_product_before': original, 'source_scope_promoted': False,
           'decisions': {}, 'reference_normalization': normalization}

    def stop(reason):
        log['gate'] = reason
        return None, log

    blocker = source_review_blocker(record, source)
    if blocker:
        return stop(blocker)
    witness = inventory(original)
    shown = packet.get('goods_scope', {})
    catalog = goods_catalog(knowledge.products)
    directory = [{'row_id': row['row_id'], 'code': row['code'], 'name': row['name']}
                 for row in catalog.rows]
    directory_sha = hashlib.sha256(json.dumps(directory, ensure_ascii=False,
        separators=(',', ':')).encode()).hexdigest()
    if (shown.get('directory') != directory or shown.get('directory_sha256') != directory_sha
            or not shown.get('all_supplied_goods_rows_present')):
        return stop('complete_goods_catalog_directory_not_shown')
    prepared = shown.get('prepared_inventory')
    if (not isinstance(prepared, list) or len(prepared) != witness['observed_item_count']
            or [item.get('name') for item in prepared]
               != [row['name']['text'] for row in witness['rows']]):
        return stop('prepared_purchase_inventory_changed')
    expected = list(range(1, len(prepared) + 1))
    relations = obj['item_relations']
    if sorted(relation['item_id'] for relation in relations) != expected:
        return stop('item_relations_not_complete_and_unique')
    if obj['purchase_kind'] != 'goods':
        return stop('model_purchase_kind_unresolved_or_mixed')
    if not set(obj['whole_purchase_units']) & set(shown.get('whole_source_units', ())):
        return stop('whole_purchase_without_certified_source')
    by_id = {item['item_id']: item for item in prepared}
    by_row = {row['row_id']: row for row in catalog.rows}
    listed = []
    for relation in relations:
        item = by_id[relation['item_id']]
        refs = set(relation['source_units'])
        if (not refs or not refs & set(item['name_source_units'])
                or not refs <= set(item['context_source_units'])):
            return stop('item_relation_without_its_original_source')
        kind, row_id = relation['relation'], relation['catalog_row_id']
        if kind == 'listed_category':
            if row_id not in by_row:
                return stop('listed_relation_without_supplied_catalog_row')
            listed.append((relation['item_id'], by_row[row_id]))
        elif row_id != 0:
            return stop('nonlisted_relation_with_catalog_row')
    unresolved = any(relation['relation'] == 'unknown' for relation in relations)
    if unresolved or obj['unresolved_scope'] != unresolved:
        return stop('goods_identity_or_scope_unresolved')
    prices = project_prices(record)
    budget_prices = software_catalog_prices(record, prices['budget'])
    rows = []
    for item_id, row in listed:
        condition = catalog_condition(row['condition'], original['estimate_won'],
            original['budget_won'], estimate_prices=prices['estimated_price'],
            budget_prices=budget_prices, record=record, product_name=row['name'])
        rows.append({'item_id': item_id, 'catalog_row_id': row['row_id'],
            'code': row['code'], 'name': row['name'], 'note': row['condition'],
            'listed': True, 'condition': condition})
    states = {row['condition']['status'] for row in rows}
    if 'unknown' in states:
        log['catalog_conditions'] = rows
        return stop('goods_catalog_conditions_require_more_facts')
    if states & {'met', 'no_stated_condition'}:
        status = 'competition'
    elif states <= {'not_met'}:
        status = 'general'
    else:  # no listed identity survived; every item was affirmatively outside.
        status = 'general'
    evidence = [witness['scope_evidence'], *[row['name'] for row in witness['rows']]]
    product = copy.deepcopy(original)
    product.update(status=status, products=rows,
        mechanism='fallible_complete_goods_catalog_review', identity_evidence=evidence,
        detail_candidates_not_unique_identity=False, uncertainty=[],
        prior_candidate_ambiguity=original['uncertainty'])
    _, facts = infer(record, baseline, knowledge._product_facts, product_override=product)
    log.update(source_scope_promoted=True, product=product,
        item_relations=relations, catalog_conditions=rows,
        qualification=facts['qualification'], decisions=facts['decisions'],
        deferred_decisions=facts.get('deferred_decisions', {}),
        gate='source_predicates_joined_to_fallible_goods_scope')
    result = {}
    for key, decision in facts['decisions'].items():
        result[key] = decision['value']
        result['e' + key[1:]] = decision['evidence']
    return result, log
