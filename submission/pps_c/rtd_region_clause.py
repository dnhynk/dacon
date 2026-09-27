"""Explicit bidder-office clauses missed by the finite region reading candidates."""
import re
from . import judge,regions
OFFICE=re.compile(r'본\s*점|본\s*사|주\s*된\s*(?:영\s*업\s*소|사\s*무\s*소)|주\s*사\s*무\s*소|사\s*업\s*장(?!\s*소)|영\s*업\s*소')
BIDDER_HELD=re.compile(r'소\s*재[^.。]{0,60}(?:업\s*체|사\s*업\s*자|법\s*인)(?:이|여)?어야')
JOINT_MEMBER=re.compile(r'공\s*동\s*(?:수\s*급|도\s*급|이\s*행)|구\s*성\s*원')
MUST=re.compile(r'있\s*어\s*야|두\s*어\s*야|소\s*재\s*하\s*여\s*야|소\s*재\s*해\s*야|위\s*치\s*하\s*여\s*야')
OFFICE_HELD=re.compile('(?:'+OFFICE.pattern+r')[^.。]{0,65}(?:두고\s*있는|둔|소재한|소재하는|있는|인|소재\s*)\s*(?:업체|사업자|법인|자)(?=$|[^가-힣]|만|에|로|은|는|이|여|으로|도)')
LOCAL_BIDDER=re.compile(r'(?:관내|내|소재한|소재하는|에\s*있는)\s*(?:업체|사업자|법인)(?=$|[^가-힣]|만|에|로|은|는|이|여|으로|도)')
BIDDER_ADDRESS=re.compile(r'(?:입찰\s*참가자|참가자|참가업체)(?:의)?\s*소재지[^.。]{0,35}(?:이어야|여야)')
RAW_BASIC=re.compile(r'(?<![가-힣])(?:'+ '|'.join(sorted(judge.X7_BASIC_UNITS,key=lambda x:-len(x))) +r')(?:시|군)(?=$|[^가-힣]|에|인|내|의|이|또는|및)')
ORDERER_BASIC=re.compile(r'\[수요기관\(기초자치단체\)\]\s*(?:관내|내|안|지역|에)')

def geography(text):
    geo=regions.mentions(text)
    # Raw municipal names are interpreted only inside an already qualified
    # bidder-location clause. Reuse the existing gazetteer; do not widen shared
    # region readings or the v24 registered-region comparison.
    if RAW_BASIC.search(text) or ORDERER_BASIC.search(text):geo['basic'].add(('literal-basic',''))
    return geo
POST_AWARD=re.compile(r'(?:낙\s*찰|선\s*정|계\s*약\s*체\s*결)\s*(?:된\s*후|후|이후)[^.。]{0,45}(?:설\s*치|개\s*설|이\s*전)')

FACILITY=re.compile(r'처\s*리\s*시\s*설|공\s*장|폐\s*기\s*물[^.。]{0,30}사\s*업\s*장|\d+\s*(?:km|㎞|킬로미터)')

def own_office_clause(b,ln):
    text=ln.text
    if judge.SENT_END.search(text):return text
    for nxt in b.notice.window(ln.i,0,5)[1:]:
        if not nxt.text.strip():continue
        if nxt.sec!=ln.sec or judge.ITEM_START.match(nxt.text) or judge.REGION_ROW_LABEL.match(nxt.text):break
        text+=' '+nxt.text.strip()
        if judge.SENT_END.search(nxt.text):break
    return text

def clauses(b):
    for ln in b.notice.lines:
        if ln.doc_type!='공고문' or ln.sec in ('EVAL','DOCS'):continue
        text=own_office_clause(b,ln)
        if FACILITY.search(text):continue
        if re.search(r'(?:낙찰|선정|계약\s*체결)\s*(?:된\s*후|후|이후)|지역\s*제한[^.。]{0,8}(?:없|않)',text):continue
        held_office=OFFICE.search(text) and (judge.BID_LOCATION.search(text) or MUST.search(text) or BIDDER_HELD.search(text) or OFFICE_HELD.search(text))
        local_qualification=judge.qual_section(ln,b.notice) and (LOCAL_BIDDER.search(text) or BIDDER_ADDRESS.search(text))
        if not (held_office or local_qualification):continue
        if JOINT_MEMBER.search(text):continue
        if judge.REGION_NONE.search(text) or judge.JV_PARTNER3.search(text):continue
        if judge.X7_OFFICE_DUTY.search(text) or POST_AWARD.search(text):continue
        if judge.X7_WORK_SITE.search(text) or judge.X7_BONUS.search(text):continue
        geo=geography(text)
        if geo['sido'] or geo['basic'] or judge.orderer_level(text):yield ln,geo,text

def v5(b,hit):
    if hit is not None or b.meta.P is None or b.meta.P<b.meta.T_hi:return hit
    return next((ln for ln,_,_ in clauses(b)),None)

def v6(b,hit):
    if hit is not None or b.meta.P is None or b.meta.P>=b.meta.T_lo or b.meta.local_private:return hit
    return next((ln for ln,geo,text in clauses(b) if geo['basic'] or judge.orderer_level(text)=='basic'),None)

def v7(b,hit):
    if hit is not None or b.meta.P is None or b.meta.P>=b.meta.T_lo or b.meta.local_private:return hit
    return next((ln for ln,geo,_ in clauses(b) if len(geo['sido'])>=2 and not (judge.switches.V7_SITE_SPAN and judge.x7_site_spans(b,geo['sido']))),None)
