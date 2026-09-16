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
_SCOPE_NAMES = ('용역명', '사업명', '공고건명', '입찰건명', '공고명', '과업명', '계약명',
                '구매품목', '구매내역', '구입품목', '구입내역', '품명', '건명', '사업내용', '용역내용', '과업내용',
                '행사내용', '행사장소', '사업목적')
TITLE_FIELDS = re.compile('(?:' + '|'.join(_SCOPE_NAMES) + r')\s*[:：|]')
_SPACED_SCOPE_NAMES = '|'.join(r'[ \t]*'.join(name) for name in _SCOPE_NAMES)
_FIELD_START = re.compile(r'^[ \t|○◯❍□■ㆍ·-]*(?:(?:\d+(?:\.\d+)*[.)]|[가-하][.)])[ \t]*)?'
    r'(?P<label>' + _SPACED_SCOPE_NAMES + r')'
    r'(?:[ \t]*[:：|][ \t]*|[ \t]+|$)')
_OTHER_FIELDS = re.compile(r'^(?:용역개요|계약기간|사업기간|용역기간|과업기간|용역기한|계약금액|'
    r'기초금액|추정가격|배정예산|사업예산|예정금액|추정금액|용역금액|사업금액|수요기관|발주기관|발주처|업체명|대표자|비고|담당업무|'
    r'입찰마감|입찰개시|입찰방식|입찰방법|계약방법|낙찰방법)(?:[:：|（(]|$)')
_TABLE_COLUMNS = frozenset(('품목', '품명', '제품명', '회사명', '제조사명', '업체명', '제조사',
    '유효성분', '규격', '단위', '수량', '단가', '금액', '비고', '번호', '순번', '계약개요', '영문', '국문',
    '사업명', '계약명', '건명', '계약건명', '사업기간', '계약기간', '계약금액', '발주처', '담당업무',
    '납품조건', '납품조건및규격', '납품기한', '납품장소', '인도조건', '제조국', '물품분류번호'))
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
            first = text.splitlines()[0] if text else ''
            field=_FIELD_START.match(first) or re.search('(?:' + _SPACED_SCOPE_NAMES + r')\s*[:：|]', text)
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


def scope_table_header(text):
    cells = [compact(re.sub(r'\([^)]*\)', '', cell)) for cell in text.split('|') if cell.strip()]
    return len(cells) >= 2 and all(cell in _TABLE_COLUMNS for cell in cells)


def usable_scope_field(text):
    """An empty form label or table header is not a conflicting purchase title.

    Check the value of the field, retaining populated and wrapped titles.
    Do not borrow a following contract-period/name field as its value.
    """
    first = text.splitlines()[0] if text else ''
    start = _FIELD_START.match(first)
    if start:
        value = compact(text[start.end():]).strip('|:：')
    else:
        value = compact(text)
        match = TITLE_FIELDS.search(value)
        if match is None:
            return False
        value = value[match.end():].strip('|:：')
    if _OTHER_FIELDS.search(value) or value in _SCOPE_NAMES or value in _TABLE_COLUMNS or scope_table_header(value):
        return False
    next_field = re.search(r'(?:계약기간|사업기간|용역기간|업체명|계약금액|발주처|대표자|비고|담당업무)[:：]', value)
    if next_field:
        value = value[:next_field.start()].strip('|:：')
    if not has_scope_content(value):
        return False
    return not re.match(r'(?:(?:계약|사업|용역)기간(?:\([^)]*\))?|계약금액|발주처|업체명|비고|담당업무)(?:[|:：]|$)', value)


def has_scope_content(value):
    """A redacted value or a document pointer does not describe a task.

    Remove masking/typographic marks only for this content check; evidence and
    offsets always retain the original text. Reference words inside real task
    content remain valid, including an actual task followed by a document link.
    """
    value = re.sub(r'\[[^\]]*\]', '', value)
    if not re.search(r'[가-힣a-zA-Z]', value):
        return False
    value = re.sub(r'[\s‘’“”\'"「」『』()（）,，.。:：|·ㆍ]', '', value)
    document = r'(?:과업(?:지시|내용|설명)서|제안요청서|(?:구매)?규격서|시방서|문서|자료)'
    prefix = r'(?:(?:세부|상세)?(?:내용|내역|사항|규격)(?:은|는)?)?(?:별첨|붙임|첨부)?'
    references = document + r'(?:(?:및|와|과|등|포함)?' + document + r')*(?:등|포함)?'
    tail = r'(?:참조|참고|와같음|과같음|에따름|에의함|의내용참조)'
    return not re.fullmatch(prefix + references + tail, value)


def intro_scope_content(text):
    value = compact(text)
    if re.search(r'구매관리번호|공고번호|사업자등록|업종코드|업종번호|입찰공고일', value):
        return False
    if re.search(r'[:：]\s*[\d.~/~～∼년월일 -]+(?:까지)?[.。]?$', value):
        return False  # A schedule alone does not establish task identity.
    if re.search(r'(?:예정가격|수의계약운영요령|입찰및계약집행기준).*(?:계약상대자|낙찰자).*(?:결정|선정)', value):
        return False  # Contract award procedure is not an operative task.
    return has_scope_content(text)


_TASK_REFERENCE_DOCUMENT = (r'(?:과업(?:지시|내용|설명)서|제안요청서|용도설명서|'
                            r'(?:구매)?(?:규격서|사양서)|시방서|입찰설명서)')
_TASK_REFERENCE_PREFIX = r'(?:(?:게시|첨부|공고)(?:된|한)?|별첨|붙임)*'
_TASK_REFERENCE_ACTION = r'(?:참조|참고|열람|확인|숙지)'
_TASK_DOCUMENT_POINTER = re.compile(
    r'(?:(?:세부|상세)?(?:내용|내역|사항|규격|사양)(?:은|는)?)?'
    r'(?:' + _TASK_REFERENCE_DOCUMENT + r'(?:은|는))?'
    + _TASK_REFERENCE_PREFIX + _TASK_REFERENCE_DOCUMENT +
    r'(?:(?:및|와|과|등|포함|/)' + _TASK_REFERENCE_PREFIX + _TASK_REFERENCE_DOCUMENT + r')*(?:등|포함)?'
    r'(?:을|를)?' + _TASK_REFERENCE_ACTION +
    r'(?:(?:하여|하고|및)' + _TASK_REFERENCE_ACTION + r'){0,2}'
    r'(?:한다|합니다|할것|요망|하시기바랍니다|바람)?'
    r'(?:(?:및|하고|하여)(?:구매요구자|수요부서|계약담당자|담당자|담당부서|발주부서|수요기관)'
    r'(?:에게|에|로)문의(?:한다|합니다|할것|요망|하시기바랍니다|바람)?)?')


def document_reading_instruction(text):
    """A complete document/contact direction supplies no actual purchase task.

    Only reading/contact verbs are admitted. A contract to write those documents
    or build a reading service is retained. This classifies a candidate's role;
    it does not remove its source from discovery or infer the referenced text.
    """
    value = re.split(r'[:：]', text, maxsplit=1)[-1]
    value = re.sub(r'\[[^\]]*\]', '', value)
    value = re.sub(r'[\s‘’“”\'"「」『』()（）,，.。·ㆍ]', '', value)
    return bool(_TASK_DOCUMENT_POINTER.fullmatch(value))


def complete_scope_range(record, scope):
    """Read a visible field continuation before calling a task fully observed.

    Discovery ranges stay unchanged. An explicit trailing connector requires
    the next physical line, with a finite limit and no missing/independent field
    reconstruction. The caller still verifies that every source word was shown.
    """
    text = record['docs'][scope['doc_index']]['text']
    lo, hi = scope['start'], scope['end']
    for _ in range(4):
        if not re.search(r'(?:및|또는|혹은)\s*$', text[lo:hi]):
            return {**scope, 'end': hi, 'text': text[lo:hi]}
        following = re.match(r'[ \t]*\r?\n(?P<line>[^\r\n]+)', text[hi:])
        if following is None:
            return None
        line = following['line']
        if (not line.strip() or _FIELD_START.match(line) or _OTHER_FIELDS.search(compact(line))
                or scope_table_header(line) or re.match(r'\s*(?:\d{1,3}[.)]|[가-하][.)]|[□■※])', line)):
            return None
        hi += following.end()
        if hi - lo > 1200:
            return None
    return None


def non_task_scope_role(text):
    """Identify administrative roles before a candidate becomes a task anchor.

    Keep high-recall discovery unchanged: budgets and qualification clauses
    can be useful context. Their addresses do not establish the purchased work.
    Explicit task fields may legitimately concern safety, certificates or
    budgets; match the role of the sentence, not those topic words alone.
    """
    first = text.splitlines()[0] if text else ''
    if scope_table_header(first):
        return 'table_column_labels'
    if document_reading_instruction(text):
        return 'procurement_document_reading_instruction'
    field = _FIELD_START.match(first)
    value = text[field.end():] if field else text
    header = re.sub(r'[^가-힣a-zA-Z]', '', re.sub(r'\[[^\]]*\]', '', value))
    if re.fullmatch(r'(?:조달물자|물품|용역|구매|전자|입찰|공고|대행|긴급|정정|변경|'
                    r'수의|견적|제출|안내|일반|제한|경쟁|계약|재공고)+', header):
        return 'generic_procurement_header'
    if field:
        if compact(field['label']) == '행사장소':
            return 'venue'
        return None  # The field names an actual task, not a bidder attribute.
    c = compact(text)
    lead = re.sub(r'^[○◯❍□■ㆍ·ㅇ①-⑳-]*(?:(?:\d+(?:\.\d+)*[.)]|[가-하][.)]))?', '', c)
    if (re.match(r'(?:납품|본|해당)?(?:물품|장비|제품|기기|기체|시스템)(?:은|는|이|가)', lead)
            and re.search(r'(?:가능|호환|연동).{0,60}(?:하여야|해야|되어야|돼야|할수있)', lead)):
        return 'equipment_property_requirement'
    if re.fullmatch(r'(?:(?:본건|본입찰|본구매)(?:은|는)?)?'
            r'(?:적격심사|총액|전자입찰|제한경쟁|일반경쟁|수의계약)(?:대상)?'
            r'(?:물품|용역|공사)?(?:구매|계약|입찰)?(?:입찰|공고)?(?:입니다|이다|임)?[.。]?', lead):
        return 'procurement_procedure'
    if re.search(r'(?:적격심사|입찰|계약).*(?:세부기준|운영요령|집행기준)(?:[（(].*)?[.。]?$', lead):
        return 'procurement_rule_reference'
    if re.match(r'(?:사업예산|배정예산|예정금액|추정금액|계약금액|용역금액|사업금액|'
                r'기초금액|추정가격|예정가격)(?:[:：|]|금?\d)', lead):
        return 'amount_field'
    if (re.search(r'확인서|증명서', c)
            and re.search(r'(?:소지|보유)(?:한업체|한자|하여야|해야)|유효기간내|확인되어야', c)):
        return 'qualification_certificate_validity'
    if re.search(r'보유.{0,120}(?:가능한|가능하여야하는)업체', c):
        return 'bidder_capacity'
    if (re.search(r'재해예방에필요한.{0,100}(?:관리체계|조치)', lead)
            or re.search(r'안전[·ㆍ]?보건관계법령에따른의무이행', lead)):
        return 'statutory_safety_duty'
    return None


def scope_spans(rec, max_spans=6, char_limit=900, *, preserve_occurrences=False):
    found = []
    for di, doc in enumerate(rec["docs"]):
        text = doc["text"]
        lines = list(re.finditer(r"[^\n]+", text))
        for j, m in enumerate(lines):
            c = compact(m.group())
            if len(c) > 380 or BOILERPLATE.search(c): continue
            role = None
            field = _FIELD_START.match(m.group())
            if TITLE_FIELDS.search(c) or field: role = "title_or_scope_field"
            elif (m.start() < 1600 and 10 <= len(c) <= 180
                  and not re.match(r"(?:제?\d+[장절.]|\(\d+\))",c)
                  and not re.search(r"적용하며|적용한다|준수|알려드|공고합니다|본시방서|기준및범위",c)
                  and re.search(r"구매|위탁|대행|구축|개발|유지보수|유지관리|운영|조사용역|설계용역|제작|설치",c)):
                role = "intro_title_candidate"
            if role is None: continue
            end = m.end()
            # A table field can be followed by its value on the next line.
            empty_field = field is not None and not m.group()[field.end():].strip()
            if (empty_field or re.search(r'(?:용역명|사업명|공고건명|입찰건명|공고명|과업명|구매품목|구매내역|구입품목|구입내역|품명|건명)[:：|]*$', c)) and j+1<len(lines):
                nxt=lines[j+1]
                next_text = compact(nxt.group())
                # Adjacent named columns are not a label/value pair. Do not
                # reconstruct a flattened table or borrow another field.
                another_field = (_FIELD_START.match(nxt.group()) or _OTHER_FIELDS.search(next_text)
                                 or scope_table_header(nxt.group()))
                if len(nxt.group())<220 and not another_field and not BOILERPLATE.search(next_text):end=nxt.end()
            if role == 'title_or_scope_field' and not usable_scope_field(text[m.start():end]):
                continue
            if role == 'intro_title_candidate' and not intro_scope_content(text[m.start():end]):
                continue
            found.append(dict(doc_index=di,start=m.start(),end=end,role=role,text=text[m.start():end],
                              priority=(0 if role=="title_or_scope_field" else 1)+(0 if doc["type"]=="공고문" else 2)))
    result=[]; seen=set(); used=0
    for s in sorted(found,key=lambda s:(s['priority'],s['doc_index'],s['start'])):
        lexical_key=lexical_text(s['text'])
        key=(s['doc_index'],s['start'],s['end']) if preserve_occurrences else lexical_key
        if not lexical_key or key in seen:continue
        cost=s['end']-s['start']
        if used+cost>char_limit:continue
        seen.add(key);result.append(s);used+=cost
        if len(result)>=max_spans:break
    return sorted(result,key=lambda s:(s['doc_index'],s['start']))


class ProductFacts:
    def __init__(self, catalog_path):
        path=Path(catalog_path)
        self.catalog_sha256=hashlib.sha256(path.read_bytes()).hexdigest()
        with path.open(encoding="utf-8-sig",newline="") as f:
            self.products={r["세부품명번호"]:r for r in csv.DictReader(f)}
        self.features={code:(lexical_grams(p['세부품명']),lexical_grams(p['제품명'])) for code,p in self.products.items()}
        # The supplied catalog is fixed for this instance. Reuse its normalized
        # names and compiled patterns; document text/positions stay notice-local.
        self.exact_name_patterns = tuple(
            (code, re.compile(re.escape(name)))
            for code, p in self.products.items()
            if len(name := normalized_map(p['세부품명'])[0]) >= 5)
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
    def condition(note, price, *, record=None, product_name=None):
        m=re.fullmatch(r"추정가격\s*(\d+)억원\s*미만에\s*한함",note.strip())
        if not m:
            from .catalog_predicates import special_condition
            return special_condition(note, record=record, product_name=product_name) or {
                "status":"not_evaluated" if note else "no_stated_condition"}
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
            meta_body_conflict=shared_price['source_conflict'],
            shared={key: shared_price[key] for key in
                    ('status','value_won','candidate_values_won','all_observed_values_won',
                     'source_conflict','effective_source','basis','unresolved_tax_basis','policy')})

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
            for code,pattern in self.exact_name_patterns:
                for m in pattern.finditer(n):
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
                support=math.fsum(self.idf[g] for g in sorted(shared))
                denom=math.sqrt(max(1,math.fsum(self.idf[g] for g in sorted(detail)))*max(1,len(query)))
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
                                'non_numeric_catalog_notes_require_review':any(p['condition']['status'] in ('not_evaluated', 'unknown') for p in catalog.values()),
                               'dropped_doc_counts':rec.get('dropped_doc_counts',{}),'input_completeness':rec.get('input_completeness',{})}
        return result


def compact_json(facts):
    return json.dumps(facts,ensure_ascii=False,separators=(',',':'))
