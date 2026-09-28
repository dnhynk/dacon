"""Red team W3 (switch W3_V7_ATTACH_REGION): v7 — below T (not 지방 수의계약), a bidder-location clause in an attachment's
qualification section naming two or more 시·도 ("다. 경기도 또는 서울 내 사업장이 위치해 있으며, … 과업을 수행할 능력이 있는
업체", 과업지시서) extends the regional restriction beyond one 시·도 as the 공고문 clauses v7 already reads do. Basis:
지방계약법 시행규칙 제25조③, 국가계약법 시행규칙 제25조; JV partner, title and "지역제한 없음" clauses are excluded as in
judge.v7_adjacent."""
import re
from . import judge, regions, switches

LOC = re.compile(r'본\s*점|본\s*사|주\s*된\s*(?:영\s*업\s*소|사\s*무\s*소)|사\s*업\s*장|영\s*업\s*소|소\s*재\s*지')
NOT_LOC = re.compile(r'납\s*품\s*장\s*소|배\s*송|주\s*소\s*[:：]|교\s*육\s*장\s*소|개\s*최|행\s*사\s*장|현\s*장\s*설\s*명|근\s*무\s*지')


def hit(b):
    P = b.meta.P
    if P is None or P >= b.meta.T_lo or b.meta.local_private:
        return None
    for ln in b.notice.lines:
        if ln.doc_type == '공고문' or not judge.qual_section(ln, b.notice):
            continue
        cl = judge.region_clause_text(b.notice, ln)
        if not LOC.search(ln.text) or NOT_LOC.search(cl):
            continue
        if judge.V7_ADJ_TITLE.search(cl) or judge.JV_PARTNER3.search(cl) or judge.REGION_NONE.search(cl):
            continue
        if len(regions.mentions(ln.text)['sido']) >= 2:
            return ln
    return None
