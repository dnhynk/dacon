"""SW-law admission of midsize firms is not an SME-only qualification."""
import re
from . import judge


def filter_hit(b, hit):
    if hit is None or hit is True:
        return hit
    own = judge.own_clause(b, hit)
    if not (judge.sw_only(own) and re.search(r'중\s*견\s*기\s*업', own)
            and re.search(r'입\s*찰\s*참\s*가\s*가\s*능', own)):
        return hit
    _, lines, _ = judge.size_state(b, positive=True, gate_normal=True)
    if any(not judge.sw_only(judge.own_clause(b, ln)) for ln in lines):
        return hit
    from .rtd4_size_unread import stated
    from .t4_size_wide import wide
    if stated(b, False) or wide(b):
        return hit
    return None
