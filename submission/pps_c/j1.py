"""J1: one per-item model judgment — a 0/1 answer token and its probability — added by OR to the CPU verdicts of v9 and v1.

The model reads the item's row of the organizer's item table, verbatim law excerpts (assets/j1_law.json, cut from the
organizer's law package by tools/j1.py law), the CPU facts and the family's candidate lines with two lines of context,
and answers 1 (violation) or 0. p1 = P(1) / (P(0) + P(1)) at the answer position, from the top-K logprobs vLLM returns
for the model's own distribution (before any grammar mask, so no grammar is used and the sampled token is not read).
main.py journals every judgment (j1.jsonl.gz); `apply` turns a CPU 0 into 1 only for an item with a threshold in
switches.J1_THRESHOLDS, which is empty by default: record-only runs and the canonical package keep the CPU verdicts.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from . import families as F, judge

ASSETS = Path(__file__).resolve().parent / 'assets'
TOP_K = 20
PROMPT_TOKEN_LIMIT = 6000
MAX_LINES = {'v9': 12, 'v1': 16}
CONTEXT = 2
ANSWER_TOKENS = 4
REASONING_END = '<channel|>'
TARGETS = {'v9': 'model', 'v1': 'inst'}     # item -> the family whose candidate lines the judgment reads
ITEM_ROWS = json.loads((ASSETS / 'items.json').read_text(encoding='utf-8'))['항목']
_LAW = {}

# The item as the table and the law define it; C's reading definitions (families.py) state the same boundaries.
DEFINITION = {
    'v1': ('입찰 참가자격을 대학·연구기관·공공기관·특정 협회 회원 같은 특정 종류의 기관으로만 한정해 일반 사업자가 참가할 수 없게 '
           '하거나, 과업 규모에 비해 과도한 시설·장비·인력(예: 전국 모든 지역의 지점·센터, 수십 명 이상의 인력) 보유를 참가자격으로 '
           '요구하면 위반이다. 법령상 그 일을 할 수 있는 자가 그 유형뿐인 경우(회계감사의 회계법인 등), 면허·업종 등록, 중소기업·'
           '소프트웨어사업자 같은 규모·업종 요건, 제출서류·평가 가점·계약 후 이행 조건, 지명경쟁의 지명은 위반이 아니다.'),
    'v9': ('납품·설치·사용할 물품(또는 그 핵심 부품)을 특정 제조사·상표·모델·시리즈로 지정하면 위반이다. "동등 이상" 허용 문구가 '
           '있어도 지정 자체는 위반으로 본다. 예로만 든 제품명, 기존 장비의 유지보수·호환 요구, USB·KS·ISO 같은 일반 규격·표준 '
           '명칭, 시험·측정 장비 이름은 위반이 아니다.'),
}
NEEDS = {
    'v1': '참가자격 요건일 것(제출서류·평가 가점·계약 후 이행 조건이 아님) / 특정 종류 기관으로 한정하거나 과업 규모에 비해 과도한 '
          '시설·장비·인력 보유를 요구할 것 / 지명경쟁이 아닐 것',
    'v9': '납품·설치·사용할 물품(또는 핵심 부품)을 정하는 요구일 것 / 특정 제조사·상표·모델·시리즈를 지정할 것(예시, 유지보수·호환, '
          '일반 규격 명칭이 아님)',
}
SYSTEM = ('너는 나라장터 입찰공고문 검토자다. 항목 하나를 두고, 항목 정의와 법령 발췌, 공고 정보, 발췌한 줄만 근거로 이 공고가 그 '
          '항목을 위반하는지 판정한다. 발췌에 적혀 있지 않은 것은 추측하지 않는다.\n'
          '답은 숫자 한 글자다. 위반이면 1, 위반이 아니거나 판단할 근거가 없으면 0.')


def law_excerpts(item):
    if not _LAW:
        _LAW.update(json.loads((ASSETS / 'j1_law.json').read_text(encoding='utf-8')))
    return _LAW.get(item, [])


def won(v):
    return '불명' if v is None else f'{int(round(v)):,}원'


def fact_lines(b, item):
    m = b.meta
    law = {'지방': '지방계약법', '국가': '국가계약법'}.get(m.law, '불명')
    comp = {True: '중소기업자간 경쟁제품', False: '경쟁제품 아님(일반 물품·용역)'}.get(b.scope.competitive, '불명')
    rows = [f'업무구분: {m.work or "불명"}', f'계약방법: {m.method or "불명"}', f'낙찰방법: {m.award or "불명"}',
            f'적용계약법: {law}', f'추정가격: {won(m.P)}', f'중소기업자간 경쟁제품 여부: {comp}']
    if item == 'v1':
        rows.append('지명경쟁: ' + ('예' if m.method == '지명경쟁' else '아니오'))
    return rows


def candidates(b, item):
    """The family's candidate lines (C's selection), capped; for v1 the qualification-section lines when there are any."""
    lines = list(b.cands.get(TARGETS[item], []))
    if item == 'v1':
        lines = [ln for ln in lines if judge.qual_section(ln, b.notice)] or lines
    return lines[:MAX_LINES[item]]


def wanted(b, verdicts, mode, thresholds):
    """Items to judge on this notice: those with candidate lines; 'record' judges every target item, 'apply' only items
    with a threshold whose CPU verdict is 0 (only there can OR change it)."""
    return [item for item in TARGETS
            if (mode == 'record' or (item in thresholds and verdicts[item][0] == 0)) and candidates(b, item)]


def messages(b, item, lines):
    row = ITEM_ROWS[item]
    law = '\n'.join(f'- {x["article"]}: {x["text"].replace("]]>", "")}' for x in law_excerpts(item))
    note = (row.get('비고') or '').strip()
    title = (b.titles[0] if b.titles else '')[:80]
    info = (f'사업명: {title} / ' if title else '') + ' / '.join(fact_lines(b, item))
    user = (f'[항목] {item} {row["항목명"]}\n'
            f'[항목 정의] {DEFINITION[item]}\n'
            + (f'[항목표 비고] {note}\n' if note else '')
            + f'[법령 발췌] (지방계약법 공고에는 지방계약법의 같은 취지 조항이 적용된다)\n{law}\n'
            f'[성립 조건] {NEEDS[item]}\n'
            f'[공고 정보] {info}\n'
            f'[발췌]\n{F.excerpt(b.notice, lines, CONTEXT)}\n'
            f'[질문] 이 공고는 "{row["항목명"]}" 항목을 위반하는가? 위반이면 1, 아니면 0. 숫자 한 글자만 답한다.')
    return [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': user}]


def digit_logprobs(cands):
    """{'0': logprob, '1': logprob} among one position's candidates; several token ids decode to the same digit, so the
    best logprob per digit is kept."""
    out = {}
    for tok, lp in cands:
        d = (tok or '').strip()
        if d in ('0', '1') and lp > out.get(d, float('-inf')):
            out[d] = lp
    return out


def answer_probability(positions, after_reasoning=False):
    """positions: [(sampled token text, [(decoded token, logprob), ...best first])] per generated token.

    The answer position is the first one (after the reasoning channel closes, when thinking) whose candidates include a
    digit. A digit outside the top-K takes the K-th candidate's logprob as an upper bound (bound=True). p1 is None when
    no position offers a digit."""
    start = 0
    if after_reasoning:
        text, start = '', None
        for k, (tok, _) in enumerate(positions):
            text += tok or ''
            if REASONING_END in text:
                start = k + 1
                break
        if start is None:
            return {'pos': None, 'lp0': None, 'lp1': None, 'p1': None, 'bound': False, 'top': []}
    for k in range(start, len(positions)):
        cands = positions[k][1]
        d = digit_logprobs(cands)
        if d:
            floor = min(lp for _, lp in cands)
            lp0, lp1 = d.get('0', floor), d.get('1', floor)
            return {'pos': k, 'lp0': round(lp0, 5), 'lp1': round(lp1, 5), 'p1': 1.0 / (1.0 + math.exp(lp0 - lp1)),
                    'bound': len(d) == 1, 'top': [[t, round(lp, 4)] for t, lp in cands[:TOP_K]]}
    top = positions[start][1][:TOP_K] if len(positions) > start else []
    return {'pos': None, 'lp0': None, 'lp1': None, 'p1': None, 'bound': False, 'top': [[t, round(lp, 4)] for t, lp in top]}


def apply(b, verdicts, recs, thresholds):
    """Verdicts with the J1 thresholds applied: a CPU 0 becomes 1 at p1 >= threshold, with the first judged line as
    evidence. Missing records or p1 leave the CPU verdict."""
    out = dict(verdicts)
    for item, th in thresholds.items():
        r = recs.get(item)
        if item in TARGETS and r is not None and r.get('p1') is not None and out[item][0] == 0 and r['p1'] >= th:
            ln = b.notice.lines[r['lines'][0]] if r.get('lines') else None
            out[item] = (1, judge.evidence(ln.text) if ln is not None else '')
    return out
