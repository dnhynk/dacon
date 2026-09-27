"""Record floors with compound Korean money, fractions, and directly wrapped amount fields."""
import re
from . import judge,amounts

RATIO=re.compile(r'(기\s*초\s*금\s*액|사\s*업\s*예\s*산|배\s*정\s*예\s*산|예\s*산\s*액?|사업비|추\s*정\s*가\s*격|예\s*정\s*가\s*격)'
 r'\s*(?:의|대비)?\s*(?:100|백)\s*분\s*의\s*(\d+(?:\.\d+)?)')
FLOOR=re.compile(r'이\s*상|초\s*과|상\s*회|넘\s*는')

# A newly parsed record floor must be affirmative.
def comparison_is_floor(text, end):
    tail=re.sub(r'\s+','',text[end:end+80])
    if re.match(r'^(?:이하|이내|미만)',tail):return False
    if re.match(r'^(?:이상|초과|상회)?(?:인|일|인것|일것)?(?:은|는)?(?:아니|아님|필요(?:가|는|은)?없|필요하지않)',tail):return False
    if re.match(r'^의?(?:(?:단일|동종|유사|용역|납품|수행|사업))*실적(?:을|은|이|의)?(?:요구하지않|필요(?:가|는|은)?없|요구하는것은아니)',tail):return False
    return True


def augment(b,hit):
    if hit is not None:return hit
    budget=b.meta.B or ((b.meta.P or 0)*1.1) or None
    if budget is None:return hit
    for ln in judge.perf_lines(b):
        if judge.CREDIT_RATING.search(ln.text):continue
        joined=judge.record_clause(b,ln).text
        # A bidder's completed record owns its floor. A bond, staff member, future
        # deliverable or rating sentence cannot lend that record an amount.
        for text in re.split(r'(?<!\d)\.(?!\d)|[;；。]',joined):
            if not judge.PERF_BIDDER_REQ.search(text) or not FLOOR.search(text):continue
            if judge.X2_STAFF_CTX.search(text) or judge.X2_THIRD_PARTY.search(text):continue
            if judge.PERF_NOTE.search(text) or judge.CREDIT_RATING.search(text):continue
            if judge.x2_single_amount(text) is not None:continue
            values=[]
            for m in RATIO.finditer(text):
                after=text[m.end():m.end()+12]
                if not FLOOR.search(after) or not comparison_is_floor(text,m.end()):continue
                base=b.meta.P if re.sub(r'\s+','',m.group(1)) in ('추정가격','예정가격') else budget
                if base:values.append(base*float(m.group(2))/100)
            money=amounts.compound_money(text)
            money += [m for m in amounts.money(text) if not any(x.start<=m.start<x.end for x in money)]
            for m in money:
                if FLOOR.search(text[m.end:m.end+20]) and comparison_is_floor(text,m.end):values.append(m.value)
            for base,mult in amounts.ratios(text.replace('％','%')):
                reference=b.meta.P if base in ('추정가격','예정가격') else budget
                if reference:values.append(reference*mult)
            # Any admissible lower record alternative prevents a >=1x claim.
            threshold=(min(values) if re.search(r'또는|혹은',text) else max(values)) if values else None
            if threshold is not None and threshold>=budget-1:return ln
    return hit
