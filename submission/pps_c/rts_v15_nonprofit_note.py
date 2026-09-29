"""A nonprofit certificate waiver does not restrict other bidders to small firms."""
import re
from . import judge

PURE_NOTE = re.compile(r'^\W*(?:비\s*영\s*리\s*법\s*인|특\s*별\s*법\s*인)')


def admission(b, ln):
    own = judge.own_clause(b, ln)
    return bool(PURE_NOTE.search(ln.text) and judge.NO_CERT_ADMISSION.search(own)
                and not re.search(r'소\s*지\s*한|보\s*유\s*한|으\s*로\s*서', own))


def filter_hit(b, hit):
    if hit is None or hit is True or not admission(b, hit):
        return hit
    _, lines, _ = judge.size_state(b, positive=True, gate_normal=True)
    from .rtd4_size_unread import stated
    from .t4_size_wide import wide
    pairs = [(ln, judge.size_class_for_gate(b, ln)) for ln in lines]
    pairs += stated(b, False) + wide(b)
    remaining = [(ln, cls) for ln, cls in pairs if not admission(b, ln)]
    if any(cls == 'sme' for _, cls in remaining) and not any(cls == 'small' for _, cls in remaining):
        return None
    return hit
