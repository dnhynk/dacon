"""Deterministic candidate facts from a supplied notice and supplied catalog.

No labels, notice IDs, model, network, or general-product decision. All offsets
are zero-based Python character offsets into the original supplied doc text.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from pathlib import Path

CODE = re.compile(r"(?<!\d)\d{10}(?!\d)")
TITLE_FIELDS = re.compile(r"(?:용역명|사업명|공고건명|입찰건명|공고명|과업명|구매품목|구매내역|품명|건명|사업내용|용역내용|과업내용|행사내용|행사장소|사업목적)\s*[:：|]")
BOILERPLATE = re.compile(r"청렴|부정당|숙지|입찰참가|참가자격|제출서류|직접생산|확인증명|실적|법률|시행령|시행규칙|유의사항|목차|홈페이지|담당자|전화|규격착오|기업성장|응답센터|하도급|낙찰자|계약이행|협약서")


def compact(text):
    return re.sub(r"\s+", "", text)


def normalized_map(text):
    chars, positions = [], []
    for i, char in enumerate(text):
        for c in unicodedata.normalize("NFKC", char).lower():
            if not c.isspace():
                chars.append(c); positions.append(i)
    return "".join(chars), positions


def lexical_text(text):
    # Identifiers are not product words. Preserve original evidence elsewhere.
    text = re.sub(r"\[[^\]]*\]", " ", text)
    text = re.sub(r"\([^)]*(?:경쟁|계약|원미만)[^)]*\)", " ", text)
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"서비스|용역|[0-9]", "", text)
    return re.sub(r"[^가-힣a-z]", "", text)


def lexical_grams(text, query=False):
    """Do not invent bigrams across spaces, punctuation, or field boundaries."""
    text=re.sub(r"\[[^\]]*\]", " ", text)
    text=unicodedata.normalize("NFKC",text).lower()
    if query:
        c=compact(text)
        # A venue establishes event context; its place name is not a product.
        if re.search(r'행사장소[:|]',c):text='행사'
        else:
            field=re.search(r'(?:용\s*역\s*명|사\s*업\s*명|공고건명|입찰건명|공고명|과업명|구매품목|구매내역|품\s*명|건\s*명|사업내용|용역내용|과업내용|행사내용|사업목적)\s*[:：|]',text)
            if field:text=text[field.end():]
    text=re.sub(r"\([^)]*(?:경쟁|계약|원미만)[^)]*\)"," ",text)
    text=re.sub(r'서비스|용역',' ',text)
    out=set()
    for word in re.findall(r'[가-힣a-z]+',text):out.update(grams(word))
    return out


def grams(text, n=2):
    return {text[i:i+n] for i in range(max(0, len(text)-n+1))}


def line_context(text, start, end, limit=280):
    lo = text.rfind("\n", 0, start) + 1
    hi = text.find("\n", end)
    if hi < 0: hi = len(text)
    if hi-lo > limit:
        lo = max(lo, start-limit//3)
        hi = min(hi, max(end, lo+limit))
    return lo, hi


def scope_spans(rec, max_spans=6, char_limit=900):
    found = []
    for di, doc in enumerate(rec["docs"]):
        text = doc["text"]
        lines = list(re.finditer(r"[^\n]+", text))
        for j, m in enumerate(lines):
            c = compact(m.group())
            if len(c) > 380 or BOILERPLATE.search(c): continue
            role = None
            if TITLE_FIELDS.search(c): role = "title_or_scope_field"
            elif (m.start() < 1600 and 10 <= len(c) <= 180
                  and not re.match(r"(?:제?\d+[장절.]|\(\d+\))",c)
                  and not re.search(r"적용하며|적용한다|준수|알려드|공고합니다|본시방서|기준및범위",c)
                  and re.search(r"구매|임차|위탁|대행|구축|개발|유지보수|유지관리|운영|조사용역|설계용역|제작|설치",c)):
                role = "intro_title_candidate"
            if role is None: continue
            end = m.end()
            # A table field can be followed by its value on the next line.
            if re.search(r"(?:용역명|사업명|공고건명|입찰건명|공고명|과업명|구매품목|구매내역|품명|건명)[:：|]*$",c) and j+1<len(lines):
                nxt=lines[j+1]
                if len(nxt.group())<220 and not BOILERPLATE.search(compact(nxt.group())):end=nxt.end()
            found.append(dict(doc_index=di,start=m.start(),end=end,role=role,text=text[m.start():end],
                              priority=(0 if role=="title_or_scope_field" else 1)+(0 if doc["type"]=="공고문" else 2)))
    result=[]; seen=set(); used=0
    for s in sorted(found,key=lambda s:(s['priority'],s['doc_index'],s['start'])):
        key=lexical_text(s['text'])
        if not key or key in seen:continue
        cost=s['end']-s['start']
        if used+cost>char_limit:continue
        seen.add(key);result.append(s);used+=cost
        if len(result)>=max_spans:break
    return sorted(result,key=lambda s:(s['doc_index'],s['start']))


def purchase_coverage(rec, identified_codes=()):
    """Preserve explicit lot/item cardinality before any whole-scope claim.

    Count declarations only inside the notice's purchase section. A known
    code is not proof that all unnamed components belong to that category.
    """
    observations = []
    for di, doc in enumerate(rec['docs']):
        if doc['type'] != '공고문':
            continue
        text = doc['text']
        heads = list(re.finditer(r'(?m)^\s*(?:\d+[.)]\s*)?(?:입찰에\s*부치는\s*사항|사업\s*개요|구매\s*내역|용역\s*개요)', text))
        for head in heads:
            end = re.search(r'(?m)^\s*\d+[.)]\s*[^\n]{0,30}(?:자격|입찰방법|입찰\s*및|제출|보증금|계약방식)', text[head.end():])
            right = head.end() + end.start() if end else min(len(text), head.end() + 3500)
            area = text[head.end():right]
            for match in re.finditer(r'(?:외|등|총)\s*(\d+)\s*(?:종|품목)', area):
                start = head.end() + match.start()
                finish = head.end() + match.end()
                observations.append({'count': int(match[1]) + (1 if match[0].lstrip().startswith('외') else 0),
                    'evidence': {'doc_index': di, 'start': start, 'end': finish, 'text': text[start:finish]}})
    # Multiple sizes of one explicitly named product are distinct line items,
    # not necessarily missing categories. Verify every supplied table column.
    meta_text = str(rec.get('meta', {}).get('세부품명번호목록') or '')
    names = re.findall(r'([가-힣A-Za-z][가-힣A-Za-z ]*)\[\d{10}\]', meta_text)
    columns, table_evidence = len(set(identified_codes)), []
    if names and len(names) == len(set(identified_codes)):
        for di, doc in enumerate(rec['docs']):
            if doc['type'] not in {'규격서', '과업지시서'}:
                continue
            for match in re.finditer(r'(?m)^\s*품\s*명\s*\|[^\r\n]+', doc['text']):
                cells = [c.strip() for c in match[0].split('|')[1:] if c.strip()]
                if cells and all(any(compact(name) in compact(c) for name in names) for c in cells):
                    columns = max(columns, len(cells))
                    table_evidence.append({'doc_index': di, 'start': match.start(), 'end': match.end(), 'text': match[0]})
    unresolved = bool(observations and max(x['count'] for x in observations) > columns)
    return {'observations': observations, 'identified_code_count': len(set(identified_codes)),
            'supported_line_item_count': columns, 'table_evidence': table_evidence,
            'whole_purchase_coverage_unresolved': unresolved}


class ProductFacts:
    def __init__(self, catalog_path):
        path=Path(catalog_path)
        self.catalog_sha256=hashlib.sha256(path.read_bytes()).hexdigest()
        with path.open(encoding="utf-8-sig",newline="") as f:
            self.products={r["세부품명번호"]:r for r in csv.DictReader(f)}
        self.features={code:(lexical_grams(p['세부품명']),lexical_grams(p['제품명'])) for code,p in self.products.items()}
        df=Counter(g for a,b in self.features.values() for g in a|b)
        self.idf={g:math.log(1+len(self.products)/(1+n)) for g,n in df.items()}

    def baseline_matches(self, rec):
        """Frozen equivalent of the previously read Knowledge.product_matches.

        Kept here to avoid importing/editing production code or reading any new
        production/config/data source during this isolated worker task.
        """
        text="\n".join(d["text"] for d in rec["docs"])
        meta=json.dumps(rec["meta"].get("세부품명번호목록"),ensure_ascii=False)
        result=[]
        for code in sorted(set(CODE.findall(text+"\n"+meta))):
            p=self.products.get(code)
            result.append({"코드":code,"고시등재":bool(p),"메타기재":code in meta,
                           **({"품명":p["세부품명"],"특이사항":p["특이사항"]} if p else {})})
        names=[];c=compact(text)
        for p in self.products.values():
            name=compact(p["세부품명"])
            if len(name)>=5 and name in c:names.append({"고시품명":p["세부품명"],"코드":p["세부품명번호"],"특이사항":p["특이사항"]})
        return {"코드대조":result[:30],"명칭언급_동일품목여부확인필요":names[:12],
                "주의":"코드·명칭이 실제 조달 대상인지와 특이사항을 본문에서 확인. 매칭 없음은 일반제품이라는 확정이 아님."}

    @staticmethod
    def condition(note, price):
        m=re.fullmatch(r"추정가격\s*(\d+)억원\s*미만에\s*한함",note.strip())
        if not m:return {"status":"not_evaluated" if note else "no_stated_condition"}
        ceiling=int(m.group(1))*100000000
        return {"kind":"estimated_price_ceiling","operator":"<","ceiling_krw":ceiling,
                "status":"unknown" if price is None else "met" if price<ceiling else "not_met"}

    def extract(self, rec, top_k=5):
        sources=[]; source_keys={}
        def source(di,start,end,role,match_start=None,match_end=None):
            key=(di,start,end,role,match_start,match_end)
            if key in source_keys:return source_keys[key]
            doc=rec['docs'][di]; ref=len(sources)
            sources.append(dict(doc_index=di,doc_id=doc.get('doc_id'),doc_type=doc['type'],start=start,end=end,
                                text=doc['text'][start:end],role=role,
                                **({'match_start':match_start,'match_end':match_end} if match_start is not None else {})))
            source_keys[key]=ref;return ref

        # Use the same role/VAT/conflict contract as every numeric consumer.
        from .prices import project_prices
        shared_price = project_prices(rec)['estimated_price']
        body_prices = []
        for reading in shared_price['body']:
            if reading['price_role'] != 'project_total_candidate':
                continue
            ev = reading['evidence']
            ref = source(ev['doc_index'], ev['start'], ev['end'], 'body_estimated_price')
            body_prices.append({'value_krw': reading['won'], 'source': ref})
        price = shared_price['value_won']
        price_info = dict(value_krw=price, basis=shared_price['basis'],
            meta_value_krw=shared_price['meta']['won'], body_values=body_prices,
            meta_body_conflict=shared_price['status']=='conflict',
            shared={key: shared_price[key] for key in
                    ('status','value_won','candidate_values_won','basis','unresolved_tax_basis','policy')})

        scopes=scope_spans(rec)
        scope_refs=[source(s['doc_index'],s['start'],s['end'],s['role']) for s in scopes]
        def in_scope(di,a,b):return any(s['doc_index']==di and s['start']<=a and b<=s['end'] for s in scopes)

        meta_codes=sorted(set(CODE.findall(json.dumps(rec['meta'].get('세부품명번호목록'),ensure_ascii=False))))
        mentions={}; counts=Counter()
        for di,d in enumerate(rec['docs']):
            t=d['text']
            for m in CODE.finditer(t):
                code=m.group(); lo,hi=line_context(t,m.start(),m.end())
                prior=compact(t[max(0,lo-230):hi])
                line=compact(t[lo:hi])
                role='body_code_unresolved'
                if '직접생산' in line or ('직접생산' in prior and '세부품명' in prior):role='certificate_code_candidate'
                elif in_scope(di,m.start(),m.end()):role='purchase_field_code'
                elif re.search('등록|참가자격|제조물품',line):role='registration_code_candidate'
                counts[(code,role)]+=1
                key=(code,role)
                if key not in mentions:
                    if role=='certificate_code_candidate' and '직접생산' not in line:
                        lo=max(0,lo-160)
                    mentions[key]=source(di,lo,hi,role,m.start(),m.end())

        exact={}; exact_counts=Counter()
        for di,d in enumerate(rec['docs']):
            n,pos=normalized_map(d['text'])
            for code,p in self.products.items():
                name=normalized_map(p['세부품명'])[0]
                # Broad short-word matches are deliberately excluded.
                if len(name)<5:continue
                for m in re.finditer(re.escape(name),n):
                    a,b=pos[m.start()],pos[m.end()-1]+1
                    role='purchase_scope_name' if in_scope(di,a,b) else 'non_scope_name'
                    exact_counts[(code,role)]+=1
                    if (code,role) not in exact:
                        lo,hi=line_context(d['text'],a,b)
                        exact[(code,role)]=source(di,lo,hi,role,a,b)

        # Deterministic lexical retrieval uses catalog strings only. IDF is
        # catalog document frequency, never fitted on notices or labels.
        ranked=[]
        kind=rec.get('meta',{}).get('업무구분')
        queries=[lexical_grams(s['text'],query=True) for s in scopes]
        for code,(detail,parent) in self.features.items():
            best=None
            for si,s in enumerate(scopes):
                query=queries[si]
                shared=detail&query; family=parent&query
                if not shared:continue  # no parent-only category assertion
                support=sum(self.idf[g] for g in shared)
                denom=math.sqrt(max(1,sum(self.idf[g] for g in detail))*max(1,len(query)))
                score=support/denom
                exact_detail=lexical_text(self.products[code]['세부품명']) in lexical_text(s['text'])
                # Tie-break using detail evidence, then parent evidence; no
                # semantic synonym table or notice-specific mapping.
                service_catalog=self.products[code]['대분류'].endswith('서비스')
                kind_agreement=(service_catalog if kind=='일반용역' else not service_catalog if kind=='물품(내자)' else True)
                key=(exact_detail,kind_agreement,score,len(shared),len(family),code)
                if best is None or key>best[0]:best=(key,scope_refs[si],sorted(shared),sorted(family))
            if best:ranked.append((code,best))
        ranked.sort(key=lambda x:(-int(x[1][0][0]),-int(x[1][0][1]),-x[1][0][2],-x[1][0][3],-x[1][0][4],x[0]))
        lexical=[]
        for code,(key,ref,shared,family) in ranked[:top_k]:
            lexical.append(dict(code=code,source=ref,score=round(key[2],4),shared_bigrams=shared,
                                detail_exact=bool(key[0]),kind_agreement=bool(key[1]),
                                lexical_support='weak' if len(shared)<2 else 'multiple_bigrams',family_shared_bigrams=family))

        catalog_codes=set(meta_codes)|{code for code,role in mentions}|{x['code'] for x in lexical}|{code for code,role in exact}
        catalog={code:{'name':self.products[code]['세부품명'],'parent_name':self.products[code]['제품명'],
                       'note':self.products[code]['특이사항'],
                       'condition':self.condition(self.products[code]['특이사항'],price)}
                 for code in sorted(catalog_codes) if code in self.products}
        result=dict(version='product-facts-prototype-1',catalog_sha256=self.catalog_sha256,
                    interpretation='candidates_only_no_general_product_inference',price=price_info,
                    meta_purchase_codes=[dict(code=c,listed=c in self.products,field='세부품명번호목록') for c in meta_codes],
                    body_code_mentions=[dict(code=c,listed=c in self.products,role=role,source=ref,occurrences=counts[(c,role)]) for (c,role),ref in sorted(mentions.items())],
                    exact_name_mentions=[dict(code=c,role=role,source=ref,occurrences=exact_counts[(c,role)]) for (c,role),ref in sorted(exact.items())],
                    purchase_scope_sources=scope_refs,lexical_candidates=lexical,catalog=catalog,sources=sources)
        result['uncertainty']={'purchase_identity':'unresolved','no_match_is_general':False,
                               'scope_recovered':bool(scopes),'code_free':not meta_codes and not mentions,
                               'non_numeric_catalog_notes_require_review':any(p['condition']['status']=='not_evaluated' for p in catalog.values()),
                               'dropped_doc_counts':rec.get('dropped_doc_counts',{}),'input_completeness':rec.get('input_completeness',{})}
        return result


def compact_json(facts):
    return json.dumps(facts,ensure_ascii=False,separators=(',',':'))
