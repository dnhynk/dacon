"""Conservative all-document absence check for the industry axis (RT-E2).

Independent of record.sec and of V24_LICENCE_SILENT. No ids, labels, model
readings, planted markers or title bands are used. Returned evidence is an
actual qualification heading, never an arbitrary line tagged QUAL.
"""
from __future__ import annotations
import re
import unicodedata
from dataclasses import dataclass

def compact(s):
    return re.sub(r'[\s.·ㆍ․‧“”‘’「」『』｢｣]+', '', unicodedata.normalize('NFC', s or ''))

MARK = re.compile(r'^\s*(?:\d{1,2}\s*[.)、]|\(\s*\d{1,2}\s*\)|[가-하]\s*[.)]|[①-⑳]|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[.)]?|[□■▢▣◆◇◈❏○●◉◎▶►▷ㅇ◦•\-※*]+)\s*')
BARE_NUMBER = re.compile(r'^\s*\d{1,2}\s+(?=\S)')
QUAL = re.compile(r'^(?:(?:입찰|견적(?:서)?제출|견적|제안(?:서)?(?:제출)?|응모|공모)?(?:참가|참여)(?:(?:자의)?자격(?:요건|조건)?|자요건)|입찰자격(?:요건)?|견적(?:서)?(?:제출)?자격(?:요건)?|제안자격|자격요건)')
QTAIL = re.compile(r'^(?:에관한사항|및(?:제한사항|제한조건|요건)|\([^)]{0,35}\)|[：:|]).*')
BAD_Q = re.compile(r'등록규정|자격등록|제한처분|참가제한|관련문의|증명하는서류|자격증명|자격제한')
OTHER_HEAD = re.compile(r'^(?:입찰(?:에부치는사항|개요|방법|방식|일정|보증금|의무효|무효|서제출)|견적(?:에부치는사항|제출기간|서제출기간|제출방법)|낙찰|계약상대자|예정가격|제출서류|구비서류|서류제출|평가|적격심사|협상|제안서(?:제출|평가|작성)|공동(?:계약|수급|도급)|하도급|기타|유의사항|청렴|문의|붙임|첨부|사업개요|과업내용|현장설명|현품설명)')
SENTENCE = re.compile(r'합니다|하여야|해야|이어야|없습니다|있습니다|업체|갖춘|갖추|소지|필한|등록|신고|허가|불가|불허|가능|금지|허용')
LIST = re.compile(r'^\s*(?:[가-하]\s*[.)]|\d{1,2}\s*[.)]|\(\s*\d{1,2}\s*\)|[①-⑳]|[□■▢❏○●◉◎▶►▷ㅇ◦•\-])')
REQ = re.compile(r'갖추|갖춘|구비|소지|보유|충족|필한|등록|신고|허가|이어야|하여야|해야|업체|참가할수없|참여할수없|해당하지않|부정당|소재|자격.*(?:있는|자로)|(?:소기업|소상공인|중소기업).*확인')
ACT = re.compile(r'등록|신고|허가|면허|지정|인가')
ACTIVE = re.compile(r'(?:등록|신고|허가|면허|지정|인가)(?:증|확인서|증명서)?(?:을|를)?(?:필|받|한|하고|된|되어|돼|하여|해야|하였|완료|보유|소지|업체)|(?:허가|면허)를?득|(?:영업|업종)(?:등록|신고|허가)|사업자(?:로)?등록|업종코드')
BUSINESS = re.compile(r'(?<![가-힣A-Za-z])([가-힣A-Za-z·ㆍ․]+(?:업|사업자|사무소))(?=[(（\s“”\"「」『』]|으로|로|을|를|의|에|과|또는|및|등록|신고|허가|면허|지정|인가|분야|$)')
GENERIC_BUSINESS = {'기업','중소기업','소기업','대기업','사업','당해사업','해당사업','본사업','사업자','신규사업자','개인사업자','법인사업자','부정당업','부정당업자','영업'}
CODE = re.compile(r'(?<!\d)\d{4}(?!\d)')
CERT = re.compile(r'(?:등록증|신고확인서|신고증|허가증|면허증|지정서|사업자일반현황관리확인서|증명서).{0,50}(?:사본|원본|[0-9]+부|제출)|(?:제출서류|구비서류|증빙서류)')
EXPLICIT_REQ = re.compile(r'업체(?:이어야|여야|에한|만)|자(?:이어야|여야)|(?:등록|신고|허가|면허|지정).{0,35}(?:필한|필하고|받은|받아야|갖춘|보유한|소지한|되어있어야|된업체)|(?:참가|참여)(?:가|할수)?(?:가능|있|없는|없)')
SCORE_ONLY = re.compile(r'배점|가점|평점|평가점수|평가항목|평가기준|점수|득점')
NEGATIVE = re.compile(r'(?:업종|면허|등록|신고|허가)(?:의)?(?:제한|요건|조건)?(?:이|은|을|를|여부)?(?:없|불필요|필요없|요구하지않|제한하지않|두지않)')
INDUSTRY_LABEL = re.compile(r'(?:업종|면허)(?:제한|코드|등록|명)?[:：]|업종코드')
CERT_ACTION = re.compile(r'(?:등록|신고|허가|면허|지정|인가)(?:을|를)?(?:필|받|하고|한(?:업체|자|사업자)|된(?:업체|자)|되어|하여|해야|하였|완료|마친|득)|허가를?득')
PERMIT_REQUIREMENT = re.compile(r'(?:인[/·ㆍ]?허가|허가|인가)(?:자격)?(?:조건|요건)(?:을|를)?(?:갖춘|갖추|구비)|영업(?:신고|허가).{0,20}(?:완료|필|받|득|갖춘)')
BUSINESS_PURPOSE = re.compile(r'사업자등록증.{0,65}(?:사업의종류|사업내용|종목|업태|업종).{0,100}(?:포함|기재|등록|추가)|사업의종류.{0,70}(?:관련된|관한)사업자등록증.{0,25}(?:교부|받|소지)')
ELIGIBILITY_LEAD = re.compile(r'(?:다음|아래).{0,60}(?:자격|요건|등록|업종|충족)|자격으로등록한자|필수요건|업체자격|제안자격')
INTRO_ONLY = re.compile(r'^(?:다음|아래)(?:의)?.{0,40}(?:모두|갖춘|갖추|충족)(?:하는)?(?:자|업체)?$')
@dataclass
class Qualification:
    heading: object
    end: int
    requirements: list

def body(text):
    s = text.strip()
    s = re.sub(r'^\s*\d{1,2}\s*\|\s*\|', '', s)
    s = MARK.sub('', s, count=1)
    s = BARE_NUMBER.sub('', s, count=1)
    return compact(s).strip('[]【】<>〈〉 ')

def qual_heading(text):
    s=body(text)
    m=QUAL.match(s)
    if not m or BAD_Q.search(s):
        return False
    tail=s[m.end():].strip(' .․·')
    if re.search(r'재직자|업체별\d+인|설명회|참석자',tail):
        return False
    return not tail or bool(QTAIL.match(tail)) and not SENTENCE.search(tail.split(':')[0].split('：')[0])

def other_heading(text):
    s=body(text)
    return len(s)<=55 and bool(OTHER_HEAD.match(s)) and not SENTENCE.search(s) and not REQ.search(s) and not re.search(r'\d+(?:부|매|통)|hwp|pdf|docx',s,re.I)

def qualifications(notice):
    lines=notice.lines
    result=[]
    for pos, head in enumerate(lines):
        if not qual_heading(head.text):
            continue
        end=pos+1
        while end<len(lines) and lines[end].doc==head.doc:
            if qual_heading(lines[end].text) or other_heading(lines[end].text):
                break
            # Unknown numbered top-level short titles also end a section; no sequential-number assumption.
            s=lines[end].text.strip()
            if re.match(r'^\d{1,2}\s*[.]',s) and len(compact(s))<35 and not SENTENCE.search(compact(s)) and not REQ.search(compact(s)):
                break
            end+=1
        groups=[]
        group=[]
        for ln in lines[pos+1:end]:
            if LIST.match(ln.text) and group:
                groups.append(group);group=[]
            if ln.text.strip():group.append(ln)
        if group:groups.append(group)
        req=[]
        for group in groups:
            t=compact(''.join(ln.text for ln in group))
            if REQ.search(t) and not INTRO_ONLY.match(body(t)) and not (CERT.search(t) and not EXPLICIT_REQ.search(t) and not CERT_ACTION.search(t)):
                req.append(group[0])
        result.append(Qualification(head,end,req))
    return result

def meta_names(meta):
    """All entries, including nested parenthetical business names, with whitespace normalized."""
    value=meta.get('면허업종제한목록') or ''
    def flatten(x):
        if isinstance(x,dict):return ' '.join(flatten(v) for v in x.values())
        if isinstance(x,(list,tuple)):return ' '.join(flatten(v) for v in x)
        return str(x)
    text=compact(flatten(value))
    names=set()
    for part in re.split(r'[\[\](),/;:：]|업종또는|업종및|또는',text):
        part=part.strip(' .')
        if len(part)>=3 and not part.isdigit() and part not in GENERIC_BUSINESS and re.search(r'[가-힣]',part):
            names.add(part)
    return sorted(names,key=lambda s:(-len(s),s)),set(CODE.findall(text))

def industry_matches(notice, qual_ranges=None):
    names,codes=meta_names(notice.meta)
    lines=notice.lines
    qual_ranges=qualifications(notice) if qual_ranges is None else qual_ranges
    qual_positions={i for q in qual_ranges for i in range(q.heading.i+1,q.end)}
    matches=[]
    for pos,ln in enumerate(lines):
        if not ln.text.strip():
            continue
        window=[ln]
        for nxt in lines[pos+1:pos+7]:
            if nxt.doc!=ln.doc or MARK.match(nxt.text) or qual_heading(nxt.text) or other_heading(nxt.text):
                break
            if nxt.text.strip():window.append(nxt)
            if len(window)>=5 or sum(len(x.text) for x in window)>=600:break
        t=compact(''.join(x.text for x in window))
        if NEGATIVE.search(t) and not CERT_ACTION.search(t):
            continue
        # A certificate count/checklist or evaluation score alone imposes no business eligibility.
        if CERT.search(t) and not CERT_ACTION.search(t) and not EXPLICIT_REQ.search(t):
            continue
        if SCORE_ONLY.search(t) and not EXPLICIT_REQ.search(t) and not CERT_ACTION.search(t):
            continue
        named=[name for name in names if name in t]
        raw_text=' '.join(x.text for x in window)
        raw_text=re.sub(r'(소프트웨어|엔지니어링)\s+사업자', r'\1사업자', raw_text)
        businesses=[compact(m.group(1)) for m in BUSINESS.finditer(raw_text) if compact(m.group(1)) not in GENERIC_BUSINESS]
        coded=bool(codes.intersection(CODE.findall(t))) or bool(re.search(r'업종코드[:：]?\d{4}',t)) or bool(businesses and re.search(r'[(\[:：]\d{4}[)\]]',t))
        action=bool(ACTIVE.search(t))
        # Type labels / codes require an eligibility context, not a bare project title.
        previous=''.join(x.text for x in lines[max(0,pos-5):pos] if x.doc==ln.doc)
        context=pos in qual_positions or bool(EXPLICIT_REQ.search(t)) or bool(INDUSTRY_LABEL.search(t)) or bool(ELIGIBILITY_LEAD.search(compact(previous)))
        reason=None
        if named and (action or context):
            reason='meta_name'
        elif businesses and (ACT.search(t) and (action or EXPLICIT_REQ.search(t)) or context and ('법' in t or ELIGIBILITY_LEAD.search(compact(previous)))):
            reason='business_registration'
        elif coded and (action or context):
            reason='industry_code'
        elif INDUSTRY_LABEL.search(t) and context and not NEGATIVE.search(t):
            reason='industry_label'
        elif PERMIT_REQUIREMENT.search(t) and (context or '업체' in t):
            reason='business_permit'
        elif BUSINESS_PURPOSE.search(t):
            reason='business_purpose'
        if reason:
            matches.append({'line':ln.i,'end':window[-1].i,'doc':ln.doc,'reason':reason,'names':named,'text':'\n'.join(x.text for x in window)})
    return matches

def inspect(notice):
    qs=qualifications(notice)
    found=industry_matches(notice,qs)
    usable=next((q for q in qs if len(q.requirements)>=2),None)
    return qs,found,usable

def detect(bundle):
    if bundle.meta.license_flag!='Y':
        return None
    qs,found,usable=inspect(bundle.notice)
    if found or usable is None:
        return None
    return usable.heading
