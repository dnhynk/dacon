"""v24p 값 파싱: 모델이 옮긴 문자열에서 금액(원)과 4자리 업종코드를 읽는다."""
import re
import unicodedata

from .. import amounts

WS = re.compile(r'\s+')
REGION_TOKEN = re.compile(r'\[(?:등록)?지역:[^\]]*\]')     # 자료의 지역 표식 [지역:r1|단위=기초|광역=경기도]
REGION_NAME = re.compile(r'광역=([^\]|]*)')
DIGIT_AMOUNT = re.compile(r'(?<![\d.])(\d{1,3}(?:,\d{3})+|\d{5,})(?![\d,])')
# 업종코드는 '업종코드: 5210'처럼 이름표가 붙거나 '…업(1261)', '…용역(1169)', '…업(세부 명칭)(1227)'처럼 업종 명칭 뒤에 온다. 내선번호 '병리과(1851)',
# 연도 '기준(2026)' 같은 괄호 숫자는 코드가 아니다.
CODE = re.compile(r'(?:업\s*종\s*(?:코\s*드|번\s*호)\s*[:：]?\s*(\d{4})(?!\d))'
                  r'|(?:업|용\s*역|사\s*업\s*자?)(?:\s*[\(（][^()（）]{0,40}[\)）])?\s*[\(\[【]\s*(\d{4})\s*[\)\]】]')
BARE_CODE = re.compile(r'^\s*(\d{4})\s*$')


def norm(s):
    """공백을 없앤 NFC 문자열(검증 게이트와 부분문자열 비교용)."""
    return WS.sub('', unicodedata.normalize('NFC', s or ''))


def norm_gate(s):
    """검증 게이트용: 지역 표식을 그 광역 이름으로 바꾼 뒤(미상이면 지움) 공백을 없앤 NFC 문자열. 모델이 표식 대신 지역명을 옮겨도 대조된다."""
    def name(m):
        g = REGION_NAME.search(m.group(0))
        return '' if not g or g.group(1) == '미상' else g.group(1)
    return norm(REGION_TOKEN.sub(name, s or ''))


NUMBER_CHARS = frozenset('0123456789일이삼사오육칠팔구십백천만억')


def number_char(char):
    return char in NUMBER_CHARS or char.isdecimal()


def copied_value_in(source, value):
    """An exact normalized copy may not start or end inside a number.

    A substring alone would ground 50,000,000 in 150,000,000, or 5억 in
    3.5억. Punctuation is numeric only when it connects to another digit.
    """
    source, value = norm_gate(source), norm_gate(value)
    if not value:
        return False
    start = source.find(value)
    while start >= 0:
        end = start + len(value)
        before = source[start - 1] if start else ''
        after = source[end:end + 1]
        left = (number_char(value[0]) and
                (number_char(before) or before in ',，.' and start >= 2 and source[start - 2].isdecimal()))
        right = (number_char(value[-1]) and
                 (number_char(after) or after in (',', '，', '.') and source[end + 1:end + 2].isdecimal()))
        if not left and not right:
            return True
        start = source.find(value, start + 1)
    return False


def money_spans(value):
    """[(금액, 시작, 끝)] — money()와 같은 값에 옮겨진 문자열 안의 위치를 붙인 것."""
    s = (value or '').replace('，', ',')
    # Parse scaled amounts first. Their digit prefixes are not separate won
    # amounts (170,000천원 is 170,000,000, not also 170,000).
    compound = amounts.compound_money(s)
    out = [(mo.value, mo.start, mo.end) for mo in compound if mo.value >= 1e5]
    for m in DIGIT_AMOUNT.finditer(s):
        if any(m.start(1) < mo.end and mo.start < m.end(1) for mo in compound):
            continue
        out.append((float(m.group(1).replace(',', '')), m.start(1), m.end(1)))
    return sorted(out, key=lambda item: item[1])


def money(value):
    """옮겨진 값에 적힌 금액(원) 목록: 쉼표 숫자(39,730,000 / ₩170,000,000 / \\300,000,000원)와 한글 복합 숫자
    (금일억칠천만원, 3.5억원, 2천5백만원, 170백만원, 일금삼천칠백구십삼만원정)."""
    out = []
    for v, _, _ in money_spans(value):
        if not any(abs(v - old) <= 1.5 for old in out):
            out.append(v)
    return out


def codes(value):
    """옮겨진 값에 적힌 4자리 업종코드 집합: '(1169)', '[5210]', '업종코드: 5246', 코드만 적힌 값."""
    s = value or ''
    found = {x for g in CODE.findall(s) for x in g if x}
    m = BARE_CODE.match(s)
    if m:
        found.add(m.group(1))
    return found


def permutes(v, ref):
    """v의 자릿수가 ref의 자릿수를 다르게 늘어놓은 것인가(전치 오타)."""
    d, r = str(int(round(v))), str(int(round(ref)))
    return len(d) == len(r) and d != r and sorted(d) == sorted(r)
