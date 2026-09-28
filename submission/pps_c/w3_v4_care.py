"""Red team W3 (switch W3_V4_CARE_KINDS): v4 — a record requirement whose buyers are care or welfare facility kinds the buyer
vocabulary lacks ("사회복지관련 기관이나, 노인관련시설, 또는 장기요양기관에서 급식위탁 운영 실적이 있는 업체"). Basis: 정부 입찰·계약
집행기준 제5조④3 (records limited to the institutions that ordered them), item 비고 "특정기관 표현 다양". Silent when the clause
admits private parties or the orderer's open wording."""
import re
from . import judge, switches

CARE = re.compile(r'(?:사\s*회\s*복\s*지\s*관\s*련\s*(?:기\s*관|시\s*설)|노\s*인\s*(?:관\s*련|복\s*지|요\s*양|의\s*료)\s*(?:시\s*설|기\s*관)'
                  r'|(?:장\s*기\s*)?요\s*양\s*(?:기\s*관|시\s*설)|장\s*애\s*인\s*(?:복\s*지\s*)?(?:시\s*설|기\s*관)|아\s*동\s*(?:복\s*지\s*)?시\s*설'
                  r'|보\s*육\s*시\s*설)')
# the facility is the record's source: a locative or genitive relation, then a record, delivery or operation word
CARE_REL = re.compile(CARE.pattern + r'\s*(?:[,·ㆍ]|이\s*나|또\s*는|및|등)?[^.。;]{0,40}?(?:에\s*서|에|의|과|와)?\s*[^.。;]{0,30}?'
                      r'(?:실\s*적|경\s*험|이\s*력|납\s*품|공\s*급|운\s*영|위\s*탁)')


def hit(b):
    recs = judge.perf_lines(b)
    known = {ln.i for ln in recs}
    recs = recs + [ln for ln in judge.x2_unread_records(b) if ln.i not in known]
    for ln in recs:
        c = judge.LAW_REF.sub(' ', judge.clause_text(b.notice, ln))
        if not CARE_REL.search(c):
            continue
        if judge.PRIVATE_SECTOR.search(c) or judge.ORDERER_OPEN2.search(c) or judge.x1_private_in_enum(c):
            continue
        return ln
    return None
