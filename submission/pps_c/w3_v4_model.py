"""Red team W3 (switch W3_V4_MODEL_BUYER): v4 — a qualification-section record clause (공고문 or attachment) the perf family read as a participation
condition limited to specific orderers ('특정 발주기관만') whose buyer the CPU vocabulary cannot name ("대한축구협회 혹은
한국프로축구연맹 산하 프로축구단 납품 실적이 있는 업체", "제1금융권* 담보부 부실채권을 … 매각한 실적을 보유한 국내
회계법인으로 제한", "교육기관(학교 …) 대상의 실적만 인정") keeps the model's reading when the CPU buyer reader found no buyer
relation at all, the clause admits no private or general party and states a requirement. Basis: 정부 입찰·계약 집행기준
제5조④3, item 비고 "특정기관 표현 다양"."""
import re
from . import judge, switches

REQ = re.compile(r'(?:실\s*적|이\s*력|경\s*험)[^.。]{0,60}?(?:있\s*어\s*야|보\s*유\s*하\s*여\s*야|보\s*유\s*해\s*야|갖\s*추\s*어\s*야)'
                 r'|(?:실\s*적|이\s*력|경\s*험)\s*(?:만|에\s*한\s*(?:함|하여|한\s*다|정))|(?:실\s*적|이\s*력|경\s*험)\s*만\s*(?:인\s*정|해\s*당)'
                 r'|(?:실\s*적|이\s*력|경\s*험)\s*(?:을|를)?\s*보\s*유\s*(?:한)?\s*[가-힣\s]{0,12}?(?:업\s*체|자|법\s*인|기\s*관|사\s*업\s*자)(?!\s*명)')
GENERAL = re.compile(r'일\s*반|기\s*업\s*체|민\s*간|모\s*든|제\s*한\s*(?:없|하지)|무\s*관|불\s*문')


def hit(b):
    for ln in judge.perf_lines(b):
        if ln.sec != 'QUAL':
            continue
        r = b.read('perf', ln)
        if r.get('역할') != '참가자격' or r.get('발주처') != '특정 발주기관만':
            continue
        clause = judge.clause_text(b.notice, ln)
        if judge.buyer_limit(clause) is not None:
            continue          # the CPU buyer reader decided this clause
        c = judge.LAW_REF.sub(' ', clause)
        bare = judge.LAW_NAME.sub(' ', clause)
        if (judge.PRIVATE_SECTOR.search(bare) or judge.x1_private_in_enum(c) or judge.ORDERER_OPEN2.search(clause)
                or GENERAL.search(bare)):
            continue
        if judge.PERF_BIDDER_REQ.search(clause) or REQ.search(clause):
            return ln
    return None
