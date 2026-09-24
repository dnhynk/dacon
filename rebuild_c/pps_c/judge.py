"""Item judgments from the fact bundle (the item table, REBUILD_BASIS §2; organizer notices).

Each rule returns the evidence line (or True for absence items) when the item is violated, else None.
"""
from __future__ import annotations

import re

from . import amounts, catalog, dates, regions, switches
from .meta import EOK, NOTICE_AMOUNT

ITEMS = [f'v{k}' for k in range(1, 25)]
ABSENCE = ('v10', 'v11', 'v16', 'v18', 'v20')


QUAL_SECTIONS = ('QUAL', 'TOP', 'OVERVIEW')
NOT_QUAL_SECTIONS = ('EVAL', 'DOCS', 'NOTE')
# "입찰방법: 제한경쟁(소기업·소상공인)" style summaries state the procedure, not a qualification clause (talkboard,
# DEV-039: a bidding-method display does not count as the participation restriction).
METHOD_SUMMARY = re.compile(r'^\W*(입\s*찰\s*(방\s*법|방\s*식)|계\s*약\s*(방\s*법|방\s*식)|경\s*쟁\s*(형\s*태|방\s*법)|입\s*찰\s*및\s*계\s*약\s*(방\s*법|방\s*식))\s*[:：|‣]'
                            r'|^\W*\(?\s*(총액|단가)?\s*(입찰)?\s*,?\s*(제한|일반|지명)\s*경쟁\s*(입찰)?\s*[\(（][^)）]*[\)）]\s*,')


def qual_section(ln, notice=None):
    """A participation qualification is what the notice's qualification section (or its summary table) states;
    evaluation criteria, document lists and cautions elsewhere are not (the model confuses them). A notice without a
    detectable qualification section falls back to its 공고문 lines outside evaluation, document and caution sections."""
    if METHOD_SUMMARY.search(ln.text):
        return False
    if ln.sec in QUAL_SECTIONS and (ln.sec == 'QUAL' or ln.doc_type == '공고문'):
        return True
    return notice is not None and not notice.has_qual and ln.doc_type == '공고문' and ln.sec not in NOT_QUAL_SECTIONS


def lines_where(b, fam, section=False, **want):
    out = []
    for ln in b.cands.get(fam, []):
        if METHOD_SUMMARY.search(ln.text):
            continue
        if section and not qual_section(ln, b.notice):
            continue
        r = b.read(fam, ln)
        if all((r.get(k) in v) if isinstance(v, (tuple, set, list)) else r.get(k) == v for k, v in want.items()):
            out.append(ln)
    return out


# ---------------------------------------------------------------- region and performance
# The orderer's own jurisdiction named by its anonymised token ("본점 소재지가 [수요기관(기초자치단체)] 내에 있는 업체") is a
# bidder-location restriction at that jurisdiction's level (dev DEV-071: 기초 → v6, v8).
ORDERER_JURISDICTION = re.compile(r'(본\s*점|주\s*된\s*(영\s*업\s*소|사\s*무\s*소)|사\s*업\s*장|소\s*재\s*지)[^.。]{0,120}'
                                  r'\[수요기관\((?P<level>기초자치단체|광역자치단체)\)[^\]]*\]\s*(관\s*내|내|안|관\s*할)')


# A bidder that must be, or own, a facility located or registered in a named 시·군·구 is restricted by location at that
# level (dev DEV-066: "여주, 양평 관내에 등록된 청소년수련시설로서").
FACILITY_AT = re.compile(r'(소\s*재|등\s*록|위\s*치|설\s*치)\s*(한|된|하고\s*있는|되어\s*있는)[^.。]{0,40}(시\s*설|센\s*터|사\s*업\s*장|공\s*장|창\s*고|차\s*고\s*지|영\s*업\s*장|매\s*장|점\s*포)'
                         r'[^.。]{0,30}(보\s*유|갖\s*춘|로\s*서|인\s*자|이어야|운\s*영\s*하)')


# A location restriction names a place or a locating verb; a line with neither (a 실적 clause) is not one.
LOCATION_CUE = re.compile(r'관\s*내|소\s*재|둔\s*(자|업\s*체|사\s*업\s*자)|두\s*고|위\s*치|지\s*역\s*(제\s*한|업\s*체|에|내)|\[수요기관')


def orderer_level(text):
    m = ORDERER_JURISDICTION.search(text)
    if m is not None:
        return 'basic' if m.group('level') == '기초자치단체' else 'sido'
    if FACILITY_AT.search(text) and regions.mentions(text)['basic']:
        return 'basic'
    return None


def region_restriction(b):
    """(restriction lines, 시·도 set, 기초 set) of bidder-location restrictions stated as qualification."""
    lines = [ln for ln in lines_where(b, 'region', section=True, 역할='참가자격', 대상='입찰자 소재지 제한')
             if LOCATION_CUE.search(ln.text) or regions.mentions(ln.text)['sido'] or regions.mentions(ln.text)['basic']]
    known = {ln.i for ln in lines}
    lines = sorted(lines + [ln for ln in b.notice.lines if ln.i not in known and qual_section(ln, b.notice)
                            and orderer_level(ln.text)], key=lambda ln: ln.i)
    sido, basic = set(), set()
    for ln in lines:
        m = regions.mentions(ln.text)
        sido |= m['sido']
        basic |= m['basic']
    if lines and not sido and not basic and b.meta.region_sido:
        sido = set(b.meta.region_sido)
    return lines, sido, basic


# A sentence whose subject is the bidder and whose predicate requires holding the record ("…실적이 있는 업체", "…실적을
# 보유하여야") is a participation qualification wherever the 공고문 states it, outside evaluation and document lists.
PERF_BIDDER_REQ = re.compile(r'(실\s*적|경\s*험|이\s*력)[^.。]{0,40}(있는|보유한|보유하고\s*있는|갖춘|가진)\s*(업\s*체|자|법인|사업자)(?!\s*명)'
                             r'|(실\s*적|경\s*험)[^.。]{0,30}(있어야|보유하여야|보유해야|갖추어야)|(수행|납품|이행|완료|준공)\s*(한|하였던)\s*(업\s*체|자)(?!\s*명)')
PERF_FORMISH = re.compile(r'서\s*식|양\s*식|작\s*성|기\s*재|\|\s*\d|배\s*점|점\s*수|평\s*가|심\s*사|대\s*체|갈\s*음|^\W*[\(（]')


# A note on how a record is proved or substituted, or on leaving a bidder out of evaluation, is not the record requirement.
PERF_NOTE = re.compile(r'대\s*체|갈\s*음|로\s*만\s*증\s*빙|평\s*가\s*대\s*상\s*(?:자\s*)?(?:에\s*서\s*)?제\s*외')


def perf_lines(b):
    lines = [ln for ln in lines_where(b, 'perf', section=True, 역할='참가자격') if not PERF_NOTE.search(ln.text)]
    stated = [ln for ln in lines_where(b, 'perf', 역할='참가자격')
              if ln not in lines and ln.doc_type == '공고문' and ln.sec not in ('EVAL', 'DOCS')
              and PERF_BIDDER_REQ.search(ln.text) and not PERF_FORMISH.search(ln.text)]
    return lines + stated


def required_amount(b, ln):
    t = ln.text
    base_b = b.meta.B or ((b.meta.P or 0) * 1.1) or None
    vals = []
    for base, mult in amounts.ratios(t):
        if base in ('추정가격', '예정가격'):
            ref = b.meta.P
        else:
            ref = base_b
        if ref:
            vals.append(ref * mult)
    for mo in amounts.money(t):
        if mo.value >= 1e6:
            vals.append(mo.value)
    return max(vals) if vals else None


INST_WORD = re.compile(r'대학|산학\s*협력단|연구\s*(기관|소|원|단체)|공공\s*기관|국공립|협회|조합|재단|비영리|사회적\s*(기업|협동)|협동\s*조합|학교|병원|시설|단체|법인|센터')
# An exclusion names the type it shuts out: the negative attaches to the institution word ("비영리법인은 참가 불가",
# "대학 및 연구기관은 참여할 수 없습니다"); a negative closing a limit ("…으로 제한합니다", "그 외는 참가 불가") is not one.
EXCLUSION = re.compile(r'(' + INST_WORD.pattern + r')[^.,。:]{0,10}?(은|는|도)\s*(입찰\s*|본\s*입찰\s*)?(에\s*)?(참여|참가|응찰)?\s*(불가|제외|할\s*수\s*없|하실\s*수\s*없|대상에서\s*제외|제한(됩|합)니다)')
TOKEN_ONLY = re.compile(r'^\W*(\[[^\]]*\]\W*)+$')


# "X가 아닌 자는 참가할 수 없음", "X 외에는 불가", "X에 한함" limit bidders to X although they read as exclusions.
ONLY_LIMIT = re.compile(r'만\s*(이\s*)?((입찰|견적|제안)(에|서)?\s*)?(참여|참가|응찰|가능|해당|신청)|(이|가)?\s*아닌\s*(자|업체|경우|기관|단체|법인|조합|회원사|사업자|회사|기업)|아니면|해당\s*(하지|되지)\s*않는\s*(자|업체|기관|경우)'
                        r'|(외|이외)(의|에는|에|는)?\s*(자|업체|기관|단체|법인|조합|회원사|사업자|회사|기업)?\s*(는|은)?\s*(참여|참가|입찰|불가)|에\s*한\s*(하며|하여|함|한다|해|정|합니다|하고)|(으로|로)\s*(한정|제한)')


# A clause that admits one more kind of bidder, or sets conditions for it, is not a limit: "2) 비영리법인(정관 목적에 …
# 기재되어 있어야)", "비영리법인일 경우 … 허가를 받은 자", "중소기업협동조합으로서 적격조합확인서를 소지한 자", "…도 참가 가능".
ALT_CLAUSE = re.compile(r'(일|인)\s*경우|협동\s*조합으로서|비영리\s*법인\s*[(（]|도\s*(입찰\s*)?(참가|참여)\s*(가능|할\s*수)|제외(?!한\s*(자|업체))|제\s*2\s*조의\s*3')
ORDERER_TOKEN = re.compile(r'\[수요기관\([^)\]]*\)[^\]]*\]')
# Names of laws, decrees and guidelines ("지방자치단체를 당사자로 하는 계약에 관한 법률", "…공공기관 정보시스템 구축·운영 지침")
# carry institution words without naming a bidder type.
LAW_NAME = re.compile(r'「[^」]{1,60}」|『[^』]{1,60}』|｢[^｣]{1,60}｣|[가-힣·\s]{2,40}(에\s*관한\s*법률|법\s*시행령|법\s*시행규칙|지침|규칙|규정|예규|고시)')


# The eligible list also names commercial bidders ("…연구기관 또는 컨설팅기관(회사)"): general businesses may bid.
COMMERCIAL = r'(?:회\s*사|(?<!회원)(?<!조합원)(?<!협력)업\s*체|(?<![가-힣])사\s*업\s*자|일\s*반\s*기\s*업|(?<![가-힣])기\s*업(?!\s*부\s*설))'
ALT_JOIN = r'(?:또\s*는|이\s*나|거\s*나|및|,|·|ㆍ|/)'
COMMERCIAL_ALT = re.compile(r'(?:' + INST_WORD.pattern + r')[^.。]{0,25}?' + ALT_JOIN + r'\s*(?:[가-힣A-Za-z]{0,8}\s*[\(（])?\s*' + COMMERCIAL + r'(?!\s*명)'
                            + r'|' + COMMERCIAL + r'\s*' + ALT_JOIN + r'[^.。]{0,10}?(?:' + INST_WORD.pattern + r')')
# A type defined by legal registration or designation ("…에 등록된 청소년수련시설", "…지정된 검사기관") is a
# registration requirement (면허·업종·등록), not a limit to institution types, unless the line says only that type may bid.
REGISTERED_TYPE = re.compile(r'(?<!업종으로\s)(?<!업종으로)(?<!자격으로\s)(?<!자격으로)(?<!업종\s)(?<!업종)(?:등\s*록|지\s*정|허\s*가|인\s*가|신\s*고)\s*(?:된|받은|을\s*받은|한)\s*[^,.。]{0,20}?(?:기\s*관|시\s*설|단\s*체|법\s*인|조\s*합|센\s*터|병\s*원|학\s*교)')
# Membership limits ("OO협회 회원사", "조합원") stay limits when phrased as shutting out non-members.
MEMBERSHIP = re.compile(r'회\s*원|조\s*합\s*원|가\s*입|소\s*속')


RESERVED_PROFESSION = re.compile(r'(?:회\s*계|감\s*정\s*평\s*가|법\s*무|세\s*무|노\s*무|특\s*허|관\s*세|손\s*해\s*사\s*정)\s*법\s*인')


def institution_limit(text):
    """A limit to institution types names one (대학, 연구기관, 협회, 법인 …, anonymised [기관(유형)] tokens included; the
    [수요기관(…)] token is the orderer, never the bidder) and is neither an exclusion of that type ("비영리법인은 참가
    불가"), a clause admitting or conditioning one more kind of bidder, a list that also admits commercial bidders, nor a
    type defined by legal registration."""
    bidder_text = LAW_NAME.sub(' ', ORDERER_TOKEN.sub(' ', text))
    if TOKEN_ONLY.match(text) or not INST_WORD.search(bidder_text):
        return False
    only = ONLY_LIMIT.search(text)
    # An institution named only inside an anonymised [기관(…)] token needs an explicit limiting phrase.
    if not INST_WORD.search(re.sub(r'\[[^\]]*\]', ' ', bidder_text)) and not only:
        return False
    if ALT_CLAUSE.search(text) and not only:
        return False
    if COMMERCIAL_ALT.search(bidder_text) and not only:
        return False
    if REGISTERED_TYPE.search(bidder_text) and not only:
        return False
    if EXCLUSION.search(text) and not only and not MEMBERSHIP.search(text):
        return False
    return True


# Holdings spread over the whole country or every region (the organizer's "전국 모든 광역단체에 수리센터") exceed any
# single contract's need.
NATIONWIDE = re.compile(r'전국|모든\s*(광역|시\s*[·ㆍ]?\s*도|지역|시\s*[·ㆍ]?\s*군)|각\s*(시\s*[·ㆍ]?\s*도|광역|지역|시\s*[·ㆍ]?\s*군)|(광역시|도)\s*단위')
HOLDING = re.compile(r'센터|지점|지사|영업소|사업장|시설|장비|인력|기술자|정비|수리|A/S|AS|서비스망|지점망')
HOLD_VERB = re.compile(r'보유|있는|갖춘|갖추|두고|둔|설치|운영|구축|확보')
# The holding itself must be required: a centre, branch or network the bidder must have across the country.
NATIONWIDE_HOLDING = re.compile(r'(전국|모든\s*(광역|시\s*[·ㆍ]?\s*도|지역|시\s*[·ㆍ]?\s*군)|각\s*(시\s*[·ㆍ]?\s*도|광역|지역|시\s*[·ㆍ]?\s*군)|(광역시|도)\s*단위)'
                                r'[^.。]{0,20}(센터|지점|지사|영업소|사업장|서비스망|지점망|네트워크|A/S|AS)[^.。]{0,12}(보유|갖춘|갖추|두고|둔|설치|구축|확보|있는\s*(업체|자))')
# "일정 규모 이상의 인력 보유" (item rule, organizer example: 보안인력 50명 이상): a staff count of ten or more.
STAFF_SCALE = re.compile(r'(인력|기술자|인원|직원|근로자|경비원|요원|전문가|강사|상담사|종사자)[^.。]{0,25}?(\d{2,})\s*(명|인)\s*이상'
                         r'|(\d{2,})\s*(명|인)\s*이상[^.。]{0,15}?(인력|기술자|인원|직원|근로자|경비원|요원|전문가|강사|상담사|종사자)')
STAFF_VERB = re.compile(r'보유|확보|고용|상시|갖춘')


def v1(b):
    for ln in b.cands.get('inst', []):
        if not qual_section(ln, b.notice):
            continue
        r = b.read('inst', ln)
        if r.get('역할') != '참가자격':
            continue
        kind = r.get('요건')
        if kind == '기관 유형 한정':
            # 지명경쟁 names its bidders by definition (지방계약법 시행령 제22조 등); a nomination list is not a limit.
            # A legally reserved profession (회계법인 for 결산, 법무법인 …) is a license unless the line says only it may bid.
            if RESERVED_PROFESSION.search(ln.text) and not ONLY_LIMIT.search(ln.text):
                continue
            if b.meta.method != '지명경쟁' and institution_limit(ln.text):
                return ln
            continue
        if NATIONWIDE_HOLDING.search(ln.text) and not b.meta.local_private:
            return ln
        if STAFF_SCALE.search(ln.text) and STAFF_VERB.search(ln.text) and not re.search(r'실\s*적', ln.text):
            return ln
        if kind in ('시설·장비 보유', '인력 보유'):
            if kind == '시설·장비 보유' and b.meta.local_private:
                continue
            if r.get('필요성') == '과도하거나 과업과 무관':
                return ln
    return None


def v2(b):
    P = b.meta.P
    if P is None or P >= NOTICE_AMOUNT or b.meta.local_private:
        return None
    lines = perf_lines(b)
    return lines[0] if lines else None


def v3(b):
    B = b.meta.B or ((b.meta.P or 0) * 1.1) or None
    if B is None:
        return None
    for ln in perf_lines(b):
        a = required_amount(b, ln)
        if a is not None and a > B + 1:
            return ln
    return None


# v4 is decided by the text: a required record limited to a kind of buyer or customer names that kind with the relation
# (…이 발주한, …에 납품한, …에서 수행한, …학생 대상). A buyer list that also admits private parties (법인, 기업, 민간, 민자,
# 업체) is no limit. The model's reading alone cannot supply a buyer the text does not name.
_END = r'(?=\s|$|[,·ㆍ\(\)\[\]"\'“”‘’/]|에|이|가|의|등|또|및|과|와|으|로|을|를|은|는|만|나|대\s*상|관\s*련)'
BUYER = re.compile(r'(?:국\s*가\s*기\s*관|국\s*가|중\s*앙\s*행\s*정\s*기\s*관|정\s*부\s*투\s*자\s*기\s*관|정\s*부|지\s*방\s*자\s*치\s*단\s*체|자\s*치\s*단\s*체|지\s*자\s*[체제]'
                   r'|관\s*공\s*서|공\s*공\s*기\s*관|공\s*기\s*업|준\s*정\s*부\s*기\s*관|출\s*자\s*[·ㆍ]?\s*출\s*연\s*기\s*관|교\s*육\s*(?:지\s*원\s*)?청|\[수요기관\([^\]]*\)[^\]]*\]'
                   r'|대\s*학\s*교|대\s*학\s*병\s*원|대\s*학|종\s*합\s*병\s*원|병\s*원|학\s*교|유\s*치\s*원|사\s*회\s*복\s*지\s*(?:기\s*관|시\s*설)|교\s*육\s*기\s*관|의\s*료\s*기\s*관'
                   r'|공\s*직\s*유\s*관\s*단\s*체)' + _END)
# A past-record relation: an ordering, delivery or performance verb within the clause after the buyer list, or a particle
# attached to the buyer itself ("공공기관에서", "대학에 대한").
BUYER_VERB = re.compile(r'발\s*주|시\s*행\s*한|납\s*품\s*(?:한|하였|실\s*적|이\s*력)|수\s*행\s*(?:한|하였|완\s*료|실\s*적)|완\s*료\s*(?:한|된)|거\s*래\s*(?:한|실\s*적)'
                        r'|계\s*약\s*(?:한|하여|체\s*결\s*한)|의\s*(?:용\s*역|사\s*업|과\s*업)')
BUYER_PARTICLE = re.compile(r'\s*(?:등\s*)?(?:에\s*서|에\s*대\s*한|에\s*게|을\s*대\s*상|대\s*상)(?!\s*(?:인\s*증|인\s*정|지\s*정|허\s*가|등\s*록|승\s*인|발\s*급|고\s*시))')
# The orderer's own token names a past buyer only with an ordering relation ("[수요기관(…)]에서 발주한").
ORDERER_PAST = re.compile(r'\s*(?:에\s*서|이|가|의)?\s*(?:발\s*주|시\s*행\s*한|납\s*품\s*한)')
PRIVATE_BUYER = re.compile(r'민\s*간|민\s*자|일\s*반\s*기\s*업|사\s*기\s*업|(?<!공)기\s*업(?!\s*부\s*설)|(?<!비영리)(?<!출연)(?<!출자)(?<!학교)법\s*인|업\s*체|회\s*사|사\s*립|개\s*인')
PRIVATE_SECTOR = re.compile(r'민\s*간|민\s*자|일\s*반\s*기\s*업|사\s*기\s*업|사\s*립')
ORDERER_OPEN = re.compile(r'민\s*간\s*(?:실\s*적\s*)?(?:도\s*)?(?:포\s*함|인\s*정)|공\s*공\s*(?:기\s*관\s*)?(?:또\s*는|및|·|ㆍ)\s*민\s*간|발\s*주\s*처\s*(?:구\s*분|불\s*문|무\s*관)')
BENEFICIARY = re.compile(r'(?:유\s*치\s*원|초\s*등\s*학\s*교|중\s*학\s*교|고\s*등\s*학\s*교|중\s*[·ㆍ,]?\s*고\s*등?\s*학\s*교|초\s*[·ㆍ,]?\s*중\s*[·ㆍ,]?\s*고)[^\n]{0,35}(?:학\s*생|원\s*생)\s*(?:을\s*)?대\s*상')
LAW_REF = re.compile(r'「[^」]{1,60}」|『[^』]{1,60}』|｢[^｣]{1,60}｣|[가-힣\s]{2,30}에\s*관한\s*법률(?:\s*시행령|\s*시행규칙)?|[가-힣]{2,20}법\s*(?:시행령|시행규칙)')


def buyer_limit(text):
    """'specific' when the required record is limited to a named kind of buyer or customer, 'open' when the buyer list
    admits private parties, None when the text names no buyer. Law names are not buyers."""
    text = LAW_REF.sub(' ', text)
    if BENEFICIARY.search(text):
        return 'specific'
    for m in BUYER.finditer(text):
        if m.group(0).startswith('[수요기관') and not ORDERER_PAST.match(text, m.end()):
            continue
        particle = BUYER_PARTICLE.match(text, m.end())
        verb = BUYER_VERB.search(text, m.end(), min(len(text), m.end() + 60))
        if particle:
            stop = particle.start()
        elif verb:
            stop = verb.start()
        else:
            continue
        span = text[m.start():stop]
        return 'open' if PRIVATE_BUYER.search(span) or ORDERER_OPEN.search(text) else 'specific'
    return None


def v4(b):
    for ln in perf_lines(b):
        clause = clause_text(b.notice, ln)
        verdict = buyer_limit(clause)
        if verdict == 'specific':
            return ln
        # No buyer relation the CPU can parse: the model's reading stands when the clause is a bidder requirement that
        # names a buyer kind (not the orderer's own token).
        bare = LAW_NAME.sub(' ', clause)
        named = any(not m.group(0).startswith('[수요기관') for m in BUYER.finditer(bare))
        if (verdict is None and named and PERF_BIDDER_REQ.search(clause) and not PRIVATE_SECTOR.search(bare)
                and b.read('perf', ln).get('발주처') == '특정 발주기관만'):
            return ln
    return None


def v5(b):
    """A bidder-location restriction stated in the qualification section, or registered on 나라장터 (meta 지역제한여부 Y
    is the restriction the bid system enforces), at P ≥ T."""
    lines, _, _ = region_restriction(b)
    P = b.meta.P
    if P is None or P < b.meta.T_hi:
        return None
    if lines:
        return lines[0]
    return True if b.meta.region_flag == 'Y' else None


def v6(b):
    lines, _, basic = region_restriction(b)
    P = b.meta.P
    if not lines or P is None or P >= b.meta.T_lo or b.meta.local_private:
        return None
    for ln in lines:
        if regions.mentions(ln.text)['basic'] or orderer_level(ln.text) == 'basic':
            return ln
    return None


def v7(b):
    lines, sido, _ = region_restriction(b)
    P = b.meta.P
    if not lines or P is None or P >= b.meta.T_lo or b.meta.local_private:
        return None
    if len(sido) >= 2:
        return max(lines, key=lambda ln: len(regions.mentions(ln.text)['sido']))
    return None


def v8(b):
    """실적 and bidder-location restrictions together; the location restriction may be the one registered on 나라장터
    (meta 지역제한여부 Y), as for v5."""
    if b.meta.local_private:
        return None
    lines, _, _ = region_restriction(b)
    perf = perf_lines(b)
    if perf and (lines or b.meta.region_flag == 'Y'):
        return lines[0] if lines else perf[0]
    return None


# A designation names the maker or model as the requirement: a 제조사·모델명·상표 label with its value, a series the
# line requires ("…시리즈일 것", "…시리즈 제품"), or a "…일 것" requirement. A product name merely listed in a
# specification table is not, even when the model name contains 시리즈 ("모니터(시리즈 7 …)", dev DEV-148 = 0). "동등 이상" does not cure a
# designation (user-approved literal default; the organizer left it open).
DESIGNATION = re.compile(r'제조\s*(사|원|회사|업체)\s*[:：·/]|모델\s*(명)?\s*[:：·/]|상표|브랜드|메이커'
                         r'|시리즈\s*(?:일\s*것|이어야|로\s*(?:한정|납품|할\s*것)|의?\s*제품)'
                         r'|(제품|것)\s*(이어야|일\s*것|으로\s*할\s*것)|일\s*것|사\s*제품|정품만')


def v9(b):
    lines = [ln for ln in lines_where(b, 'model', 성격='구매 대상의 제조사·모델 지정') if DESIGNATION.search(ln.text)
             and (switches.V9_EQUIVALENT_VIOLATION or b.read('model', ln).get('동등') != '동등 이상 허용')]
    return lines[0] if lines else None


# ---------------------------------------------------------------- competition products and enterprise size
ITEM_START = re.compile(r'^\s*(?:\d{1,2}\s*[\.\)]|\(\s*\d{1,2}\s*\)|[가-하]\s*[\.\)]|[①-⑳]|[○●◎▶►▷ㅇ◦•❍□■\-‣※]|\|)')


SENT_END = re.compile(r'(다|함|음|것|임|요|자|체)\s*[.。)]?\s*$|[.。]\s*$')


def clause_text(notice, ln):
    """The list item a line belongs to: wrapped continuation lines before and after are joined (one document).
    Documents extracted with a blank line after every line wrap a clause across blank lines; there a blank line is
    crossed unless the text before it ends a sentence."""
    doc = [x for x in notice.window(ln.i, 9, 9)]
    k = next(n for n, x in enumerate(doc) if x.i == ln.i)

    def prev_text(n):
        m = n - 1
        while m >= 0 and not doc[m].text.strip():
            m -= 1
        return m, m < n - 1

    def next_text(n):
        m = n + 1
        while m < len(doc) and not doc[m].text.strip():
            m += 1
        return m, m > n + 1

    start, joined = k, 0
    while joined < 4 and not ITEM_START.match(doc[start].text):
        m, gap = prev_text(start)
        if m < 0 or gap and SENT_END.search(doc[m].text):
            break
        start, joined = m, joined + 1
    end, joined = k, 0
    while joined < 4:
        m, gap = next_text(end)
        if m >= len(doc) or ITEM_START.match(doc[m].text) or gap and SENT_END.search(doc[end].text):
            break
        end, joined = m, joined + 1
    return ' '.join(x.text.strip() for x in doc[start:end + 1] if x.text.strip())


def size_class(b, ln):
    """Allowed enterprise class of a restriction clause, read from its wording (law and agency names removed); the
    model's reading only when the wording names no class."""
    from .families import size_words
    cls = size_words(clause_text(b.notice, ln))
    if cls:
        return cls
    v = b.read('size', ln).get('허용 대상')
    return {'중소기업 전체': 'sme', '소기업·소상공인만': 'small'}.get(v)


# 판로지원법 시행령 제2조의3 ① lets the orderer skip the SME restriction, stated in the notice (②; talkboard: only a written
# exception counts). The clause must say the restriction or the preferential procurement is not applied; a clause that
# only admits one more kind of bidder into the restricted bid ("제2조의3 제1항제2호에 해당하는 비영리법인은 입찰참여가
# 가능", "간주되는 특별법인은 참가 가능", "비영리법인인 경우 확인서는 제외") waives nothing: the organizer marks size
# violations on such notices too.
SIZE_ADMISSION = re.compile(r'(비\s*영\s*리|특\s*별\s*법\s*인|간\s*주\s*되는)[^.。]{0,80}?(참\s*여|참\s*가|입\s*찰)[^.。]{0,16}?(가\s*능|할\s*수)'
                            r'|확\s*인\s*서\s*(가|를)?\s*(없\s*어\s*도|제\s*출\s*하\s*지\s*않\s*아\s*도|소\s*지\s*하\s*지\s*않\s*아\s*도)'
                            r'|비\s*영\s*리[^.。]{0,40}확\s*인\s*서\W{0,3}(는|은)?\s*제\s*외')
SIZE_WAIVER = re.compile(r'(우\s*선\s*조\s*달|제\s*한|경\s*쟁\s*입\s*찰)[^.。]{0,40}?(예\s*외|제\s*외|비\s*대\s*상|적\s*용\s*(하\s*지|을\s*배\s*제|배\s*제|않))'
                         r'|예\s*외\s*(를\s*)?적\s*용|예\s*외\s*(사\s*유|품\s*목|대\s*상|용\s*역|건)|예\s*외\s*의\s*사\s*유|제\s*외\s*(용\s*역|대\s*상|품\s*목)'
                         r'|제\s*한\s*하\s*지\s*(않|아니)|제\s*한\s*을\s*하\s*지|비\s*대\s*상|외\s*의\s*방\s*법\s*으\s*로|소\s*지\s*여\s*부\s*와\s*관\s*계\s*없\s*이'
                         r'|(대\s*기\s*업|중\s*기\s*업)[^.。]{0,12}참\s*(여|가)\s*(가\s*)?(가\s*능|할\s*수)|통\s*합\s*발\s*주|분\s*리\s*발\s*주'
                         r'|판\s*로\s*지\s*원[^.。]{0,40}(예\s*외|적\s*용\s*(하\s*지\s*)?(않|제\s*외|배\s*제))'
                         r'|제\s*2\s*조\s*의?\s*3[^.。]{0,60}?(예\s*외|제\s*외)|운\s*영\s*요\s*령\W{0,3}\s*제\s*44\s*조')
# Citing 제2조의3 of the 판로지원법 decree is an exception only when the clause does not merely admit another bidder.
SIZE_CITE = re.compile(r'(판\s*로\s*지\s*원|구\s*매\s*촉\s*진|우\s*선\s*조\s*달)[^.。]{0,80}?제\s*2\s*조\s*의?\s*3')


def waives_size_limit(b, ln):
    t = clause_text(b.notice, ln)
    if SIZE_ADMISSION.search(t) and not SIZE_WAIVER.search(t):
        return False
    return bool(SIZE_WAIVER.search(t) or SIZE_CITE.search(t))


# 판로지원법 시행령 제2조의3 ①3: goods and services that another law lets the orderer buy by 수의계약 from a designated class
# (국가계약법 시행령 제26조①5가5·7, 지방계약법 시행령 제25조①5다·바: 여성기업, 장애인기업, 사회적기업, 사회적협동조합,
# 자활기업, 마을기업, 청년창업기업) are outside the SME preference. A qualification limited to such a class states that
# basis (dev DEV-172: 여성기업 수의계약, v18 = 0). The certificate rule's name "…및 장애인기업 확인요령" is not such a limit.
DESIGNATED_CLASS = re.compile(r'여\s*성\s*기\s*업|장\s*애\s*인\s*기\s*업|사\s*회\s*적\s*(기\s*업|협\s*동\s*조\s*합)|자\s*활\s*기\s*업|마\s*을\s*기\s*업|청\s*년\s*창\s*업\s*기\s*업')
DESIGNATED_NAMES = re.compile(r'[^\s「」｢｣『』“”‘’<>]*장\s*애\s*인\s*기\s*업\s*(확\s*인\s*요\s*령|활\s*동\s*촉\s*진\s*법)|여\s*성\s*기\s*업\s*지\s*원\s*에\s*관\s*한\s*법\s*률'
                              r'|사\s*회\s*적\s*기\s*업\s*육\s*성\s*법|협\s*동\s*조\s*합\s*기\s*본\s*법')


def designated_class_limit(b):
    for ln in b.notice.lines:
        if ln.doc_type != '공고문' or not qual_section(ln, b.notice):
            continue
        t = DESIGNATED_NAMES.sub(' ', ln.text)
        if DESIGNATED_CLASS.search(t) and not re.search(r'가\s*점|우\s*대|배\s*점|평\s*가', t):
            return True
    return False


def size_state(b, positive=False):
    """('small' | 'sme' | 'unknown' | None, restriction lines, exception stated).

    positive=True (v13–v15, v17: the restriction's class is the violation) counts only the qualification section;
    otherwise (v11, v16, v18: absence) a restriction stated anywhere in the documents counts as present.
    """
    lines = lines_where(b, 'size', section=positive, 역할='참가자격 제한')
    exc = any(waives_size_limit(b, ln) for ln in lines_where(b, 'size', 역할='판로지원 예외 명시'))
    if not lines:
        return None, lines, exc
    # The class comes from the clauses that define who may bid, not from certificate-validity conditions
    # ("발급된 소기업·소상공인확인서가 … 다른 경우 입찰참가자격이 없습니다").
    primary = [ln for ln in lines if not SIZE_CONDITION.search(ln.text)] or lines
    classes = [size_class(b, ln) for ln in primary]
    if 'small' in classes:
        return 'small', lines, exc
    if 'sme' in classes:
        return 'sme', lines, exc
    return 'unknown', lines, exc


SIZE_CONDITION = re.compile(r'경\s*우|신청한\s*(사항|업체)|확인되지\s*않|간주되는|특별\s*법인')
DP_SANCTION = re.compile(r'위반|제재|해지|취소|불이익|부정당|직접생산\s*확인\s*기준|하도급|타사\s*제품')


def dp_required(b, positive=False):
    return lines_where(b, 'dp', section=positive, 역할='참가자격 소지 요구')


def dp_present(b):
    """Absence test for v10: the model read a possession requirement, or the qualification section mentions the
    certificate outside a sanction or document-list line (a deleted requirement leaves only such mentions)."""
    from .families import DOC_LIST
    if dp_required(b):
        return True
    for ln in b.cands.get('dp', []):
        if qual_section(ln, b.notice) and not METHOD_SUMMARY.search(ln.text) and not DP_SANCTION.search(ln.text) \
                and not DOC_LIST.search(ln.text) and ln.sec != 'DOCS':
            return True
    return False


# The notice states that the competition-product rules do not apply (판로지원법 시행령 제7조 exceptions, or an explicit
# exclusion from the direct-production / SME-competition procedure).
COMPETITION_EXCEPTION = re.compile(r'(판로\s*지원|구매\s*촉진).{0,60}시행령\s*[」』｣]?\s*제\s*7\s*조|중소\s*기업자?\s*간\s*경쟁\s*(제품|입찰)?.{0,40}(예외|제외|적용\s*(하지|을\s*배제|배제))'
                                   r'|직접\s*생산\s*확인\s*(품목|대상)?\s*(에서|을)?\s*제외')


def competition_exception(b):
    return any(COMPETITION_EXCEPTION.search(ln.text) for ln in b.notice.lines if ln.doc_type == '공고문')


def certified_as_listed(b):
    """A service whose 직접생산 requirement certifies a listed competition service admitted at P, which the model reads as
    the procured work itself: the purchase is that competition service (v12 is judged by the procured object)."""
    if b.meta.work != '용역':
        return False
    cat = catalog.load()
    return any(b.read('dp', ln).get('인증 품목') == '과업과 같은 종류' and catalog.cites_listed(ln.text, cat, b.meta.P, services_only=True)
               for ln in dp_required(b))


def competitive_service(b):
    """v10·v11·v13 are judged on service purchases only: the organizer marks none of them on competition-product goods
    (dev 0 of 16, including goods with no 직접생산 text at all), and the replica has no planted or teacher-agreed goods
    cell among 102 goods firings."""
    if not switches.SW_SERVICE_COMPETITIVE and b.scope.basis.endswith(':sw'):
        return False
    return b.meta.work == '용역' and (b.scope.competitive is True or certified_as_listed(b))


def v10(b):
    if not competitive_service(b) or competition_exception(b):
        return None
    if b.meta.P is not None and b.meta.P < 1e7:
        return None
    return None if dp_present(b) else True


def v11(b):
    """판로지원법 제7조 requires SME competition in the *bidding* for a competition product (item: "…경쟁제품 입찰 중소
    없음"); a 소액수의 quotation is not a bid, as the organizer's 소액수의 exception for v13 also reflects."""
    if not competitive_service(b) or competition_exception(b) or (b.meta.private and not switches.V11_PRIVATE):
        return None
    state, _, _ = size_state(b)
    return True if state is None else None


def v12(b):
    """v12 is judged by the procured object (talkboard): a certificate for a listed competition product that the model
    reads as the procured work itself shows the purchase is that product, whatever our title families say."""
    if b.scope.competitive is not False:
        return None
    cat = catalog.load()
    lines = [ln for ln in dp_required(b, positive=True)
             if not (b.read('dp', ln).get('인증 품목') == '과업과 같은 종류' and catalog.cites_listed(clause_text(b.notice, ln), cat, b.meta.P))
             and not (DP_VERIFY.search(ln.text) and not DP_POSSESS.search(ln.text)) and not DP_CONDITIONAL.search(ln.text)]
    return lines[0] if lines else None


# "…직접생산확인증명서가 종합정보망에서 확인이 안 될 경우 입찰참가자격이 없습니다" verifies a certificate required
# elsewhere; on its own it is not the possession requirement v12 judges (dev: both such lines are organizer 0).
DP_VERIFY = re.compile(r'(확\s*인|조\s*회)\s*(\([^)]{0,120}\))?\s*(이|가)?\s*(안\s*될|안\s*되는|되지\s*않을|되지\s*않는|불가할)\s*(경우|시)|확\s*인\s*(이\s*)?가\s*능\s*하\s*여\s*야')
# A requirement conditioned on the purchase being a competition product ("중소기업자간 경쟁제품으로 입찰공고한 경우 … 보유",
# "해당 품목의 경우") demands nothing of a purchase that is not one.
DP_CONDITIONAL = re.compile(r'(경\s*쟁\s*제\s*품|해\s*당\s*(품\s*목|물\s*품|제\s*품)|대\s*상\s*품\s*목)[^.。]{0,24}(인|일|으로\s*입\s*찰\s*공\s*고\s*한|에\s*해\s*당\s*하\s*는)\s*경\s*우|해\s*당\s*(시|하\s*는\s*경\s*우)')
DP_POSSESS = re.compile(r'(소\s*지|보\s*유|발\s*급\s*받|취\s*득)\s*(한|하고|하여야|해야)\s*(자|업\s*체|사\s*업\s*자)?')


def v13(b):
    if not competitive_service(b) or b.meta.private or competition_exception(b):
        return None
    state, lines, _ = size_state(b, positive=True)
    if state == 'small':
        return next(ln for ln in lines if size_class(b, ln) == 'small')
    return None


def _general(b):
    if not switches.SIZE_PRIVATE and b.meta.method == '수의계약':
        return False
    return b.scope.competitive is False and b.meta.P is not None and not certified_as_listed(b)


def v14(b):
    if not _general(b) or b.meta.P < NOTICE_AMOUNT:
        return None
    state, lines, _ = size_state(b, positive=True)
    return lines[0] if state in ('small', 'sme') else None


def v15(b):
    if not _general(b) or not (EOK <= b.meta.P < NOTICE_AMOUNT):
        return None
    state, lines, _ = size_state(b, positive=True)
    if state == 'small':
        return next(ln for ln in lines if size_class(b, ln) == 'small')
    return None


def v16(b):
    if not _general(b) or not (EOK <= b.meta.P < NOTICE_AMOUNT):
        return None
    state, _, exc = size_state(b)
    return True if state is None and not exc and not designated_class_limit(b) else None


# 판로지원법 시행령 제2조의2 ①1 단서: below 1억 the restriction may widen to all SMEs when the 소기업·소상공인 bid failed (유찰)
# or three or fewer eligible small firms evidently exist; a re-announcement or a stated 가목·나목 reason is such a case.
REBID_TITLE = re.compile(r'재\s*공고|재\s*입찰|\(\s*재\s*\)')
SME_WIDENING = re.compile(r'유\s*찰\s*(되어|됨에|로\s*인|에\s*따라|되었|된\s*(건|사업|입찰))|제\s*2\s*조의\s*2\s*제\s*1\s*항\s*제\s*1\s*호\s*(가|나)\s*목'
                          r'|3\s*인\s*이하임이\s*명백')


def sme_widening_allowed(b):
    return bool(REBID_TITLE.search(' '.join(b.titles[:2]))) or any(
        SME_WIDENING.search(ln.text) for ln in b.notice.lines[:120] if ln.doc_type == '공고문')


def v17(b):
    if not _general(b) or b.meta.P >= EOK or sme_widening_allowed(b):
        return None
    state, lines, _ = size_state(b, positive=True)
    return lines[0] if state == 'sme' else None


def v18(b):
    if not _general(b) or b.meta.P >= EOK:
        return None
    state, _, exc = size_state(b)
    return True if state is None and not exc and not designated_class_limit(b) else None


# ---------------------------------------------------------------- pledges, software, joint contracts, briefings
# 집행기준 제5조의3 ③: the orderer concludes the supply/support agreement with the maker before the notice and the winner
# obtains the pledge after the award; a line reporting that agreement, recommending a document, or asking for partner
# cooperation agreements is not a pledge demanded of bidders, and "제출 가능한 업체" without a time sets no bid-time duty.
ORDERER_AGREEMENT = re.compile(r'(수\s*요\s*기\s*관|발\s*주\s*기\s*관|\[수요기관[^\]]*\])\s*(와|과|은|는|이|가|에서)?.{0,24}(제\s*조|공\s*급|기\s*술\s*지\s*원)[^.]{0,30}협\s*약.{0,12}(체\s*결|되어)')
PLEDGE_NOT_DEMANDED = re.compile(r'권\s*고|권\s*장|상\s*호\s*협\s*력|협\s*력\s*(관\s*계|체\s*계)')
UNTIMED_CAPABILITY = re.compile(r'제\s*출\s*(이\s*)?가\s*능\s*한\s*(업\s*체|자)')
BID_TIME = re.compile(r'입\s*찰|투\s*찰|견\s*적|마\s*감|전\s*일|이\s*전|까\s*지|제\s*안\s*서|적\s*격\s*심\s*사|낙\s*찰\s*자\s*(결\s*정|선\s*정)\s*전')


# The pledge must come from a third party the line names (organizer v19 lines name 제조사·제조회사·공급사·기술지원사); a
# "확약서 1부" list entry or an attached form with no issuer is the bidder's own undertaking as far as the notice says.
THIRD_PARTY = re.compile(r'제\s*조\s*(사|원|회\s*사|업\s*체|자)|공\s*급\s*(사|업\s*체|원)|기\s*술\s*지\s*원\s*사|총\s*판|대\s*리\s*점|본\s*사|원\s*천\s*사|개\s*발\s*사|제\s*작\s*사|판\s*매\s*사')


# 집행기준 제5조의3: the 물품공급·기술지원 확약서 is issued by the maker, supplier or support company, so a sentence that
# demands that document (제출·보유·발급·첨부 …) names its third-party issuer; a document-list entry ("…확약서 1부") or a form
# title does not (dev DEV-051, 096).
SUPPLY_PLEDGE = re.compile(r'(?:물\s*품\s*|정\s*품\s*)?공\s*급\s*[·ㆍ및\s]*(?:기\s*술\s*지\s*원\s*|무\s*상\s*지\s*원\s*)?(?:\(\s*A\s*/\s*S\s*\)\s*)?확\s*약\s*서'
                           r'|기\s*술\s*지\s*원\s*(?:\(\s*A\s*/\s*S\s*\)\s*)?확\s*약\s*서')
PLEDGE_DEMAND = re.compile(r'제\s*출|보\s*유|발\s*급|첨\s*부|구\s*비|소\s*지|받\s*아|받\s*은')
LIST_ENTRY = re.compile(r'\d+\s*부\s*[\.。)）]?\s*$|각\s*\d+\s*부|_\s*\d+\s*부')


def names_issuer(ln):
    t = ln.text
    if THIRD_PARTY.search(t):
        return True
    return bool(SUPPLY_PLEDGE.search(t) and PLEDGE_DEMAND.search(t)) and ln.sec != 'DOCS' and not LIST_ENTRY.search(t)


def pledge_demanded(ln):
    t = ln.text
    if ORDERER_AGREEMENT.search(t) or PLEDGE_NOT_DEMANDED.search(t) or not names_issuer(ln):
        return False
    return not (UNTIMED_CAPABILITY.search(t) and not BID_TIME.search(t))


def v19(b):
    for ln in b.cands.get('pledge', []):
        r = b.read('pledge', ln)
        if str(r.get('발급 주체', '')).startswith('제3자') and str(r.get('시점', '')).startswith('입찰 전') and pledge_demanded(ln):
            return ln
    return None


# The 제48조 statement: a line that states whether large (or 중견) SW businesses may bid, or restricts bidding to 중소
# SW businesses under 제48조 / the 중소 SW사업자 지침 (talkboard: SW 신고, 중소기업 확인서 and 상호출자 alone are not it).
SW_STATEMENT = re.compile(r'제\s*48\s*조.{0,80}(참여|참가|입찰|제한|하한|금액)|(대기업|중견\s*기업).{0,40}(참여|참가).{0,20}(제한|불가|가능|허용|없)'
                          r'|중소\s*소프트웨어\s*사업자.{0,30}(만|참여|참가)|참여\s*제한\s*(금액|하한|대상)|사업\s*참여\s*지원에\s*관한\s*지침')
CROSS_ONLY = re.compile(r'상호\s*출자')


def sw_statement(b):
    if lines_where(b, 'sw', 표기='대기업 참여제한 여부·근거 기재'):
        return True
    for ln in b.notice.lines:
        t = ln.text
        if SW_STATEMENT.search(t) and not (CROSS_ONLY.search(t) and not re.search(r'대기업|중견|중소\s*소프트웨어|하한|금액', t)):
            return True
    return False


def v20(b):
    if b.sw_project != '소프트웨어 개발·구축·유지관리·운영':
        return None
    if switches.V20_MIN_ESTIMATE and (b.meta.P or 0) < switches.V20_MIN_ESTIMATE:
        return None
    return None if sw_statement(b) else True


JV_LINE = re.compile(r'지\s*분|출\s*자\s*비\s*율|참\s*여\s*비\s*율|구\s*성\s*비\s*율|참\s*여\s*지\s*분')
PCT = re.compile(r'(\d{1,3}(?:\.\d+)?)\s*(%|퍼센트|％)')


JV_CONTEXT = re.compile(r'공\s*동\s*(수\s*급|도\s*급|이\s*행|계\s*약)|구\s*성\s*원')


def member_minimum(t):
    """Minimum member share (%) a joint-contract line sets, or None."""
    best = None
    for m in PCT.finditer(t):
        v = float(m.group(1))
        if v <= 0 or v >= 50:
            continue
        before, after = t[max(0, m.start() - 24):m.start()], t[m.end():m.end() + 16]
        if re.search(r'대\s*표|주\s*관|주\s*계\s*약|합\s*계|총|전\s*체|±|\+|-', before[-12:]) or re.search(r'차\s*이|초\s*과|이\s*내|이\s*하', after[:8]):
            continue
        floor = re.search(r'^\s*(\)|\s)*이\s*상', after) or re.search(r'최\s*소|하\s*한', before) or re.search(r'^\s*미\s*만.{0,14}(불가|없|제한|안\s*됨|아니)', after)
        if floor:
            best = v if best is None else min(best, v)
    return best


def v21(b):
    limit = 10.0 if b.meta.law == '국가' else 5.0
    prev = None
    for ln in b.notice.lines:
        t = ln.text
        context = JV_LINE.search(t) or (prev is not None and prev.doc == ln.doc and JV_LINE.search(prev.text) and ln.sec in ('JV', 'QUAL'))
        prev = ln
        if not context or not PCT.search(t):
            continue
        if re.search(r'분담\s*이행', t) and not re.search(r'공동\s*이행', t):
            continue
        low = member_minimum(t)
        if low is not None and low < limit and (JV_CONTEXT.search(t) or re.search(r'최\s*소|지\s*분\s*율|출\s*자\s*비\s*율', t)):
            return ln
    return None


# A bidder's proposal presentation (제안설명회·제안서 발표 in an evaluation, order or screening context) is not the
# orderer's briefing session; "제안설명회" alone is ambiguous in practice and stays a briefing.
# The bidder's own proposal presentation is not the orderer's briefing: it is scored (평정, 최저점), held after the
# proposal is in, conducted by the bidder, or requested by the orderer when needed.
PRESENTATION = re.compile(r'제\s*안\s*(서\s*)?(설\s*명\s*회?|발\s*표).{0,40}(평\s*가|발\s*표|순\s*서|심\s*사)|발\s*표\s*(회|평\s*가|순\s*서|시\s*간)|평\s*가\s*위\s*원|정\s*성\s*평\s*가'
                          r'|제\s*안\s*서?\s*(를\s*)?(제\s*출|접\s*수)\s*(한\s*)?(후|이\s*후|뒤)|제\s*안\s*(업\s*체|사|자)\s*(는|가|은).{0,40}설\s*명\s*회?\s*를?\s*(실\s*시|개\s*최|하\s*여\s*야)'
                          r'|평\s*정|최\s*저\s*점|필\s*요\s*(시|한\s*경\s*우)[^.。]{0,40}설\s*명\s*회?\s*를?\s*요\s*구|기\s*술\s*능\s*력\s*평\s*가\s*를?\s*위\s*하'
                          r'|(심\s*사|평\s*가|선\s*정)\s*(을|를)?\s*거\s*쳐[^.。]{0,20}설\s*명\s*회|설\s*명\s*회\s*참\s*가\s*자\s*격\s*을\s*제\s*한'
                          r'|(평\s*가|심\s*사)\s*결\s*과[^.。]{0,40}설\s*명\s*회|상\s*위\s*\d+\s*개\s*(업\s*체|사)[^.。]{0,30}설\s*명\s*회')
# Who may attend (대표·위임장) is not a duty to attend; resident hearings and reports are contract work, not bid briefings.
ATTENDEE_ONLY = re.compile(r'참\s*(가|석)\s*자\s*(는|의).{0,25}(대\s*표|위\s*임\s*장|대\s*리\s*인|재\s*직)')
NON_BID_BRIEFING = re.compile(r'주\s*민\s*설\s*명\s*회|공\s*청\s*회|보\s*고\s*회|결\s*과\s*설\s*명\s*회')
BID_BRIEFING = re.compile(r'현\s*장\s*설\s*명|과\s*업\s*설\s*명|사\s*업\s*설\s*명|요\s*청\s*서?\s*설\s*명|입\s*찰\s*설\s*명')


# A condition on documents ("구비서류가 누락되면 현장설명·입찰참가 자격이 없다") and a quoted legal provision that merely
# permits the practice ("시행령 제43조제5항에 따라 … 참가하게 할 수 있다") impose no attendance.
# Events the contractor attends while performing ("사업설명회/워크숍 : 담당자 … 주관 참석") are contract duties.
WORK_DUTY = re.compile(r'워\s*크\s*숍|주\s*관\s*(하\s*는\s*)?[^.。]{0,10}참\s*석')
DOC_CONDITION = re.compile(r'서\s*류[^.。]{0,24}(누\s*락|미\s*비|미\s*제\s*출|맞\s*지\s*않)')
PERMISSIVE_LAW = re.compile(r'제\s*\d+\s*조[^.。]*할\s*수\s*있(다|음|습\s*니\s*다)')


# The proposers' own session ("제안업체가 5개 이하인 경우: 모든 제안업체 설명회 참가") is the presentation stage unless the
# line names the orderer's briefing.
PROPOSER_SESSION = re.compile(r'제\s*안\s*(참\s*여\s*)?업\s*체[^.。]{0,20}설\s*명\s*회\s*(에\s*)?(참\s*가|참\s*석|실\s*시)')


def bid_briefing(ln):
    t = ln.text
    if PRESENTATION.search(t) or ATTENDEE_ONLY.search(t) or DOC_CONDITION.search(t) or PERMISSIVE_LAW.search(t) or WORK_DUTY.search(t):
        return False
    if PROPOSER_SESSION.search(t) and not BID_BRIEFING.search(t):
        return False
    return not (NON_BID_BRIEFING.search(t) and not BID_BRIEFING.search(t))


def v22(b):
    if not b.meta.negotiation:
        return None
    lines = [ln for ln in lines_where(b, 'brief', 참석='참석해야 입찰·제안 가능') if ln.sec != 'EVAL' and bid_briefing(ln)]
    return lines[0] if lines else None


NO_BRIEF = re.compile(r'생략|미\s*개최|개최\s*(하지\s*)?않|(설명회|설명)\s*(는|은)?\s*[:：]?\s*(없음|없습니다|미개최)|해당\s*없음|미\s*실시|실시\s*하지\s*않|(으로|로)\s*갈음')
DEADLINE = re.compile(r'제\s*안\s*서.{0,24}(제\s*출|접\s*수|마\s*감)|(제\s*출|접\s*수)\s*마\s*감')


def briefing_date(b):
    year = b.meta.posted.year if b.meta.posted else None
    for ln in b.cands.get('brief', []):
        r = b.read('brief', ln)
        if r.get('참석') == '설명회 아님(제안 발표 등)' or not bid_briefing(ln):
            continue
        window = b.notice.window(ln.i, 0, 3)
        text = ' '.join(w.text for w in window)
        if NO_BRIEF.search(ln.text):
            continue
        found = dates.find(text, year)
        if found:
            return found[0][0], ln
    return None, None


def proposal_deadline(b):
    year = b.meta.posted.year if b.meta.posted else None
    best = None
    for ln in b.notice.lines:
        if not DEADLINE.search(ln.text):
            continue
        window = ' '.join(w.text for w in b.notice.window(ln.i, 0, 2))
        found = [d for d, _ in dates.find(window, year)]
        if found:
            d = max(found)
            best = d if best is None or d > best else best
    return best


def v23(b):
    if not (b.meta.local and b.meta.negotiation):
        return None
    brief, ln = briefing_date(b)
    if brief is None:
        return None
    P = b.meta.P or 0
    need = 40 if P >= 10 * EOK else (20 if P >= EOK else 10)
    deadline = proposal_deadline(b)
    if deadline is not None and deadline > brief and (deadline - brief).days < need:
        return ln
    if b.meta.posted is not None and brief > b.meta.posted and (brief - b.meta.posted).days < 7:
        return ln
    return None


# ---------------------------------------------------------------- v24: notice vs 나라장터 input on the same field
TAG = re.compile(r'\((수의계약|제한경쟁|일반경쟁|지명경쟁)[·ㆍ・]\s*(\d+(?:천만|억)원)(미만|이상)\)')
BANDS = [('2천만원', 2e7), ('5천만원', 5e7), ('1억원', 1e8), ('3억원', 3e8), ('10억원', 1e9), ('50억원', 5e9), ('100억원', 1e10)]
# A field label, then its value (table cells separated by "|" included).
FIELD_SEP = r'[\s:：|]*(?:금\s*)?[₩￦]?\s*'
EST_LINE = re.compile(r'추\s*정\s*가\s*격' + FIELD_SEP + r'(\d[\d,]{3,})\s*원')
BUDGET_LINE = re.compile(r'(배\s*정\s*예\s*산|사\s*업\s*예\s*산|예\s*산\s*액|소\s*요\s*예\s*산|예\s*산\s*금\s*액)\s*(?:금\s*액)?' + FIELD_SEP + r'(\d[\d,]{3,})\s*원')
METHOD_FIELD = re.compile(r'(계\s*약\s*방\s*법|입\s*찰\s*방\s*법|계\s*약\s*방\s*식|입\s*찰\s*방\s*식)\s*[:：|]?\s*([^\n]{0,85})')
QUOTATION = re.compile(r'소\s*액\s*수\s*의|수\s*의\s*견\s*적|견\s*적\s*(제\s*출|입\s*찰)')
STATED_AMOUNT = re.compile(r'(\d{1,3}(?:,\d{3})+|\d{5,})\s*원')


def band_ok(value, label, side):
    if value is None:
        return None
    edges = dict(BANDS)
    edge = edges.get(label)
    if edge is None:
        return None
    if side == '이상':
        return value >= edge
    lower = 0.0
    for name, e in BANDS:
        if e == edge:
            break
        lower = e
    return lower <= value < edge


def v24_title(b):
    for ln in b.notice.notice_lines()[:80]:
        m = TAG.search(ln.text)
        if not m:
            continue
        method, label, side = m.group(1), m.group(2), m.group(3)
        if b.meta.method and method != b.meta.method:
            return ln
        oks = [band_ok(v, label, side) for v in (b.meta.P, b.meta.B) if v is not None]
        if oks and not any(oks):
            return ln
        return None
    return None


def v24_transposed(b):
    # A stated amount whose digits permute the registered 배정예산 or 추정가격 is that field mistyped (a transposition),
    # so the notice and 나라장터 disagree on it (dev DEV-029: 39,730,000 registered, 37,930,000 stated).
    registered = {str(int(v)) for v in (b.meta.B, b.meta.P) if v}
    for ln in b.notice.notice_lines():
        for m in STATED_AMOUNT.finditer(ln.text):
            d = m.group(1).replace(',', '')
            if any(len(d) == len(r) and d != r and sorted(d) == sorted(r) for r in registered):
                return ln
    return None


def v24_amount(b):
    for ln in b.notice.notice_lines()[:150]:
        t = ln.text
        # Same field only: a value far from the registered one (unit price, one lot) is a different amount.
        m = EST_LINE.search(t)
        if m and b.meta.P is not None and '단가' not in t:
            v = float(m.group(1).replace(',', ''))
            if v >= 1e5 and abs(v - b.meta.P) > 1.5 and 0.5 <= v / b.meta.P <= 2:
                return ln
        m = BUDGET_LINE.search(t)
        if m and b.meta.B is not None and '단가' not in t:
            v = float(m.group(2).replace(',', ''))
            if v >= 1e5 and abs(v - b.meta.B) > 1.5 and abs(v - (b.meta.P or 0)) > 1.5 and 0.5 <= v / b.meta.B <= 2:
                return ln
    return None


def stated_methods(value):
    v = re.sub(r'\s', '', re.sub(r'[(（]\s*(단가|총액)\s*[)）]', '', value))
    found = {k for k in ('제한경쟁', '일반경쟁', '지명경쟁') if k in v}
    if re.search(r'수의', v):
        found.add('수의계약')
    return found


def v24_method(b):
    # The contract-method field ("계약방법", "입찰방법 | 일반경쟁입찰") read wherever it is stated; every statement that names
    # one method must agree, and that method must differ from the registered one.
    if not b.meta.method:
        return None
    said, first = set(), None
    lines = b.notice.notice_lines()[:120]
    for ln in lines:
        for m in METHOD_FIELD.finditer(ln.text):
            found = stated_methods(m.group(2))
            if len(found) == 1:
                said |= found
                first = first or ln
    if len(said) != 1 or b.meta.method in said:
        return None
    # Quotation wording (수의 견적, 2인 이상 견적입찰, 소액수의) describes how quotes are taken, not the registered method; and a
    # notice whose own title tag carries the registered method agrees with 나라장터 on that field (dev DEV-144, DEV-191).
    if '수의계약' in said or b.meta.method == '수의계약' and (b.meta.award == '소액수의견적' or any(QUOTATION.search(ln.text) for ln in lines)):
        return None
    if any(m.group(1) == b.meta.method for ln in lines[:80] for m in TAG.finditer(ln.text)):
        return None
    return first


def v24_region(b):
    if b.meta.region_flag != 'Y' or not b.meta.region_sido:
        return None
    lines, sido, _ = region_restriction(b)
    if lines and sido and sido != b.meta.region_sido:
        return lines[0]
    return None


LICENSE_CODE = re.compile(r'(?:업종\s*코드|업종\s*번호)\s*[:：]?\s*(\d{4})(?!\d)|\((\d{4})\)|\[(\d{4})\]')


def v24_license(b):
    if b.meta.license_flag != 'Y' or not b.meta.license:
        return None
    meta_codes = set(re.findall(r'\((\d{4})\)', str(b.meta.license)))
    if not meta_codes:
        return None
    for ln in b.notice.lines:
        if ln.sec != 'QUAL' or '업종' not in ln.text:
            continue
        codes = {x for g in LICENSE_CODE.findall(ln.text) for x in g if x}
        if codes and not (codes & meta_codes):
            return ln
    return None


# The model copies the stated 예산·추정가격 and names the stated contract method; the CPU compares them with meta on the
# same field (1원 rounding, VAT-inclusive budget vs estimate, and values far from the registered one are not mismatches).
V24_MODEL_VALUES = False
METHOD_OF = {'일반경쟁': '일반경쟁', '제한경쟁': '제한경쟁', '지명경쟁': '지명경쟁', '수의계약': '수의계약'}


def _digits(v):
    v = re.sub(r'[^0-9]', '', v or '')
    return float(v) if v else None


def v24_values(b):
    if not V24_MODEL_VALUES or not b.values:
        return None
    line = next(iter(b.cands.get('values', [])), None)
    budget, estimate = _digits(b.values.get('예산')), _digits(b.values.get('추정가격'))
    B, P = b.meta.B, b.meta.P
    hit = None
    if budget and B and budget >= 1e5 and abs(budget - B) > 1.5 and abs(budget - (P or 0)) > 1.5 and 0.5 <= budget / B <= 2:
        hit = 'budget'
    if estimate and P and estimate >= 1e5 and abs(estimate - P) > 1.5 and 0.5 <= estimate / P <= 2:
        hit = 'estimate'
    said = METHOD_OF.get(b.values.get('계약방법'))
    if said and b.meta.method and said != b.meta.method:
        hit = 'method'
    if hit is None:
        return None
    return line if line is not None else True


V24_AXES = (v24_title, v24_amount, v24_transposed, v24_method, v24_region, v24_license, v24_values)


def v24(b):
    for axis in V24_AXES:
        ln = axis(b)
        if ln is not None:
            return ln
    return None


RULES = {'v1': v1, 'v2': v2, 'v3': v3, 'v4': v4, 'v5': v5, 'v6': v6, 'v7': v7, 'v8': v8, 'v9': v9, 'v10': v10,
         'v11': v11, 'v12': v12, 'v13': v13, 'v14': v14, 'v15': v15, 'v16': v16, 'v17': v17, 'v18': v18, 'v19': v19,
         'v20': v20, 'v21': v21, 'v22': v22, 'v23': v23, 'v24': v24}


def judge(b):
    """{item: (0|1, evidence text)}; evidence is an exact source line (≤500 chars) or '' for absence items."""
    out = {}
    for it in ITEMS:
        hit = RULES[it](b)
        if hit is None:
            out[it] = (0, '')
            continue
        ev = '' if (it in ABSENCE or hit is True) else evidence(hit.text)
        out[it] = (1, ev)
    return out


def evidence(text):
    t = (text or '').strip()
    if len(t) > 500:
        t = t[:500].rstrip()
    while t and t[0] in '=+@':
        t = t[1:].lstrip()
    return t
