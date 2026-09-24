"""A10 premise score from source-only facts (runs/rebuild_20260924/stage1/precheck/A10_PREMISE.md, fixed before use).

Four facts of the supplied-catalog qualification reading, each worth one point: a listed competitive product among
the purchase codes, an estimated price from 100 million up to 230 million won, an operative bidder-size restriction,
and an observed direct-production certificate requirement. No model answer is read.
"""
from __future__ import annotations

FLOOR, NOTICE = 100_000_000, 230_000_000


def score(knowledge, record):
    from .qualification import band_estimate
    _, facts = knowledge.qualification_decisions(record, {f'v{k}': '0' for k in range(10, 19)})
    product, qualification = facts['product'], facts['qualification']
    estimate = product['estimate_won']
    if estimate is None:
        estimate = band_estimate(product)[0]
    return sum((any(row['listed'] for row in product['products']),
                estimate is not None and FLOOR <= estimate < NOTICE,
                bool(qualification['allowed']),
                bool(qualification['direct_certificate_coverage']['observations'])))
