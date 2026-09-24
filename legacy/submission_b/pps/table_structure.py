"""Source-addressed table hypotheses, never a reconstructed original document.

Literal pipe columns and proposed vertical-row relationships remain distinct.
Missing cells are not synthesized. Local alternatives are factorized instead
of selecting a convenient full-table interpretation or inferring an absence.
"""
from __future__ import annotations

from decimal import Decimal
from itertools import combinations
import re

from .amounts import NUMBER
from .purchase_tables import purchase_tables, _cell, _NAMES
from .retrieval import _source_units

_ROLES = {'규격':'specification','사양':'specification','단위':'unit',
          '수량':'quantity','단가':'unit_price','금액':'amount','비고':'note',
          '번호':'index','순번':'index','연번':'index'}
_NUMERIC_ROLES = ('quantity','unit_price','amount')
_UNIT = re.compile(r'(?:(?:\d+)?(?:개|매)(?:입)?[/／])?'
                   r'(?:개|팩|포|대|식|병|장|권|매|부|본|통|세트|봉|조|쌍|박스|개[·ㆍ]팩)$')
_SPEC = re.compile(r'\d\s*(?:[~×x*/.±-]\s*\d+\s*)?(?:cm|mm|ml|cc|kg|[mLℓ%]|매입|개입)|'
                   r'^[（(]?(?:흡수량|색상|가로|세로|두께)|^(?:남자|여자)$',re.I)
_NOTE = re.compile(r'부가(?:가치)?세|배송비|스티커|동등|규격참조|별도협의|포함|제외|서약|준수|합계|^계$|^절사$')


def _ref(text, lo, hi, role=None):
    while lo<hi and text[lo].isspace():lo+=1
    while lo<hi and text[hi-1].isspace():hi-=1
    result={'start':lo,'end':hi,'text':text[lo:hi]}
    if role:result['role']=role
    return result


def _role(text):
    label=_cell(text)
    return 'name' if label in _NAMES else _ROLES.get(label,'unresolved_header')


def _number(cell):
    if cell is None or not re.fullmatch(NUMBER,cell['text']):return None
    value=Decimal(cell['text'].replace(',',''))
    return value if value>=0 else None


def pipe_separators(text, lo=0, hi=None):
    """Literal column delimiters, excluding anonymous-token attributes."""
    from .anonymized_tokens import anonymous_tokens
    hi = len(text) if hi is None else hi
    raw = text[lo:hi]
    if '|' not in raw:
        return []
    tokens = list(anonymous_tokens(raw))
    return [lo + m.start() for m in re.finditer(r'\|', raw)
            if not any(t.start <= m.start() < t.end for t in tokens)]


def pipe_cells(text,lo,hi, *, fences=None):
    """Keep empty cells and source coordinates under the header's fences."""
    start=lo;result=[]
    for end in pipe_separators(text,lo,hi):
        result.append(_ref(text,start,end));start=end+1
    result.append(_ref(text,start,hi))
    leading=text[lo:hi].lstrip().startswith('|');trailing=text[lo:hi].rstrip().endswith('|')
    if fences is None:fences=(leading,trailing)
    if leading and fences[0]:result=result[1:]
    if trailing and fences[1]:result=result[:-1]
    return result


def _arithmetic(alternatives, *, certified=False):
    # Even equality is only a diagnostic unless the quantity/unit-price unit,
    # tax basis and rounding rules were independently established. Never solve
    # a missing quantity by dividing two observed monetary values.
    readings=[]
    for alternative in alternatives:
        q,p,a=(_number(alternative.get(k)) for k in _NUMERIC_ROLES)
        if None in (q,p,a):continue
        readings.append(a-q*p)
    if not readings:
        return {'status':'not_testable','constraint_certified':False,'missing_values_inferred':False}
    same=len(set(readings))==1
    return {'status':(('equal' if readings[0]==0 else 'inconsistent') if certified else
                      ('equal_with_unverified_basis' if readings[0]==0 else 'different_with_unverified_basis'))
                     if same else 'layout_dependent',
            'difference_won':str(readings[0]) if same else None,
            'constraint_certified':certified,'missing_values_inferred':False,
            'unverified':[] if certified else ['quantity_unit_vs_pack_size','tax_basis','rounding']}


def _explicit_arithmetic_basis(headers, unit, text):
    """Only literal same-unit, same-tax headers plus a no-rounding rule suffice."""
    by_role={h['role']:h['text'] for h in headers}
    q,p,a=(re.sub(r'\s+','',by_role.get(k,'')) for k in _NUMERIC_ROLES)
    declared_unit=re.fullmatch(r'수량[（(](개|팩|대|병|매|장|세트)[)）]',q)
    if not declared_unit or unit is None or unit['text']!=declared_unit[1]:return False
    if not re.search(r'[(（]원[/／]'+re.escape(declared_unit[1])+r'[,，]',p):return False
    if not re.search(r'[(（]원[,，]',a):return False
    def tax(s):
        yes=bool(re.search(r'(?:부가(?:가치)?세|(?i:VAT))포함',s))
        no=bool(re.search(r'(?:부가(?:가치)?세|(?i:VAT))(?:별도|제외|미포함|불포함)',s))
        return 'included' if yes and not no else 'excluded' if no and not yes else None
    if tax(p) is None or tax(p)!=tax(a):return False
    n=re.sub(r'\s+','',text)
    return bool(re.fullmatch(r'금액은수량[×*]단가로산정하며반올림(?:과|및)절사를?하지않(?:는다|음)[.。]?',n))


def _name_candidates(text,cells):
    names=[]
    for cell in cells:
        value=cell['text'];normalized=re.sub(r'\s+','',value)
        if (2<=len(normalized)<=100 and re.search(r'[가-힣A-Za-z]{2}',normalized)
                and not (_NOTE.search(normalized) or _SPEC.search(normalized)
                         or _UNIT.fullmatch(normalized) or _number(cell) is not None)):
            names.append({**cell,'role':'name_candidate'})
    # Retain a wrapped or shared label as an alternative, with every original
    # character and address. No merged string is asserted to be source text.
    if len(cells)>1 and names:
        first=next((i for i,c in enumerate(cells) if c['start']==names[0]['start']),0)
        while first and len(cells[first-1]['text'])==1 and re.fullmatch('[가-힣]',cells[first-1]['text']):first-=1
        last=next(i for i,c in enumerate(cells) if c['end']==names[-1]['end'])
        combined=_ref(text,cells[first]['start'],cells[last]['end'],'name_candidate')
        if all(n['start']!=combined['start'] or n['end']!=combined['end'] for n in names):names.append(combined)
    return names


def _vertical_rows(text,body,headers,max_candidates):
    slots=[h['role'] for h in headers if h['role'] in _NUMERIC_ROLES]
    if len(slots)!=len(set(slots)):return [],True,[]
    anchors=[i for i,c in enumerate(body) if _UNIT.fullmatch(re.sub(r'\s+','',c['text']))]
    rows=[];truncated=False;covered=[];previous_end=0
    for position in anchors:
        prefix=body[previous_end:position]
        # Numeric cells before the next unit belong to the preceding proposed
        # row, not its successor. Totals/notes remain separately observable.
        while prefix and (_number(prefix[0]) is not None or _NOTE.search(re.sub(r'\s+','',prefix[0]['text']))):prefix=prefix[1:]
        end=position+1
        while end<len(body) and _number(body[end]) is not None:end+=1
        numbers=body[position+1:end];names=_name_candidates(text,prefix)
        previous_end=end
        alternatives=[];row_truncated=False
        if len(numbers)<=len(slots):
            for chosen in combinations(slots,len(numbers)):
                if len(alternatives)==max_candidates:
                    truncated=row_truncated=True;break
                alternative={k:None for k in _NUMERIC_ROLES}
                alternative.update(zip(chosen,numbers));alternatives.append(alternative)
        row_start=prefix[0]['start'] if prefix else body[position]['start']
        row={'start':row_start,'end':body[end-1]['end'],
             'literal_column_alignment':False,'name_candidates':names,'unit':body[position],
             'numeric_alternatives':alternatives,'candidates':[],
             'candidate_search_truncated':row_truncated,
             'arithmetic':_arithmetic(alternatives),
             'assumptions':['one unit cell per item','row order retained in this layout family'],
             'other_layouts_possible':True}
        rows.append(row)
        if names and alternatives:
            covered.extend([(c['start'],c['end']) for c in [*prefix,body[position],*numbers]])
    return rows,truncated,covered


def _complete_layouts(body,headers,max_candidates):
    """Try row-major and column-major dumps without filling any cell."""
    roles=[h['role'] for h in headers];width=len(roles)
    if not width or len(roles)!=len(set(roles)) or len(body)%width:return [],False
    count=len(body)//width
    if not count:return [],False
    layouts=[];seen=set()
    for family in ('row_major','column_major'):
        rows=[];valid=True
        for r in range(count):
            mapped={role:body[r*width+c if family=='row_major' else c*count+r] for c,role in enumerate(roles)}
            for role,cell in mapped.items():
                if role in _NUMERIC_ROLES and _number(cell) is None:valid=False
                if role=='unit' and not _UNIT.fullmatch(re.sub(r'\s+','',cell['text'])):valid=False
                if role=='name' and (not re.search(r'[가-힣A-Za-z]{2}',cell['text']) or _NOTE.search(cell['text'])):valid=False
            rows.append(mapped)
        key=tuple(tuple((role,c['start'],c['end']) for role,c in row.items()) for row in rows)
        if not valid or key in seen:continue
        if len(layouts)==max_candidates:return layouts,True
        seen.add(key)
        layouts.append({'family':family,'rows':rows,'constraints':['observed cell count','column data types'],
                        'missing_cells_inferred':False,'original_layout_certified':False})
    return layouts,False


def table_structures(text, *, max_candidates=32):
    """Enumerate local monotone assignments; an unmodeled layout stays unknown.

    Values are cell observations, names in vertical tables are hypotheses.
    Alternative rows are factorized, so an unresolved quantity in one item
    does not prevent reading a different item's explicit pipe-delimited name.
    """
    if type(max_candidates) is not int or max_candidates<1:raise ValueError('Positive candidate cap required')
    structures=[]
    for table in purchase_tables(text):
        lo,hi=table['start'],table['end']
        lines=[(lo+a,lo+b) for a,b in _source_units(text[lo:hi])]
        header_lines=[(a,b) for a,b in lines if b<=table['header_end']]
        if table['layout']=='pipe':
            headers=pipe_cells(text,*header_lines[0])
        else:headers=[_ref(text,a,b) for a,b in header_lines]
        headers=[{**h,'role':_role(h['text'])} for h in headers]
        rows=[];covered=[];truncated=False;layouts=[]
        body=[_ref(text,a,b) for a,b in lines if a>=table['header_end']]
        if table['layout']=='vertical':
            rows,truncated,covered=_vertical_rows(text,body,headers,max_candidates)
            layouts,layout_truncated=_complete_layouts(body,headers,max_candidates)
            truncated|=layout_truncated
        else:
            roles=[h['role'] for h in headers]
            header_text=text[slice(*header_lines[0])]
            fences=(header_text.lstrip().startswith('|'),header_text.rstrip().endswith('|'))
            for line in body:
                if re.fullmatch(r'[\s|:\-]+',line['text']):continue
                cells=pipe_cells(text,line['start'],line['end'],fences=fences)
                if len(cells)!=len(headers) or len(set(roles))!=len(roles):continue
                candidate={h['role']:(c if c['text'] else None) for h,c in zip(headers,cells)}
                if candidate.get('name') is None:continue
                if any(candidate.get(k) is not None and _number(candidate[k]) is None for k in _NUMERIC_ROLES):continue
                candidate.update({k:candidate.get(k) for k in _NUMERIC_ROLES})
                candidate['cells']=cells
                names=[{**candidate['name'],'role':'observed_name_column'}]
                rows.append({'start':line['start'],'end':line['end'],'literal_column_alignment':True,
                    'name_candidates':names,'unit':candidate.get('unit'),
                    'numeric_alternatives':[{k:candidate[k] for k in _NUMERIC_ROLES}],
                    'candidate_search_truncated':False,
                    'candidates':[candidate],'arithmetic':_arithmetic([candidate],
                        certified=_explicit_arithmetic_basis(headers,candidate.get('unit'),
                            candidate.get('note',{}).get('text','') if candidate.get('note') else '')),
                    'assumptions':[],'other_layouts_possible':False})
                covered.append((line['start'],line['end']))
        unresolved=[c for c in body if not any(a<=c['start'] and b>=c['end'] for a,b in covered)]
        structures.append({**table,'headers':headers,'rows':rows,
            'complete_layout_candidates':layouts,
            'candidate_search_truncated':truncated,'all_layouts_certified':False,
            'whole_purchase_certified':False,'unresolved_source_ranges':unresolved,
            'source_modified':False,'provenance':'original_text_character_offsets',
            'layout_family':'literal_pipe_columns' if table['layout']=='pipe' else 'unit_anchored_row_order_hypotheses'})
    return structures


def numeric_invariant(row, field, *, lower=0, upper=None):
    """A condition on every local interpretation; never a whole-item verdict."""
    if field not in _NUMERIC_ROLES:raise ValueError('Unknown numeric column')
    alternatives=row['numeric_alternatives']
    values=[_number(c[field]) for c in alternatives]
    outcomes={v>=lower and (upper is None or v<upper) for v in values if v is not None}
    known=(bool(values) and None not in values and not row['candidate_search_truncated']
           and row['arithmetic']['status']!='inconsistent')
    return {'status':'unknown' if not known else 'invariant' if len(outcomes)==1 else 'varies',
        'value':next(iter(outcomes)) if known and len(outcomes)==1 else None,
        'authority':'observed_column' if row['literal_column_alignment'] else 'conditional_on_row_layout',
        'whole_judgment_certified':False}


def reading_order_candidates(text):
    """Flag suspicious intrusions without deciding what may be removed."""
    patterns={
        'contact_banner_candidate':r'(?:부조리|부패|청렴|비리)\s*신고[^\r\n]{0,100}?(?:https?://|www\.)[A-Za-z0-9./_%?=&#~-]+',
        'interleaved_heading_candidate':r'(?m)^[ \t]*\d+[.)][ \t]*[^\r\n]{3,60}?[ \t]+[가-하][.][ \t]+[^\r\n]{1,100}',
        'page_number_candidate':r'(?m)^[ \t]*[-—]\s*\d{1,4}\s*[-—][ \t]*$',
    }
    found=[]
    for kind,pattern in patterns.items():
        for m in re.finditer(pattern,text):
            found.append({**_ref(text,m.start(),m.end()),'kind':kind,'may_delete':False,
                'original_reading_order_recovered':False})
    return sorted(found,key=lambda x:(x['start'],x['end'],x['kind']))
