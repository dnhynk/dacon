"""Conservative per-record SME facts; no IDs, labels, learned rules or I/O.

Pass a preloaded ProductFacts catalog helper. Original text offsets are kept.
Catalog candidate retrieval is reused, but weak candidates never set scope.
"""
from __future__ import annotations
from .legal_context import applicable_law
import re
from .products import normalized_map, ProductFacts

ITEMS=tuple(range(10,19))
FLOOR=100_000_000
NOTICE=230_000_000
CODE=re.compile(r'(?<!\d)\d{10}(?!\d)')
CLASS=r'(?:중소기업|중[·ㆍᆞ․‧・∙･.,/-]소기업|중기업|소기업|소상공인)'
SEP=r'(?:[·ㆍᆞ․‧・∙･.,\/()\-]|또는|및|혹은|와|과)*'
CERT=re.compile(CLASS+r'(?:자)?(?:'+SEP+CLASS+r'(?:자)?)*'+r'[)]?(?:확인서|확인증)')
SIZE_SIGNAL=re.compile(CLASS)
DIRECT=re.compile(r'직접생산(?:확인)?(?:증명|확인)?서|직접생산확인기준|직접생산하는')
ELIG=re.compile(r'참가자(?:의)?자격|참가자격|참여자격|입찰자격|응모자격|참가조건|제안자격')
_HOLDING = (r'(?:소지|보유)(?:한|하여|해야|(?:업체(?:\(자\))?|자)'
            r'(?=$|[.,]|이어야|여야|로서|에한(?:함|한다)))')
_CONNECTED_HOLDING = r'(?:소지|보유)하고.{0,160}(?:충족(?:하여야|해야|한)|갖추어야)'
_POSTPOSED_SIZE = (r'등록(?:을)?한자[,，]?\((' + CLASS + r'(?:자)?(?:' + SEP + CLASS +
                   r'(?:자)?)*)으로제한\)')
END=re.compile(_HOLDING + '|' + _CONNECTED_HOLDING + '|' + _POSTPOSED_SIZE +
    r'|갖춘|업체이어야|업체여야|자이어야|참가할수|참가가능')


def norm(s):return normalized_map(s)[0]


def list_marker(n):
    """Visible list grammar and ordinal, without guessing indentation."""
    m = re.match(r'^([가나다라마바사아자차카타파하])([.)])', n)
    if m:
        return ('korean'+m[2], '가나다라마바사아자차카타파하'.index(m[1]))
    for chars, family in [('①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳', 'circled'),
                          ('➀➁➂➃➄➅➆➇➈➉', 'circled'), ('㉠㉡㉢㉣㉤㉥㉦㉧㉨㉩㉪㉫㉬㉭', 'circled_korean')]:
        if n[:1] and n[0] in chars:
            return (family, chars.index(n[0]))
    m = re.match(r'^(\d{1,3})([.)])(?!\d)', n)
    return ('number'+m[2], int(m[1])) if m else None


def _restore_sibling_roles(lines, roles, recognize):
    """End a child role at a visible later sibling of its enclosing list.

    This restores only line roles. It does not invent a closed eligibility
    range or use a recovered positive clause to certify source completeness.
    """
    from .qualification_structure import explicit_other_title, path
    parent, parent_marker, members, restored = None, None, {}, None
    for i, line in enumerate(lines):
        n, marker = norm(line[0]), list_marker(line[0].strip())
        new = recognize(n)
        context = roles[i]
        if new == 'eligibility':
            if parent is None or marker:
                parent, parent_marker, members = context, marker, {}
            restored = None
            continue
        if parent is None:
            continue
        number = path(line[0])
        parent_number = path(parent['heading']['text']) if parent['heading'] else None
        descendant = bool(number and parent_number and len(number)>len(parent_number)
                          and number[:len(parent_number)] == parent_number)
        # Explicit peer document sections and later stages remain barriers.
        if re.match(r'^(?:붙임|첨부)[:：]', n) or (new and (explicit_other_title(n) or
                (marker and parent_marker and marker[0] == parent_marker[0]
                 and marker[1] > parent_marker[1] and not descendant))):
            parent, parent_marker, members, restored = None, None, {}, None
            continue
        sibling = marker and marker[0] in members and marker[1] > members[marker[0]]
        if (new == 'other' and descendant
                and re.search(r'(?:입찰)?참가자격등록$', n)):
            restored = parent
        elif sibling:
            restored = parent if new not in ('forms', 'scoring') else None
        elif new in ('forms', 'scoring'):
            restored = None
            if not marker and not descendant:
                parent, parent_marker, members = None, None, {}
                continue  # An unnumbered new role has no proven list parent.
        if (marker and (context['role'] == 'eligibility' or sibling or
                        (new in ('forms', 'scoring') and not members))
                and marker != parent_marker):
            members[marker[0]] = marker[1]
        if restored:
            roles[i] = {**restored, 'ancestors':list(restored['ancestors'])}


def small_enterprise_special_reference(text):
    """Resolve the statute namespace, not just the article number.

    Article 7-2 also occurs in negotiated-contract price evaluation rules.
    Likewise, an ordinary joint project is not an SME procurement exception.
    """
    n = norm(text)
    statute = (r'(?:중소기업제품구매촉진및판로지원에관한법률|판로지원법)'
               r'[」｣』]?(?:시행령[」｣』]?)?(?:제)?7조의2')
    return bool(re.search(statute, n) or re.search(
        r'(?:소기업|소상공인).{0,80}(?:공동사업|유찰)|'
        r'공동사업.{0,80}(?:소기업|소상공인)|'
        r'(?:중소기업|소기업).{0,80}자격.{0,35}3인이하', n))


def evidence(record,di,a,b):
    d=record['docs'][di]
    return {'doc_index':di,'doc_id':d.get('doc_id'),'document_role':d.get('type'),
            'start':a,'end':b,'text':d['text'][a:b]}


def mask_laws(n):
    # Same-length masking preserves positions in normalized strings.
    def mask(m):
        return ' '*len(m.group()) if re.search(r'법|규정|규칙|기준|지침|요령',m.group()) else m.group()
    n=re.sub(r'[「｢『][^」｣』]{1,180}[」｣』]',mask,n)
    for title in ['중소기업범위및확인에관한규정','중소기업공공구매종합정보망','중소기업제품공공구매종합정보망','중소기업기본법','소상공인기본법',
                  '중소기업제품구매촉진및판로지원에관한법률','중소기업협동조합법',
                  '소상공인보호및지원에관한법률']:
        n=n.replace(title,' '*len(title))
    # Article captions and a cooperative's name identify a rule/entity type,
    # not the size of ordinary bidders. Leave any actual certificate intact.
    n=re.sub(r'(?<=\()중소기업자간경쟁입찰참여제한(?:등)?(?=\))',
             lambda m:' '*len(m[0]),n)
    n=re.sub(r'중소기업(?=협동조합)',lambda m:' '*len(m[0]),n)
    return n


_HEADING_DECORATION = r'[|○●□■❍•·ㆍ※-]*'
_SECTION_PATH = r'\d{1,3}(?:[.-]\d{1,3}){0,4}'
_HEADING_PREFIX = (_HEADING_DECORATION + r'(?:' + _SECTION_PATH +
                   r'[.)]?|[가-하][.)]|[ivx]{1,8}[.)]?)?' + _HEADING_DECORATION)
_BID_METHOD = (r'(?:입찰서제출|입찰제출|견적서제출|견적제출자|견적제출|견적서|견적입찰|전자입찰|'
               r'용역입찰|물품입찰|제안서제출|용역업체|공급업체|제안업체|입찰|견적|공모|응모|제안|사업)')
_QUALIFICATION_TITLE = re.compile('^' + _HEADING_PREFIX + r'(?:계약방법및)?(?P<below>(?:아래|다음)의)?(?:' +
    _BID_METHOD + r'(?:[·ㆍ/]' + _BID_METHOD + r'|\(' + _BID_METHOD + r'\)|및)?)?(?:' + ELIG.pattern + r'|견적(?:서)?제출자격)(?:요건)?')
_QUALIFICATION_OBLIGATION = (r'(?:갖춘(?:자|업체|사업자)(?:(?:이어야|여야)(?:만)?(?:함|한다|합니다)|에한함)?|'
    r'(?:갖추어야|갖춰야)(?:만)?(?:함|한다|합니다)|갖출것|'
    r'(?:충족|만족)(?:(?:하는|한)(?:자|업체|사업자)(?:에한함)?|할것|(?:하여야|해야)(?:만)?(?:함|한다|합니다))?)')
_ALL_CONDITIONS = re.compile(r'(?:(?:다음|아래)(?:의)?(?:각(?:호|항)(?:의)?)?|각아래)(?:입찰참가)?(?:조건|요건|자격|사항|기준|호)?(?:을|를)?(?:동시에)?모두' +
                             _QUALIFICATION_OBLIGATION + r'(?:[/,]증빙서류(?:要|요|필요))?')
_ENUMERATED_CONDITIONS = re.compile(r'(?:하기|아래)(?:\d{1,2}\)[,]?){1,10}의자격사항을모두' + _QUALIFICATION_OBLIGATION)
_QUALIFICATION_REFERENCE = re.compile(r'(?:자세한사항은)?입찰공고(?:문|서)(?:에의함|참조)(?:\(나라장터g2b\))?')
_NUMBERED_SECTION = re.compile(r'^(?:' + _SECTION_PATH + r'[.)](?![\d.]|$)|[ivx]{1,8}[.)])')
_SCALAR_LINE = re.compile(r'^\d+(?:\.\d+)+(?:[.]?)(?:%|ghz|mhz|khz|hz|mm|cm|km|kg|억원|만원|원|이상|이하|미만|초과)')


def heading(n):
    if not n or len(n) >= 220:
        return None
    from .qualification_tables import certificate_header
    if certificate_header(n):
        return None  # Column captions do not erase a parent example/stage.
    # Interpretation only: a known HWP export prefix is not part of the
    # title. extract_inventory still records the entire original line.
    n = re.sub(r'^parashape="\d+"style="\d+">', '', n)
    if len(n) >= 150:
        return None
    if n.startswith('【') and n.endswith('】'):
        n = n[1:-1]
    n = re.sub(r'참가\((?:입찰서|견적서)제출\)자격', '참가자격', n)
    title = _QUALIFICATION_TITLE.match(n)
    if title:
        tail = n[title.end():].lstrip(':：').rstrip('.。')
        if re.search(r'예시|작성예|가정|참고용|적용하지|적용되지|요구하지|삭제|철회|필요.{0,5}없', tail):
            return 'other'  # Close any prior operative section before this example/withdrawal.
        # A separately marked note does not erase the title. Its complete
        # text remains available to the obligation/exception consumers. PDF
        # text may put that marker inside the heading's parentheses, so unwrap
        # both before and after removing the note.
        def unwrap(value):
            if value[:1] in ('(', '[', '<') and value.endswith(
                    {'(':')', '[':']', '<':'>'}[value[0]]):
                return value[1:-1].rstrip('.。')
            return value
        tail = unwrap(tail)
        tail = unwrap(tail.partition('※')[0].rstrip('.。'))
        if title['below']:
            if re.fullmatch(r'을모두' + _QUALIFICATION_OBLIGATION, tail):
                return 'eligibility'
        elif (tail in ('', '및조건', '조건', '요건', '및방법', '및계약방법', '및선정방법', '및등록', '및유의사항', '에관련공통사항',
                       '에관한사항', '에관한공통사항', '모두해당', '모두충족',
                       '일반경쟁입찰', '제한경쟁입찰', '지명경쟁입찰')
              or _ALL_CONDITIONS.fullmatch(tail) or _ENUMERATED_CONDITIONS.fullmatch(tail)
              or re.fullmatch(r'(?:동시에)?모두' + _QUALIFICATION_OBLIGATION, tail)
              or re.fullmatch(r'(?:해당|상기)?자격(?:은|을)?계약체결일까지유지(?:되어야|하여야|해야)함', tail)
              or re.fullmatch(r'(?:공동수급|공동계약)(?:불허|불가)', tail)
              or _QUALIFICATION_REFERENCE.fullmatch(tail)):
            return 'eligibility'
        else:
            # A statutory preamble can precede the actual "all following
            # conditions" governor on the same heading line.  Preserve the
            # whole line, but recognize the section when that governor ends it.
            all_conditions = _ALL_CONDITIONS.search(tail)
            if (all_conditions and all_conditions.end() == len(tail)
                    and re.search(r'자격을갖춘(?:자|업체)(?:로서)?[,，]?$',
                                  tail[:all_conditions.start()])):
                return 'eligibility'
    # Instruction sentences inside a note do not open a submission/scoring
    # section merely because they mention those words. Short captions remain.
    if n.startswith('※') and re.search(r'하여야|하시기|바랍니다|합니다|제출시|경우', n):
        return None
    # A mention in a sanction, registration sentence or verification note is
    # not a header and cannot create a closed section proving absence.
    form = re.search(r'제출서류|구비서류|제출목록|제안서작성|서식\d|붙임\d', n)
    if (len(n)<100 and form and not re.search(r'직접생산.{0,150}(?:소지한|보유한)', n[:form.start()])
            and not re.search(r'제출한(?:자|업체)[.]?$', n)):
        return 'forms'
    if len(n)<90 and re.search(r'배점|평가기준|평가항목|평가방법|정량평가',n) and not re.search(r'각\d+부|자료.{0,15}\d+부',n):return 'scoring'
    if re.fullmatch(_HEADING_PREFIX + r'(?:입찰서|견적서)제출안내', n):
        return 'other'
    from .qualification_structure import explicit_other_title
    if explicit_other_title(n):
        return 'other'
    if (len(n)<85 and _NUMBERED_SECTION.match(n) and not _SCALAR_LINE.match(n)
            and not END.search(n) and not direct_verification_requirement(n)
            and not re.search(r'입찰참가자(?:격)?등록규정.{0,12}(?:에의하여|에따라)', n)):
        return 'other'
    return None


def class_set(s):
    s=norm(s)
    if re.search(r'중소기업|중[·ㆍᆞ․‧・∙･.,/-]소기업',s):return {'medium','small','micro'}
    allowed=set()
    if '중기업' in s:allowed.add('medium')
    if '소기업' in s:allowed.update(('small','micro'))
    if '소상공인' in s:allowed.add('micro')
    return allowed


def size_facts(n):
    original=n
    n=mask_laws(n)
    limit = re.search(_POSTPOSED_SIZE, n)
    if limit:
        return {'allowed':sorted(class_set(limit[1])), 'basis':'postposed_bidder_limit',
                'connective':'single', 'certificate_phrases':[], 'commercial_only':True}
    certificates=list(CERT.finditer(n))
    # The final actual certificate specification can narrow a broad preamble.
    if certificates:
        sets=[class_set(m.group()) for m in certificates]
        union=bool(re.search(r'중하나|어느하나|확인서[,·ㆍ]*(?:또는|혹은)',n))
        # Parenthetical broad certificate aliases are not a second condition.
        primary=[(m,s) for m,s in zip(certificates,sets) if not (m.start()>0 and n[m.start()-1]=='(')]
        sets=[s for m,s in primary] or sets
        allowed=set().union(*sets) if union else set.intersection(*sets)
        # Both statutory entity wording and certificate scope constrain the
        # same applicant. A broad form name cannot relax an explicit small-
        # entity gate, nor can a broad preamble relax a narrow certificate.
        preamble_allowed=None
        if re.match(r'^(?:[가-하][.)]|[①-⑳]|\d+[-.)]|[「｢『])',original):
            prefix=n[:certificates[0].start()]
            if re.search(r'로서|으로서|에따른|에해당',prefix):
                preamble_sets=[class_set(m.group()) for m in SIZE_SIGNAL.finditer(prefix)]
                if preamble_sets:
                    preamble_allowed=set().union(*preamble_sets)
                    allowed &= preamble_allowed
        return {'allowed':sorted(allowed),'basis':'certificate','connective':'OR' if union else 'AND_or_single',
                'certificate_phrases':[m.group() for m in certificates],
                'eligible_entity_preamble':sorted(preamble_allowed) if preamble_allowed is not None else None,
                'commercial_only':True}
    # Bare legal/statutory wording is not a size restriction without a noun
    # phrase identifying the eligible business and an operative predicate.
    m=re.search('('+CLASS+r'(?:자)?)(?:로서|으로서|에해당|인업체|인자|간제한경쟁|만참가)',n)
    if m:return {'allowed':sorted(class_set(m[1])),'basis':'eligible_entity','connective':'single','certificate_phrases':[],'commercial_only':True}
    return None


def exception_kind(text):
    """A conditional price settlement does not grant bidder eligibility.

    Retain the source observation, and leave ambiguous permission or statutory
    references for review. The narrow nonoperative case requires both a bidder
    hypothetical and an explicit tax/profit deduction in the contract price.
    """
    n = norm(text)
    statutory = bool(re.search(r'제2조의3|우선조달.{0,15}(?:예외|제외|적용하지)', n))
    nonprofit = bool(re.search(r'비영리.{0,40}(?:참가|참여)', n))
    if not statutory and not nonprofit:
        return None
    if re.search(r'제2조의3.{0,25}해당되지않|비영리.{0,40}참가불가', n):
        return 'priority_exception_denied'
    permission = re.search(r'비영리.{0,100}(?:참가|참여)(?:할수있|가가능|가능|를허용)|'
                           r'비영리.{0,100}(?:확인서|자격).{0,40}(?:없어도|면제|불필요)|'
                           r'(?:참가|참여)자격.{0,20}(?:인정|부여)|참가대상.{0,30}비영리', n)
    hypothetical = re.search(r'비영리.{0,100}(?:투찰할경우|(?:낙찰자|계약상대자).{0,30}경우)|'
                             r'(?:낙찰자|계약상대자).{0,45}비영리.{0,40}경우', n)
    deduction = re.search(r'(?:이윤|부가가치세|부가세).{0,35}(?:제외|차감|공제).{0,45}(?:금액|계약)', n)
    if nonprofit and hypothetical and deduction and not statutory and not permission:
        return 'conditional_price_settlement'
    return 'nonprofit_alternative' if nonprofit else 'priority_exception_reference'


def requires_exception_review(observation):
    return observation['kind'] not in {'priority_exception_denied', 'conditional_price_settlement'}


def direct_verification_requirement(text):
    """An explicit database check with exclusion is a substantive obligation.

    A database mention alone is not possession. Keep this narrower relation
    separate from certificate-holding language and commodity-code assignment.
    """
    n = norm(text)
    subject = r'직접생산(?:여부|확인(?:증명)?서|증명서)?(?:의)?(?:확인)?(?:은|는|이|가|도)?'
    system = r'(?:중소기업(?:제품)?공공구매종합정보망|공공구매종합정보망)(?:\([^)]{1,100}\))?'
    prefix = subject + system + r'에서'
    affirmative = r'확인(?:이)?(?:가능하여야|가능해야|되어야|돼야)(?:하며|하고|한다|합니다)'
    denied = r'확인(?:이)?(?:되지않(?:을|는|은)|안(?:되|될))경우(?:에는|에)?(?:입찰|견적)(?:참가|제출)?자격(?:이|은)?없'
    # Both halves must share this direct-production subject. Another note or
    # certificate cannot supply a missing predicate.
    return bool(re.search(prefix + affirmative + r'[,.;。]?' + denied, n)
                or re.search(prefix + denied, n))


def extract_inventory(record, *, heading_fn=None):
    recognize = heading if heading_fn is None else heading_fn
    inventory=[];sections=[];quotes=[];exceptions=[];declarations=[]
    for di,d in enumerate(record['docs']):
        t=d['text'];ls=list(re.finditer(r'[^\r\n]+',t))
        from .qualification_tables import certificate_rows
        from .table_structure import pipe_separators
        table_rows = certificate_rows(t)
        from .qualification_structure import contexts
        roles, doc_sections = contexts(record, di, ls, recognize, norm, evidence)
        _restore_sibling_roles(ls, roles, recognize)
        sections.extend(doc_sections)
        for li,m in enumerate(ls):
            raw=m.group();n=norm(raw)
            role,head=roles[li]['role'],roles[li]['heading']
            ev=evidence(record,di,m.start(),m.end())
            if len(n)<250 and re.search(r'소액수의|수의계약.{0,15}(?:견적|안내)|견적제출안내공고|견적서제출안내공고',n) and not re.search(r'경우|법률|시행령|준용',n):quotes.append(ev)
            kind = exception_kind(n)
            if kind is not None:
                exceptions.append({'kind':kind,'evidence':ev,'role':role})
            if small_enterprise_special_reference(n):
                exceptions.append({'kind':'small_enterprise_special_case_reference','evidence':ev,'role':role})
            if CODE.search(n) and re.search(r'세부품명|품명번호|품목번호',n):
                purchase=bool(re.search(r'본입찰대상물품|본사업대상물품|구매대상물품',n))
                registration=bool(re.search(r'등록한|등록된|등록하여|등록을필|등록되어',n))
                direct=bool(DIRECT.search(n))
                if purchase or (registration and not direct) or (re.search(r'품명[:：|]',n) and role not in ('eligibility','forms') and not direct):
                    declarations.append({'codes':CODE.findall(n),'role':'explicit_purchase' if purchase else 'purchase_registration' if registration else 'purchase_field',
                                         'evidence':ev})
            signal=bool('직접생산' in n or SIZE_SIGNAL.search(n))
            if not signal:continue
            end=m.end()
            # Join immediately following wrapped wording only; headings stop it.
            if signal and not END.search(n) and len(n)<400 and not pipe_separators(raw):
                for nx in ls[li+1:li+5]:
                    nn=norm(nx.group())
                    if (pipe_separators(nx.group()) or recognize(nn)
                            or (re.match(r'^(?:※|다만|단[,.:]|[-✓])',nn)
                                and (CERT.search(mask_laws(n)) or DIRECT.search(n)))
                            or list_marker(nx.group().strip())
                            or re.match(r'^[ㅇ○〇●□■❍•]', nx.group().lstrip())
                            # A wrapped 10-digit item code is not a list number.
                            or re.match(r'^\d{1,3}(?:[-.]\d{1,3})*[.)]',nn)):break
                    if nx.end()-m.start()>900:break
                    end=nx.end();n=norm(t[m.start():end])
                    if END.search(n):break
            ev=evidence(record,di,m.start(),end);masked=mask_laws(n)
            direct='직접생산' in n;sz=size_facts(n)
            direct_required=bool(re.search(r'직접생산.{0,240}(?:'+_HOLDING+'|'+_CONNECTED_HOLDING+r'|업체이어야)',masked) or
                                 re.search(r'직접생산확인기준.{0,150}세부품명.{0,100}소지한',n))
            verified_requirement = direct_verification_requirement(n)
            direct_required |= verified_requirement
            is_certificate=bool(re.search(r'확인서|확인증|직접생산',n))
            operative=role=='eligibility' and bool(END.search(n))
            if (re.search(r'(?:소지|보유)(?:업체|자)', n)
                    and re.search(r'예시|작성예|참고용|가점|우대|권장', masked)):
                operative = False
            note=bool(re.match(r'^(?:※|다만|단[,.:]|[-✓])',n))
            # A mid-size firm listed among the excluded bidders is not an
            # alternative entity branch of the size condition.
            conditional=bool(re.search(r'특별법인|중소기업으로간주|중소기업자로간주|협동조합|초기중견',n)
                             or any(not re.match(r'[^.。]{0,25}?(?:불가|할수없|없습니다|제외|불허|허용하지|허용되지|하지못)',
                                                  n[m.end():]) for m in re.finditer(r'중견기업',n)))
            permission=bool(re.search(r'(?:확인서|직접생산).{0,60}(?:없어도|불필요|요구하지|제한하지|면제|무관)',n))
            withdrawn=bool(re.search(r'(?:규정|조건|요건|요구사항).{0,20}(?:삭제|철회)',n))
            conditional |= bool(re.search(r'분담.{0,50}(?:구성원|업체)|(?:구성원|업체).{0,50}분담',n))
            if withdrawn:status='incidental_or_unresolved'
            elif permission:status='explicit_permission'
            elif conditional:status='special_entity_branch'
            elif role=='forms':status='submission_or_form'
            elif role=='scoring':status='scoring'
            elif role=='eligibility' and verified_requirement:status='mandatory_eligibility'
            elif operative and not note:status='mandatory_eligibility'
            elif (operative and note and re.match(r'^[-✓]',n) and is_certificate
                  and re.search(r'소지한|보유한|소지하여|보유하여',masked)
                  and not re.search(r'경우|신청|다만|없어도|불필요|면제',n)):
                status='mandatory_eligibility'  # A list bullet can require an issued certificate.
            elif operative and note and not re.search(r'경우|신청|유효|발급된',n):status='mandatory_eligibility'
            elif note and is_certificate:status='verification_or_exception_note'
            else:status='incidental_or_unresolved'
            table = table_rows.get(m.start())
            if table:
                if (table['header']['kind'] == 'checklist' or table['header']['submission_caption']) and role in ('forms', 'eligibility', 'unknown'):
                    status = 'submission_or_form'
                elif table['status'] != 'applicant_scope' and status == 'mandatory_eligibility':
                    status = 'incidental_or_unresolved'
            inventory.append({'status':status,'section_role':role,'heading':head,'evidence':ev,
                **({'certificate_table':table} if table else {}),
                'heading_ancestors':roles[li]['ancestors'],
                'direct_production':direct,'direct_requirement':direct_required,'size':sz,'codes':CODE.findall(n),
                'direct_requirement_basis': ('mandatory_database_verification_with_exclusion' if verified_requirement
                                             else 'possession_wording' if direct_required else None),
                'other_entity_options':re.findall(r'비영리법인|벤처기업|창업기업|특별법인|협동조합|중견기업',masked),
                'alternative_size_branch_unresolved':bool(re.search(r'(?:또는|혹은)(?:벤처기업|창업기업)|(?:벤처기업|창업기업).{0,30}(?:중하나|어느하나|또는|혹은)',masked)),
                'validity':{'required_valid_period':bool(re.search(r'유효기간(?:내|이내)|유효한',n)),
                            'pre_bid_issue_wording':bool(re.search(r'마감.{0,12}전일까지.{0,12}(?:발급|신청)',n)),
                            'application_grace_wording':bool(re.search(r'신청한.{0,12}(?:업체|사항)|5일이내',n)),
                            'actual_bidder_certificate':'not_supplied_not_verified'},
                'nonprofit_alternative':bool(re.search(r'비영리.{0,35}(?:법인|참가|참여)',n))})
    # Wrapped candidates can overlap; retain the earliest complete span.
    result=[]
    for x in inventory:
        e=x['evidence']
        if any(y['evidence']['doc_index']==e['doc_index'] and y['evidence']['start']<=e['start'] and e['end']<=y['evidence']['end'] and y['status']==x['status'] for y in result):continue
        result.append(x)
    return result,sections,quotes,exceptions,declarations


def product_scope(record,pf,product,inventory,declarations,price):
    meta={x['code'] for x in product['meta_purchase_codes']}
    declared={code for d in declarations for code in d['codes']}
    supported=[]
    for d in declarations:
        for c in d['codes']:
            if d['role']=='explicit_purchase' or (meta and c in meta) or d['role']=='purchase_field':
                supported.append({'code':c,'evidence':d['evidence'],'identity_support':d['role']})
    # Exact catalog parent identity corroborates an actual certificate target;
    # never use arbitrary bigram rank as identity. Short parents need an exact
    # field/title occurrence, and all supplied notes remain binding.
    scopes=[product['sources'][s] for s in product['purchase_scope_sources']]
    mandatory=[x for x in inventory if x['status']=='mandatory_eligibility' and x['direct_requirement']]
    for x in mandatory:
        for c in x['codes']:
            row=pf.products.get(c)
            if not row:continue
            parent=norm(row['제품명']);detail=norm(row['세부품명'])
            for s in scopes:
                n=norm(s['text'])
                exact_parent_task=bool(len(parent)>=2 and re.search(re.escape(parent)+r'[』」〉>”"‘’:]*(?:행사)?(?:기획|대행)(?:용역|서비스)?',n))
                kind_agrees=(record.get('meta',{}).get('업무구분')=='일반용역' and row['대분류'].endswith('서비스'))
                if (len(detail)>=5 and detail in n) or (kind_agrees and exact_parent_task):
                    supported.append({'code':c,'evidence':s,'certificate_evidence':x['evidence'],
                                      'identity_support':'exact_catalog_name_or_parent_in_purchase_scope'})
                    break
    codes=sorted({s['code'] for s in supported})
    rows=[{'code':c,'listed':c in pf.products,
           'name':pf.products[c]['세부품명'] if c in pf.products else None,
           'note':pf.products[c]['특이사항'] if c in pf.products else None,
           'condition':ProductFacts.condition(pf.products[c]['특이사항'],price,
               record=record, product_name=pf.products[c]['세부품명']) if c in pf.products else {'status':'unlisted'}} for c in codes]
    status='unknown'
    if rows:
        states=[r['condition']['status'] for r in rows]
        if all(s in ('met','no_stated_condition') for s in states):status='competition'
        elif all(s=='not_met' for s in states):status='general_in_supplied_catalog'
        # Unlisted codes are retained as lookup facts, never closed-world
        # proof that the real purchased product is general. Names, aliases,
        # mixed lots, or a code-registration error can remain unresolved.
    conflicts=[]
    if meta and declared and not meta.issubset(declared):conflicts.append('metadata_purchase_codes_not_all_confirmed_by_body')
    if any(c not in meta for c in declared) and meta:conflicts.append('additional_body_purchase_codes')
    # Do not conclude a whole mixed contract is general or competition from a
    # subset of explicit metadata targets.
    if meta and not meta.issubset(set(codes)):status='unknown'
    if conflicts:status='unknown'
    return {'status':status,'supported_products':rows,'identity_evidence':supported,
            'declared_body_products':declarations,'meta_codes':sorted(meta),'uncertainty':conflicts,
            'weak_lexical_candidates_are_not_identity':True}


def extract_sme_facts(record,pf, *, product_override=None):
    product=pf.extract(record,top_k=3)
    inventory,sections,quotes,exceptions,declarations=extract_inventory(record)
    # The shared resolver preserves registration disagreement separately from
    # the notice value that governs the official applicability bands.
    p=product['price'];price=p['value_krw']
    scope=product_scope(record,pf,product,inventory,declarations,price)
    if product_override is not None:
        scope=product_override
    active=[x for x in inventory if x['status']=='mandatory_eligibility']
    sizes=[x for x in active if x['size']]
    direct=[x for x in active if x['direct_requirement']]
    from .production_certificate import coverage as certificate_coverage
    direct_coverage=certificate_coverage(record,direct)
    definite_direct=[e['evidence'] for e in direct_coverage['observations']
        if e['production_required_in_every_branch']]
    # Distinct mandatory commercial clauses combine by AND, while OR inside
    # one certificate clause is retained by size_facts.
    allowed=set.intersection(*(set(x['size']['allowed']) for x in sizes)) if sizes else None
    unresolved_size_branch=any(x['alternative_size_branch_unresolved'] for x in active)
    for x in sizes:
        e=x['evidence']
        context=norm(record['docs'][e['doc_index']]['text'][max(0,e['start']-700):e['start']])
        if re.search(r'(?:다음|아래|각호).{0,30}(?:어느하나|중하나)',context):
            unresolved_size_branch=True  # Cross-clause alternatives need a scoped parse.
    if unresolved_size_branch:allowed=None
    from .input_contract import provided_complete
    complete=provided_complete(record)
    recovered=any(s['closed'] and s['evidence']['document_role']=='공고문' for s in sections)
    unclosed=[s['evidence'] for s in sections if not s['closed']]
    # Absence needs full-record scan, completed input, a closed eligibility
    # section and no unresolved lexical candidate for the relevant obligation.
    direct_ambiguous=[x for x in inventory if x['direct_production'] and x['status'] not in ('scoring','incidental_or_unresolved')]
    size_ambiguous=[x for x in inventory if x['size'] and x['status'] not in ('scoring',)]
    no_direct=complete and recovered and not unclosed and not any(x['direct_production'] for x in inventory)
    raw_size_uncertain=[x for x in inventory if SIZE_SIGNAL.search(mask_laws(norm(x['evidence']['text']))) and x['status'] not in ('scoring',)]
    no_size=complete and recovered and not unclosed and not raw_size_uncertain and not sizes
    commercial_exceptions=[x for x in exceptions if x['role']=='eligibility' and requires_exception_review(x)]
    meta_exception=record.get('meta',{}).get('조항호내용')
    meta_exception_relevant=bool(re.search(r'제2조의3|비영리|우선조달.{0,10}예외',str(meta_exception)))
    exception_uncertain=bool(commercial_exceptions or meta_exception_relevant)
    meta_small_special=small_enterprise_special_reference(norm(str(meta_exception)))
    quote_uncertain=bool(quotes) or record.get('meta',{}).get('계약방법')=='수의계약'
    law=applicable_law(record)
    ordinary=law in ('국가계약법','지방계약법') and record.get('meta',{}).get('업무구분') in ('일반용역','물품(내자)')
    decisions={f'v{i}':{'value':None,'reason':'insufficient_semantic_proof','evidence':[]} for i in ITEMS}
    def put(i,value,reason,evs=()):decisions[f'v{i}']={'value':value,'reason':reason,'evidence':list(evs)}
    if ordinary:
        # Necessary price predicates yield negatives independently of product
        # identity. These are not inferred from empty snippets.
        if price is not None:
            if price<NOTICE:put(14,0,'outside_v14_price_band')
            if not FLOOR<=price<NOTICE:
                put(15,0,'outside_v15_price_band');put(16,0,'outside_v16_price_band')
            if price>=FLOOR:
                put(17,0,'outside_v17_price_band');put(18,0,'outside_v18_price_band')
        es=[x['evidence'] for x in sizes]
        if allowed:
            put(11,0,'operative_size_qualification_present',es)
            put(16,0,'operative_size_qualification_present',es)
            put(18,0,'operative_size_qualification_present_not_absence',es)
            if 'medium' in allowed:put(13,0,'medium_enterprise_explicitly_permitted',es);put(15,0,'medium_enterprise_explicitly_permitted',es)
            else:put(17,0,'small_or_micro_only_not_broad_sme_restriction',es)
        known=scope['status'];identity=[s['evidence'] for s in scope['identity_evidence']]
        targets={r['code'] for r in scope['supported_products']}
        direct_codes=set(direct_coverage['guaranteed_codes'])
        all_declared_supported=(not scope['uncertainty'] and set(scope['meta_codes']).issubset(targets))
        if targets and all_declared_supported and targets.issubset(direct_codes):put(10,0,'all_supported_purchase_targets_have_operative_direct_requirement',[x['evidence'] for x in direct])
        if known=='general_in_supplied_catalog':
            for i in (10,11,13):put(i,0,'supported_purchase_outside_supplied_competition_catalog',identity)
            if definite_direct:put(12,1,'general_purchase_with_mandatory_direct_production',identity+definite_direct)
            if price is not None:
                if price>=NOTICE and allowed:put(14,1,'general_above_notice_has_commercial_sme_restriction',es+identity)
                if FLOOR<=price<NOTICE and allowed and 'medium' not in allowed and not quote_uncertain and not exception_uncertain and not meta_small_special:put(15,1,'general_middle_band_excludes_medium_enterprise',es+identity)
                if price<FLOOR and allowed and 'medium' in allowed and not quote_uncertain and not exception_uncertain and not meta_small_special:put(17,1,'general_low_band_permits_medium_enterprise',es+identity)
                if not quote_uncertain and not exception_uncertain and not meta_small_special and no_size:
                    if FLOOR<=price<NOTICE:put(16,1,'full_observed_record_no_size_requirement',identity)
                    if price<FLOOR:put(18,1,'full_observed_record_no_size_requirement',identity)
        elif known=='competition':
            for i in (12,14,15,16,17,18):put(i,0,'supported_purchase_in_competition_catalog',identity)
            if not quote_uncertain and not exception_uncertain:
                if no_direct:put(10,1,'full_observed_record_no_direct_requirement',identity)
                if no_size:put(11,1,'full_observed_record_no_size_requirement',identity)
                if allowed and 'medium' not in allowed:
                    # The provided competition table does not establish the
                    # separate Article 7-2 small-enterprise designation list.
                    put(13,None,'small_only_competition_requires_article7_2_designation_check',es+identity)
        from .small_quote import review as small_quote_review
        quote_sizes={tuple(sorted(x['size']['allowed'])) for x in sizes}
        quote_review=small_quote_review(record,price,
            allowed if len(quote_sizes)==1 and not unresolved_size_branch else None)
        if quote_review['status']=='permitted_small_size_route':
            put(13,0,quote_review['reason'],[*quote_review['evidence'],*es])
    return {'version':'sme_logic_v1','product':scope,'product_candidates':product,
            'price':{'effective_won':price,**p},'inventory':inventory,'eligibility_sections':sections,
            'enterprise_size':{'allowed_commercial':sorted(allowed) if allowed else None,'active_clauses':len(sizes),
                               'unresolved_alternative_branch':unresolved_size_branch,
                               'special_entities_are_separate':True},
            'direct_production':{'active_clauses':len(direct),'supported_target_codes':sorted(direct_codes) if ordinary else [],
                                 'certificate_coverage':direct_coverage},
            'absence_proof':{'full_input_scanned':True,'complete':complete,'closed_notice_eligibility_found':recovered,
                             **({'unclosed_eligibility':unclosed} if unclosed else {}),
                             'no_direct_requirement':no_direct,'no_size_requirement':no_size,
                             'unresolved_direct_candidates':len(direct_ambiguous),'size_candidates':len(size_ambiguous),
                             'dropped_doc_counts':record.get('dropped_doc_counts'), 'input_completeness':record.get('input_completeness')},
            'exceptions':{'body':exceptions,'actual_quote_evidence':quotes,'meta_reason':meta_exception,
                          'small_quote_v13_review':quote_review if ordinary else None,
                          'meta_reason_relevant':meta_exception_relevant,'priority_exception_requires_review':exception_uncertain,
                          'meta_small_enterprise_special_case':meta_small_special,'quote_or_quote_metadata':quote_uncertain,
                          'article7_2_designation_status':'not_established_from_competition_catalog'},
            'decisions':decisions}


def compact_prompt(facts):
    """Prompt adapter. Audit JSON contains the complete full-record inventory."""
    lines=['SME FACTS: None means unresolved, not compliant.']
    lines.append('PRODUCT '+facts['product']['status']+'; unlisted codes and lexical candidates do not prove general status')
    for p in facts['product']['supported_products']:lines.append(str(p))
    lines.append('ESTIMATED_PRICE '+str(facts['price']['effective_won'])+'; meta/body conflict='+str(facts['price']['meta_body_conflict']))
    for x in facts['product']['identity_evidence']:
        e=x['evidence'];lines.append(f"PURCHASE [D{e['doc_index']} {e['start']}:{e['end']}] {e['text']}")
    lines.append('COMMERCIAL_SIZE '+str(facts['enterprise_size']))
    seen=set()
    candidates=[x for x in facts['inventory'] if x['status'] in ('mandatory_eligibility','explicit_permission') and (x['size'] or x['direct_production'])]
    for x in candidates:
        e=x['evidence'];key=(e['doc_index'],e['start'],e['end'])
        if key in seen:continue
        seen.add(key)
        if x['heading']:lines.append('HEADING '+x['heading']['text'])
        lines.append(f"[{e['document_role']} D{e['doc_index']} {e['start']}:{e['end']}] {e['text']}")
        lines.append(f"role={x['status']}; size={x['size']}; direct={x['direct_production']}; validity={x['validity']}")
    for x in facts['exceptions']['body']:
        label = 'EXCEPTION' if requires_exception_review(x) else 'NONOPERATIVE_EXCEPTION_OBSERVATION'
        e=x['evidence'];lines.append(f"{label} {x['kind']} [D{e['doc_index']} {e['start']}:{e['end']}] {e['text']}")
    lines.append('META_EXCEPTION '+str(facts['exceptions']['meta_reason']))
    for e in facts['exceptions']['actual_quote_evidence']:
        lines.append(f"QUOTE [D{e['doc_index']} {e['start']}:{e['end']}] {e['text']}")
    lines.append('ABSENCE '+str(facts['absence_proof']))
    lines.append('DECISIONS '+str({k:(d['value'],d['reason']) for k,d in facts['decisions'].items()}))
    return '\n'.join(lines)
