"""Red team W3 (switch W3_V19_REVIEW_LIST): v19 — a 확약서 entry of a document list whose other entries name the 적격심사
submission ("8. 제출 서류 … 다. 제조사의 물품공급 및 기술지원확약서 사본 1부 … 마. … 적격심사 신청서 및 관련 증빙자료 제출")
is due at the 적격심사 stage after the bid opening, not at the bid; a CPU list timing is withdrawn there. A model reading that
places the demand before the bid stands. Basis: organizer 9/28 00:17 (the submission section and its deadline decide the stage)."""
import re
from . import judge, switches

REVIEW = re.compile(r'적\s*격\s*심\s*사\s*(?:신\s*청\s*서|서\s*류|관\s*련\s*(?:서\s*류|증\s*빙)|자\s*료)')
BID_HEAD = re.compile(r'입\s*찰\s*(?:참\s*가\s*)?(?:신\s*청|등\s*록)|입\s*찰\s*서\s*(?:와|과)?\s*함\s*께|견\s*적\s*서|제\s*안\s*서\s*제\s*출|투\s*찰')
HEAD = re.compile(r'^\s*(?:\d{1,2}\s*[.)]|[가-하]\s*[.)]|[□■○●◎▣◆◇▶]|\(\s*\d{1,2}\s*\))')


def timed(b, ln, r, was_timed):
    if not was_timed or str(r.get('시점', '')).startswith('입찰 전') or ln.doc_type != '공고문':
        return was_timed
    lines = b.notice.lines
    # the list: up from the entry to its heading (a numbered top line within 12 lines), and 12 lines down in the same section
    top = ln.i
    for x in reversed(lines[max(0, ln.i - 12):ln.i]):
        if x.doc != ln.doc or x.sec != ln.sec:
            break
        top = x.i
    block = [x for x in lines[top:ln.i + 13] if x.doc == ln.doc and x.sec == ln.sec]
    if any(BID_HEAD.search(x.text) for x in block if x.i < ln.i and HEAD.match(x.text)):
        return was_timed
    if any(REVIEW.search(x.text) for x in block if x.i != ln.i):
        return False
    return was_timed
