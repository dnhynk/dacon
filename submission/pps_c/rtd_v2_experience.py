"""Completed bidder work is experience even when the noun '실적' is absent."""
import re
from . import judge

WORK = re.compile(r'용\s*역|물\s*품|장\s*비|품\s*목|사\s*업|행\s*사|교\s*육|연\s*구|계\s*약|납\s*품|공\s*급|설\s*치|운\s*영|제\s*조|제\s*작')
PAST = re.compile(r'(?:수행|이행|준공|납품|공급|운영|개최|제작|설치)\s*(?:한|하였던|했던|해\s*본|하여\s*본)'
                  r'|(?:용역|사업|행사|납품|제작|연구|검수|운영)\s*(?:을|를)?\s*완료\s*(?:한|하였던|했던)'
                  r'|(?:납품|공급|설치)\s*(?:을|를)?\s*마친|(?:준공|검수)\s*검사\s*(?:를)?\s*받은'
                  r'|검수\s*(?:를)?\s*완료한')
ENTITY = r'(?:업\s*체|사\s*업\s*자|법\s*인|회\s*사|자)(?=$|[^가-힣]|만|에|로|은|는|이|여|으로|도)'
ADJECTIVE = re.compile('(?:'+PAST.pattern + r')\s*' + ENTITY + r'|(?:수행|이행|운영|납품)\s*한\s*적이\s*있는\s*' + ENTITY)
HISTORY = re.compile(r'(?:수행|이행|운영|납품|공급|완료)\s*한\s*(?:적|사실|경험)[^.。]{0,24}(?:있어야|증명할\s*수\s*있는\s*' + ENTITY + r')')
HELD = re.compile(r'(?:실\s*적|경\s*험|이\s*력|경\s*력)[^.。]{0,28}(?:보유|갖추|갖춘|있는|있어야)')
NO_RECORD_BAN = re.compile(r'(?:실\s*적|경\s*험|이\s*력)[^.。]{0,12}없는\s*' + ENTITY + r'[^.。]{0,12}(?:입찰|참가|참여)[^.。]{0,14}(?:수\s*없|불가|불허)')
WAIVER = re.compile(r'(?:실\s*적|경\s*험|이\s*력)[^.。]{0,25}(?:없어도|없더라도|무관|묻지|보유하지\s*않아도|요구하지|제한하지)'
                   r'|(?:실적|경험)\s*(?:이|을)?\s*없(?:는)?\s*업체\s*도|(?:실적|경험)\s*제한\s*없')
FUTURE = re.compile(r'(?:낙찰|선정|계약\s*체결)\s*(?:후|이후)|계약\s*(?:상대자|이행\s*중)|착수\s*후')
PREFERENCE = re.compile(r'우대|가점|배점|점수|평가\s*항목')

def requirement(text):
    if not WORK.search(text) or WAIVER.search(text) or FUTURE.search(text) or PREFERENCE.search(text):return False
    if judge.X2_STAFF_CTX.search(text) or judge.X2_THIRD_PARTY.search(text):return False
    if judge.CREDIT_RATING.search(text) or judge.X2_STATUTORY_RECORD.search(text):return False
    if judge.PERF_NOTE.search(text) or judge.PERF_FORMISH.search(text) or re.search(r'명단|참고자료|예시|사례|목록',text):return False
    # Each independently eligible branch must require completed work. An
    # admissible certificate-only alternative makes the record optional.
    parts=[];start=0
    for branch in re.finditer(r'(?:업체|사업자|법인|회사|(?<![가-힣])자)\s*[,，]?\s*(?:또는|혹은)',text):
        joiner=re.search(r'또는|혹은',branch.group())
        parts.append(text[start:branch.start()+joiner.start()])
        start=branch.start()+joiner.end()
    parts.append(text[start:])
    return all(WORK.search(part) and (ADJECTIVE.search(part) or HISTORY.search(part) or NO_RECORD_BAN.search(part)) for part in parts)

def augment(b,hit):
    if hit is not None:return hit
    P=b.meta.P
    if judge.switches.AUDIT_FIXES3 and P is not None and P<1e6 and b.meta.B and b.meta.B>=1e6:P=b.meta.B/1.1
    if P is None or P>=judge.NOTICE_AMOUNT or b.meta.local_private:return None
    for ln in b.notice.lines:
        if ln.doc_type!='공고문' or ln.sec!='QUAL' or not judge.qual_section(ln,b.notice):continue
        if not WORK.search(ln.text) or judge.evaluation_context(b,ln) or judge.in_form_annex(b,ln):continue
        # Qualification predicates belong to their own sentence, never to an
        # adjacent staff entry or an evaluation/document heading.
        text=judge.clause_text(b.notice,ln)
        for own in re.split(r'(?<!\d)\.(?!\d)|[。；;]',text):
            if requirement(own):return ln
    return None
