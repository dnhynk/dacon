"""Exact monetary syntax shared by fact extraction and decision modules.

This parser assigns no tax basis, project scope, applicability or legal threshold.
"""
import re
from decimal import Decimal, InvalidOperation

NUMBER = r'(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)'
_ARABIC_AMOUNT = NUMBER + r'(?:\s*[조억만천백십]\s*(?:' + NUMBER + r')?)*'
_KOREAN = r'[일이삼사오육칠팔구영공조억만천백십]'
_KOREAN_NUMBER = _KOREAN + r'(?:\s*' + _KOREAN + r')*'
_SPELLED_NUMBER = r'(?:일금|금)?\s*' + _KOREAN_NUMBER + r'\s*'
_WON_JEON = r'원\s*(?:' + _KOREAN_NUMBER + r'\s*전\s*)?'
_SPELLED = _SPELLED_NUMBER + r'(?:' + _WON_JEON + r')?(?:정\s*)?'
_SPELLED_WON = _SPELLED_NUMBER + _WON_JEON + r'(?:정\s*)?'
_TAX = (r'(?:부가(?:가치)?세|(?i:vat))'
        r'(?:\s*(?:및|[·ㆍ,])\s*(?:대행수수료|수수료|이윤|제경비|보험료|운송비|설치비))*'
        r'\s*(?:는\s*)?(?:미포함|불포함|별도|제외|포함)')
_NOTE_END = r'(?:\s*[,，/／]?\s*' + _TAX + r')?\s*'
_NOTE_BODY = r'\s*' + _SPELLED + _NOTE_END
_NOTE = r'(?:\(' + _NOTE_BODY + r'\)|（' + _NOTE_BODY + r'）)'
_NOTE_WON_BODY = r'\s*' + _SPELLED_WON + _NOTE_END
_NOTE_WON = r'(?:\(' + _NOTE_WON_BODY + r'\)|（' + _NOTE_WON_BODY + r'）)'
# Optional notes must not backtrack to a shorter, apparently valid numeric
# amount when the following monetary annotation is malformed or contradictory.
# Ordinary notes such as (이윤 포함) are not spelled-out monetary duplicates.
_NOTE_START = (r'[(（]\s*(?:일금|금)?\s*' + _KOREAN + r'(?:\s*' + _KOREAN + r')*'
               r'\s*(?:원|정|[,，)）]|$)')
_EXACT_END = r'정(?=$|[\s,，.;:：|)）(（])'
# Consume an observed exact suffix atomically: dropping it must not bypass the
# following damaged monetary duplicate (원정(팔천만원...).
_EXACT = r'(?:' + _EXACT_END + r')?(?!' + _EXACT_END + r')'
WON = re.compile(r'(?<![\d.,조억만천백십])' + _ARABIC_AMOUNT +
                 r'\s*(?:(?:' + _NOTE + r'\s*)?원' + _EXACT +
                 r'(?:\s*' + _NOTE + r')?|' + _NOTE_WON + r')(?!\s*' + _NOTE_START + r')')
# Field assignments can spell their sole amount in Korean. Keep the broader
# discovery scanners unchanged: this variant is used only after a field owner
# has been found and its value prefix validated.
_FIELD_SPELLED_BASE = _SPELLED_NUMBER + _WON_JEON + _EXACT
_REVERSE_NOTE_BODY = (r'\s*[￦₩]?\s*' + _ARABIC_AMOUNT + r'\s*(?:원)?' + _EXACT
                      + r'(?:\s*[,，/／]?\s*(?:부가(?:가치)?세|(?i:vat))[^()（）]{0,90})?\s*')
_REVERSE_NOTE = r'(?:\(' + _REVERSE_NOTE_BODY + r'\)|（' + _REVERSE_NOTE_BODY + r'）)'
_REVERSE_NOTE_START = r'[(（]\s*(?:[￦₩]\s*)?\d[\d.,조억만천백십 \t]*(?:원|[,，)）]|$)'
_FIELD_SPELLED = _FIELD_SPELLED_BASE + r'(?:\s*' + _REVERSE_NOTE + r')?(?!\s*' + _REVERSE_NOTE_START + r')'
FIELD_WON = re.compile(WON.pattern + r'|(?<![\d가-힣])' + _FIELD_SPELLED)
_UNITS = {'조': Decimal(10**12), '억': Decimal(10**8), '만': Decimal(10**4),
          '천': Decimal(1000), '백': Decimal(100), '십': Decimal(10)}
_DIGITS = dict(zip('일이삼사오육칠팔구', range(1, 10)))


def positive_number(value):
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip()
    if not re.fullmatch(NUMBER, text):
        return None
    try:
        number = Decimal(text.replace(',', ''))
        return number if number.is_finite() and number > 0 else None
    except InvalidOperation:
        return None


def _unit_value(tokens, *, spelled=False):
    """2천3백만 = (2*1000 + 3*100)*10000; descending units only."""
    total = group = Decimal(0)
    pending = None
    last_large, last_small = Decimal('Infinity'), Decimal('Infinity')
    for token in tokens:
        if token not in _UNITS:
            if pending is not None:
                return None
            pending = Decimal(_DIGITS[token]) if spelled and token in _DIGITS else positive_number(token)
            if pending is None:
                return None
            continue
        scale = _UNITS[token]
        if scale >= 10000:
            if scale >= last_large:
                return None
            coefficient = group + (pending if pending is not None else 0)
            if spelled and not coefficient:
                coefficient = Decimal(1)
            if coefficient <= 0:
                return None
            total += coefficient * scale
            group, pending, last_large, last_small = Decimal(0), None, scale, Decimal('Infinity')
        else:
            if spelled and pending is None:
                pending = Decimal(1)
            if pending is None or scale >= last_small:
                return None
            group += pending * scale
            pending, last_small = None, scale
    result = total + group + (pending if pending is not None else 0)
    return result if result > 0 else None


def _spelled_value(text):
    spelling = re.sub(r'\s+', '', text)
    spelling = re.sub(r'^(?:일금|금)', '', spelling).removesuffix('정')
    whole, _, fraction = spelling.partition('원')
    value = _unit_value(whole, spelled=True)
    if value is None:
        return None
    if fraction:
        jeon = _unit_value(fraction.removesuffix('전'), spelled=True)
        if jeon is None or jeon >= 100:
            return None
        value += jeon / 100
    return value


def won_value(text):
    """Read one exact literal; every numeric/spelled-out duplicate must agree.

    Validate before removing spaces: ``2 3원`` is not ``23원``. Qualifiers
    stay in the matched source for the field's tax/scope consumer to interpret.
    """
    value = str(text).strip()
    if not WON.fullmatch(value):
        if not re.fullmatch(_FIELD_SPELLED, value):
            return None
        result = _spelled_value(re.match(_FIELD_SPELLED_BASE, value)[0])
        for note in re.finditer(_REVERSE_NOTE, value):
            numeric = re.match(r'\s*[￦₩]?\s*(' + _ARABIC_AMOUNT + r')', note[0][1:-1])[1]
            if _unit_value(re.findall(NUMBER + r'|[조억만천백십]', numeric)) != result:
                return None
        return result
    number = re.match(_ARABIC_AMOUNT, value)
    result = _unit_value(re.findall(NUMBER + r'|[조억만천백십]', number[0]))
    if result is None:
        return None
    for note in re.finditer(_NOTE, value):
        spelling = re.match(_SPELLED, note[0][1:-1].strip())[0]
        spelled_value = _spelled_value(spelling)
        if spelled_value != result:
            return None
    return result
