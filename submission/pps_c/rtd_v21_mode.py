"""Keep the share floor attached to the declared joint-contract performance mode."""
import re
from dataclasses import replace
from . import judge
JOINT=re.compile(r'공\s*동\s*이\s*행')
SPLIT=re.compile(r'분\s*담\s*이\s*행')
DENIED=re.compile(r'불\s*허|불\s*가|하\s*지\s*않|아\s*니|할\s*수\s*없')
DECLARE=re.compile(r'공\s*동\s*(?:수\s*급(?:\s*체)?|계\s*약)\s*(?:은|는|을|\s*방\s*식\s*은)?\s*[:：(\s]*분\s*담\s*이\s*행\s*(?:방\s*식)?\s*(?:으\s*로|으\s*로\s*만|만|[)）])'
                   r'|분\s*담\s*이\s*행\s*(?:방\s*식)?\s*만\s*허\s*용'
                   r'|(?:공\s*동\s*계\s*약|이\s*행)\s*방\s*식\s*[:：]\s*분\s*담\s*이\s*행')
NEXT_QUAL=re.compile(r'^\s*[가-하]\s*[.)]')
PERCENT=re.compile(r'(?<![\d.])\d+(?:\.\d+)?\s*(?:%|％|퍼센트)|(?:100|백)\s*분\s*의\s*(?:\d+(?:\.\d+)?|[영일이삼사오육칠팔구십]+)')

def active(pattern,text):
    return any(not DENIED.search(text[m.end():m.end()+25]) for m in pattern.finditer(text))

def split_only(b,ln):
    # A stated common-performance floor remains relevant in a mixed-mode tender.
    if active(JOINT,ln.text):return False
    if active(SPLIT,ln.text):return True
    # An immediately following parenthesis can qualify this share/count field.
    nxt=next((x for x in b.notice.window(ln.i,0,3)[1:] if x.text.strip()),None)
    if nxt and nxt.sec==ln.sec and re.match(r'^\s*[(（※]',nxt.text):
        if DECLARE.search(nxt.text) and active(SPLIT,nxt.text) and not active(JOINT,nxt.text):return True
    for prev in reversed(b.notice.window(ln.i,30,0)[:-1]):
        if not prev.text.strip():continue
        if prev.sec!=ln.sec or prev.head!=ln.head:break
        if active(JOINT,prev.text):return False
        if DECLARE.search(prev.text) and active(SPLIT,prev.text):return True
        if NEXT_QUAL.match(prev.text):break
    return False

def filter_hit(b,hit,base):
    current=b;seen=set()
    while hit is not None and hasattr(hit,'i') and split_only(current,hit):
        if hit.i in seen:return None
        seen.add(hit.i)
        # Continue looking: removing the first false witness must not hide a
        # later, genuine common-performance share clause in the same notice.
        lines=list(current.notice.lines)
        lines[hit.i]=replace(hit,text=PERCENT.sub('[share value]',hit.text))
        current=replace(current,notice=replace(current.notice,lines=lines))
        hit=base(current)
    return b.notice.lines[hit.i] if hit is not None and hasattr(hit,'i') else hit
