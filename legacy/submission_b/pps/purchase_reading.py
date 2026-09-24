"""Source-to-catalog-to-source retrieval with one cumulative reading budget.

The seed is observed purchase structure, not a title-only identity gate. Each
intermediate original range stays in the final reading. Static catalog scores
are only query suggestions; they do not set applicability or certify absence.
"""
from __future__ import annotations

from dataclasses import asdict, replace
import re

from .notice_search import merge_ranges, _validate_source_budget
from .products import CODE, compact, non_task_scope_role, scope_spans
from .purchase_tables import purchase_tables, _NAMES, _ATTRS, _OTHER, _cell
from .retrieval import Span, _source_units
from .source_units import unitize


GENERIC = (
    '이번 계약으로 구입하고 납품하는 물품 전체의 품명, 종류와 수량을 나열한 목록, 규격, 구성품',
    '납품 제품의 실제 용도와 재질, 성능과 사양, 개별 장비와 부속품의 관계',
    '제시한 제품이나 규격을 대체하거나 동등품을 납품할 수 있는 조건, 공통 적용사항과 예외',
)
_LIST = re.compile(r'^(?:[○◯❍□■ㆍ·-]|(?:\d+[.)]|[가-하][.)]))*'
                   r'(?:구입|구매|물품)(?:품목|내역|목록)(?:[:：|]|$)')
_SPEC_FIELD = re.compile(r'^[ \t]*(?:[（(]?[0-9가-하]+[）).][ \t]*)?'
                        r'(?:형[ \t]*식[ \t]*명|기[ \t]*종[ \t]*명|물[ \t]*품[ \t]*명)[ \t]*[:：]')
_FORM = re.compile(r'신청서|서약서|동의서|운송장|완료보고서|실적증명|평가표|평가항목|인감')
_BULLET = re.compile(r'^\s*[-•·ㆍ○◯❍]')
_ONLY_UNIT = re.compile(r'(?:[0-9.,×*~±+%()/\- ]|[kmgcnu]?m|kg|ml|hz|gb|개|식|대|명|장|팩|병|봉|세트|톤|입)+$', re.I)


def seed_candidates(record):
    candidates = []
    def add(di, lo, hi, role, priority, context=()):
        ranges = merge_ranges([(di,lo,hi),*context],record['docs'])
        if any(c['ranges']==ranges for c in candidates):
            return
        candidates.append({'ranges':ranges,'role':role,'priority':priority,
            'doc_index':di,'start':lo,'end':hi,'whole_purchase_certified':False})
    for span in scope_spans(record,max_spans=1000,char_limit=1_000_000):
        if span['role'] != 'title_or_scope_field' or non_task_scope_role(span['text']) is not None:
            continue
        add(span['doc_index'],span['start'],span['end'],'purchase_field',2)
    for di,doc in enumerate(record['docs']):
        text=doc['text']; units=_source_units(text)
        for i,(lo,hi) in enumerate(units):
            line=text[lo:hi]; n=compact(line)
            if _LIST.match(n):
                end=hi
                # A labelled list may carry several following bullets. Preserve
                # the full selected lines, including conditions and references.
                for a,b in units[i+1:i+17]:
                    if not _BULLET.match(text[a:b]) or re.search(r'참가자격|확인서|등록한|입찰등록',text[a:b]):
                        break
                    end=b
                add(di,lo,end,'explicit_purchase_list',0)
            if _SPEC_FIELD.match(line) and re.search(r'[가-힣a-zA-Z]',line[_SPEC_FIELD.match(line).end():]):
                add(di,lo,hi,'specification_subject_candidate',2)
        for table in purchase_tables(text):
            prefix=text[max(0,table['start']-200):table['start']]
            if _FORM.search(prefix):
                continue
            context=[(di,*table['parent_range'])] if table['parent_range'] else []
            add(di,table['start'],table['end'],'purchase_table_candidate',1,context)
        # A specification's actual opening is another candidate when language
        # labels or a missing title hide its subject. It never establishes that
        # every mentioned component is the complete purchased product.
        if doc['type'] in {'규격서','과업지시서','시방서'} and units:
            first=[(a,b) for a,b in units if b<=900]
            if first and not _FORM.search(text[:min(200,len(text))]):
                add(di,first[0][0],first[-1][1],'specification_opening_candidate',3)
    return sorted(candidates,key=lambda c:(c['priority'],c['doc_index'],c['start'],c['end']))


def seed_reading(tool, *, token_budget):
    """Reserve at most one third of the total budget, all-or-none per range."""
    _validate_source_budget(token_budget)
    seed_budget=max(1,token_budget//3)
    selected=[]; omitted=[]; ranges=()
    for candidate in seed_candidates(tool.rec):
        proposed=merge_ranges([*ranges,*candidate['ranges']],tool.rec['docs'])
        if proposed==ranges:
            continue
        if tool.token_cost(proposed)<=seed_budget:
            ranges=proposed;selected.append(candidate)
        else:
            omitted.append(candidate)
    result=tool.read(ranges,token_budget=token_budget)
    result['diagnostics']['purchase_seed']={'cap':seed_budget,'candidates':selected,
        'omitted_candidates':omitted,'whole_purchase_certified':False}
    return result


def catalog_source_queries(record, seed, *, max_queries=32, query_policy='units'):
    """Query only text actually returned by the seed; retain original addresses."""
    if type(max_queries) is not int or max_queries<1:
        raise ValueError('A positive query count is required')
    if query_policy not in {'units', 'context_blocks', 'table_candidates'}:
        raise ValueError('Unknown source query policy')
    spans=[]
    for s in seed['spans']:
        if record['docs'][s['doc_index']]['text'][s['start']:s['end']]!=s['text']:
            raise ValueError('Seed query text is not the original notice')
        spans.append(Span(s['doc_index'],s['doc_type'],s['start'],s['end'],s['text']))
    units=unitize(spans)
    if query_policy == 'context_blocks':
        # Combine adjacent physical lines before retrieval, preserving the
        # source's actual order. Numbers/units then accompany nearby names
        # instead of each becoming an independent catalog vote. This does not
        # reconstruct a PDF table or assert which item owns an adjacent value.
        blocks=[]
        for unit in units:
            if (blocks and blocks[-1].doc_index==unit.doc_index and blocks[-1].end==unit.start
                    and len(blocks[-1].text)+len(unit.text)<=220):
                prior=blocks[-1]
                blocks[-1]=replace(prior,end=unit.end,text=prior.text+unit.text)
            else:
                blocks.append(unit)
        units=blocks
    found=[];seen=set()
    if query_policy == 'table_candidates':
        from .table_structure import table_structures
        # Analyze only text already read. Structure does not grant permission
        # to use unreturned cells or a header outside the original token cap.
        for span in spans:
            for table in table_structures(span.text):
                if _FORM.search(span.text[max(0,table['start']-200):table['start']]):continue
                for row in table['rows']:
                    for cell in row['name_candidates']:
                        query=cell['text'];key=re.sub(r'\s+',' ',query).strip()
                        if not _cell(query) or key in seen:continue
                        seen.add(key)
                        ev=Span(span.doc_index,span.doc_type,span.start+cell['start'],span.start+cell['end'],query)
                        found.append({'query':query,'evidence':asdict(ev),
                            'structure':{'role':'observed_name_column' if row['literal_column_alignment'] else 'name_candidate',
                                'table_start':span.start+table['start'],'table_end':span.start+table['end'],
                                'row_start':span.start+row['start'],'row_end':span.start+row['end'],
                                'candidate_search_truncated':row['candidate_search_truncated'],
                                'purchase_identity_certified':False}})
                        if len(found)==max_queries:return found
    for unit in units:
        raw=unit.text.strip(); n=_cell(raw)
        # Header normalization removes parenthetical units. Query identity
        # must retain those qualifiers and spaces inside observed numbers.
        # Match the catalog's whitespace-only query normalization instead.
        key=re.sub(r'\s+',' ',raw).strip()
        if (n in _NAMES|_ATTRS|_OTHER|{'영문','국문','번호','품명','규격서','개팩'}
                or not re.search(r'[가-힣a-zA-Z]{2}',raw) or _ONLY_UNIT.fullmatch(n)
                or _FORM.search(raw) or re.search(r'참가자격|입찰참가|등록한|공고번호',raw)):
            continue
        if not n or key in seen:
            continue
        seen.add(key)
        found.append({'query':raw,'evidence':asdict(unit)})
        if len(found)==max_queries:
            break
    return found


def followup_queries(catalog_result):
    """Original catalog names/conditions guide a search, not a legal conclusion."""
    questions=[]
    for row in catalog_result['candidates']:
        question=(f"구매 대상 {row['name']} ({row['parent']})의 실제 용도, 구성품, 규격과 대체 허용 조건"
                  + ('. 지정조건 확인: '+row['condition'] if row['condition'] else ''))
        if question not in questions:
            questions.append(question)
    return questions


def read_purchase(tool, catalog=None, *, token_budget, catalog_method=None,
                  source_method='hybrid', seed=None, catalog_result=None,
                  query_policy='units', candidate_policy='facility', followup_policy='replace'):
    """Run one bounded feedback round; no label or previous notice is consulted."""
    _validate_source_budget(token_budget)
    if source_method not in {'lexical','hybrid'} or catalog_method not in {None,'lexical','hybrid'}:
        raise ValueError('Unknown purchase reading route')
    if candidate_policy not in {'facility','rank_frontier'}:
        raise ValueError('Unknown catalog candidate selection policy')
    if followup_policy not in {'replace', 'fact_groups', 'condition_groups'}:
        raise ValueError('Unknown follow-up question policy')
    if catalog_method is not None and catalog is None:
        raise ValueError('Catalog feedback requires the supplied static catalog')
    seed=seed_reading(tool,token_budget=token_budget) if seed is None else seed
    from .prompts import verified_search_spans
    original=verified_search_spans(tool.rec,seed,tool.tokenizer)
    if seed['source_token_budget']!=token_budget:
        raise ValueError('Seed and final reading must share the same source budget')
    ranges=[(s.doc_index,s.start,s.end) for s in original]
    queries=catalog_source_queries(tool.rec,seed,query_policy=query_policy)
    if catalog_method is not None and queries:
        required=sorted(set(CODE.findall(str(tool.rec.get('meta',{}).get('세부품명번호목록') or ''))))
        if catalog_result is None:
            catalog_result=catalog.search([q['query'] for q in queries],tool.tokenizer,token_budget=2048,
                method=catalog_method,required_codes=required,max_candidates=24,selection_policy=candidate_policy)
        if (catalog_result['catalog_sha256']!=catalog.catalog_sha256
                or catalog_result['method']!=catalog_method
                or catalog_result.get('selection_policy','facility')!=candidate_policy
                or catalog_result['queries']!=list(dict.fromkeys(re.sub(r'\s+',' ',q['query']).strip() for q in queries))
                or [x['code'] for x in catalog_result['required_lookups']]!=required):
            raise ValueError('Catalog feedback belongs to different inputs or method')
    else:
        catalog_result=None
    questions=followup_queries(catalog_result) if catalog_result is not None else []
    condition_plan = None
    if followup_policy in {'fact_groups', 'condition_groups'}:
        from .notice_search import FACT_QUERIES
        search_questions = {'query_groups': {
            'specification': FACT_QUERIES['specification'],
            'eligibility': FACT_QUERIES['eligibility'],
            'purchase_candidates': questions or GENERIC}}
        if followup_policy == 'condition_groups':
            from .catalog_condition_search import query_plan
            condition_plan = query_plan(catalog_result['candidates'] if catalog_result else [])
            if condition_plan['queries']:
                search_questions['query_groups']['designation_conditions'] = condition_plan['queries']
    else:
        search_questions = {'queries': questions or GENERIC}
    result=tool.search([9,10,11,18],token_budget=token_budget,method=source_method,
        **search_questions,required_ranges=ranges,selection_policy='rrf')
    final=[(s['doc_index'],s['start'],s['end']) for s in result['spans']]
    union=merge_ranges([*ranges,*final],tool.rec['docs'])
    assert tool.token_cost(union)==result['source_tokens']<=token_budget
    result['diagnostics']['purchase_feedback']={'source_method':source_method,'catalog_method':catalog_method,
        'query_policy':query_policy,'candidate_policy':candidate_policy,
        'seed_source_tokens':seed['source_tokens'],'seed_ranges':ranges,'source_queries':queries,
        'cumulative_unique_source_tokens':tool.token_cost(union),'previous_text_discarded':False,
        'source_selection_is_not_product_identity':True,'catalog':catalog_result,
        'catalog_is_static_separate_from_original_source_budget':True,
        'catalog_query_empty':not bool(queries),'static_catalog_tokens':catalog_result['catalog_tokens'] if catalog_result else 0}
    if followup_policy != 'replace':
        result['diagnostics']['purchase_feedback']['followup_policy'] = followup_policy
    if condition_plan is not None:
        result['diagnostics']['purchase_feedback']['condition_plan'] = condition_plan
    return result
