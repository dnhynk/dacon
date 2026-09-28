"""Size-class wordings that name the small class through the SME word (switches U2_SIZE_SUBSET, U2_MEDIUM_BARRED,
U2_V13_SUBSET_TEXT, U2_WIDE_SUBSET).

판로지원법 시행령 제2조의2 ① limits a bid either to 중소기업자 or to 소기업·소상공인, the part of 중소기업자 that
중소기업기본법 제2조② and 소상공인기본법 제2조 define. A clause that names the SME class only to take its small part names
the small class:
- "중소기업(자) 중 / 중의 / 중에서(도) / 가운데(서) [「…」 제2조에 따른] 소기업·소상공인 …";
- "중소기업(자)(소기업·소상공인에 한함 / 에 한정 / 만 / 만 해당 / 으로 한정)";
- "소기업 또는 소상공인에 해당하는(인) 중소기업자", "중소기업자로서 소기업·소상공인에 해당하는 자 (인 자, 인 업체)".
A clause that bars 중기업 in words the older readers miss ("중기업의 (입찰) 참가를 제한", "중기업 확인서 소지자는 입찰에 참가할
수 없음", "중기업은 입찰참가자격이 없습니다", "중기업은 본 입찰의 참가대상이 아닙니다") leaves only 소기업·소상공인. "중소기업
중소기업" (a doubled word) and "(소기업·소상공인 포함)" name no part.
"""
import re

SME_WORD = r'중\s*[·ㆍ・‧․]?\s*소\s*기\s*업\s*(?:자|체)?'
SMALL_FIRST = r'(?:(?<![중·ㆍ・‧․•･/])소\s*기\s*업\s*자?|소\s*상\s*공\s*인)'
SMALL_NEXT = r'(?:소\s*기\s*업\s*자?|소\s*상\s*공\s*인)'
SMALL_LIST = SMALL_FIRST + r'(?:\s*(?:[·ㆍ・‧․,，/]|및|또\s*는|과|와|이\s*나)\s*' + SMALL_NEXT + r')*'
# A reference between the subset word and the class ("중 「…」 제2조에 따른 소상공인"); it may not name another class.
REFS = r'(?:(?:(?!중\s*[·ㆍ・‧․]?\s*소\s*기\s*업|중\s*기\s*업|대\s*기\s*업|중\s*견)[^.。]){0,40}?(?:에\s*따른|에\s*의한|에\s*규정된|상의?)\s*)?'
SUBSET = re.compile(SME_WORD + r'\s*(?:중(?:\s*에\s*서(?:\s*도)?|\s*의)?(?=\s)|가\s*운\s*데(?:\s*에\s*서|\s*서)?(?:\s*도)?)(?=\s*' + REFS + SMALL_FIRST + r')')
PAREN = re.compile(SME_WORD + r'\s*[\(（]\s*(?P<cls>' + SMALL_LIST + r')\s*'
                   r'(?:(?:에|으\s*로|로)\s*(?:한\s*(?:함|정|하\s*여|한\s*다|해)|해\s*당)|만(?:\s*해\s*당)?)[^)）]{0,8}[\)）]')
QUALIFIED = re.compile(r'(?P<cls>' + SMALL_LIST + r')\s*(?:에\s*해\s*당\s*하\s*는|인)\s*' + SME_WORD)
AS_SME = re.compile(SME_WORD + r'\s*(?:으\s*로\s*서|로\s*서)\s*[,，]?\s*(?=' + SMALL_LIST
                    + r'\s*(?:에\s*해\s*당|인\s*(?:자|업\s*체|기\s*업)(?![가-힣])))')


def subset_small(t):
    """The clause with the SME word removed where it only names the SME class to take its small part."""
    t = SUBSET.sub(' ', t)
    t = PAREN.sub(lambda m: ' ' + m.group('cls') + ' ', t)
    t = QUALIFIED.sub(lambda m: ' ' + m.group('cls') + ' ', t)
    return AS_SME.sub(' ', t)


MEDIUM = r'(?<![가-힣])중\s*기\s*업'
BARRED = re.compile(
    MEDIUM + r'\s*(?:은|는|의)?\s*(?:본\s*)?(?:입\s*찰\s*(?:에\s*|의\s*)?)?(?:참\s*가|참\s*여|응\s*찰)\s*(?:를|을)?\s*(?:제\s*한|배\s*제|금\s*지|불\s*허)'
    r'|' + MEDIUM + r'\s*(?:확\s*인\s*서\s*)?(?:를\s*|을\s*)?(?:소\s*지|보\s*유)\s*(?:한\s*)?(?:자|업\s*체|기\s*업)\s*(?:는|은)\s*(?:본\s*)?'
    r'(?:입\s*찰\s*(?:에\s*)?)?(?:참\s*가|참\s*여|응\s*찰)\s*(?:할\s*수\s*없|불\s*가|불\s*허)'
    r'|' + MEDIUM + r'\s*(?:은|는)\s*(?:본\s*)?(?:입\s*찰\s*(?:의|에\s*)?\s*)?(?:입\s*찰\s*)?(?:참\s*가|참\s*여)\s*(?:자\s*격|대\s*상)\s*(?:이|에)?\s*'
    r'(?:없|아\s*[니닙]|해\s*당\s*(?:하\s*지\s*않|되\s*지\s*않|없))')
# "중기업의 참가를 제한하지 않습니다", "…제한 없음", "…제한하는 경우" bar nobody.
BARRED_NOT = re.compile(r'\s*(?:하\s*지\s*(?:않|아\s*니)|되\s*지\s*않|없|은\s*없|이\s*없|하\s*는\s*경\s*우|할\s*경\s*우|시)')


def medium_barred(t):
    return any(not BARRED_NOT.match(t, m.end()) for m in BARRED.finditer(t))


# A clause naming the small part of the SME class that states who may bid ("…인 자", "…에 해당하는 업체", "…만을 대상으로",
# "…과의 우선조달계약 대상", "…으로서") is that class's participation clause.
SUBSET_WHO = re.compile(r'인\s*(?:자|업\s*체|기\s*업|법\s*인)(?![가-힣])|에\s*해\s*당\s*하\s*는\s*(?:자|업\s*체|기\s*업|법\s*인)(?![가-힣])'
                        r'|만\s*(?:을\s*)?(?:대\s*상|참\s*가|참\s*여|입\s*찰)|우\s*선\s*조\s*달\s*(?:계\s*약\s*)?(?:의\s*)?대\s*상|(?:으\s*로|로)\s*서')


def subset_core(t):
    """subset_small, with law and agency names removed as the shared reading does, when the clause names the small part of
    the SME class ("중소기업기본법상 중소기업 중 소기업 …")."""
    s = subset_small(t)
    if s == t:
        return s
    from .families import SIZE_NAMES
    return SIZE_NAMES.sub(' ', s)


def subset_who(t):
    return subset_small(t) != t and bool(SUBSET_WHO.search(subset_small(t)))


def wide_limit(t):
    """A clause outside the qualification section that bars 중기업 or names the small part of the SME class as who may bid."""
    return medium_barred(t) or subset_who(t)
