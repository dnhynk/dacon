"""Verdict-token confidence from vLLM output logprobs (config ``verdict_logprobs`` K > 0; K = 0 leaves requests unchanged).

Each judged item's verdict is one 0/1 token inside the compact answer's ``"v":[...]`` array (or one ``"v":<digit>``
field of a per-item object). With ``SamplingParams(logprobs=K)`` vLLM returns, for every output token, the top-K
candidates' log-probabilities taken before the grammar mask, so the two digits' log-probabilities give the model's own
belief in the verdict; ``confidence`` is P(1) / (P(0) + P(1)). This is a journal diagnostic for aggregate measurement
(runs/rebuild_20260924/natural_fp/PROPOSAL.md 4-B); no cell decision reads it.
"""
from __future__ import annotations

import math
import re

_ARRAY = re.compile(r'"v":\[([01](?:,[01])*)\]')
_OBJECT = re.compile(r'"v(\d+)":\{.*?"v":([01]),"e":')
TAIL_TOKENS = 200   # the verdict array sits in the answer's tail; suffix decoding stays cheap for long thoughts
TOP_JOURNALED = 20  # candidates kept per verdict position in the journal


def _candidates(entry):
    """[(decoded token, logprob)] of one output position's candidates (vLLM Logprob objects or dicts), best first."""
    out = []
    for value in (entry or {}).values():
        if isinstance(value, dict):
            token, logprob = value.get('decoded_token'), value.get('logprob')
        else:
            token, logprob = getattr(value, 'decoded_token', None), getattr(value, 'logprob', None)
        if isinstance(token, str) and logprob is not None:
            out.append((token, float(logprob)))
    return sorted(out, key=lambda t: -t[1])


def _digit_candidates(candidates):
    """{'0': logprob, '1': logprob}: several token ids decode to the same digit (byte fallbacks among them), so the
    best logprob per digit is kept; taking the last one seen recorded -39 for both digits in the first run."""
    out = {}
    for token, logprob in candidates:
        digit = token.strip()
        if digit in ('0', '1') and logprob > out.get(digit, float('-inf')):
            out[digit] = logprob
    return out


def _confidence(lp0, lp1):
    if lp0 is None or lp1 is None:
        return None
    return 1. / (1. + math.exp(lp0 - lp1))


def verdict_confidence(token_ids, logprobs, tokenizer, items, *, skip_special_tokens=True):
    """[{item, verdict, lp0, lp1, confidence, top}] for the judged items whose verdict token was located, else [].

    Offsets come from decoding suffixes of the generated tokens, so multi-byte merges earlier in the answer do not
    shift the mapping. A digit whose alternative is outside the top-K yields confidence None.
    """
    token_ids, logprobs = list(token_ids), list(logprobs)
    if not token_ids or len(logprobs) != len(token_ids):
        return []
    lengths = {}
    suffix = ''
    for k in range(1, min(TAIL_TOKENS, len(token_ids)) + 1):
        suffix = tokenizer.decode(token_ids[-k:], skip_special_tokens=skip_special_tokens)
        lengths[k] = len(suffix)
        if _ARRAY.search(suffix) or _OBJECT.search(suffix):
            break
    match = _ARRAY.search(suffix)
    if match:
        digits = [(match.start(1) + 2 * j, ch) for j, ch in enumerate(match.group(1).split(','))]
        numbers = list(items)
    else:
        digits, numbers = [], []
        for found in _OBJECT.finditer(suffix):
            numbers.append(int(found.group(1)))
            digits.append((found.start(2), found.group(2)))
    if not digits or len(digits) != len(numbers):
        return []
    total = len(suffix)
    kmax = max(lengths)
    result = []
    for number, (offset, digit) in zip(numbers, digits):
        distance = total - offset                 # characters from this digit to the end, inclusive
        k = next((k for k in range(1, kmax + 1) if lengths.get(k, 0) >= distance), None)
        if k is None:
            return []
        candidates = _candidates(logprobs[len(token_ids) - k])
        digits = _digit_candidates(candidates)
        lp0, lp1 = digits.get('0'), digits.get('1')
        # The position's top candidates are journaled too, so any other confidence definition can be computed offline.
        result.append({'item': number, 'verdict': int(digit), 'lp0': lp0, 'lp1': lp1,
                       'confidence': _confidence(lp0, lp1),
                       'top': [[token, round(logprob, 4)] for token, logprob in candidates[:TOP_JOURNALED]]})
    return result


def apply_thresholds(thresholds, entries, values, items):
    """Withdraw a model positive whose journaled confidence is below its item's fixed threshold (config
    ``verdict_thresholds``, pairs of item number and threshold). Returns the withdrawn item numbers; ``values`` is
    edited in place. A missing confidence never withdraws."""
    if not thresholds or not isinstance(entries, list):
        return []
    limit = {int(k): float(t) for k, t in thresholds}
    confidence = {e.get('item'): e.get('confidence') for e in entries if isinstance(e, dict)}
    withdrawn = []
    for k in items:
        c = confidence.get(k)
        if k in limit and c is not None and values[k - 1] == 1 and c < limit[k]:
            values[k - 1] = 0
            withdrawn.append(k)
    return withdrawn
