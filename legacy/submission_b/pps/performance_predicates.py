"""Additional affirmative predicates for CPU consumption, never prompt facts."""
import re


def evaluative_role(n):
    """Explicit score captions and experience forms, without bidder duties."""
    if len(n) > 95 or re.search(r'참가|하여야|해야|갖춘|보유한|소지한', n):
        return None
    if re.search(r'\(\d+(?:\.\d+)?점\)$', n):
        return 'scoring'
    if re.fullmatch(r'[가-힣a-z0-9·ㆍ.()\[\]<>:：\-]{0,70}'
                    r'(?:실적표|실적총괄표)(?:양식)?', n):
        return 'forms'
    return None


def chronological_experience_form(n):
    return bool(re.search(r'실적.{0,100}연도순(?:서)?으로기재', n)
                and not re.search(r'참가|(?:보유한|있는|갖춘)(?:업체|자)', n))


def mandatory(n):
    if re.search(r'예시|가정|참고용|삭제|철회|권장|배점|가점|평가자료', n):
        return False
    return bool(re.search(
        r'(?:실적|경험).{0,100}(?:함께갖추어야|모두충족해야|보유를모두충족해야)|'
        r'(?:추가)?참가조건으로.{0,80}실적.{0,30}요구하며|'
        r'(?:등록하려는|입찰에참가하는)업체는.{0,100}완료한실적도제출하여야|'
        r'최근\d+년.{0,100}\d+건이상마친업체만입찰에참여할수있', n))


def completed_experience(n):
    return bool(re.search(r'최근\d+년.{0,100}\d+건이상마친업체|'
        r'(?:수행|이행|납품|완료)(?:한|했던|해본)경험(?:이있는|을보유한)'
        r'(?:업체|사업자|기관|단체|자)', n))


def eligibility_permission(n):
    """Admission without experience is distinct from a certificate waiver."""
    if re.search(r'예시|가정|참고용|삭제|철회', n):
        return False
    return bool(re.search(
        r'(?:실적|경험)이?없는(?:업체|사업자|자)의?(?:입찰|견적)'
        r'(?:참가|등록|제출)(?:도|을|를)?(?:허용(?:합니다|한다|함|된다)|가능(?:합니다|하다|함))|'
        r'(?:실적|경험)(?!증명서|확인서|표)[^.;。]{0,120}'
        r'(?:입찰)?참가자격(?:요건)?(?:으로는|으로|은|는)삼지않(?:습니다|는다|음)', n))


def noun_alternative(n):
    """One holding predicate governs both entity nouns, not two qualifications."""
    return bool(re.search(r'보유한업체또는단체(?:\(현재수행중인용역은제외\))?[.。]?$', n))


def office_clause(n):
    return bool(re.search(r'본점|본사|주된영업소|주된사무소', n)
                and re.search(r'소재|둔|두고', n))


def eligibility_heading(n):
    if len(n) >= 150:
        return False
    # Complete captions and their all-conditions governors only; mentions in
    # registration/sanction sentences must not open an eligibility section.
    caption = re.fullmatch(
        r'(?:\d+(?:[-.]\d+)*[.)]|[○●□■◆◇❍◦◐•])?'
        r'(?:입찰참가자격|참가자격|응찰자격|제안\(입찰참가\)자격)[:：]?'
        r'(?:(?:아래|다음)의?(?:자격|조건|요건)을모두(?:충족한|갖춘)'
        r'(?:자|업체|사업자)(?:이어야(?:합니다|한다|함))?[.]?)?', n)
    return bool(caption or re.fullmatch(
        r'\d+[.)]입찰참가자격[:：](?:아래|다음)의(?:입찰참가자격|자격요건)을모두'
        r'갖춘(?:자|업체)(?:이어야합니다)?[.]?', n))


def additional_region(n):
    return re.search(r'세종특별자치시', n)


def region_obligation(n):
    return re.search(r'모두충족해야|함께갖추어야|함께충족해야', n)


def office_anchor(n):
    """The registered-office anchor of 영 제21조①6호 / 제20조①6호.

    Its parenthetical names the sole proprietor's 사업장의 소재지; the shared
    region vocabulary also covers 영업장 소재지 and the registration address.
    """
    from .regions import OFFICE
    return OFFICE.search(n) or re.search(r'사업장(?:의)?소재지', n)


def office_location_duty(n):
    """A bidder-location predicate written as a duty or as an assignment."""
    return re.search(r'(?:소재|위치)(?:하여야|해야)|두어야|있어야|'
                     r'[시도‘’“”\'"」｣)]인(?:자|업체|사업자)(?:$|[.,(이]|로서)', n)


def location_predicate(n):
    return re.search(r'(?:소재|위치)(?:하여야|해야|한|하고있는|해있는)|두어야|둔|두고', n)
