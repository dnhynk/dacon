"""Optional reviews over the normal packet's exact original-source selection."""
import copy
import dataclasses
import hashlib
import time

from .prompts import build_prompt
from .generation_contract import generation_schema


def selection(record, control, tokenizer):
    if control.get('source_search') is not None:
        return control['source_search']
    tokens=sum(len(tokenizer.encode(s['text'],add_special_tokens=False)) for s in control['spans'])
    return {'record_id':record['id'],'method':'canonical_shared_source',
        'spans':control['spans'],'source_tokens':tokens,'source_token_budget':max(1,tokens),
        'documents':[{'doc_index':i,'doc_id':d['doc_id'],
            'doc_sha256':hashlib.sha256(d['text'].encode()).hexdigest()} for i,d in enumerate(record['docs'])],
        'coverage':{**control['coverage'],'absence_verified':False},
        'diagnostics':{'same_normal_source':True}}


def packet(record, body, source, *, family, profile, fmt, output_tokens, max_model_len):
    from submission.b4_entry import digest
    if len(body['token_ids'])+output_tokens+32>max_model_len:
        raise ValueError('Optional specialist exceeds the fixed context budget without source truncation')
    spans=[dataclasses.asdict(s) for s in body['spans']]
    return {'request_key':f'{family}:{record["id"]}:{body["items"][0]}',
        'record_id':record['id'],'family':family,'batch':profile,'items':body['items'],
        'messages':body['messages'],'token_ids':body['token_ids'],'spans':spans,
        'prompt_sha256':digest(body['messages']),'token_ids_sha256':digest(body['token_ids']),
        'source_sha256':digest(spans),'source_search':source,'coverage':body['coverage'],
        'catalog_scope':body.get('catalog_scope'),
        **({'goods_scope':body['goods_scope']} if body.get('goods_scope') is not None else {}),
        'source_unitization':body.get('source_unitization'),
        **({'task_field_groups':body['task_field_groups']} if 'task_field_groups' in body else {}),
        'generation':{'response_format':fmt,'thinking_budget':0,'max_output_tokens':output_tokens},
        'generation_schema_sha256':digest(generation_schema(fmt,len(spans),body['items']))}


def catalog_packet(pipe, record, control):
    from .catalog_scope import eligible as service_eligible, prompt as service_prompt
    from .goods_scope import (catalog_candidates as goods_candidates,
        eligible as goods_eligible, goods_catalog, inventory as goods_inventory,
        prompt as goods_prompt, select_source as goods_source)
    _, facts=pipe.knowledge.qualification_decisions(record,{f'v{i}':'0' for i in range(10,19)})
    source_product=facts['product']
    service=service_eligible(record,source_product)
    goods=goods_eligible(record,source_product)
    if not (service or goods):
        return None
    shared=selection(record,control,pipe.tokenizer)
    policy=getattr(pipe.config,'catalog_source_policy','shared')
    first=getattr(pipe.config,'prompt_layout','current')!='current'
    grouped=getattr(pipe.config,'catalog_task_groups',False)
    stats=pipe.catalog_source_preparation
    stats['eligible_notices'] += 1
    cap=shared['source_tokens']
    stats['source_token_cap_total'] += cap
    began=time.monotonic()
    if goods:
        method='hybrid' if policy=='task_hybrid' else 'lexical'
        if method=='hybrid' and pipe._source_encoder is None:
            from .embeddings import BGEDenseEncoder
            pipe._source_encoder=BGEDenseEncoder()
        if getattr(pipe,'_goods_catalog',None) is None:
            pipe._goods_catalog=goods_catalog(pipe.knowledge.products,
                pipe._source_encoder if method=='hybrid' else None)
        witness=goods_inventory(source_product)
        required_codes=sorted({
            code for code in (
                list(source_product.get('paired_meta_purchase_codes', ()))
                + [row.get('code') for row in source_product.get('products', ())])
            if isinstance(code, str) and code
        })
        candidates=goods_candidates(pipe._goods_catalog,witness,pipe.tokenizer,
            method=method,required_codes=required_codes)
        from .notice_search import NoticeSearch
        tool=NoticeSearch(record,pipe.tokenizer,
            pipe._source_encoder if method=='hybrid' else None)
        attempted=[]
        budget=cap
        for _ in range(9):
            attempted.append(budget)
            try:
                src=goods_source(record,pipe.tokenizer,pipe._source_encoder,source_product,
                    token_budget=budget,method=method,tool=tool)
                src['diagnostics']['integrated_goods_catalog_producer']={
                    'policy':policy,'method':method,'shared_Q_source_token_cap':cap,
                    'attempted_source_budgets':list(attempted),
                    'scope':'current_notice_only','retrieval_is_not_absence_proof':True,
                    'complete_catalog_name_directory_in_prompt':True}
                body=goods_prompt(record,src,pipe.tokenizer,pipe._goods_catalog,candidates,source_product)
                result=packet(record,body,src,family='Q',profile='Q10',fmt='goods_scope',
                              output_tokens=1536,max_model_len=pipe.config.max_model_len)
            except ValueError as exc:
                if (not str(exc).startswith('Optional specialist exceeds ')
                        and not str(exc).startswith('Goods inventory source reserve exceeds ')):
                    raise
                if budget<=1:
                    break
                budget=max(1,int(budget*.8));stats['context_budget_reductions']+=1
                continue
            stats['searched_notices']+=1
            stats['source_tokens_total']+=src['source_tokens']
            stats['seconds']+=time.monotonic()-began
            return result
        stats['shared_fallbacks']+=1
        stats['seconds']+=time.monotonic()-began
        return None
    if policy == 'shared' or not cap:
        body=service_prompt(record,shared,pipe.tokenizer,pipe.knowledge.products,
                    explain_contract=pipe.config.catalog_review=='explicit',
                    q10_variant=pipe.config.q10_variant,max_model_len=pipe.config.max_model_len,catalog_first=first)
        stats['source_tokens_total'] += shared['source_tokens']
        stats['seconds'] += time.monotonic()-began
        return packet(record,body,shared,family='Q',profile='Q10',fmt='catalog_scope',
                      output_tokens=1536,max_model_len=pipe.config.max_model_len)

    from .task_context import TaskContextSearch
    if policy == 'task_hybrid' and pipe._source_encoder is None:
        from .embeddings import BGEDenseEncoder
        pipe._source_encoder=BGEDenseEncoder()
    tool=TaskContextSearch(record,pipe.tokenizer,
                           pipe._source_encoder if policy == 'task_hybrid' else None)
    budget=cap
    attempted=[]
    for _ in range(9):
        attempted.append(budget)
        src=tool.select(budget,method='hybrid' if policy == 'task_hybrid' else 'lexical')
        src['diagnostics']['integrated_catalog_producer']={
            'policy':policy,'shared_Q_source_token_cap':cap,
            'attempted_source_budgets':list(attempted),'task_groups':grouped,
            'scope':'current_notice_only','retrieval_is_not_absence_proof':True}
        body=service_prompt(record,src,pipe.tokenizer,pipe.knowledge.products,
                    explain_contract=pipe.config.catalog_review=='explicit',task_groups=grouped,
                    q10_variant=pipe.config.q10_variant,max_model_len=pipe.config.max_model_len,catalog_first=first)
        try:
            result=packet(record,body,src,family='Q',profile='Q10',fmt='catalog_scope',
                          output_tokens=1536,max_model_len=pipe.config.max_model_len)
        except ValueError as exc:
            if not str(exc).startswith('Optional specialist exceeds '):
                raise
            if budget <= 1:
                break
            budget=max(1,int(budget*.8))
            stats['context_budget_reductions'] += 1
            continue
        stats['searched_notices'] += 1
        stats['source_tokens_total'] += src['source_tokens']
        stats['seconds'] += time.monotonic()-began
        return result

    # Keep the previously valid Q packet when the extra source addresses cannot
    # fit. This fallback is whole-packet and cannot mix model inputs or answers.
    fallback=copy.deepcopy(shared)
    fallback.setdefault('diagnostics',{})['integrated_catalog_fallback']={
        'requested_policy':policy,'reason':'task_context_packet_exceeds_context',
        'shared_Q_source_token_cap':cap,'attempted_source_budgets':attempted}
    body=service_prompt(record,fallback,pipe.tokenizer,pipe.knowledge.products,
                explain_contract=pipe.config.catalog_review=='explicit',
                q10_variant=pipe.config.q10_variant,max_model_len=pipe.config.max_model_len,catalog_first=first)
    stats['shared_fallbacks'] += 1
    stats['source_tokens_total'] += fallback['source_tokens']
    stats['seconds'] += time.monotonic()-began
    return packet(record,body,fallback,family='Q',profile='Q10',fmt='catalog_scope',
                  output_tokens=1536,max_model_len=pipe.config.max_model_len)


def software_packet(pipe, record, control):
    src=selection(record,control,pipe.tokenizer)
    cfg=dataclasses.replace(pipe.route.config,response_format='software_refs',legal_chars=0,product_facts=False)
    body=build_prompt(record,pipe.knowledge,cfg,pipe.tokenizer,(20,),source_selection=src)
    return packet(record,body,src,family='W',profile='W20',fmt='software_refs',
                  output_tokens=2048,max_model_len=pipe.config.max_model_len)
