"""Literal joint-member minimum shares; no model calls or notice-specific conditions."""
import re
from . import judge

JV = re.compile(r'공\s*동\s*(?:수\s*급|도\s*급|이\s*행|계\s*약)|구\s*성\s*원')
SHARE = re.compile(r'지\s*분(?:\s*율)?|출\s*자\s*비\s*율|참\s*여\s*비\s*율|구\s*성\s*비\s*율')
OTHER_NUMBER = re.compile(r'보\s*증\s*금|계\s*약\s*보\s*증|지\s*체\s*상\s*금|가\s*점|배\s*점|점\s*수')
SPLIT_MODE = re.compile(r'분\s*담\s*이\s*행|주\s*계\s*약\s*자\s*관\s*리')
FRACTION = re.compile(r'(?:100|백)\s*분\s*의\s*(\d+(?:\.\d+)?|[일이삼사오육칠팔구]?십[일이삼사오육칠팔구]?|백|영|일|이|삼|사|오|육|칠|팔|구)(?![\d십백천만억])')
KDIGIT = {'영':0,'일':1,'이':2,'삼':3,'사':4,'오':5,'육':6,'칠':7,'팔':8,'구':9,'십':10}

def augment(b, hit):
    if hit is not None or b.meta.law not in ('국가','지방'):
        return hit
    threshold=10 if b.meta.law=='국가' else 5
    for ln in b.notice.lines:
        parts=[ln]
        # Continue only a directly adjacent unfinished share field, in the same document/section.
        if not SHARE.search(ln.text):
            prev=next((x for x in reversed(b.notice.window(ln.i,3,0)[:-1]) if x.text.strip()),None)
            if prev is None or prev.sec!=ln.sec or not SHARE.search(prev.text) or judge.SENT_END.search(prev.text):
                continue
            parts.insert(0,prev)
        text=' '.join(x.text for x in parts)
        if not JV.search(text) or not SHARE.search(text):continue
        if judge.SHAREHOLDING.search(text) or OTHER_NUMBER.search(text) or re.search(r'이해\s*충돌|공직자|특수\s*관계인',text):continue
        if SPLIT_MODE.search(text) and not re.search(r'공\s*동\s*이\s*행',text):continue
        # The share floor belongs to the current joint-contract mode, not a preceding bid-bond field.
        context=' '.join(x.text for x in b.notice.window(ln.i,12,3) if x.sec==ln.sec)
        if SPLIT_MODE.search(context) and not re.search(r'공\s*동\s*이\s*행',context):continue
        def percent(m):
            token=m.group(1)
            if token in KDIGIT:value=KDIGIT[token]
            elif token=='백':value=100
            elif '십' in token:
                tens,units=token.split('십');value=10*(KDIGIT[tens] if tens else 1)+(KDIGIT[units] if units else 0)
            else:value=float(token)
            # A malformed fraction cannot become a small trailing percentage.
            return str(value)+'%' if 0 < value <= 100 else '[invalid share]'
        normalized=FRACTION.sub(percent,text).replace('％','%')
        normalized=re.sub(r'(%|퍼센트)\s*보다\s*낮아서는\s*안\s*(?:된다|됩니다|됨)',r'\1 이상',normalized)
        # member_minimum already binds the number to minimum/at-least wording and rejects representative/maximum shares.
        for subject in SHARE.finditer(normalized):
            start=max(normalized.rfind('.',0,subject.start()),normalized.rfind(';',0,subject.start()),normalized.rfind('。',0,subject.start()))+1
            lead=normalized[start:subject.start()]
            owners=list(re.finditer(r'대표(?:사|자)?|주관|주계약|구성원|각사|참여\s*업체',lead))
            if owners and re.match(r'대표|주관|주계약',owners[-1].group()):continue
            bound=re.split(r'(?<!\d)\.(?!\d)|[;；。]',normalized[start:],maxsplit=1)[0]
            value=judge.member_minimum(bound)
            if value is None or value>=threshold:continue
            # The supplied national joint-contract rule allows a stated 20%
            # adjustment. Its 8%-10% interval is not an automatic violation.
            adjusted=re.search(r'제\s*9\s*조\s*제\s*5\s*항\s*단\s*서|20\s*(?:%|퍼센트)[^.。]{0,25}(?:가\s*감|조\s*정|감\s*소)',context)
            if b.meta.law=='국가' and adjusted and value>=threshold*0.8:continue
            return ln
    return hit
