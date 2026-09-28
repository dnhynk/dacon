"""Red team W3 (switch W3_V4_ONLY_REQ): v4 — a statement limiting the counted records to schools or a public-buyer kind
("3) 최근 3년 이내 수련활동 실적증명서 1부. [중·고등학교 실적만 해당]") when the qualification section itself requires such a
record ("공고일 현재 3년 이내에 수련활동 용역 실적이 있는 업체"): the statement then narrows a participation requirement to
records of specific institutions (정부 입찰·계약 집행기준 제5조④3). A statement with no record requirement in the
qualification section, or inside 적격심사·평가 text, is evaluation material (organizer 9/28: context, not position, decides)."""
import re
from . import judge, switches

EVAL_NEAR = re.compile(r'적\s*격\s*심\s*사|심\s*사\s*(?:기\s*준|항\s*목|표)|평\s*가\s*(?:기\s*준|항\s*목|표)|배\s*점|평\s*점')


def hit(b):
    reqs = [ln for ln in judge.perf_lines(b) if ln.doc_type == '공고문' and ln.sec == 'QUAL'
            and not judge.x2_not_bidder_record(b, ln)]
    if not reqs:
        return None
    for ln in b.notice.lines:
        if ln.doc_type != '공고문' or ln.sec == 'EVAL' or not judge.ONLY_REC.search(ln.text) \
                or not judge.ONLY_RECORDS.search(ln.text):
            continue
        c = judge.LAW_REF.sub(' ', judge.clause_text(b.notice, ln))
        if judge.ONLY_PRIVATE.search(c) or judge.evaluation_context(b, ln):
            continue
        if any(EVAL_NEAR.search(x.text) for x in b.notice.window(ln.i, 6, 2)):
            continue
        if judge.ONLY_SCHOOLS.search(c) or judge.buyer_limit(c) == 'specific':
            return ln
    return None
