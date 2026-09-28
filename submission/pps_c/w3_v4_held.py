"""Red team W3 (switch W3_V4_HELD_HISTORY): v4 — a qualification record clause naming a public buyer kind next to the record
("… 단일 납품건 30억 이상 공공기관 고압가스용기 납품 완료 이력을 보유하여야 합니다") that the model read as limited to specific
orderers, when the requirement is worded "…이력/실적/경험 … 보유하여야/있어야" further than the shared predicate reaches. Basis:
정부 입찰·계약 집행기준 제5조④3; the other gates of the model-reading path in judge.v4_base are kept (a named buyer other than
the orderer token, no private party)."""
import re
from . import judge, switches

HELD_REQ = re.compile(r'(?:실\s*적|경\s*험|이\s*력)[^.。]{0,60}?(?:있\s*어\s*야|보\s*유\s*하\s*여\s*야|보\s*유\s*해\s*야|갖\s*추\s*어\s*야)'
                      r'|참\s*가\s*하\s*기\s*위\s*해\s*서\s*는[^.。]{0,80}?(?:실\s*적|이\s*력|경\s*험)')


def hit(b):
    for ln in judge.perf_lines(b):
        if ln.doc_type != '공고문' or ln.sec != 'QUAL':
            continue
        clause = judge.clause_text(b.notice, ln)
        if judge.buyer_limit(clause) is not None or judge.PERF_BIDDER_REQ.search(clause) or not HELD_REQ.search(clause):
            continue
        bare = judge.LAW_NAME.sub(' ', clause)
        named = any(not m.group(0).startswith('[수요기관') for m in judge.buyer_matches(bare))
        if (named and not judge.PRIVATE_SECTOR.search(bare) and not judge.x1_private_in_enum(judge.LAW_REF.sub(' ', clause))
                and b.read('perf', ln).get('발주처') == '특정 발주기관만'):
            return ln
    return None
