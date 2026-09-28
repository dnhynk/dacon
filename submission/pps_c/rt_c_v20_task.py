"""RT-C: explicit software deliverables inside the supplied task documents.

SW Act Article 2 includes software development/operation/maintenance. This narrow
fallback reads a concrete current software task, not a business-registration code
or a project's title. It is intentionally conservative about the absence test.
"""
import re


SPACE = re.compile(r'\s+')
SOFTWARE = r'(?:소프트웨어|(?<![A-Z])S/?W(?![A-Z]))'
PART = r'(?:(?:자동화|자동|점검|분석|운영|관리|응용|통합|전용)*(?:도구|모듈|프로그램))?'
ACTION = r'(?:개발|구축|고도화|유지[·ㆍ‧･・]?보수|유지[·ㆍ‧･・]?관리)'
TASK = re.compile(SOFTWARE + PART + r'(?:을|를)?' + ACTION + r'(?!자|업|서비스|비|사업자|환경|도구|방법|교육|능력|보안|인력)', re.I)
ROLE = re.compile(r'수행실적|실적증명|유사실적|업종|업태|등록|신고|확인서|증명서|교육|실습|양성|훈련|강좌|학과|산업분류|예시|소프트웨어개발\(\)|개발비|개발업|개발및공급업|기술자|개발인력|인력유치|채용연계')
NEGATIVE = re.compile(ACTION + r'(?:은|는|을|를|이|가)?(?:제외|불포함|하지않|하지아니|대상아님|불필요)'
                      r'|개발할경우|개발하는경우|개발한경우|개발경험|개발실적|기개발|이미개발|기존소프트웨어|보유소프트웨어')
ABOUT_WORK = re.compile(ACTION + r'(?:에관한|관련|을위한|를위한|의)?(?:정책|계획|전략|방안|타당성|현황|실태|필요성|이력|경험|실적)|(?:개발|구축|고도화)(?:한|했던|하였던)(?:실적|경험|이력)')
TASK_FORM = re.compile(r'^\s*(?:[○●❍□■◦•·⦁\-]|\d+[.)])|본\s*과업|세부\s*과업|과업\s*(?:내용|범위|목적)|산출물')
PURPOSE = re.compile(r'(?:개발|구축|고도화)(?:하는데|하여야|해야|한다|한다는|할것|하여납품|하여제출|하고납품|하고제출)')
# Any relevant mention makes this absence-only fallback abstain. Existing readers
# still decide citation-only, generic exclusion, malformed or misfitting clauses.
LIMIT_MENTION = re.compile(r'대기업|중견|하한제도|사업금액의?하한|사업참여지원|소프트웨어(?:산업)?진흥법.{0,24}(?:제)?48조|S/?W진흥법.{0,24}(?:제)?48조', re.I)


def task_lines(notice):
    """Current software-work clauses; no title tag or dataset artifact is used."""
    out = []
    for ln in notice.lines:
        if ln.doc_type not in ('과업지시서', '제안요청서', '규격서') and ln.sec != 'OVERVIEW':
            continue
        t = SPACE.sub('', ln.text)
        if not TASK.search(t) or ROLE.search(t) or NEGATIVE.search(t) or ABOUT_WORK.search(t):
            continue
        if not TASK_FORM.search(ln.text) and not PURPOSE.search(t):
            continue
        # A development checkbox/list on a past-performance form is not a task.
        near = ''.join(x.text for x in notice.window(ln.i, 2, 2))
        if re.search(r'수행\s*실적\s*(?:내용|명세)|실적\s*증명|과거\s*실적|소프트웨어\s*개발\s*\(\s*\)', near):
            continue
        out.append(ln)
    return out


def extra(b):
    from . import judge, switches
    if b.meta.work != '용역':
        return None
    if b.meta.private and not switches.V20_PRIVATE:
        return None
    if switches.V20_ORDERER and judge.unbound_orderer(b):
        return None
    if switches.V20_MIN_ESTIMATE and (b.meta.P or 0) < switches.V20_MIN_ESTIMATE:
        return None
    if not task_lines(b.notice):
        return None
    if any(LIMIT_MENTION.search(SPACE.sub('', d.get('text', ''))) for d in b.notice.docs):
        return None
    # Keep the existing statement reader as an additional conservative veto,
    # including an SW-specific SME restriction whose wording never says 대기업.
    if judge.sw_statement(b):
        return None
    return True
