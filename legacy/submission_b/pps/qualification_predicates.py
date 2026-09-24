"""Consumer-only bidder predicates; document roles and source offsets survive."""
import re
from . import sme

NONASSERTED = re.compile(r'예시|참고용|가정|(?<!확)인용|삭제|철회|권장|평가자료|가점|배점')


def size_permission(entry):
    n = sme.norm(entry['evidence']['text'])
    if entry['section_role'] != 'eligibility' or NONASSERTED.search(n):
        return False
    # Document exemption alone cannot prove that all company sizes may bid.
    unrestricted = re.search(
        r'(?:기업|업체)(?:의)?규모(?:별|에따른)?(?:에|와)?(?:관계없이|무관)|'
        r'(?:기업|업체)(?:의)?규모(?:에따른)?제한(?:은|이)?없(?:다|고|으며|습니다|음)|'
        r'(?:기업|업체)(?:의)?규모.{0,35}(?:참가제한|참가구분)(?:은|을)?두지(?:않|아니)|'
        r'(?:기업|업체)의규모는.{0,35}참가심사기준으로사용하지않|'
        r'(?:기업|업체)(?:의)?규모(?:는|를|은)(?:입찰|참가)(?:자격)?'
        r'(?:판단|심사)에서제외(?:하|합)|'
        r'(?:기업|업체)(?:의)?규모를(?:추가)?(?:참가)?(?:자격)?요건으로정하지않|'
        r'참가범위를(?:기업|업체)(?:의)?규모로한정하지않|'
        r'참가업체를중소기업자로한정하지않|'
        r'중소기업(?:자)?(?:(?:가|이)?아닌|에해당하지않는)(?:업체|기업|자)도'
        r'[^.;。]{0,45}(?:입찰(?:에)?참가(?:할수있|가가능)|제안서를제출할수있)', n)
    return bool(unrestricted and not re.search(
        r'(?:다만|단[,，]).{0,100}(?:소기업|소상공인|중소기업).{0,40}(?:한정|소지|보유|한하)', n))


def direct_requirement(entry):
    n = sme.norm(entry['evidence']['text'])
    if (entry.get('certificate_table') or entry['status'] == 'mandatory_eligibility'
            and entry['direct_requirement']):
        return False  # Preserve the table's own subject/stage and existing duties.
    if (not entry['direct_production']
            or NONASSERTED.search(n) or re.search(r'낙찰후|계약체결후|계약상대자|면제|불필요|없어도|또는|혹은', n)):
        return False
    cert = sme.DIRECT.search(n)
    if not cert:
        return False
    tail = n[cert.end():]
    holding = re.search(r'(?:소지|보유)(?:하여야|해야|한)(?:하며|한다|합니다|업체|자)', tail)
    prebid = re.search(r'입찰등록전|입찰서제출마감일전일|견적(?:제출|참가)|입찰등록', n)
    excluded = re.search(r'(?:미보유|조회되지않는|인증만가진|증빙만갖춘).{0,60}'
                         r'(?:받지않|인정하지않|참가할수없|무효)', n)
    submission = re.search(r'(?:제출|첨부)하여야', tail)
    lookup = re.search(r'공공구매종합정보망에서조회되는자', tail)
    # Exclusive admission of pre-bid submitters is an operative certificate
    # duty. Keep the submission object bound to this certificate, rather than
    # borrowing another document's requirement later in the clause.
    exclusive_submitter = re.match(
        r'(?:을|를)?(?:입찰등록(?:때|시|전)|견적제출(?:전|시)|입찰서제출마감일전일)'
        r'(?:에|까지|전에)?(?:제출|첨부)한(?:업체만|자만|업체에한하여|자에한하여)'
        r'(?:입찰|견적|제안|참가)(?:할수있|가능)', tail)
    # Issuance to the applicant and exclusion of certificate nonholders are
    # also possession prerequisites. Bind the predicate immediately to this
    # certificate (allowing its own item qualifier), never a later document.
    certificate_tail = re.sub(r'^(?:\([^)]{0,180}\)|\[[^\]]{0,180}\])', '', tail)
    issued = re.match(r'(?:을|를)?발급받(?:은(?:업체|자)|아유효기간내에있어야)', certificate_tail)
    nonholder_exclusion = re.match(
        r'(?:이|가)?없는(?:업체|자)(?:는|은)(?:입찰|견적|제안서)?'
        r'(?:참가|접수)(?:대상)?에서제외(?:하|합)', certificate_tail)
    # Two predicates can share the same bidder: holding this certificate
    # and completing bid registration. Do not borrow another document's verb.
    registered_holder = re.match(
        r'(?:을|를)?(?:소지|보유)하고[,，]?'
        r'(?:(?!확인서|증명서|등록증|확약서|허가증|면허증).){0,160}'
        r'입찰참가등록한(?:업체|자)', certificate_tail)
    # Submission and exclusive admission refer back to the same certificate;
    # an ordinary submission list or another document's validity is not enough.
    valid_submitter = re.match(
        r'(?:은|는)(?:제안서|규격입찰서|입찰서|견적서)(?:와|과)함께'
        r'(?:제출|첨부)(?:하여야|해야)(?:하며|하고)[,，]?'
        r'(?:그|해당)(?:증명서|확인서)가유효한(?:업체|자)에한하여'
        r'(?:입찰)?참가를허용', certificate_tail)
    return bool(entry['section_role'] == 'eligibility' and holding
                or entry['section_role'] == 'eligibility' and exclusive_submitter
                or entry['section_role'] == 'eligibility' and (issued or nonholder_exclusion)
                or entry['section_role'] == 'eligibility' and (registered_holder or valid_submitter)
                or prebid and excluded and (submission or lookup or holding))


def commercial_exclusion(entry):
    """An explicit bidder exclusion survives a broader SME certificate label."""
    n = sme.norm(entry['evidence']['text'])
    standalone = (entry['evidence']['document_role'] == '공고문'
                  and re.search(r'(?:공급업체|참가업체|응찰자)(?:는|은)', n))
    if (entry['section_role'] != 'eligibility' and not standalone) or NONASSERTED.search(n):
        return None
    small = re.search(r'소기업.{0,25}소상공인', n)
    excluded = re.search(r'(?<!소)중기업.{0,45}(?:참가(?:대상에서)?제외|참가대상에서제외|'
                         r'(?:입찰에)?참가할수없|접수하지않|응찰할수없|신청할수없)', n)
    if not small or not excluded or re.search(r'중기업.{0,40}(?:제외하지않|참가할수있)', n):
        return None
    return {'commercial_upper_bound': ['micro', 'small'],
            'ordinary_medium_enterprises_excluded': True,
            'special_entity_alternatives': [], 'exact_allowed_set_certified': False,
            'evidence': entry['evidence']}


def size_requirement(entry):
    n = sme.norm(entry['evidence']['text'])
    if entry['section_role'] != 'eligibility' or not entry['size'] or NONASSERTED.search(n):
        return False
    entity = re.search(sme.CLASS + r'자?에해당하는업체만(?:신청|입찰|참가)할수있', n)
    bound = re.search(r'소기업또는소상공인\(.{0,200}입찰은무효.{0,30}\)이어야합니다', n)
    deemed = (entry['status'] == 'special_entity_branch'
        and re.search(r'(?:판로지원법|중소기업제품구매촉진및판로지원에관한법률)[」｣]?제33조제1항', n)
        and re.search(r'(?:중소기업자|소기업)(?:으로서|으로|로서|로)?(?:간주되는|보는)', n)
        and not re.search(r'벤처|창업|비영리|중견기업|대기업', n)
        and re.search(r'(?:소지|보유)한(?:업체|자)', n))
    return bool(entity or bound or deemed)


def deemed_special_inclusion(entry):
    """A deemed-SME special corporation named inside an ordinary size clause.

    ``소기업자(소상공인, 소기업 간주 특별법인 포함)`` or ``...확인서를 소지한 업체
    또는 ... 소기업으로 간주되는 특별법인`` adds 판로지원법 제33조 entities to the same
    eligible set. It admits no larger ordinary enterprise, so the clause's own
    commercial size bound stays operative. Other entity branches stay unresolved.
    """
    n = sme.norm(entry['evidence']['text'])
    # The ministry name (중소벤처기업부) is not a venture-enterprise branch.
    masked = re.sub(r'중소벤처기업부', '', sme.mask_laws(n))
    if (entry['section_role'] != 'eligibility' or entry['status'] != 'special_entity_branch'
            or not entry['size'] or entry.get('certificate_table')
            or NONASSERTED.search(n) or re.match(r'^(?:※|다만|단[,.:]|[-✓])', n) or not sme.END.search(n)
            or re.search(r'벤처|창업|비영리|대기업|중견기업|초기중견|협동조합|분담|구성원', masked)):
        return False
    included = re.search(r'\([^()]*(?:간주|보는)[^()]*(?:특별법인|법인또는단체)[^()]*포함\)', n)
    alternative = re.search(r'(?:소지|보유)한(?:업체|자)(?:또는|혹은)[,，]?.{0,80}'
                            r'(?:간주되는|보는)(?:특별법인|법인또는단체)', n)
    return bool(included or alternative)


def complete_direct_code(record, entry):
    """A ten-digit wrapped code cannot be a numbered qualification item."""
    if not entry['direct_production'] or entry['section_role'] != 'eligibility':
        return
    ev = entry['evidence']
    if not re.search(r'(?:세부품명번호|세부제품번호)[:：]$', sme.norm(ev['text'])):
        return
    text = record['docs'][ev['doc_index']]['text']
    continuation = re.match(r'\s*\d{10}\)\]\s*(?:를|을)\s*소지한\s*(?:업체|자)(?:이어야|여야)?\s*(?:합니다|한다)?[.]?', text[ev['end']:])
    if continuation:
        entry['evidence'] = sme.evidence(record, ev['doc_index'], ev['start'], ev['end']+continuation.end())
        entry['codes'] = sme.CODE.findall(entry['evidence']['text'])


def certificate_qualifier_openers(n, end):
    return [m.end() for m in sme.DIRECT.finditer(n[:end])
            if re.match(r'\[(?:세부품명|품명)[:：]', n[m.end():])]


def nonprofit_exception(exception):
    if exception['kind'] != 'priority_exception_reference' or exception['role'] != 'eligibility':
        return False
    n = sme.norm(exception['evidence']['text'])
    # Bind the statute and the exception's named beneficiary in the same note.
    return bool(re.fullmatch(r'※?(?:단[,，]?)?[｢「]?(?:중소기업제품구매촉진및판로지원에관한법률|판로지원법)'
        r'시행령[｣」]?제2조의3에따라[‘\']?비영리법인[’\']?의경우예외적용함[.]?', n))


def unrelated_exception(exception):
    """An article number in another expressly named statute is not this exception."""
    if exception['kind'] != 'priority_exception_reference':
        return False
    n = sme.norm(exception['evidence']['text'])
    statutes = re.findall(r'[「｢『]([^」｣』]+)[」｣』]제2조의3', n)
    return bool(statutes and all('법' in name and '판로지원' not in name for name in statutes)
                and not re.search(r'판로지원|우선조달|비영리', n))


def section_close(n):
    return bool(len(n) < 150 and (re.fullmatch(r'.{0,100}특수계약조건', n)
        or re.match(r'^제1조\((?:목적|총칙)\)', n)))
