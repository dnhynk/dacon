"""v24p 비교: 모델이 옮긴(검증 게이트를 통과한) 값을 나라장터 meta와 같은 필드에서 비교한다. 규칙의 의미는 기존 v24 축과 같고
(judge.v24_amount_wide, v24_base_zone, v24_transposed, v24_license, v24_region, v24_title/S3), 입력만 정규식 대신 모델 판독이다.
축 순서: 전치 > 금액 > 기초금액 > 업종 > 지역 > 계약방법. 첫 발화 줄이 근거다."""
import re

from .. import judge as J, regions, switches
from . import parse

# 예산·추정금액은 배정예산(B)과, 추정가격은 입찰추정가격(P)과 같은 필드다. 기초금액은 자기 존 규칙(base_zone)으로만 본다: 기초금액이 B와 같아도
# 편집된 예산 줄은 남는다(심어진 편집은 한 줄만 바뀐다).
GROUP = {'예산': 'B', '추정금액': 'B', '추정가격': 'P'}
AMOUNT_FIELDS = ('예산', '추정금액', '추정가격')
TAG_LOOSE = re.compile(r'(수의계약|제한경쟁|일반경쟁|지명경쟁)\s*[·ㆍ・,]\s*(\d+(?:\.\d+)?\s*(?:천\s*만|억|만)\s*원)\s*(미만|이상|이하|초과)')
UNIT_NOTE = re.compile(r'단\s*위\s*[:：]?\s*천\s*원')
# 금액 뒤에 미만·이상·이하·초과가 붙으면 적격심사·실적 기준의 금액대이고 공고의 금액 진술이 아니다("추정가격 2억원 미만인 용역").
THRESHOLD_AFTER = re.compile(r'^\s*(?:원)?\s*(?:미\s*만|이\s*상|이\s*하|초\s*과|이\s*면\s*서)')
# 기준·평가 문구가 있는 줄의 한글 숫자 금액(억·천만원)은 기준 금액대다.
CRITERIA = re.compile(r'적\s*격\s*심\s*사|평\s*가\s*기\s*준|낙\s*찰\s*하\s*한|별\s*표|고\s*시\s*금\s*액|실\s*적|세\s*부\s*기\s*준|결\s*정\s*기\s*준')
# 평가기준금액·실적 금액은 공고의 금액 진술이 아니다(숫자 금액이어도).
CRITERIA_STRICT = re.compile(r'평\s*가\s*기\s*준\s*금\s*액|기\s*준\s*금\s*액|실\s*적|낙\s*찰\s*하\s*한')
# 호차·호기·구역·권역별 금액과 여러 건의 합계는 부분·집계 금액이다.
LOT = re.compile(r'\d+\s*호\s*(?:차|기)|\d+\s*차\s*분|구\s*역|권\s*역|\d+\s*차\s*년|차\s*수\s*별|총\s*\d+\s*건')
# 주소·제출 장소·연락처 줄의 지역명은 참가자 소재지 제한이 아니다.
ADDRESS = re.compile(r'주\s*소|제\s*출\s*장\s*소|우\s*편|우\s*\)|사\s*업\s*장\s*소|사\s*서\s*함|☏|☎|전\s*화|\[상세주소\]|\[우편번호\]|납\s*품|이\s*행\s*장\s*소|일\s*원|장\s*소\s*[:：]')
# 소재지 제한 문구.
RESTRICT = re.compile(r'소\s*재|본\s*점|주\s*된\s*(?:영\s*업\s*소|사\s*무\s*소)|지\s*역\s*제\s*한|둔\s*(?:업\s*체|자|기\s*업)|등\s*록\s*지\s*역|관\s*내|지\s*역\s*업\s*체|(?:소재|주소)\s*지')
BASE_LABEL = re.compile(r'기\s*초\s*금\s*액')
# 단가 × 수량 산식으로 적힌 금액은 산출 내역이다("금69,000,000원(2,500원×120명×230일)").
UNIT_FORMULA = re.compile(r'원\s*[×xX*]\s*\d')
NEIGHBOUR = 8        # 구역별 기초금액 옆(같은 문서 ±8줄)에 등록 금액과 같은 총액이 적혀 있으면 부분 금액이다
HEAD_METHOD_LINES = 12
TAG_LINES = 80
LISTING = 3          # 한 필드에 서로 다른 값이 이만큼 이상 적히면 여러 건·구역의 목록이다
# PX 가드: 예산 줄이 스스로 추정·변동 가능 금액이라고 적은 개산 표기("예산액으로 추정금액이며 … 변동될 수 있습니다")는 등록 금액과 대조할
# 진술이 아니다. 표의 맨 숫자 칸(값만 있는 줄)과 그 표의 범위(±20줄)는 부분 행 가드가 본다.
ESTIMATE_NOTE = re.compile(r'(추\s*정|예\s*상)\s*(금\s*액|액)\s*(이\s*며|으\s*로\s*서)|변\s*동\s*(될|할|이)\s*수|변\s*동\s*가\s*능|개\s*산\s*(액|금\s*액|예\s*산)')
BARE_CELL = re.compile(r'^\W*(?:금\s*)?[₩￦\\]?\s*\d[\d,]*(?:\.\d+)?\s*(?:천\s*만\s*원|백\s*만\s*원|만\s*원|천\s*원|원)?\s*$')
TABLE_SPAN = 20


def agree(v, ref):
    """같은 값으로 본다: 동일, 부가가치세 ×1.1 관계, 0.2% 안의 차이(1원 반올림·절사), 끝자리 0 단위(10만원까지)로 절사·반올림·올림한 값."""
    if ref is None or ref <= 0:
        return False
    if abs(v - ref) <= 1.5 or J.vat_related(v, ref) or abs(v - ref) / ref <= 0.002:
        return True
    digits = str(int(round(v)))
    zeros = min(len(digits) - len(digits.rstrip('0')), 5)
    if zeros < 1:
        return False
    unit = 10 ** zeros
    return v in (ref // unit * unit, round(ref / unit) * unit, -(-ref // unit) * unit, (ref + unit / 2) // unit * unit)


def agree_any(v, refs, mults=(1,)):
    return any(agree(v * m, r) for m in mults for r in refs if r)


def thousand_unit(b, ln):
    """값 줄 위 20줄 안(같은 문서)에 '단위: 천원' 표기가 있으면 ×1,000 후보도 본다."""
    return any(UNIT_NOTE.search(w.text) for w in b.notice.window(ln.i, 20, 0))


def band_like(value, start, end, line_text):
    """옮겨진 금액이 금액대(…원 미만/이상)로 쓰였는가. 옮겨진 문자열 안, 없으면 줄 안에서 그 금액 뒤를 본다."""
    if THRESHOLD_AFTER.match(value[end:end + 10]):
        return True
    raw = value[start:end]
    idx = line_text.find(raw)
    return idx >= 0 and bool(THRESHOLD_AFTER.match(line_text[idx + len(raw):idx + len(raw) + 10]))


def stated(b, items, fields):
    """[(줄, 항목, 이름표, 금액)]. 한 값 안에 등록 금액과 같은 금액이 하나라도 있으면(숫자와 한글 표기, A·B 구역 쌍) 그 값은 전부 침묵한다."""
    P, B = b.meta.P, b.meta.B
    out = []
    for field in fields:
        for ln, label, value in items.get(field, ()):
            if J.UNIT_CUE.search(ln.text) or LOT.search(ln.text) or CRITERIA_STRICT.search(ln.text) or UNIT_FORMULA.search(ln.text):
                continue
            criteria = bool(CRITERIA.search(ln.text))
            mults = (1, 1000) if thousand_unit(b, ln) else (1,)
            found = []
            for v, start, end in parse.money_spans(value):
                if v < 1e5 or band_like(value, start, end, ln.text):
                    continue
                if criteria and not re.search(r'\d,\d{3}', value[start:end]):
                    continue                          # 기준 문구 줄의 한글 숫자 금액대
                if not any(abs(v - old) <= 1.5 for old in found):
                    found.append(v)                 # Korean and digit spellings of one copied amount are not two lots
            if any(agree_any(v, (P, B), mults) for v in found):
                continue
            out.extend((ln, field, label, v) for v in found)
    return out


def group_agrees(b, items, group):
    """같은 등록 필드(B: 예산·추정금액·기초금액, P: 추정가격)에 적힌 값 하나가 등록 금액과 같으면 그 필드는 일치한다. 다른 값은 구역·건별·내역 금액이다."""
    P, B = b.meta.P, b.meta.B
    for field, g in GROUP.items():
        if g != group:
            continue
        for ln, label, value in items.get(field, ()):
            mults = (1, 1000) if thousand_unit(b, ln) else (1,)
            if any(v >= 1e5 and agree_any(v, (P, B), mults) for v, _, _ in parse.money_spans(value)):
                return True
    return False


def transposed(b, items):
    refs = [r for r in (b.meta.B, b.meta.P) if r]
    if not refs:
        return None
    for ln, field, label, v in stated(b, items, AMOUNT_FIELDS + ('기초금액',)):
        if any(J.vat_related(v, r) for r in refs):
            continue
        if any(parse.permutes(v, r) for r in refs):
            return ln
    return None


# PX 가드(switches.V24P_GUARDS, audit/expert/PX/REPORT.md "V24W1P"): 표의 맨 숫자 칸에서 옮긴 값은, 같은 표(같은 문서 ±20줄)에
# 그보다 큰 금액이 그 값의 필드 등록 금액과 같게 적혀 있으면 부분·소계 행이다(학교별 우유 소계 옆의 '계', 기관별 표의 다른 행, '총금액'
# 행 아래의 '도서 구입' 행). 부가가치세 관계는 같은 값으로 보지 않고(기초금액·추정가격 칸이 서로 VAT 관계다), 기초금액으로 옮겨졌거나
# 기초금액 이름표가 붙은 줄은 보지 않는다(기초금액이 B와 같아도 편집된 예산·추정금액 칸은 남는다).
def component_row(b, ln, v, ref, items):
    if not ref or not BARE_CELL.match(ln.text):
        return False
    base_lines = {x.i for x, _, _ in items.get('기초금액', ())}
    for w in b.notice.window(ln.i, TABLE_SPAN, TABLE_SPAN):
        if w.i == ln.i or w.i in base_lines or base_labelled(b, w):
            continue
        for u, _, _ in parse.money_spans(w.text):
            if u > v and (abs(u - ref) <= 1.5 or abs(u - ref) / ref <= 0.002):
                return True
    return False


def amount(b, items):
    P, B = b.meta.P, b.meta.B
    if not (P or B):
        return None
    found = stated(b, items, AMOUNT_FIELDS)
    if not found:
        return None
    vals = [v for *_, v in found]
    if len(vals) >= 2 and any(r and abs(sum(vals) - r) <= 2 for r in (P, B)):
        return None                                   # 분할 발주 부분금액의 합이 등록 금액
    for group in ('B', 'P'):
        if group_agrees(b, items, group):
            continue
        rows = [x for x in found if GROUP[x[1]] == group]
        if len({round(v) for *_, v in rows}) >= LISTING:
            continue                                  # 여러 건·구역의 금액 목록
        for ln, field, label, v in rows:
            if J.BREAKDOWN.search(ln.text) or J.BREAKDOWN.search(label or ''):
                continue
            ref = (P if group == 'P' else B) or P or B
            if switches.V24P_GUARDS and (ESTIMATE_NOTE.search(ln.text) or component_row(b, ln, v, ref, items)):
                continue                              # PX 가드: 개산 표기, 표의 부분·소계 행
            registered = field == '추정가격' or bool(J.REGISTERED_FIELD.search(label or ''))
            lo, hi = (0.2, 5) if registered else (0.5, 2)
            if not lo <= v / ref <= hi:
                continue
            if any(abs(r / v - k) <= 0.005 * k for r in (P, B) if r for k in (2, 3, 4, 5)):
                continue                              # 등록 총액의 정확한 1/k: 연차·구역별 금액
            return ln
    return None


def base_labelled(b, ln):
    """기초금액 이름표가 그 줄 또는 바로 위 줄(세로 표)에 있는가. 표 머리에서 멀리 떨어진 칸(학교별·구역별 행)은 아니다."""
    if BASE_LABEL.search(ln.text):
        return True
    prev = [w for w in b.notice.window(ln.i, 3, 0) if w.i < ln.i and w.text.strip()]
    return bool(prev) and bool(BASE_LABEL.search(prev[-1].text))


def neighbour_agrees(b, ln, refs):
    """값 줄 앞뒤 ±8줄(같은 문서)에 등록 금액과 5% 안에서 같은 금액이 적혀 있는가(구역별 행 옆의 합계·총액)."""
    for w in b.notice.window(ln.i, NEIGHBOUR, NEIGHBOUR):
        if w.i == ln.i:
            continue
        for v, _, _ in parse.money_spans(w.text):
            if v >= 1e5 and any(abs(v / r - 1) <= 0.05 for r in refs):
                return True
    return False


def base_zone(b, items):
    """fable_v24 S1: 기초금액을 최근접 등록 금액(B, 1.1P, P, B/1.1)과 비교. 5% 안의 진술이 하나라도 있으면 침묵, 0.5–0.95배 또는 1.05–2배면 발화."""
    P, B = b.meta.P, b.meta.B
    refs = [r for r in (B, P and P * 1.1, P, B and B / 1.1) if r]
    if not refs:
        return None
    rel = []
    for ln, field, label, v in stated(b, items, ('기초금액',)):
        if not base_labelled(b, ln) or neighbour_agrees(b, ln, refs):
            continue
        near = min(refs, key=lambda r: abs(v / r - 1))
        rel.append((ln, v, v / near))
    if not rel or any(abs(r - 1) <= 0.05 for _, _, r in rel) or len({round(v) for _, v, _ in rel}) >= 2:
        return None                                   # 서로 다른 기초금액 여러 개는 구역별 금액
    for ln, v, r in rel:
        if 0.5 <= r < 0.95 or 1.05 < r <= 2:
            return ln
    return None


def licence(b, items):
    """줄(절)에 적힌 업종코드 전부와 등록 코드의 교집합이 비면 발화. 한 줄의 대안 코드 중 하나가 등록돼 있으면 일치다(기존 v24_license와 같다)."""
    if b.meta.license_flag != 'Y' or not b.meta.license:
        return None
    meta_codes = set(re.findall(r'\((\d{4})\)', str(b.meta.license)))
    if not meta_codes:
        return None
    for ln, label, value in items.get('업종', ()):
        t = J.clause_text(b.notice, ln)
        if J.LICENSE_GUIDE.search(t) or J.LICENSE_ALT.search(t) or J.JV_PARTNER3.search(t) or J.PARTNER_WORK.search(t):
            continue
        if switches.V24P_GUARDS and J.alternative_item(b.notice, ln):
            continue                                  # PX 가드: "다음 중 어느 하나" 목록의 대안 자격
        codes = parse.codes(value)
        if not codes:
            continue
        codes |= parse.codes(t)
        if not (codes & meta_codes):
            return ln
    return None


def region(b, items):
    if b.meta.region_flag != 'Y' or not b.meta.region_sido:
        return None
    meta = set(b.meta.region_sido)
    for ln, label, value in items.get('지역제한', ()):
        t = J.clause_text(b.notice, ln)
        if J.JV_PARTNER3.search(t) or J.PARTNER_WORK.search(t) or ADDRESS.search(ln.text) or not RESTRICT.search(t):
            continue
        sido = regions.mentions(value)['sido']
        if sido and sido - meta:                      # 공고문이 등록되지 않은 시·도를 허용한다
            return ln
    return None


def method_band_ok(value, band, side):
    if side in ('이하', '초과'):
        edge = dict(J.BANDS).get(band)
        if edge is None or value is None:
            return None
        return value <= edge if side == '이하' else value > edge
    return J.band_ok(value, band, side)


def method(b, items):
    """제목 괄호 표기(방법·금액대)와 머리 12줄의 방법 표기만 본다(fable_v24 S3: 자연 노출 0). 본문 난 표기는 기존 축이 맡는다.
    수의계약 표기와 소액수의견적·견적 문구(기존 v24_method의 예외, dev DEV-144·191)는 침묵한다."""
    m = b.meta.method
    if not m:
        return None
    lines = b.notice.notice_lines()
    head = {ln.i for ln in lines[:HEAD_METHOD_LINES]}
    tagged_agree = any(t.group(1) == m for ln in lines[:TAG_LINES] for t in J.TAG.finditer(ln.text))
    quotation = m == '수의계약' and (b.meta.award == '소액수의견적' or any(J.QUOTATION.search(ln.text) for ln in lines[:120]))
    for ln, label, value in items.get('계약방법', ()):
        tag = TAG_LOOSE.search(value)
        if tag:
            said, band, side = tag.group(1), re.sub(r'\s', '', tag.group(2)), tag.group(3)
            if said != m and not quotation:
                return ln
            oks = [method_band_ok(v, band, side) for v in (b.meta.P, b.meta.B) if v is not None]
            oks = [o for o in oks if o is not None]
            if oks and not any(oks):
                return ln
            continue
        if ln.i not in head or tagged_agree or quotation:
            continue
        said = J.stated_methods(value)
        if len(said) != 1 or m in said or '수의계약' in said:
            continue
        return ln
    return None


AXES = (transposed, amount, base_zone, licence, region, method)


def explain(b):
    """(축 이름, 발화 줄) 또는 (None, None)."""
    items = getattr(b, 'v24p', None)
    if not items:
        return None, None
    for axis in AXES:
        ln = axis(b, items)
        if ln is not None:
            return axis.__name__, ln
    return None, None


def verdict(b):
    return explain(b)[1]
