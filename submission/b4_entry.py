"""One canonical consumer with explicitly recorded input strategies."""
from __future__ import annotations
import copy
import dataclasses
import hashlib
import json
from pathlib import Path
import time
import jsonschema

HERE=Path(__file__).resolve().parent
from submission.original_a.knowledge import Knowledge as OriginalKnowledge
from submission.original_a.prompts import Config as OriginalConfig, build_shared_prompts as original_prompts
from submission.pps.knowledge import Knowledge
from submission.pps.pipeline import _response_row, parse_output
from submission.pps.prompts import Config, output_schema, build_shared_prompts
from submission.pps.retrieval import Span
from submission.pps.v20_route import UniformV20Route
from submission.pps.response_contract import loads as response_json

GROUPS=(tuple(range(1,10)),tuple(range(10,19)),tuple(range(19,25)))
PROFILES=('A1','A10','A19','L19')


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False).encode()).hexdigest()


def restored(packet):
    return {**packet,'spans':[Span(**s) if isinstance(s,dict) else s for s in packet['spans']]}


def parse_error(packet,response):
    try:
        if response.get('finish_reason') not in ('stop','eos_token'):
            raise ValueError('Incomplete first/final answer')
        obj=response_json(response['text'])
        if packet['generation']['response_format'] == 'specification_candidates':
            from submission.pps.specification_candidate_review import decode
            if tuple(packet['items']) != (9,):
                raise ValueError('Candidate specification review requires v9')
            decode(response['text'], restored(packet)['spans'], packet.get('specification_inventory'))
        elif packet['generation']['response_format'] == 'catalog_semantics':
            from submission.pps.catalog_semantics import decode
            if tuple(packet['items']) != GROUPS[1]:
                raise ValueError('Semantic condition review requires items10..18')
            decode(response['text'], restored(packet)['spans'],
                   source_roles=packet['generation'].get('catalog_roles'))
        elif packet['generation']['response_format'] == 'catalog_conditions':
            from submission.pps.catalog_condition_review import ITEMS, decode
            if tuple(packet['items']) != ITEMS:
                raise ValueError('Condition review requires items10..18')
            decode(response['text'], restored(packet)['spans'])
        elif packet['generation']['response_format'] == 'catalog_scope':
            from submission.pps.catalog_scope import ITEMS, decode_response
            if tuple(packet['items']) != ITEMS:
                raise ValueError('Service scope review requires items10..18')
            decode_response(response['text'], restored(packet)['spans'])
        elif packet['generation']['response_format'] == 'goods_scope':
            from submission.pps.goods_scope import ITEMS, decode_response
            if tuple(packet['items']) != ITEMS:
                raise ValueError('Goods scope review requires items10..18')
            decode_response(response['text'], restored(packet)['spans'])
        else:
            jsonschema.validate(obj,output_schema(packet['generation']['response_format'],len(packet['spans']),packet['items']))
        if packet['generation']['response_format'] in {'specification_scope', 'specification_relations'}:
            if packet['generation']['response_format'] == 'specification_relations':
                from submission.pps.specification_relations import decode
            else:
                from submission.pps.specification_scope import decode
            decode(response['text'], restored(packet)['spans'])
        elif packet['generation']['response_format'] in {'software_facts', 'software_refs'}:
            from submission.pps.software_facts import validate
            validate(response['text'], restored(packet)['spans'], expected_format=packet['generation']['response_format'])
        elif packet['generation']['response_format'] not in {
                'catalog_scope', 'goods_scope', 'catalog_conditions',
                'catalog_semantics', 'specification_candidates'}:
            parse_output(response['text'],restored(packet)['spans'],tuple(packet['items']),rec=None)
        return None
    except (ValueError,TypeError,KeyError,jsonschema.ValidationError) as exc:
        return type(exc).__name__+': '+str(exc).splitlines()[0]


class B4Pipeline:
    def __init__(self,data_dir,tokenizer,input_strategy=None, *, source_policy=None, encoder=None,
                 specification_review=None, legal_policy=None, catalog_review=None, software_review=None,
                 a10_thinking_budget=None, a_cohort_size=None, a10_question_policy=None,
                 catalog_source_policy=None, catalog_task_groups=None):
        self.config=Config.load(HERE/'model/config.json')
        # Validate the requested combination once. Intermediate replacements can
        # reject a valid preserved control before its other overrides are applied.
        overrides = {field:value for field,value in (
            ('input_strategy',input_strategy), ('notice_source_policy',source_policy),
            ('specification_review',specification_review), ('legal_source_policy',legal_policy),
            ('catalog_review',catalog_review), ('software_review',software_review),
            ('thinking_token_budget',a10_thinking_budget), ('a_cohort_size',a_cohort_size),
            ('a10_question_policy',a10_question_policy), ('catalog_source_policy',catalog_source_policy),
            ('catalog_task_groups',catalog_task_groups))
            if value is not None}
        if overrides:
            self.config=dataclasses.replace(self.config,**overrides)
        self.original_config=OriginalConfig.load(HERE/'model/original_a.json')
        self.knowledge=Knowledge(data_dir)
        self.original_knowledge=OriginalKnowledge(data_dir)
        self.route=UniformV20Route(data_dir,tokenizer,
                                   audited_config=self.config if self.config.input_strategy=='audited' else None)
        self.tokenizer=tokenizer
        self._source_encoder=encoder
        self._goods_catalog=None
        self.source_preparation={'notices':0,'seconds':0.,'context_budget_reductions':0}
        self.catalog_source_preparation={'eligible_notices':0,'searched_notices':0,'seconds':0.,
            'context_budget_reductions':0,'shared_fallbacks':0,'source_token_cap_total':0,
            'source_tokens_total':0}
        self.specialist_preparation=[]
        assert self.original_config.rubric_version=='v4'
        assert self.original_config.response_format=='fact_compact'

    def _searched_prompts(self, record, a_control, l_control):
        """Use the normal producer with a matched original-source token cap.

        Document vectors live in this call's NoticeSearch and are reused by A
        and L only for this notice. The fixed encoder can live across notices;
        no record content, search scores or predictions are shared across them.
        """
        policy = self.config.notice_source_policy
        if policy == 'current':
            return [*a_control, l_control]
        if self.tokenizer is None:
            raise ValueError('Integrated source search requires the actual source tokenizer')
        from submission.pps.notice_search import NoticeSearch, factual_queries
        began = time.monotonic()
        context_policy = policy in {'purchase_context','purchase_context_hybrid'}
        if context_policy:
            from submission.pps.purchase_context_search import PurchaseContextSearch
            tool = PurchaseContextSearch(record, self.tokenizer, self._source_encoder)
            if not tool.has_context:
                return [*a_control, l_control]
        if policy in {'evidence_cover','purchase_context_hybrid'} and self._source_encoder is None:
            from submission.pps.embeddings import BGEDenseEncoder
            self._source_encoder = BGEDenseEncoder()
        if context_policy:
            tool.encoder = self._source_encoder if policy == 'purchase_context_hybrid' else None
        else:
            tool = NoticeSearch(record, self.tokenizer,
                                self._source_encoder if policy == 'evidence_cover' else None)

        def selected(controls, items, build):
            assert all(c['spans'] == controls[0]['spans'] for c in controls)
            cap = sum(len(self.tokenizer.encode(s.text, add_special_tokens=False)) for s in controls[0]['spans'])
            if not cap:
                return controls
            budgets = []
            budget = cap
            for _ in range(9):
                budgets.append(budget)
                kwargs = ({'method':'hybrid', 'selection_policy':'evidence_cover'} if policy == 'evidence_cover'
                          else {'method':'lexical', 'selection_policy':'rrf', 'queries':factual_queries(items)})
                selection = (tool.select(budget, method='hybrid' if policy == 'purchase_context_hybrid' else 'lexical')
                             if context_policy else tool.search(items, token_budget=budget, **kwargs))
                selection['diagnostics']['integrated_producer'] = {
                    'policy':policy, 'current_source_token_cap':cap, 'attempted_source_budgets':list(budgets),
                    'scope':'current_notice_only', 'retrieval_is_not_absence_proof':True}
                try:
                    result = build(selection)
                except ValueError as exc:
                    if not str(exc).startswith('Verified search result exceeds '):
                        raise
                    if budget <= 1:
                        break
                    budget = max(1, int(budget * .8))
                    self.source_preparation['context_budget_reductions'] += 1
                    continue
                return result
            # The baseline already fits. A fixed fallback preserves a valid
            # entrypoint when the serialized source enumeration costs too much.
            return [{**c, 'source_strategy_fallback': {'policy':policy,
                'reason':'searched_source_enumeration_exceeds_context',
                'current_source_token_cap':cap, 'attempted_source_budgets':budgets}} for c in controls]

        a = selected(a_control, tuple(range(1,25)), lambda selection: build_shared_prompts(
            record,self.knowledge,self.config,self.tokenizer,GROUPS,source_selection=selection))
        l = ([l_control] if context_policy else
             selected([l_control], (20,), lambda selection: [self.route.prompt(record,source_selection=selection)]))
        self.source_preparation['notices'] += 1
        self.source_preparation['seconds'] += time.monotonic() - began
        return [*a, *l]

    def bundle(self,record):
        if self.config.input_strategy=='audited':
            a_config=self.config
            prompts=build_shared_prompts(record,self.knowledge,a_config,self.tokenizer,GROUPS)
        else:
            a_config=self.original_config
            prompts=original_prompts(record,self.original_knowledge,a_config,self.tokenizer,GROUPS)
        prompts=self._searched_prompts(record,prompts,self.route.prompt(record))
        result=[]
        for profile,prompt in zip(PROFILES,prompts):
            family=profile[0];first=int(profile[1:]);rid=record['id']
            cfg=self.route.config if family=='L' else a_config
            spans=[dataclasses.asdict(s) for s in prompt['spans']]
            ids=prompt['token_ids']
            if ids is None:raise ValueError('A fixed local tokenizer is required')
            if len(ids)+cfg.max_output_tokens+32>cfg.max_model_len:
                raise ValueError('Current prompt exceeds the fixed context budget')
            result.append({'request_key':f'{family}:{rid}:{first}','record_id':rid,'family':family,'batch':profile,
                'items':list(prompt['items']),'messages':prompt['messages'],'token_ids':ids,
                'input_strategy':self.config.input_strategy,'rubric_version':cfg.rubric_version,
                'prompt_sha256':digest(prompt['messages']),'token_ids_sha256':digest(ids),
                'spans':spans,'source_sha256':digest(spans),'comparison_facts':prompt.get('comparison_facts'),
                'coverage':prompt['coverage'],'legal_diagnostics':prompt.get('legal_diagnostics'),
                **({'source_search':prompt['source_search']} if prompt.get('source_search') is not None else {}),
                **({'source_strategy_fallback':prompt['source_strategy_fallback']} if 'source_strategy_fallback' in prompt else {}),
                **({'source_unitization':prompt['source_unitization']} if 'source_unitization' in prompt else {}),
                'generation':{'response_format':cfg.response_format,
                    'thinking_budget':cfg.thinking_budget_for(prompt['items']),'max_output_tokens':cfg.max_output_tokens},
                'schema_sha256':digest(output_schema(cfg.response_format,len(spans),prompt['items']))})
        if self.config.legal_source_policy != 'current':
            result[1] = self.legal_packet(record, result[1])
        if self.config.a10_question_policy == 'source_questions':
            from .pps.source_questions import prepare
            result[1] = prepare(self, record, result[1])
        if self.config.specification_review in {'candidates','gated_candidates','gated_source_candidates'}:
            specialist = self.specification_packet(record, result[0])
            if specialist is not None:
                result.append(specialist)
        from .pps.specialist_packets import catalog_packet, software_packet
        for enabled,prepare,base in ((self.config.catalog_review!='current',catalog_packet,result[0]),
                                    (self.config.software_review!='current',software_packet,result[3])):
            if enabled:
                specialist=prepare(self,record,base)
                if specialist is not None:
                    result.append(specialist)
                    self.specialist_preparation.append({'record_id':record['id'],'status':'prepared',
                        'profile':specialist['batch'],'source_tokens':specialist['source_search']['source_tokens']})
        return result

    def legal_packet(self, record, control):
        """An explicit A10 alternative with the same notice and law token caps."""
        from submission.pps.legal_query_contract import prepare_legal_arm
        if control['batch'] != 'A10' or tuple(control['items']) != GROUPS[1]:
            raise ValueError('Integrated legal search requires the shared A10 packet')
        packet = prepare_legal_arm(control, record, self.knowledge, self.tokenizer,
                                   topic='direct_production')
        if len(packet['token_ids']) + self.config.max_output_tokens + 32 > self.config.max_model_len:
            return {**control, 'legal_strategy_fallback': 'dependency_enumeration_exceeds_context'}
        return packet

    def specification_packet(self, record, control):
        """Review v9 against exactly the A source, without a second source search.

        Oversized source inventories retain the shared judgment and record why
        no specialist was called. No candidates, cells or answers are truncated.
        """
        from submission.pps.specification_candidate_review import matched_prompts, context_prompts, FORMAT, CONTEXT_ARM
        from submission.pps.generation_contract import generation_schema
        spans = restored(control)['spans']
        source_tokens = sum(len(self.tokenizer.encode(s.text, add_special_tokens=False)) for s in spans)
        selection = control.get('source_search')
        if selection is None:
            selection = {'record_id':record['id'], 'method':'canonical_shared_source',
                'spans':[dataclasses.asdict(s) for s in spans],
                'source_tokens':source_tokens, 'source_token_budget':max(1,source_tokens),
                'documents':[{'doc_index':di, 'doc_id':doc['doc_id'],
                    'doc_sha256':hashlib.sha256(doc['text'].encode()).hexdigest()}
                    for di,doc in enumerate(record['docs'])],
                'diagnostics':{'shared_A_source_unchanged':True},
                'coverage':{**control['coverage'], 'absence_verified':False}}
        try:
            from submission.pps.supply_lists import candidates as supply_candidates
            include_supply = (self.config.specification_review == 'gated_source_candidates'
                              and any(supply_candidates(d['text']) for d in record['docs']))
            # Versioned inventories keep old packets replayable, while every
            # newly prepared S9 packet can expose a typed flattened-table
            # relation when that complete relation is already inside A's
            # finite source budget.
            options = {'include_flattened': True}
            if include_supply:
                options['include_supply'] = True
            if self.config.specification_review == 'gated_source_candidates':
                prompt = context_prompts(record, self.knowledge, self.config, self.tokenizer,
                                         selection, **options)[CONTEXT_ARM]
            else:
                prompt = matched_prompts(record, self.knowledge, self.config, self.tokenizer,
                                         selection, **options)[FORMAT]
        except ValueError as exc:
            bounded_errors = ('Verified search result exceeds ', 'Matched specification inputs exceed context;',
                'Candidate review input exceeds the common context budget',
                'Candidate context input exceeds the common model context budget',
                'Candidate review requires bounded units and candidates;')
            if not str(exc).startswith(bounded_errors):
                raise
            self.specialist_preparation.append({'record_id':record['id'],
                'status':'shared_judgment_retained', 'reason':str(exc), 'source_tokens':source_tokens})
            control['specialist_fallback'] = self.specialist_preparation[-1]
            return None
        values = [dataclasses.asdict(s) for s in prompt['spans']]
        packet = {**control, 'request_key':f"S:{record['id']}:9", 'batch':'S9', 'family':'A', 'items':[9],
            'messages':prompt['messages'], 'token_ids':prompt['token_ids'],
            'prompt_sha256':digest(prompt['messages']), 'token_ids_sha256':digest(prompt['token_ids']),
            'spans':values, 'source_sha256':digest(values), 'source_layout':'finite_units',
            'source_search':selection, 'coverage':prompt['coverage'], 'comparison_facts':None,
            'legal_diagnostics':prompt.get('legal_diagnostics'),
            'specification_inventory':prompt['specification_inventory'], 'generation':prompt['generation'],
            'schema_sha256':digest(output_schema(FORMAT,len(values),(9,))),
            'generation_schema_sha256':digest(generation_schema(FORMAT,len(values),(9,),
                specification_inventory=prompt['specification_inventory']))}
        # The specialist has a separate prompt; shared-A-only annotations must
        # not purport to describe its legal content or finite source layout.
        for key in ('source_unitization','legal_reading','legal_control','specialist_fallback'):
            packet.pop(key, None)
        self.specialist_preparation.append({'record_id':record['id'], 'status':'prepared',
            'source_tokens':source_tokens, 'candidates':len(prompt['specification_inventory']['candidates'])})
        return packet

    def packets(self,records):
        if not records or len({r['id'] for r in records})!=len(records):
            raise ValueError('Current records must be nonempty and uniquely identified')
        bundles=[]
        for record in records:
            guard=getattr(self,'preparation_guard',None)
            if guard is not None:
                guard()
            bundles.append(self.bundle(record))
            if guard is not None:
                guard()
        return [packet for profile in (*PROFILES,'Q10','W20','S9') for bundle in bundles
                for packet in bundle if packet['batch'] == profile]

    def consume(self,record,packet,response):
        question_plan = None
        if 'source_questions' in packet:
            from .pps.source_questions import validate
            question_plan = validate(record, packet, self.knowledge)
            if not question_plan['model_items']:
                raise ValueError('Code-only A10 must not consume a model response')
        prompt=restored(packet)
        if packet['generation']['response_format'] == 'specification_candidates':
            if packet['family'] != 'A':
                raise ValueError('Candidate specification review uses the A family')
            from submission.pps.specification_candidate_review import review, overlay_review
            consumer = overlay_review if packet.get('batch') == 'S9' else review
            return consumer(record, response, prompt)
        if packet['generation']['response_format'] == 'catalog_semantics':
            if packet['family'] != 'C':
                raise ValueError('Semantic condition review uses the C family')
            from submission.pps.catalog_semantics import review
            return review(record, response, prompt, self.knowledge)
        if packet['generation']['response_format'] == 'catalog_conditions':
            if packet['family'] != 'C':
                raise ValueError('Condition review uses the C family')
            from submission.pps.catalog_condition_review import review
            return review(record, response, prompt, self.knowledge)
        if packet['generation']['response_format'] in {'specification_scope', 'specification_relations'}:
            if packet['family'] != 'A':
                raise ValueError('Specification scope uses the A family')
            if packet['generation']['response_format'] == 'specification_relations':
                from submission.pps.specification_relations import review
            else:
                from submission.pps.specification_scope import review
            return review(record, response, prompt)
        if packet['generation']['response_format'] == 'catalog_scope':
            if packet['family'] != 'Q':
                raise ValueError('Optional scope diagnostics require the Q family')
            from submission.pps.catalog_scope import review
            return review(record, response, prompt, self.knowledge)
        if packet['generation']['response_format'] == 'goods_scope':
            if packet['family'] != 'Q':
                raise ValueError('Optional goods scope diagnostics require the Q family')
            from submission.pps.goods_scope import review
            return review(record, response, prompt, self.knowledge)
        if packet['family']=='W':
            if tuple(packet['items'])!=(20,) or packet['generation']['response_format']!='software_refs':
                raise ValueError('Optional software review requires only v20 source relations')
            from submission.pps.software_facts import decide
            decision=decide(record,response['text'],prompt['spans'],expected_format='software_refs',
                            absence_scope='source_scan')
            row={} if decision['value'] is None else {'v20':decision['value'],'e20':''}
            return row,[{'source':'optional_source_bound_software_review','decision':decision,
                         'unknown_preserves_independent_judgment':True}]
        if packet['family']=='L':
            return self.route.consume(record,response,prompt)
        items=tuple(packet['items'])
        row,details=_response_row(record,response,prompt,items,self.config,self.knowledge,items)
        result = {f'{field}{k}':int(row[f'v{k}']) if field=='v' else row[f'e{k}'] for k in items for field in ('v','e')}
        if question_plan is not None:
            from .pps.source_questions import fixed_row
            result.update(fixed_row(question_plan))
            details.append({'source': 'independent_source_question_plan',
                'fixed': question_plan['fixed'], 'model_items': question_plan['model_items'],
                'source_questions_sha256': packet['source_questions_sha256']})
        return result,details


def assemble(records,packets,call_rows):
    b3={r['id']:{'id':r['id'],**{f'v{k}':None for k in range(1,25)},**{f'e{k}':'' for k in range(1,25)}} for r in records}
    for packet in packets:
        row=call_rows[packet['request_key']]
        if packet['family'] in {'A','Q'} and row is not None:
            if packet['family']=='Q' and not set(row)<={f'{f}{i}' for i in range(10,19) for f in ('v','e')}:
                raise ValueError('Optional catalog review may only update items10..18')
            b3[packet['record_id']].update(row)
    b4=copy.deepcopy(b3)
    for packet in packets:
        if packet['family']=='L':
            row=call_rows[packet['request_key']]
            if row is not None and set(row) not in (set(), {'v20','e20'}):
                raise ValueError('Uniform L route may only replace v20/e20')
            b4[packet['record_id']].update(row if row is not None else {'v20':None,'e20':''})
    for packet in packets:
        if packet['family']=='W':
            row=call_rows[packet['request_key']]
            if row is not None and not set(row)<={'v20','e20'}:
                raise ValueError('Optional software review may only update v20/e20')
            if row is not None:
                b4[packet['record_id']].update(row)
    assert all(b4[rid][key]==row[key] for rid,row in b3.items() for key in row if key not in {'v20','e20'})
    return b3,b4
