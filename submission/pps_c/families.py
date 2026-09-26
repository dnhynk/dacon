"""Item families: CPU candidate lines, the grammar-bound reading question, and CPU default readings.

A family call shows the candidate lines with two lines of context on each side (section names marked) and asks, for
every candidate line, only typed fields (role, subject, timing, …). It never asks whether a line is a violation.
The CPU default of each field (section heading + wording) is what the judge uses when the model gave no reading.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from . import regions, switches

UNKNOWN = '불명'
SECTION_NAME = {'QUAL': '참가자격', 'DOCS': '제출서류', 'JV': '공동계약', 'BRIEF': '설명회', 'OVERVIEW': '개요',
                'EVAL': '낙찰·평가', 'BID': '입찰·개찰', 'NOTE': '유의사항·기타', 'OTHER': '기타', 'TOP': '머리',
                'ATTACH': '첨부'}
LINE_CHARS = 360
DOC_LIST = re.compile(r'(\d+\s*부|사본|원본)\s*[\.。]?\s*$|\d+\s*부\s*[\(（]|제출\s*서류|구비\s*서류')
QUAL_CUE = re.compile(r'참\s*가\s*자\s*격|자격\s*요건|기업\s*형태|입찰\s*참가|참가\s*가능|참여\s*가능|자\s*이어야|업체이어야|자로서|자에\s*한|업체에\s*한|[한\s]정|제한')


@dataclass
class Field:
    name: str
    values: tuple
    help: str
    pattern: str | None = None      # a copied value (digits) instead of an enum choice

    def schema(self):
        if self.pattern:
            return {'type': 'string', 'pattern': self.pattern, 'maxLength': 20}
        return {'type': 'string', 'enum': list(self.values)}

    def accepts(self, v):
        if self.pattern:
            return isinstance(v, str) and re.fullmatch(self.pattern, v) is not None
        return v in self.values


@dataclass
class Family:
    name: str
    items: tuple
    title: str
    guide: str
    fields: tuple
    max_lines: int = 12
    reason: bool = True
    context: int = 2
    selector: object = None
    default: object = None
    meta_lines: tuple = ('업무구분', '계약방법', '낙찰방법')


def shown(text):
    t = text.strip()
    return t if len(t) <= LINE_CHARS else t[:LINE_CHARS] + '…'


def rank(notice, lines):
    """Candidate order: 참가자격 section first, then the 공고문, then attachments; stable within a group."""
    def key(ln):
        return (0 if ln.sec == 'QUAL' else 1, 0 if ln.doc_type == '공고문' else 1, ln.i)
    return sorted(lines, key=key)


def pick(notice, pattern, cap, pred=None):
    hits = [ln for ln in notice.lines if ln.text.strip() and pattern.search(ln.text) and (pred is None or pred(ln))]
    seen, out = set(), []
    for ln in rank(notice, hits):
        norm = re.sub(r'\s+', '', ln.text)
        if norm in seen:
            continue
        seen.add(norm)
        out.append(ln)
        if len(out) >= cap:
            break
    return sorted(out, key=lambda ln: ln.i)


# ---------------------------------------------------------------- region (v5–v8)
REGION_ANY = re.compile(r'\[(?:등록)?지역:|특별시|광역시|특별자치|[가-힣]도(?=[\s,·ㆍ/\)\]]|$)|서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주')
REGION_CUE = re.compile(r'소재|주된\s*영업소|본점|본사|영업소|사업장|둔\s*(자|업체|사업자|법인|곳)|두고|두어야|관내|지역\s*업체|지역\s*제한|지역제한|등록된\s*(자|업체)|있는\s*(자|업체)')
BIDDER_LOC = re.compile(r'(주된\s*영업소|본점|본사|사업장|영업소)\s*(의)?\s*(소재지?|가|를|이|는|는\s*반드시)?|소재(지|하는|한|하고)\s*(업체|자|사업자|법인)|에\s*(두고|둔|소재)|관내\s*(업체|사업자)|지역\s*업체')
PLACE = re.compile(r'납품\s*(장소|지)|장\s*소|위\s*치|현\s*장|행사\s*장|배송|설치\s*장소|수행\s*장소|용역\s*위치|사업\s*(대상)?지|개최')


def region_select(notice):
    if switches.AUDIT_FIXES3 and switches.FIX3_PROMPTS:
        # Audit R3-C: a 공고문 qualification clause naming the bidder's location is a candidate even when REGION_ANY misses its
        # region words ("…본사업장 소재지가 수도권 또는 충청북도인 업체", "…경상북도에 두고 있는 자"). Changes prompts.
        return pick(notice, REGION_CUE, 12, lambda ln: REGION_ANY.search(ln.text) is not None or '지역제한' in ln.text.replace(' ', '')
                    or ln.doc_type == '공고문' and ln.sec == 'QUAL' and BIDDER_LOC.search(ln.text) is not None
                    and bool(regions.mentions(ln.text)['sido']))
    return pick(notice, REGION_CUE, 12, lambda ln: REGION_ANY.search(ln.text) is not None or '지역제한' in ln.text.replace(' ', ''))


def region_default(notice, ln):
    t = ln.text
    role = '참가자격' if (ln.sec == 'QUAL' or QUAL_CUE.search(t)) and not DOC_LIST.search(t) else (
        '제출서류' if ln.sec == 'DOCS' or DOC_LIST.search(t) else ('평가·가점' if ln.sec == 'EVAL' else '안내·기타'))
    target = '입찰자 소재지 제한' if BIDDER_LOC.search(t) else ('납품·수행 장소' if PLACE.search(t) else '기타')
    return {'역할': role, '대상': target}


REGION = Family(
    name='region', items=('v5', 'v6', 'v7', 'v8'), title='지역 표기',
    guide='각 줄에 나오는 지역이 무엇을 뜻하는지 분류한다.',
    fields=(
        Field('역할', ('참가자격', '제출서류', '평가·가점', '안내·기타', UNKNOWN),
              '참가자격 = 입찰·견적에 참가하려면 갖춰야 하는 요건으로 적힘. 제출서류 = 제출 서류 목록의 항목. 평가·가점 = 평가표·배점·가점. 안내·기타 = 그 밖의 설명'),
        Field('대상', ('입찰자 소재지 제한', '납품·수행 장소', '발주기관 소재', '기타', UNKNOWN),
              '입찰자 소재지 제한 = 입찰자의 본점·주된 영업소·사업장이 그 지역에 있어야 함. 납품·수행 장소 = 물품 납품지, 용역 수행지, 행사장, 공사 위치. 발주기관 소재 = 발주기관·수요기관의 주소나 관할'),
    ),
    selector=region_select, default=region_default)


# ---------------------------------------------------------------- performance record (v2–v4, v8)
PERF_CUE = re.compile(r'실\s*적|수행\s*경험|납품\s*(경험|이력)|이행\s*경험|수행한\s*경험')
# A line that asks for a record (not one that only mentions 실적 as a word, e.g. 등록·실적 등에 의한 판단기준일).
PERF_REQ = re.compile(r'실\s*적\s*(이|을)?\s*(있는|있어야|보유|이상|가진|갖춘|갖추|증명|을\s*가진)|(수행|납품|이행|준공|계약)\s*(한|하였던|된)?\s*(단일\s*)?실\s*적'
                      r'|실\s*적.{0,20}(이상|억\s*원|만\s*원|건)|(경험|실적)이\s*있는\s*(자|업체)|(억\s*원|만\s*원|원)\s*이상.{0,30}실\s*적')
ORDERER_LIMIT = re.compile(r'공공\s*기관|국가\s*기관|정부\s*(기관|부처)|관공서|지방\s*자치\s*단체|지자체|공기업|공사\s*·?\s*공단|[\[(]수요기관|대학\s*병원|국공립|국립|공립|중앙\s*행정|광역\s*자치|기초\s*자치|교육청|학교')
ORDERER_OPEN = re.compile(r'민간|기업체|또는\s*민간|공공\s*(기관)?\s*(또는|및|·)\s*민간|발주처\s*(구분|불문|무관)')


# Audit R2-A/R2-E: a firm-subject record requirement without the word 실적 ("…제작 경험이 있는 업체", "…을 수행한 자",
# "5억원 이상의 동종 용역을 1건 이상 수행한 업체") is a performance requirement too; the firm subject keeps staff careers out.
PERF_FIRM = re.compile(r'(제\s*작|운\s*영|수\s*행|납\s*품|시\s*공|개\s*발|구\s*축|공\s*급|대\s*행|위\s*탁|이\s*행|준\s*공)\s*(한|하였던)?\s*경\s*험\s*이\s*'
                       r'(있\s*는|있\s*어\s*야|보\s*유\s*한)\s*(업\s*체|자|법\s*인|사\s*업\s*자|기\s*관)'
                       r'|을\s*수\s*행\s*한\s*(업\s*체|자|법\s*인)(?!\s*명)'
                       r'|(억\s*원?|천\s*만\s*원?|\d{1,3}(?:,\d{3}){2,}\s*원)\s*이\s*상[^.。]{0,50}(수\s*행|납\s*품|준\s*공|이\s*행|완\s*료)\s*(한|하였던|된)\s*(업\s*체|자|법\s*인)')
PERF_CUE_FIX2 = re.compile(PERF_CUE.pattern + '|' + PERF_FIRM.pattern)
# Audit R3-A: "…컨설팅 유경험 업체" states the firm's experience too (유경험자 alone is staff). Changes prompts.
FIRM_EXPERIENCE = r'유\s*경\s*험\s*(업\s*체|사\s*업\s*자|법\s*인|기\s*관)'


def perf_select(notice):
    cue = PERF_CUE_FIX2 if switches.AUDIT_FIXES2 else PERF_CUE
    if switches.AUDIT_FIXES3 and switches.FIX3_PROMPTS:
        cue = re.compile(cue.pattern + '|' + FIRM_EXPERIENCE)
    return pick(notice, cue, 12)


def perf_default(notice, ln):
    from . import amounts
    t = ln.text
    if ln.sec == 'DOCS' or DOC_LIST.search(t):
        role = '제출서류'
    elif ln.sec == 'EVAL' or re.search(r'배점|점수|가점|평가|\|\s*\d+\s*\|', t):
        role = '평가·가점'
    elif (PERF_REQ.search(t) or switches.AUDIT_FIXES2 and PERF_FIRM.search(t)
          or switches.AUDIT_FIXES3 and switches.FIX3_PROMPTS and re.search(FIRM_EXPERIENCE, t)) and (ln.sec == 'QUAL' or QUAL_CUE.search(t)):
        role = '참가자격'
    else:
        role = '안내·기타'
    if amounts.ratios(t):
        form = '기초·추정 대비 배수'
    elif any(m.value >= 1e6 for m in amounts.money(t)):
        form = '금액'
    elif re.search(r'\d+\s*(건|회|년|개)\s*이상|경험|실적이\s*있는|실적을\s*보유', t):
        form = '건수·경험'
    else:
        form = '기타'
    orderer = '특정 발주기관만' if ORDERER_LIMIT.search(t) and not ORDERER_OPEN.search(t) else '한정 없음'
    return {'역할': role, '형태': form, '발주처': orderer}


PERF = Family(
    name='perf', items=('v2', 'v3', 'v4', 'v8'), title='실적 요건',
    guide='각 줄이 실적(과거 수행·납품 경험)을 어떻게 다루는지 분류한다.',
    fields=(
        Field('역할', ('참가자격', '평가·가점', '제출서류', '안내·기타', UNKNOWN),
              '참가자격 = 그 실적이 있어야 입찰·견적에 참가할 수 있음. 평가·가점 = 평가표·배점. 제출서류 = 실적증명서 등 서류 목록. 안내·기타 = 계약 후 실적 관리 등 그 밖의 설명'),
        Field('형태', ('금액', '기초·추정 대비 배수', '건수·경험', '기타', UNKNOWN),
              '금액 = "5억원 이상"처럼 금액으로 요구. 기초·추정 대비 배수 = "기초금액의 130%"처럼 비율로 요구. 건수·경험 = 금액 없이 건수나 경험만'),
        Field('발주처', ('특정 발주기관만', '한정 없음', UNKNOWN),
              '특정 발주기관만 = 국가기관·공공기관·지자체·대학병원·학교처럼 특정 종류의 발주처나 납품·고객 기관을 상대로 한 실적만 인정(예: "공공기관이 발주한", "대학병원에 납품한", "중고등학교 학생 대상"). '
              '한정 없음 = 발주처·고객을 한정하지 않거나 민간 실적도 인정(일의 분야만 정한 "유사 용역 실적"은 한정 없음)'),
    ),
    selector=perf_select, default=perf_default)


# ---------------------------------------------------------------- institution type and holdings (v1)
INST_TYPE = re.compile(r'(대학|산학\s*협력단|연구\s*기관|연구소|연구원|공공\s*기관|협회|조합|재단|비영리|사회적\s*기업|협동\s*조합|국공립|학교|병원|법인|단체)')
INST_ONLY = re.compile(r'만\s*(이\s*)?(입찰|견적|제안)?\s*(참여|참가|응찰|신청)?\s*(가능|할\s*수)|에\s*한(하여|함|한다)|으로\s*한정|로\s*한정|만\s*해당')
FACILITY = re.compile(r'(센터|시설|장비|차량|공장|사업장|창고|연구실|실험실|장치|설비|기계|버스|대수|\d+\s*대).{0,24}(보유|갖춘|갖추|있는|구비|확보|소유)')
PERSONNEL = re.compile(r'\d+\s*(명|인)\s*이상|(인력|기술자|전문가|기술\s*인력|상시\s*근로자|직원).{0,20}(보유|고용|확보|갖춘|이상)')
LICENSE = re.compile(r'면허|업종|등록|신고|허가|인가|지정|자격증')
GENERAL = re.compile(r'부정당|제재|결격|조세\s*포탈|전자\s*입찰|이용자\s*등록|지문\s*인식|공인\s*인증|시행령\s*제\s*1[23]\s*조|시행규칙\s*제\s*14\s*조|입찰\s*참가\s*자격\s*(등록|을\s*갖춘)|청렴|담합')
INST_EXTRA = re.compile(r'만\s*(입찰\s*|견적\s*|제안\s*)?(참여|참가|응찰)|참여\s*가능\s*(기관|대상)|보유(한|하고\s*있는)\s*(자|업체)')


def inst_select(notice):
    qual = [ln for ln in notice.lines if ln.sec == 'QUAL' and ln.text.strip() and len(ln.text.strip()) > 3]
    extra = [ln for ln in notice.lines if ln.sec != 'QUAL' and ln.text.strip() and INST_EXTRA.search(ln.text)]
    lines = qual + extra
    if len(lines) > 40:
        scored = sorted(lines, key=lambda ln: (0 if (INST_TYPE.search(ln.text) or FACILITY.search(ln.text) or PERSONNEL.search(ln.text)) else 1,
                                               1 if GENERAL.search(ln.text) else 0, ln.i))
        lines = scored[:40]
    if not lines:
        lines = [ln for ln in notice.lines if ln.doc_type == '공고문' and re.search(r'자격|참가|참여', ln.text)][:20]
    return sorted({ln.i: ln for ln in lines}.values(), key=lambda ln: ln.i)


def inst_default(notice, ln):
    t = ln.text
    if GENERAL.search(t):
        kind = '결격·일반 자격'
    elif INST_TYPE.search(t) and INST_ONLY.search(t):
        kind = '기관 유형 한정'
    elif FACILITY.search(t):
        kind = '시설·장비 보유'
    elif PERSONNEL.search(t):
        kind = '인력 보유'
    elif re.search(r'실\s*적', t):
        kind = '실적'
    elif re.search(r'소재|영업소|본점', t):
        kind = '지역'
    elif re.search(r'중소\s*기업|소기업|소상공인', t):
        kind = '기업 규모'
    elif re.search(r'직접\s*생산', t):
        kind = '직접생산'
    elif LICENSE.search(t):
        kind = '면허·업종·등록'
    else:
        kind = '기타'
    role = '참가자격' if ln.sec == 'QUAL' and not DOC_LIST.search(t) else ('제출서류' if DOC_LIST.search(t) else '안내')
    need = '과도하거나 과업과 무관' if kind == '기관 유형 한정' or (kind in ('시설·장비 보유', '인력 보유') and re.search(r'전국|모든|\d{2,}\s*(명|인|대)', t)) else '해당 없음'
    return {'요건': kind, '역할': role, '필요성': need}


INST = Family(
    name='inst', items=('v1',), title='참가자격 요건',
    guide='참가자격 절의 각 줄이 어떤 종류의 요건인지 분류한다.',
    fields=(
        Field('요건', ('기관 유형 한정', '기관 유형 대안', '시설·장비 보유', '인력 보유', '면허·업종·등록', '인증·확인서',
                      '지역', '실적', '기업 규모', '직접생산', '결격·일반 자격', '기타', UNKNOWN),
              '기관 유형 한정 = 참가자를 대학·연구기관·공공기관·특정 협회 회원 등 특정 종류의 기관으로만 한정해 일반 사업자는 참가할 수 없음(특정 종류를 빼는 배제 조항, 중소기업·소프트웨어사업자 같은 규모·업종 요건, 발주기관 이름은 아님). '
              '기관 유형 대안 = 특정 기관(비영리법인·협동조합 등)도 참가할 수 있다는 뜻이나 그 기관의 조건 설명일 뿐 일반 업체도 참가 가능. '
              '법령상 그 일을 할 수 있는 자가 그 유형뿐인 경우(결산·회계감사의 회계법인, 금고·주거래은행의 금융기관, 보험의 보험회사)는 면허·업종·등록. '
              '시설·장비 보유 = 특정 시설·센터·장비·차량을 보유해야 함. 인력 보유 = 일정 수의 인력·기술자를 보유해야 함. 면허·업종·등록 = 법령상 면허·업종 등록·신고. '
              '결격·일반 자격 = 부정당업자 제재, 전자입찰 등록 같은 공통 자격'),
        Field('역할', ('참가자격', '제출서류', '평가·가점', '계약 후 이행 조건', '안내', UNKNOWN),
              '참가자격 = 입찰·견적 참가 요건. 계약 후 이행 조건 = 낙찰 뒤 과업 수행 중에 갖추면 되는 조건'),
        Field('필요성', ('과업 수행의 기본 수단', '과도하거나 과업과 무관', '해당 없음', UNKNOWN),
              '시설·장비·인력·기관 요건일 때만: 과업 수행의 기본 수단 = 과업을 하려면 당연히 필요한 최소 수단. 과도하거나 과업과 무관 = 과업 규모에 비해 과도하거나(전국 센터, 수십 명 인력) 과업과 직접 관련 없는 요건. 그 밖의 요건은 해당 없음'),
    ),
    max_lines=40, reason=False, context=0, selector=inst_select, default=inst_default)


# ---------------------------------------------------------------- enterprise size (v11, v13–v18)
SIZE_CUE = re.compile(r'중소\s*기업|중\s*·\s*소\s*기업|중기업|소기업|소상공인|판로\s*지원|중소기업자\s*간|기업\s*형태|기업\s*규모')
# Names of laws, notices and agencies that contain size words without restricting anyone.
DOT = r'\s*[·ㆍ・‧․•･/,]?\s*'
SIZE_NAMES = re.compile(r'중소\s*기업\s*제품\s*구매\s*촉진\s*및\s*판로\s*지원에\s*관한\s*법률(\s*시행령|\s*시행규칙)?|판로\s*지원\s*법(\s*시행령)?'
                        r'|중소\s*기업\s*기본법(\s*시행령)?|중소\s*기업\s*법|중소\s*기업\s*창업\s*지원법|소상공인\s*(보호\s*및\s*지원에\s*관한\s*법률|기본법)(\s*시행령)?'
                        r'|중소\s*기업\s*범위\s*및\s*확인에\s*관한\s*규정|중' + DOT + r'소\s*기업' + DOT + r'소상공인\s*및\s*장애인\s*기업\s*확인\s*요령'
                        r'|중소\s*기업\s*(제품\s*)?공공\s*구매\s*(종합\s*)?정보망|중소\s*벤처\s*기업부|(지방\s*)?중소\s*기업청|중소\s*기업\s*현황\s*정보\s*시스템'
                        r'|중소\s*기업\s*제품|중소\s*기업\s*자\s*간\s*경쟁\s*제품|중소\s*기업\s*공공\s*구매|벤처\s*기업\s*육성에\s*관한\s*특별법'
                        r'|중소\s*기업자?\s*와의\s*우선\s*조달\s*계약(에\s*대한\s*예외)?|중소\s*기업자?\s*로\s*간주되는')
SMALL_ONLY = re.compile(r'(?<![중·ㆍ・‧․•･/])소\s*기업|소상공인')
SME_ALL = re.compile(r'중소\s*기업|중' + DOT + r'소\s*기업|중\s*기업')
SMALL_CERT = re.compile(r'소\s*기업' + DOT + r'소상공인\s*(등\s*)?확인서|소상공인\s*확인서')  # checked after SME_CERT
SME_CERT = re.compile(r'중' + DOT + r'소\s*기업\s*(' + DOT + r'|\s*(또는|및)\s*|[\(（]\s*)?(소상공인\s*[\)）]?\s*)?(등\s*)?확인서'
                      r'|중\s*기업\s*(,|，|·|ㆍ|및|또는|/)\s*소\s*기업[^확]{0,14}확인서')
NO_MEDIUM = re.compile(r'중\s*기업\s*[×xX]|중\s*기업\s*(은|는)?\s*(제외|불가|참여\s*(불가|할\s*수\s*없))')


CERT_WORD = re.compile(r'확\s*인\s*서')
CERT_CUT = re.compile(r'으로서|로서|으로|(자|체)\s*로\s|발급된|발급한|따라|[,，<‘“「\(（]')
# A bracketed title that names a law, decree or rule (「중소기업기본법」, ｢중소기업 범위 및 확인에 관한 규 · 정｣) never names the
# bidder, even when stray spaces or dots keep SIZE_NAMES from matching it; a bracketed certificate name is kept.
TITLE_BRACKET = re.compile(r'[「｢『⌜]([^」｣』⌟]{1,80})[」｣』⌟]')
LAW_SUFFIX = re.compile(r'(법|법률|시행령|시행규칙|규정|규칙|요령|고시|지침|기준|예규|훈령|조례)$')


def strip_law_titles(text):
    return TITLE_BRACKET.sub(lambda m: ' ' if LAW_SUFFIX.search(re.sub(r'[\s·ㆍ・‧․•･]', '', m.group(1))) else m.group(0), text)


# Audit C: 연구소기업 (연구개발특구법) is no enterprise-size class, and a size word inside a preference period
# ("(단, 소기업·소상공인·창업기업의 경우 7년 이내)") restricts nobody.
RESEARCH_FIRM = re.compile(r'연\s*구\s*소\s*기\s*업')
PREFERENCE_PAREN = re.compile(r'[\(（][^)）]{0,40}(소\s*기\s*업|소\s*상\s*공\s*인|창\s*업\s*기\s*업|중\s*소\s*기\s*업)[^)）]{0,30}(의\s*경\s*우|은|는)'
                              r'[^)）]{0,20}?\d+\s*년[^)）]{0,20}[\)）]')


# Audit R2-B/R2-C: typography and titles that restrict nobody. Dot variants (⸱ ∙ ⋅ …) are the middle dot and a space inside
# 기업 ("중・소기 업") is none; an article title in parentheses ("제2조의2(중소기업자의 우선조달계약)"), the 창업자 definition
# ("중소기업을 창업하여"), notes on 판단기준일·유효기간 and commas between certificate classes ("중기업,소기 업,소상공인확인서")
# name no class. The unified certificate "중소기업 ·소기업(소상공인)확인서" is the SME certificate; "중소기업확인서(소기업·
# 소상공인)" is the small-business one. size_normal is applied to the clause the judge classes (judge.size_class), not to
# candidate selection.
DOT_VARIANTS = str.maketrans({c: '·' for c in '⸱∙⋅•‧・･․ㆍ'})
KI_EOP = re.compile(r'((?:중\s*·?\s*)?소)\s*기\s+업')
ARTICLE_TITLE = re.compile(r'제\s*\d+\s*조(?:\s*의\s*\d+)?\s*[\(（][^)）]{0,40}[\)）]')
STARTUP_DEF = re.compile(r'중\s*소\s*기\s*업\s*을\s*창\s*업\s*하')
NOTE_PAREN = re.compile(r'[\(（][^)）]{0,80}(판\s*단\s*기\s*준\s*일|유\s*효\s*기\s*간)[^)）]{0,80}[\)）]')
# Audit R3-C: a 판단기준일 note runs up to ≈200 characters ("(입찰참가자격의 판단기준일은 중, 소기업 또는 소상공인 확인서의 경우 …
# 입니다.)"), and "중, 소기업" is the 중·소기업 of the unified certificate.
NOTE_PAREN3 = re.compile(r'[\(（][^)）]{0,200}(판\s*단\s*기\s*준\s*일|유\s*효\s*기\s*간)[^)）]{0,200}[\)）]')
MID_COMMA = re.compile(r'중\s*[,，]\s*(?=소\s*기\s*업)')
CLASS_COMMA = re.compile(r'(중\s*기\s*업|중\s*·?\s*소\s*기\s*업|소\s*기\s*업|소\s*상\s*공\s*인)\s*[,，]\s*(?=중\s*기\s*업|중\s*·?\s*소\s*기\s*업|소\s*기\s*업|소\s*상\s*공\s*인)')
SME_CERT2 = re.compile(SME_CERT.pattern + r'|중\s*소\s*기\s*업\s*[·ㆍ]?\s*소\s*기\s*업\s*[\(（]?\s*소\s*상\s*공\s*인\s*[\)）]?\s*확\s*인\s*서')
# A certificate qualified by its class in parentheses ("중소기업확인서(소기업·소상공인)") is that class's certificate.
CERT_CLASS_PAREN = re.compile(r'(?:중\s*·?\s*소\s*기\s*업\s*)?확\s*인\s*서\s*[\(（]\s*(소\s*기\s*업\s*[·,]?\s*소\s*상\s*공\s*인|소\s*상\s*공\s*인)\s*[\)）]')


def size_normal(text):
    t = CERT_CLASS_PAREN.sub(r'\1 확인서', text.translate(DOT_VARIANTS))
    t = KI_EOP.sub(r'\1기업', MID_COMMA.sub('중·', t) if switches.AUDIT_FIXES3 else t)
    t = (NOTE_PAREN3 if switches.AUDIT_FIXES3 else NOTE_PAREN).sub(' ', STARTUP_DEF.sub(' ', ARTICLE_TITLE.sub(' ', t)))
    return CLASS_COMMA.sub(r'\1·', t)


def size_words(text):
    """The size class a clause restricts to once law, agency and article names are removed: 'sme' (중기업 included),
    'small' (소기업·소상공인 only) or None.

    Order: an explicit exclusion of 중기업; the certificates the bidder must hold, each read from the words just before
    "확인서" (only 소기업·소상공인 options means small; a 중기업·중소기업 option means the certificate decides
    nothing, since 중·소기업·소상공인 확인서 is issued to every SME); then the entity the clause names."""
    t = SIZE_NAMES.sub(' ', strip_law_titles(text or ''))
    if switches.AUDIT_FIXES:
        t = PREFERENCE_PAREN.sub(' ', RESEARCH_FIRM.sub(' ', t))
    if NO_MEDIUM.search(t):
        return 'small'
    small_cert = sme_cert = False
    spans = []
    for m in CERT_WORD.finditer(t):
        lead = t[max(0, m.start() - 30):m.start()]
        # The certificate issued to every SME is named with a list separator too: "중·소기업, 소상공인 확인서".
        whole = next((c for c in (SME_CERT2 if switches.AUDIT_FIXES2 else SME_CERT).finditer(t, max(0, m.start() - 30), m.end())
                      if c.end() == m.end()), None)
        if whole:
            spans.append((whole.start(), m.end()))
            sme_cert = True
            continue
        phrase = CERT_CUT.split(lead)[-1]
        spans.append((m.start() - len(phrase), m.end()))
        if SME_ALL.search(phrase):
            sme_cert = True
        elif SMALL_ONLY.search(phrase):
            small_cert = True
    if small_cert and not sme_cert:
        return 'small'
    rest = t
    for a, z in reversed(spans):
        rest = rest[:a] + ' ' + rest[z:]
    if SME_ALL.search(rest):
        return 'sme'
    if SMALL_ONLY.search(rest):
        return 'small'
    if sme_cert:
        return 'sme'
    return 'small' if small_cert else None

EXCEPTION = re.compile(r'판로\s*지원.{0,40}(제\s*2\s*조의\s*3|예외|적용\s*(하지\s*)?(않|제외|배제))|(중소기업자?\s*(와의|간)?\s*(우선\s*조달|제한)|소기업.{0,10}제한).{0,30}(예외|적용\s*(하지\s*)?(않|제외|배제))|제\s*2\s*조의\s*3')


def size_select(notice):
    return pick(notice, SIZE_CUE, 12, lambda ln: size_words(ln.text) is not None or EXCEPTION.search(ln.text) is not None
                or re.search(r'기업\s*(형태|규모)', ln.text) is not None)


def size_default(notice, ln):
    t = ln.text
    cls = size_words(t)
    if EXCEPTION.search(t):
        role = '판로지원 예외 명시'
    elif cls is None:
        role = '안내·기타'
    elif ln.sec == 'DOCS' or DOC_LIST.search(t):
        role = '제출서류'
    elif ln.sec == 'EVAL' or re.search(r'배점|가점|점수|우대', t):
        role = '평가·가점'
    elif (ln.sec == 'QUAL' or QUAL_CUE.search(t)) and re.search(r'자|업체|기업|한함|한정|제한|소지|확인서', t):
        role = '참가자격 제한'
    else:
        role = '안내·기타'
    allowed = {'sme': '중소기업 전체', 'small': '소기업·소상공인만'}.get(cls, '기타')
    return {'역할': role, '허용 대상': allowed}


SIZE = Family(
    name='size', items=('v11', 'v13', 'v14', 'v15', 'v16', 'v17', 'v18'), title='기업 규모 표기',
    guide='각 줄이 기업 규모(중소기업·소기업·소상공인)를 어떻게 다루는지 분류한다.',
    fields=(
        Field('역할', ('참가자격 제한', '제출서류', '평가·가점', '판로지원 예외 명시', '안내·기타', UNKNOWN),
              '참가자격 제한 = 그 규모의 기업만 입찰·견적에 참가할 수 있다고 정함(해당 확인서를 소지해야 참가 가능하다는 요건 포함). 제출서류 = 서류 목록의 확인서 항목. '
              '평가·가점 = 평가·가점·우대. 판로지원 예외 명시 = 판로지원법 시행령 제2조의3 등의 사유로 중소기업 제한을 하지 않는다고 밝힘. 안내·기타 = 공고 제목·입찰방법 표시, 제품 구매 안내 등'),
        Field('허용 대상', ('중소기업 전체', '소기업·소상공인만', '기타', UNKNOWN),
              '중소기업 전체 = 중기업을 포함한 중소기업(중소기업자, 중·소기업, 중소기업 또는 소상공인). 소기업·소상공인만 = 소기업·소상공인만 허용(중기업 제외). 기타 = 규모와 무관'),
    ),
    selector=size_select, default=size_default)


# ---------------------------------------------------------------- direct production (v10, v12)
DP_CUE = re.compile(r'직접\s*생산|직생|(판로\s*지원에\s*관한\s*법률|판로지원법)[」』｣\s]*\s*(제\s*\d+\s*조\s*,?\s*)*제\s*9\s*조')


def dp_select(notice):
    return pick(notice, DP_CUE, 8)


DP_ITEM = Field('인증 품목', ('과업과 같은 종류', '과업과 다른 종류', '품목 명시 없음', UNKNOWN),
                '증명서에 적힌 세부품명이 이 공고의 구매 대상과 어떤 관계인지: 과업과 같은 종류 = 구매하는 물품·용역 자체이거나 과업의 주된 부분. '
                '과업과 다른 종류 = 구매 대상과 다른 종류의 품목(예: 연구 용역에 행사대행서비스, 운영 지원 용역에 인터넷지원개발서비스). 품목 명시 없음 = 세부품명·번호를 적지 않음')
CITED_ITEM = re.compile(r'(?<!\d)\d{10}(?!\d)|세부\s*품명|품명\s*[:：(]|서비스\s*[(\[]')


def dp_default(notice, ln):
    t = ln.text
    if ln.sec == 'DOCS' or DOC_LIST.search(t):
        role = '제출서류'
    elif (ln.sec in ('QUAL', 'TOP', 'OVERVIEW') or QUAL_CUE.search(t)) and re.search(r'소지|보유|발급|필한|갖춘|갖추|받은|있는', t):
        role = '참가자격 소지 요구'
    else:
        role = '안내·제재·기타'
    return {'역할': role, DP_ITEM.name: UNKNOWN if CITED_ITEM.search(t) else '품목 명시 없음'}


DP = Family(
    name='dp', items=('v10', 'v12'), title='직접생산확인',
    guide='각 줄이 직접생산확인(증명서)을 어떻게 다루는지 분류한다.',
    fields=(
        Field('역할', ('참가자격 소지 요구', '제출서류', '안내·제재·기타', UNKNOWN),
              '참가자격 소지 요구 = 직접생산확인증명서를 가진 자만 입찰·견적에 참가할 수 있다고 정함. 제출서류 = 서류 목록에 증명서 사본이 있을 뿐. 안내·제재·기타 = 계약 후 직접생산 위반 제재, 일반 안내'),
        DP_ITEM,
    ),
    selector=dp_select, default=dp_default)


# ---------------------------------------------------------------- maker / model designation (v9)
MODEL_WORD = re.compile(r'제조사|제조원|제조\s*회사|모델\s*명|모델\s*[:：]|상표|브랜드|동등\s*(이상|제품|품)|정품|호환|시\s*리\s*즈')
LATIN_MODEL = re.compile(r'\b[A-Z][A-Za-z]+[\s\-]?[A-Z]?\d{1,4}[A-Za-z]{0,3}\b|\b[A-Z]{2,}[\-\s]?\d{2,}[A-Za-z]*\b|\b[A-Z][a-z]+[A-Z][A-Za-z]+\b')
SPEC_NOISE = re.compile(r'^\s*(USB|HDMI|IP\d|ISO|KS|LED|LCD|CPU|RAM|SSD|HDD|GB|TB|MB|DDR|PCI|UHD|FHD|HD|LTE|5G|Wi-?Fi|DC|AC|RS)\b', re.I)


CREDIT = re.compile(r'\b(AAA|AA[+0\-]?|A[+0\-]|BBB[+0\-]?|BB[+0\-]?|B[+0\-]|CCC|CC|C|D)\b(?=[\s,/·)]|$)')


def model_select(notice):
    def ok(ln):
        if ln.sec in ('EVAL', 'DOCS') or CREDIT.search(ln.text) and '등급' in ln.text:
            return False
        if MODEL_WORD.search(ln.text):
            return True
        if ln.doc_type in ('규격서', '과업지시서', '제안요청서') or ln.sec in ('OVERVIEW', 'TOP'):
            hits = [m.group(0) for m in LATIN_MODEL.finditer(ln.text) if not SPEC_NOISE.match(m.group(0))]
            return bool(hits)
        return False
    lines = [ln for ln in notice.lines if ln.text.strip() and ok(ln)]
    lines.sort(key=lambda ln: (0 if MODEL_WORD.search(ln.text) else 1, 0 if ln.doc_type != '공고문' else 1, ln.i))
    seen, out = set(), []
    for ln in lines:
        norm = re.sub(r'\s+', '', ln.text)
        if norm not in seen:
            seen.add(norm)
            out.append(ln)
        if len(out) >= 12:
            break
    return sorted(out, key=lambda ln: ln.i)


def model_default(notice, ln):
    t = ln.text
    if re.search(r'유지\s*(보수|관리)|기존\s*(장비|제품|시스템)|호환', t):
        kind = '기존 장비 유지보수·호환'
    elif re.search(r'(제조사|모델\s*명?)\s*[:：·]', t) and LATIN_MODEL.search(t):
        kind = '구매 대상의 제조사·모델 지정'
    elif re.search(r'예\s*[\)）:]|예시|예를\s*들|등\s*$', t):
        kind = '예시·참고'
    else:
        kind = '해당 없음'
    eq = '동등 이상 허용' if re.search(r'동등\s*(이상|품|제품)', t) else '명시 없음'
    return {'성격': kind, '동등': eq}


MODEL = Family(
    name='model', items=('v9',), title='제조사·모델 표기',
    guide='규격·과업 문서의 각 줄에 나오는 제조사·모델·상표 표기의 성격을 분류한다.',
    fields=(
        Field('성격', ('구매 대상의 제조사·모델 지정', '예시·참고', '기존 장비 유지보수·호환', '일반 규격·표준 명칭', '해당 없음', UNKNOWN),
              '구매 대상의 제조사·모델 지정 = 납품·설치·사용할 물품(또는 그 핵심 부품)을 특정 제조사의 제품이나 특정 모델·시리즈·칩셋으로 정함(예: "제조사·모델명: ○○", "○○사 ○○ 시리즈일 것"). '
              '예시·참고 = "예:", "등"처럼 예로만 듦. 기존 장비 유지보수·호환 = 이미 보유한 장비를 유지보수하거나 그것과 호환되어야 함. '
              '일반 규격·표준 명칭 = USB·HDMI·KS·ISO 같은 규격 이름, 신용평가등급·인증 이름, 제조사 정품(순정품)을 쓰라는 일반 요구, 시험·측정에 쓰는 장비 이름'),
        Field('동등', ('동등 이상 허용', '명시 없음', UNKNOWN), '동등 이상 허용 = "또는 동등 이상"처럼 다른 제품도 허용한다고 적음'),
    ),
    selector=model_select, default=model_default)


# v9 read in two questions (switches.V9_READ2; audit RA): what the named maker or model is in this contract, and how the line
# asks for it. The one-question 성격 reading took serviced equipment, flights and OS names for designations of the procured item.
MODEL2_TARGET = Field(
    '대상', ('납품 물품', '과업용 장비·SW', '기존 장비', '호환 대상', '해당 없음', UNKNOWN),
    '줄에 적힌 제조사·상표·모델의 고유한 이름(모델 코드 포함)이 이 계약에서 가리키는 것. 납품 물품 = 이 계약으로 사거나 빌리거나 만들어 넘기는 '
    '물품과 그 부품. 과업용 장비·SW = 용역에서 계약자가 준비해 쓰거나 새로 설치하는 장비·SW. 기존 장비 = 발주기관이 이미 가진 장비·시스템으로 '
    '이 계약이 유지보수·수리·점검·교정·보험·운영하는 대상. 호환 대상 = 새 물품이 연결·호환되어야 하는 기존 장비·시스템. 해당 없음 = 줄에 그런 '
    '고유한 이름이 없음(재질·규격·성능·수량, "정품"·"제조사" 같은 일반 말만 있음), 또는 그 이름이 운영체제·오피스 같은 SW 플랫폼, KS·ISO·USB 같은 '
    '규격, 등급·인증, 항공편·차량번호, 기관·사람 이름, ○○○ 같은 빈칸이거나 값 없는 "모델명:"')
MODEL2_MANNER = Field(
    '방식', ('지정', '예시', '현황 목록', UNKNOWN),
    '지정 = 그 이름의 제품이어야 한다고 요구하거나, 규격표·자격 조건에 납품·설치할 제품의 이름으로 적음. 예시 = "예:", "등", "(참고)"처럼 '
    '예로만 듦. 현황 목록 = 보유 장비 현황표·기존 시스템 구성표처럼 이미 있는 것을 나열함')


def model2_default(notice, ln):
    r = model_default(notice, ln)
    target, manner = {'구매 대상의 제조사·모델 지정': ('납품 물품', '지정'), '기존 장비 유지보수·호환': ('기존 장비', UNKNOWN),
                      '예시·참고': (UNKNOWN, '예시')}.get(r['성격'], ('해당 없음', UNKNOWN))
    return {'대상': target, '방식': manner, '동등': r['동등']}


MODEL2 = Family(
    name='model2', items=('v9',), title='제조사·모델 표기(대상·방식)',
    guide='각 줄에 나오는 제조사·모델·상표 이름이 이 계약에서 무엇을 가리키는지, 그 이름을 어떻게 적었는지 분류한다.',
    fields=(MODEL2_TARGET, MODEL2_MANNER, MODEL.fields[1]),
    context=3, selector=model_select, default=model2_default, meta_lines=('업무구분', '계약방법', '세부품명번호목록'))


# ---------------------------------------------------------------- supply / tech-support pledges (v19)
PLEDGE_CUE = re.compile(r'확약\s*서|확약|공급\s*(확인|증명|협약)|기술\s*지원\s*(확약|협약|확인|증명)|협약\s*서|딜러|총판|대리점\s*(증명|확인)|제조사\s*(확인|증명|발행|발급)')
THIRD = re.compile(r'제조사|제조\s*업체|공급사|공급\s*업체|기술\s*지원\s*사|원\s*제조|총판|본사로부터|로부터')
BEFORE_BID = re.compile(r'입찰\s*(서)?\s*(제출\s*)?(마감|시|전|과\s*함께|와\s*함께)|투찰\s*(전|시)|견적\s*(서)?\s*(제출\s*)?(마감|시|전)|제안\s*서\s*(제출\s*)?(마감|시|전)|보유\s*하여야|보유하고')
AT_CONTRACT = re.compile(r'계약\s*(체결\s*)?(시|전|일|체결일)')
AFTER = re.compile(r'낙찰\s*(후|자는|자로)|계약\s*(체결\s*)?후|납품\s*(시|후)')


def pledge_select(notice):
    return pick(notice, PLEDGE_CUE, 8)


def pledge_default(notice, ln):
    t = ' '.join(x.text for x in notice.window(ln.i, 1, 1))
    issuer = '제3자(제조사·공급사·기술지원사)' if THIRD.search(ln.text) else ('입찰자 자신' if re.search(r'자체|자사|입찰자(가|는)\s*(작성|제출)', ln.text) else '해당 없음')
    if BEFORE_BID.search(ln.text):
        timing = '입찰 전 발급·보유 또는 입찰서와 함께 제출'
    elif AT_CONTRACT.search(ln.text):
        timing = '계약 체결 시 제출'
    elif AFTER.search(ln.text):
        timing = '낙찰 후·계약 후'
    elif BEFORE_BID.search(t):
        timing = '입찰 전 발급·보유 또는 입찰서와 함께 제출'
    else:
        timing = UNKNOWN
    return {'발급 주체': issuer, '시점': timing}


PLEDGE = Family(
    name='pledge', items=('v19',), title='확약서·공급 확인',
    guide='각 줄의 확약서·공급확인 요구가 누가 발급하는 문서를 언제 요구하는지 분류한다.',
    fields=(
        Field('발급 주체', ('제3자(제조사·공급사·기술지원사)', '입찰자 자신', '해당 없음', UNKNOWN),
              '제3자 = 제조사·공급사·기술지원사·총판 등이 입찰자에게 발급해 주는 확약서·확인서. 입찰자 자신 = 입찰자가 스스로 작성하는 확약·서약. 해당 없음 = 확약서 요구가 아님'),
        Field('시점', ('입찰 전 발급·보유 또는 입찰서와 함께 제출', '계약 체결 시 제출', '낙찰 후·계약 후', '제출 가능 여부만(시점 없음)', UNKNOWN),
              '입찰 전 발급·보유 또는 입찰서와 함께 제출 = 입찰서 제출 마감 전까지 받아 두거나 입찰 서류로 내야 함. 계약 체결 시 제출 = 계약할 때만 내면 됨. 낙찰 후·계약 후 = 낙찰자·계약상대자가 나중에 냄. 제출 가능 여부만 = "제출할 수 있는 업체"처럼 언제 내는지 적지 않음'),
    ),
    max_lines=8, selector=pledge_select, default=pledge_default)


# ---------------------------------------------------------------- software project (v20)
SW_HINT = re.compile(r'소프트웨어|S/?W|정보\s*시스템|전산|홈페이지|누리집|플랫폼|데이터\s*베이스|DB|어플리케이션|앱\s*(개발|구축)|프로그램\s*(개발|구축|유지)|시스템\s*(구축|개발|고도화|유지|운영|개선|재구축)|솔루션|클라우드|정보화|빅데이터|인공지능|AI')
SW_RESTRICT = re.compile(r'대기업|중견\s*기업|참여\s*(를\s*)?제한|제\s*48\s*조|중소\s*소프트웨어|상호\s*출자|대기업인\s*소프트웨어')
SW_PURPOSE = re.compile(r'목\s*적|개\s*요|과업\s*(내용|범위)|사업\s*(내용|범위)|주요\s*(내용|과업)|구축\s*(내용|범위)')


def sw_candidate(notice, meta, titles):
    text = ' '.join(titles) + ' ' + str(meta.license or '')
    if SW_HINT.search(text):
        return True
    head = ' '.join(ln.text for ln in notice.lines[:60] if ln.doc_type == '공고문')
    return bool(SW_HINT.search(head))


def sw_select(notice):
    restrict = [ln for ln in notice.lines if ln.text.strip() and SW_RESTRICT.search(ln.text)][:8]
    purpose = [ln for ln in notice.lines if ln.text.strip() and len(ln.text.strip()) > 6
               and (ln.sec == 'OVERVIEW' or SW_PURPOSE.search(ln.text) or SW_HINT.search(ln.text))][:6]
    if not purpose:
        purpose = [ln for ln in notice.lines if ln.doc_type == '공고문' and len(ln.text.strip()) > 6][:6]
    out = {ln.i: ln for ln in purpose + restrict}
    return sorted(out.values(), key=lambda ln: ln.i)


def sw_default(notice, ln):
    t = ln.text
    if re.search(r'대기업|중견', t) and re.search(r'참여|제한|제\s*48\s*조', t):
        kind = '대기업 참여제한 여부·근거 기재'
    elif re.search(r'상호\s*출자', t):
        kind = '상호출자제한기업 참여제한만'
    elif re.search(r'소프트웨어\s*사업자|신고|중소\s*기업\s*확인|확인서', t):
        kind = 'SW사업자 신고·중소기업 확인 등 서류'
    else:
        kind = '해당 없음'
    return {'표기': kind}


SW = Family(
    name='sw', items=('v20',), title='소프트웨어 사업',
    guide='사업 내용과 대기업 참여제한 표기를 분류한다.',
    fields=(
        Field('표기', ('대기업 참여제한 여부·근거 기재', '상호출자제한기업 참여제한만', 'SW사업자 신고·중소기업 확인 등 서류', '해당 없음', UNKNOWN),
              '대기업 참여제한 여부·근거 기재 = 소프트웨어 진흥법 제48조에 따른 대기업(·중견기업)인 소프트웨어사업자의 참여 제한 여부나 그 근거를 적음. '
              '상호출자제한기업 참여제한만 = 상호출자제한기업집단 소속 회사의 참여 제한만 적음. SW사업자 신고·중소기업 확인 등 서류 = 소프트웨어사업자 신고, 중소기업 확인서 같은 자격·서류'),
    ),
    max_lines=14, reason=False, selector=sw_select, default=sw_default)
SW_PROJECT = Field('사업 성격', ('소프트웨어 개발·구축·유지관리·운영', '하드웨어·장비 구매·설치 위주', '소프트웨어와 무관', UNKNOWN),
                   '소프트웨어 개발·구축·유지관리·운영 = 정보시스템·프로그램·홈페이지·데이터베이스를 개발·구축·고도화·유지관리·운영하는 사업(소프트웨어 진흥법 제2조의 소프트웨어사업). '
                   '하드웨어·장비 구매·설치 위주 = 장비·기기를 사고 설치하는 것이 중심. 소프트웨어와 무관 = 그 밖의 사업')


# ---------------------------------------------------------------- briefing session (v22, v23)
BRIEF_CUE = re.compile(r'설\s*명\s*회|현\s*장\s*설\s*명|과\s*업\s*설\s*명|사\s*업\s*설\s*명|요\s*청\s*서?\s*설\s*명')
MUST_ATTEND = re.compile(r'참석(하지|한\s*(자|업체)|업체에\s*한|자에\s*한|자만|업체만|하여야|해야)|미\s*참석.{0,20}(불가|제외|않|없|무효|허용되지)|불참.{0,20}(불가|제외|않|없|무효)|참석.{0,10}(의무|필수)')
OPTIONAL = re.compile(r'참석\s*여부(와|에)?\s*(관계|상관)\s*없|자율\s*참석|선택\s*참석|참석하지\s*않(아도|더라도)')


# Audit R2-E: a mandatory site visit ("현장답사에 참석한 업체에 한하여") is the orderer's briefing too (attendance as a
# qualification); a site visit without a mandatory-attendance expression is not a candidate.
SITE_VISIT = re.compile(r'현\s*장\s*(답\s*사|확\s*인)')
BRIEF_CUE_FIX2 = re.compile(BRIEF_CUE.pattern + '|' + SITE_VISIT.pattern)


def brief_select(notice):
    if switches.AUDIT_FIXES2:
        return pick(notice, BRIEF_CUE_FIX2, 8, lambda ln: BRIEF_CUE.search(ln.text) is not None or MUST_ATTEND.search(ln.text) is not None)
    return pick(notice, BRIEF_CUE, 8)


def brief_default(notice, ln):
    t = ln.text
    if OPTIONAL.search(t):
        att = '선택 참석'
    elif MUST_ATTEND.search(t):
        att = '참석해야 입찰·제안 가능'
    elif re.search(r'제안\s*(서)?\s*발표|발표\s*평가', t):
        att = '설명회 아님(제안 발표 등)'
    else:
        att = '개최 안내만'
    return {'참석': att}


BRIEF = Family(
    name='brief', items=('v22', 'v23'), title='설명회',
    guide='각 줄이 설명회 참석을 어떻게 다루는지 분류한다.',
    fields=(
        Field('참석', ('참석해야 입찰·제안 가능', '선택 참석', '개최 안내만', '설명회 아님(제안 발표 등)', UNKNOWN),
              '참석해야 입찰·제안 가능 = 설명회에 참석한 업체만 입찰·제안서 제출이 가능하거나 불참 업체를 제외함. 선택 참석 = 참석 여부와 관계없이 참가 가능. 개최 안내만 = 일시·장소 등 개최 사실만 알림'),
    ),
    max_lines=8, selector=brief_select, default=brief_default, meta_lines=('낙찰방법', '적용계약법'))


# ---------------------------------------------------------------- stated values for the notice–나라장터 comparison (v24)
VALUE_CUE = re.compile(r'예\s*산|추\s*정\s*가\s*격|추\s*정\s*금\s*액|기\s*초\s*금\s*액|사\s*업\s*(비|금\s*액)|용\s*역\s*금\s*액|계\s*약\s*금\s*액'
                       r'|계\s*약\s*방\s*(법|식)|입\s*찰\s*방\s*(법|식)|(일반|제한|지명)\s*경\s*쟁|수\s*의\s*(계\s*약|견\s*적)')


def values_select(notice):
    first = notice.notice_lines()[:400]
    return [ln for ln in first if ln.text.strip() and VALUE_CUE.search(ln.text)
            and ln.sec in ('TOP', 'OVERVIEW', 'BID', 'OTHER')][:14]


VALUE_BUDGET = Field('예산', (), '공고서에 적힌 배정예산·사업예산·예산액·사업비(부가가치세 포함 총액). 기초금액·추정금액·단가는 아님',
                     pattern=r'^[0-9,]{0,15}$')
VALUE_ESTIMATE = Field('추정가격', (), '공고서에 적힌 추정가격(부가가치세 제외). 추정금액·기초금액은 아님', pattern=r'^[0-9,]{0,15}$')
VALUE_METHOD = Field('계약방법', ('일반경쟁', '제한경쟁', '지명경쟁', '수의계약', '표기 없음', UNKNOWN),
                     '공고서가 밝힌 계약 방법(제목 괄호나 "계약방법 :" 줄의 표기)')
VALUES = Family(
    name='values', items=('v24',), title='공고서 기재값',
    guide='공고서 발췌에서 예산·추정가격 숫자와 계약방법을 적힌 그대로 옮긴다. 숫자는 원 단위 숫자만 옮기고, 적혀 있지 않으면 빈 문자열로 둔다.',
    fields=(), max_lines=14, reason=False, context=0, selector=values_select, default=lambda notice, ln: {},
    meta_lines=('업무구분',))

# v9 second stage (switches.V9_STAGE2): asked by main.py only on the lines judge.v9_lines takes from the model family's reading,
# never selected from the notice. The negative option does not name the field's key noun (a '모델명 아님' option drew real
# model names in the model2 run).
MODEL_OBJ = Family(
    name='v9obj', items=('v9',), title='지정된 제조사·모델의 대상',
    guide='각 줄에 적힌 제조사·상표·모델 이름이 이 계약에서 무엇의 이름인지 분류한다.',
    fields=(Field('대상', ('납품 물품', '과업용 장비·SW', '기존 장비', '호환 대상', '해당 없음', UNKNOWN),
                  '납품 물품 = 이 계약으로 사거나 빌리거나 만들어 넘기는 물품과 그 부품. 과업용 장비·SW = 용역에서 계약자가 준비해 쓰거나 새로 '
                  '설치하는 장비·SW. 기존 장비 = 발주기관이 이미 가진 장비·시스템으로 이 계약이 유지보수·수리·점검·교정·보험·운영하는 대상. '
                  '호환 대상 = 새 물품이 연결·호환되어야 하는 기존 장비·시스템. 해당 없음 = 적힌 이름이 제조사·상표·모델이 아님(항공편, '
                  '운영체제·오피스, KS·ISO 같은 규격, 등급·인증, 기관·사람 이름, ○○○ 같은 빈칸)'),),
    selector=lambda notice: [], default=lambda notice, ln: {'대상': UNKNOWN})


FAMILIES = {f.name: f for f in (INST, REGION, PERF, SIZE, DP, MODEL, PLEDGE, SW, BRIEF, VALUES, MODEL2, MODEL_OBJ)}
TOP_FIELDS = {'sw': (SW_PROJECT,), 'values': (VALUE_BUDGET, VALUE_ESTIMATE, VALUE_METHOD)}


def top_fields(name):
    return TOP_FIELDS.get(name, ())


# ---------------------------------------------------------------- prompt and grammar
SYSTEM = (
    '너는 나라장터 입찰공고문을 읽고, 번호가 붙은 줄의 성격을 정해진 선택지로 분류한다. 위반 여부는 판단하지 않는다.\n'
    '- 분류할 줄마다 그 줄의 문장이 말하는 내용만 분류한다. 앞뒤 줄과 절 이름은 문맥으로만 쓴다.\n'
    '- 문장에 적힌 그대로 읽는다. 적혀 있지 않은 것을 추측하지 않는다. 판단할 수 없으면 "불명"을 고른다.\n'
    '- 출력은 JSON 하나다.')


def field_block(fam, extra=()):
    lines = []
    for f in tuple(extra) + fam.fields:
        if f.pattern:
            lines.append(f'- {f.name}: 숫자만(쉼표 허용), 적혀 있지 않으면 빈 문자열')
        else:
            lines.append(f'- {f.name}: ' + ' / '.join(v for v in f.values if v != UNKNOWN) + ' / 불명')
        lines.append(f'  ({f.help})')
    return '\n'.join(lines)


def excerpt(notice, cands, context):
    """Candidate lines with context, grouped by contiguous blocks; section names are shown at block starts."""
    idx = set()
    for ln in cands:
        for w in notice.window(ln.i, context, context):
            idx.add(w.i)
    rows, prev, prev_sec = [], None, None
    for i in sorted(idx):
        ln = notice.lines[i]
        if not ln.text.strip():
            continue
        tag = f'{ln.doc_type}·{SECTION_NAME.get(ln.sec, ln.sec)}'
        if prev is None or i != prev + 1 or tag != prev_sec:
            if rows:
                rows.append('…')
            rows.append(f'[{tag}]')
            prev_sec = tag
        rows.append(f'L{i}: {shown(ln.text)}')
        prev = i
    return '\n'.join(rows)


def meta_summary(meta_raw, keys):
    return ' / '.join(f'{k}: {meta_raw.get(k)}' for k in keys if meta_raw.get(k) is not None)


def schema(fam, cands, extra_top=()):
    props, req = {}, []
    for f in extra_top:
        props[f.name] = f.schema()
        req.append(f.name)
    for ln in (cands if fam.fields else ()):
        key = f'L{ln.i}'
        item_props, item_req = {}, []
        if fam.reason:
            item_props['이유'] = {'type': 'string', 'minLength': 1, 'maxLength': 40}
            item_req.append('이유')
        for f in fam.fields:
            item_props[f.name] = f.schema()
            item_req.append(f.name)
        props[key] = {'type': 'object', 'additionalProperties': False, 'required': item_req, 'properties': item_props}
        req.append(key)
    return {'type': 'object', 'additionalProperties': False, 'required': req, 'properties': props}


def messages(notice, fam, cands, extra_top=(), title=''):
    ask = ', '.join(f'L{ln.i}' for ln in cands) if fam.fields else ''
    user = (f'[공고 정보] {meta_summary(notice.meta, fam.meta_lines)}' + (f' / 사업명: {title}' if title else '') + '\n'
            f'[과제] {fam.guide}\n'
            f'[선택지]\n{field_block(fam, extra_top)}\n'
            f'[발췌]\n{excerpt(notice, cands, fam.context)}'
            + ((f'\n[분류할 줄] {ask}' + (' (줄마다 "이유"를 40자 안으로 먼저 쓴다)' if fam.reason else '')) if ask else ''))
    return [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': user}]


def max_tokens(fam, cands, extra_top=()):
    per = (60 if fam.reason else 0) + 24 * len(fam.fields) + 8
    return 32 + (per * len(cands) if fam.fields else 0) + 24 * len(extra_top)


def parse(text, fam, cands, extra_top=()):
    """{line index: {field: value}} and top-level fields; values outside the enums are dropped."""
    try:
        obj = json.loads(text.split('<channel|>', 1)[-1])
    except (json.JSONDecodeError, AttributeError):
        return None, None
    if not isinstance(obj, dict):
        return None, None
    lines = {}
    for ln in cands:
        v = obj.get(f'L{ln.i}')
        if not isinstance(v, dict):
            continue
        got = {}
        for f in fam.fields:
            if f.accepts(v.get(f.name)):
                got[f.name] = v[f.name]
        if fam.reason and isinstance(v.get('이유'), str):
            got['이유'] = v['이유']
        lines[ln.i] = got
    top = {f.name: obj.get(f.name) for f in extra_top if f.accepts(obj.get(f.name))}
    return lines, top
