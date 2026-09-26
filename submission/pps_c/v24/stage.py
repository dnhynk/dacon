"""v24p 단계의 런타임 이음새: 요청 빌드(발췌 → 메시지 → 토큰, 한도 초과 시 발췌 축소), 응답 소비(JSON → 검증 게이트 → b.v24p), 판정."""
import json
from dataclasses import replace

from .. import families as F, switches
from . import compare, parse, prompt, select

FAM = 'v24p'
PROMPT_TOKEN_LIMIT = 12000
MIN_CAP = 12


def request(engine, b, k, request_cls):
    """발췌가 토큰 한도에 들 때까지 상한을 75%씩 줄인다(main.make_request의 후보 잘림 대신 우선순위 순으로 줄인다)."""
    cap = select.CAP
    view = b
    while True:
        cands = select.excerpt(view.notice, attach=getattr(switches, "V24P_ATTACH", False), cap=cap)
        if not cands:
            return None
        ids = engine.token_ids(prompt.messages(view, cands))
        if len(ids) + prompt.MAX_TOKENS <= min(PROMPT_TOKEN_LIMIT, engine.max_model_len - 64):
            break
        if cap > MIN_CAP:
            cap = max(MIN_CAP, int(cap * 0.75))
        elif view is b:
            view = replace(b, notice=replace(b.notice, meta={key: None if value is None else F.shown(str(value))
                                                           for key, value in b.notice.meta.items()}))
        elif cap > 1:
            cap = max(1, int(cap * 0.75))
        else:
            raise ValueError('v24p: minimum reading request exceeds the engine token limit')
    return request_cls(k, FAM, cands, (), ids, prompt.schema(), prompt.MAX_TOKENS, 0)


def locate(notice, cands, i, value):
    """옮겨진 값이 실제로 적힌 줄. 모델이 가리킨 줄, 그 앞뒤 한 줄, 그다음 발췌 줄 순으로 찾고, 줄바꿈으로 갈린 값은 앞뒤 줄을 이어 본다.
    발췌에 실제로 보여 준 문자열만 인정한다. 어디에도 없으면 None(모델이 지어냈거나 고친 값)."""
    nv = parse.norm_gate(value)
    if len(nv) < 2:
        return None
    order = []
    shown_ids = {ln.i for ln in cands}
    if isinstance(i, int) and i in shown_ids:
        order.append(notice.lines[i])
        order += [w for w in notice.window(i, 1, 1) if w.i != i and w.i in shown_ids]
    order += [c for c in cands if all(c.i != o.i for o in order)]
    for ln in order:
        if parse.copied_value_in(F.shown(ln.text), value) and parse.copied_value_in(ln.text, value):
            return ln
    if isinstance(i, int) and i in shown_ids:
        window = [w for w in notice.window(i, 1, 1) if w.text.strip()]
        joined = ''.join(F.shown(w.text) for w in window)
        original = ''.join(w.text for w in window)
        if (all(w.i in shown_ids for w in window) and parse.copied_value_in(joined, value)
                and parse.copied_value_in(original, value)):
            return notice.lines[i]
    return None


def consume(b, cands, text):
    """모델 출력을 읽어 b.v24p = {항목: [(줄, 이름표, 값)]}를 세운다. JSON이 아니면 False(재시도 대상)."""
    try:
        obj = json.loads(text.split('<channel|>', 1)[-1])
    except (ValueError, AttributeError):
        return False
    if not isinstance(obj, dict):
        return False
    if any(not isinstance(obj.get(field), list) for field in prompt.FIELD_NAMES):
        return False
    for field in prompt.FIELD_NAMES:
        if len(obj[field]) > prompt.MAX_ITEMS:
            return False
        if any(not isinstance(it, dict) or type(it.get('줄')) is not int
               or not isinstance(it.get('이름표'), str) or not isinstance(it.get('값'), str)
               for it in obj[field]):
            return False
    out = {}
    for field in prompt.FIELD_NAMES:
        arr = obj.get(field)
        kept = []
        if isinstance(arr, list):
            for it in arr[:prompt.MAX_ITEMS]:
                if not isinstance(it, dict):
                    continue
                value = str(it.get('값') or '').strip()
                label = str(it.get('이름표') or '').strip()
                if not value:
                    continue
                ln = locate(b.notice, cands, it.get('줄'), value)
                if ln is not None:
                    kept.append((ln, label, value))
        out[field] = kept
    b.v24p = out
    return True


def verdict(b):
    return compare.verdict(b)
