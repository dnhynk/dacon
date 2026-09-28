"""Fill missing v6/v7 evidence from an explicit bidder-location qualification."""
import re

_QUAL = re.compile(r'(?:입\s*찰|견\s*적|제\s*안)?\s*참\s*가\s*자\s*격|입\s*찰\s*참\s*가\s*자')
_FIELD = re.compile(
    r'^\W*(?:[가-하]\s*[.)]\s*|\d{1,2}\s*[.)]\s*)?'
    r'(?:참\s*가\s*업\s*체|제\s*안\s*업\s*체|입\s*찰\s*자|업\s*체|사\s*업\s*자)(?:의)?\s*'
    r'(?:위\s*치|소\s*재\s*지|본\s*점|주\s*소\s*지)\s*[:：|]')
_ADDRESS = re.compile(
    r'(?:소\s*재\s*지|주\s*소\s*지)(?:\s*[(（][^)）]{0,60}[)）])?\s*(?:가|는|를)?'
    r'[^.。]{0,180}(?:업\s*체|사\s*업\s*자|법\s*인)')
_LOCATED = re.compile(r'소\s*재|위\s*치|두\s*고|둔|두\s*어\s*야|있\s*어\s*야|[:：|]')
_LOCAL = re.compile(r'지\s*역\s*(?:내\s*)?(?:소\s*재\s*)?업\s*체')
_NOT = re.compile(
    r'(?:낙\s*찰|선\s*정|계\s*약\s*체\s*결)\s*(?:된\s*후|후|이\s*후)|'
    r'(?:소\s*재\s*지|주\s*소\s*지|지\s*역)[^.。]{0,25}(?:무\s*관|불\s*문|상\s*관\s*없|제\s*한\s*없)|'
    r'전\s*국\s*(?:의\s*)?(?:업\s*체|사\s*업\s*자)|'
    r'\[상세주소\]|(?:전\s*화|팩\s*스|FAX)\s*[:：]', re.I)
_OTHER_SUBJECT = re.compile(
    r'^\W*(?:[가-하]\s*[.)]\s*|\d{1,2}\s*[.)]\s*)?'
    r'(?:발\s*주\s*기\s*관|수\s*요\s*기\s*관|발\s*주\s*처|납\s*품\s*장\s*소|'
    r'수\s*행\s*장\s*소|행\s*사\s*장\s*소|용\s*역\s*위\s*치|사\s*업\s*위\s*치)')


def _qualification_line(b, ln, text):
    from . import judge, rtd_region_clause as rc
    if ln.sec in ('EVAL', 'DOCS', 'NOTE', 'JV'):
        return False
    if not (ln.sec == 'QUAL' or _QUAL.search(text)):
        return False
    if (_NOT.search(text) or _OTHER_SUBJECT.search(text) or judge.REGION_NONE.search(text)
            or judge.JV_PARTNER3.search(text) or rc.JOINT_MEMBER.search(text) or judge.X7_OFFICE_DUTY.search(text)):
        return False
    own = bool(_FIELD.search(text) or _ADDRESS.search(text) or rc.LOCAL_BIDDER.search(text)
               or _LOCAL.search(text) or rc.OFFICE.search(text) and _LOCATED.search(text))
    if not own:
        return False
    geo = rc.geography(text.replace('・', '·'))
    return bool(geo['sido'] or geo['basic'] or judge.orderer_level(text))


def fill(b):
    """Return an exact source line, or ''. No metadata synthesis or bundle mutation."""
    from . import judge
    preferred = {ln.i for ln in b.cands.get('region', ())
                 if b.read('region', ln).get('역할') == '참가자격'
                 and b.read('region', ln).get('대상') == '입찰자 소재지 제한'}
    lines = sorted(b.notice.lines, key=lambda ln: (ln.i not in preferred, ln.doc_type != '공고문', ln.i))
    for ln in lines:
        if not _qualification_line(b, ln, ln.text):
            continue
        quote = judge.evidence(ln.text)
        if _qualification_line(b, ln, quote) and any(quote in d['text'] for d in b.notice.docs):
            return quote
    return ''
