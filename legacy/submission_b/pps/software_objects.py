"""Ground the target of a claimed SW action in its original clause.

A verb match and a model's ``software`` enum do not establish the verb's
object. Keep that distinction explicit. Named objects remain candidates for
the model's semantic classification; no brand dictionary or title gate is used.
This check is not certification of procurement scope or legal applicability.
"""
import re


_TYPED = re.compile(
    r'소프트웨어|S\s*/\s*W|\bSW\b|사용권|라이[선센]스|license|subscription|구독권|'
    r'실행\s*파일|응용\s*프로그램|전산\s*프로그램|정보\s*시스템|운영\s*체제|데이터베이스', re.I)
_PROGRAM = re.compile(r'프로그램')
_COMPUTING = re.compile(r'컴퓨터|전산|코딩|프로그래밍|실행\s*코드|알고리즘|애플리케이션|어플리케이션')
# These are grammatical heads, not a list of observed development-set products.
_DOCUMENT = re.compile(
    r'계획서?|방안|보고서|명세서|매뉴얼|안내서|설명서|계약서|확인서|증명서|확약서|'
    r'보증서|증명원|신고필증|요구\s*사항|요구서|목록|도면|도서|문서|자료|영상|'
    r'대본|교안|콘텐츠|실적|경력|경험|능력|역량')
_PHYSICAL = re.compile(
    r'장비|기기|부품|기계|설비|전기\s*공사|전기\s*설비|건축|토목|시설물|자재|소재|'
    r'물품|원자재|기구|도구|하드웨어|가구|식품|의류|차량')
_PARTICLE = re.compile(r'^\s*(?:을|를|은|는|이|가|와|과)(?:\s|$)')
_COORDINATE = re.compile(r'\s+(?:및|그리고|와|과)\s+|[,·+]\s*')
_NAMED = re.compile(r'(?<![\w/@.])(?:[A-Za-z][A-Za-z0-9_.+/-]{2,}|[“「『][^”」』\n]{2,50}[”」』])')
_GENERIC_NAMES = {'USB', 'AS', 'A/S', 'FW', 'F/W', 'PCB', 'SW', 'S/W', 'ISO', 'KCMVP', 'CC', 'ESG'}
_LEGAL_OR_CERTIFICATE = re.compile(r'법률|시행령|시행규칙|고시|인증|인증서|증명|등록증|확인서|실적')


def object_review(text, action_start, action_end, work):
    """Return observed target anchors and whether they can support this claim.

    Object nouns after a typed term ("SW manual") change its head. An explicit
    case particle or coordination ("SW and manual") preserves separate objects.
    Only this action's original clause is inspected; unrelated document-wide SW
    mentions and other witnesses cannot lend it a target.
    """
    start, end = work['start'], work['end']
    head = text[start:action_start]
    observed, rejected = [], []

    def anchor(match, kind, reason=None):
        value = {'start': start + match.start(), 'end': start + match.end(),
                 'quote': match[0], 'kind': kind}
        if reason:
            value['reason'] = reason
        return value

    def changed_head(match):
        rest = head[match.end():]
        # An object marker closes the noun phrase; a following manual is a
        # separate argument/adjunct rather than the head of "software".
        if _PARTICLE.match(rest) or _COORDINATE.match(rest):
            return False
        return bool(_DOCUMENT.search(rest) or _PHYSICAL.search(rest))

    for match in _TYPED.finditer(head):
        if changed_head(match):
            rejected.append(anchor(match, 'typed_word', 'typed_word_modifies_a_different_object_head'))
        else:
            observed.append(anchor(match, 'source_typed_object'))
    for match in _PROGRAM.finditer(head):
        if any(a['start'] <= start+match.start() < a['end'] for a in observed + rejected):
            continue
        if _COMPUTING.search(head) and not changed_head(match):
            observed.append(anchor(match, 'source_computing_program'))
        else:
            rejected.append(anchor(match, 'program_word', 'program_does_not_identify_computing_content'))
    if not observed:
        for match in _NAMED.finditer(head):
            name = match[0]
            if name[0] in '“「『' and not re.match(r'^\s*(?:을|를|와|과|및)(?:\s|$)', head[match.end():]):
                # Quoted parties, legislation and headings are not product
                # names merely because they have quotation marks.
                continue
            if (name.upper() in _GENERIC_NAMES or '://' in head or '@' in head
                    or _LEGAL_OR_CERTIFICATE.search(head)):
                continue
            if changed_head(match):
                rejected.append(anchor(match, 'named_object', 'name_modifies_a_different_object_head'))
            else:
                observed.append(anchor(match, 'named_object_model_type_unverified'))
    return {'start': start, 'end': end, 'quote': text[start:end],
            'target_anchors': observed, 'rejected_anchors': rejected,
            'can_support_model_object_claim': bool(observed),
            'object_type_certified_by_code': False,
            'issues': [] if observed else ['claimed_software_action_has_no_bound_software_target']}
