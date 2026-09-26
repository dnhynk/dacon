"""v24p 발췌: 공고문에서 예산·추정가격·기초금액·계약방법·지역제한·업종이 적힌 줄과, 이름표만 있는 줄 아래의 값 줄(세로 표)."""
import re

# 예산 축이 비교하는 금액의 이름표(배정예산·추정가격과 공고문이 그 값에 붙이는 이름들)와 기초금액.
AMOUNT_LABEL = re.compile(r'예\s*산|추\s*정\s*(?:가\s*격|금\s*액)|기\s*초\s*금\s*액|(?:총\s*)?사\s*업\s*(?:비|금\s*액)|용\s*역\s*(?:비|금\s*액)'
                          r'|(?:구\s*매|용\s*역|사\s*업|공\s*사|구\s*입)\s*예\s*정\s*금\s*액')
DIGIT_AMOUNT = re.compile(r'\d{1,3}(?:,\d{3}){2,}|(?<![\d.])\d{7,}(?![\d.-])')                 # 백만원 이상으로 적힌 숫자
KOREAN_AMOUNT = re.compile(r'(?:(?<![가-힣])|(?<=금))[일이삼사오육칠팔구십백천만억\d.,\s]{1,14}?(?:억|천\s*만|백\s*만|만)\s*원')
UNIT_NOTE = re.compile(r'단\s*위\s*[:：]?\s*(?:천\s*원|백\s*만\s*원|만\s*원)')
METHOD_CUE = re.compile(r'계\s*약\s*방\s*(?:법|식)|입\s*찰\s*방\s*(?:법|식)|(?:일\s*반|제\s*한|지\s*명)\s*경\s*쟁|수\s*의\s*계\s*약')
REGION_CUE = re.compile(r'지\s*역\s*제\s*한|소\s*재\s*지|본\s*점|주\s*된\s*(?:영\s*업\s*소|사\s*무\s*소)|\[(?:등록)?지역:')
LICENSE_CUE = re.compile(r'업\s*종|면\s*허|[\(\[【]\s*\d{4}\s*[\)\]】]')
# 이름표 줄에 값이 함께 있는지(없으면 다음 줄이 값 줄이다).
VALUE_PRESENT = re.compile(r'\d{1,3}(?:,\d{3})+|\d{5,}|[일이삼사오육칠팔구십백천만억]{2,}\s*원|(?:일\s*반|제\s*한|지\s*명)\s*경\s*쟁|수\s*의'
                           r'|[가-힣]{1,4}(?:특별|광역|자치)?(?:시|도)(?![가-힣])')
TITLE_LINES = 3
CAP = 60
HEAD = 80          # 계약방법 단서는 공고문 머리에서만 본다(제목 괄호, 계약방법 난).
ATTACH_CAP = 10
PRIORITY_AMOUNT, PRIORITY_LICENSE, PRIORITY_REGION, PRIORITY_METHOD = 0, 1, 2, 3


def priority(text, head):
    if AMOUNT_LABEL.search(text) or DIGIT_AMOUNT.search(text) or KOREAN_AMOUNT.search(text) or UNIT_NOTE.search(text):
        return PRIORITY_AMOUNT
    if LICENSE_CUE.search(text):
        return PRIORITY_LICENSE
    if REGION_CUE.search(text):
        return PRIORITY_REGION
    if head and METHOD_CUE.search(text):
        return PRIORITY_METHOD
    return None


def excerpt(notice, attach=False, cap=CAP):
    """공고문(여러 개면 전부)의 제목 3줄 + 단서 줄 + 값 없는 이름표 줄 아래의 첫 값 줄. cap을 넘으면 금액 > 업종 > 지역 > 계약방법 순으로 남긴다."""
    lines = notice.notice_lines()
    chosen = {}
    for ln in [x for x in lines if x.text.strip()][:TITLE_LINES]:
        chosen[ln.i] = -1
    for k, ln in enumerate(lines):
        t = ln.text
        if not t.strip():
            continue
        pr = priority(t, k < HEAD)
        if pr is None:
            continue
        chosen[ln.i] = min(chosen.get(ln.i, 9), pr)
        if not VALUE_PRESENT.search(t):
            for nxt in lines[k + 1:k + 4]:
                if nxt.text.strip():
                    if nxt.doc == ln.doc:
                        chosen[nxt.i] = min(chosen.get(nxt.i, 9), pr)
                    break
    if attach:
        n = 0
        for ln in notice.lines:
            if ln.doc_type == '공고문' or not ln.text.strip() or n >= ATTACH_CAP:
                continue
            if AMOUNT_LABEL.search(ln.text) and (DIGIT_AMOUNT.search(ln.text) or KOREAN_AMOUNT.search(ln.text)):
                chosen[ln.i] = min(chosen.get(ln.i, 9), PRIORITY_AMOUNT)
                n += 1
    if len(chosen) > cap:
        keep = sorted(chosen, key=lambda i: (chosen[i], i))[:cap]
        chosen = {i: chosen[i] for i in keep}
    return [notice.lines[i] for i in sorted(chosen)]
