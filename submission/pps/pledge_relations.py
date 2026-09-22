"""Positive-only recovery of source-bound third-party pre-bid obligations."""
import re

from .pledge_modality import WITHDRAWN


ISSUER = re.compile(r'제조\s*(?:\(수입\))?\s*(?:사|업체|회사)|공급\s*(?:사|업체)|기술\s*지원사|수입원|출판사|제조\([^\n)]*\)하는\s*회사')
DOCUMENT = re.compile(r'확\s*약\s*서|협약서|증명서|증명원|인증서|발행\s*확약')
FUNCTION = re.compile(r'공급(?!업체|사|자)|정품|기술\s*지원|A\s*/\s*S|사후\s*관리|유지\s*보수')
EARLY = re.compile(
    r'(?:전자\s*)?(?:입찰|제안|견적)(?:서)?(?:를|을)?\s*'
    r'(?:(?:제출|접수)\s*)?(?:마감(?:일시|일)?\s*(?:전일까지|전까지|이전|전|까지)|'
    r'전(?:까지|일|에)?|시|제출\s*(?:시|할\s*때))|'
    r'입찰\s*등록\s*서류|견적서에는')
LATE = re.compile(r'낙찰\s*(?:후|이후)|계약\s*(?:체결\s*)?(?:시|전|후)|납품\s*(?:전|후)|착수\s*전')
DEADLINE = re.compile(r'마감(?:일시|일)?\s*(?:전일까지|전까지|이전까지|까지)')
ACTION = re.compile(r'제출|보유|소지|발급\s*받|갖추|갖춘|확보|첨부|포함|붙여|내야')
OTHER_OBJECT = re.compile(r'(?:등록증|보증서|확인서|서약서|증명서|증명원|인증서)(?:은|는|을|를)')


def clauses(text):
    """Keep hard list boundaries; join only an unfinished OCR line."""
    lines = list(re.finditer(r'[^\r\n]+', text))
    for i, line in enumerate(lines):
        if not DOCUMENT.search(line[0]):
            continue
        end = line.end()
        while end-line.start() < 500 and i+1 < len(lines):
            following = lines[i+1]
            if re.search(r'[.。;；]$|(?:한다|합니다|함|업체|자|부)$', text[line.start():end].strip()):
                break
            if re.match(r'\s*(?:[○●※□■*-]|[가-하][.)]|\d+[.)]|[①-⑳])', following[0]):
                break
            if following.end()-line.start() > 500:
                break
            end = following.end()
            i += 1
        # A sentence cannot borrow its issuer from the preceding sentence.
        for unit in re.finditer(r'[^。;；]+?(?:[.。;；](?=\s|$)|$)', text[line.start():end]):
            yield line.start()+unit.start(), line.start()+unit.end()


def positive(rec):
    for di, doc in enumerate(rec.get('docs', [])):
        text = doc['text']
        for left, right in clauses(text):
            q = text[left:right]
            if not 0 < len(q) <= 500 or WITHDRAWN.search(q):
                continue
            for target in DOCUMENT.finditer(q):
                prefix = q[:target.start()]
                issuers = list(ISSUER.finditer(prefix))
                if not issuers or not FUNCTION.search(prefix[max(0, target.start()-100):]):
                    continue
                issuer = issuers[-1]
                relation = prefix[issuer.end():]
                # A supplier as the bidding subject is not an external issuer.
                if re.match(r'\s*(?:는|은)', relation):
                    continue
                if re.match(r'\s*가', relation) and not re.search(r'발급|발행|작성|제공', relation):
                    continue
                # A relationship with the maker is not proof of maker authorship.
                if re.search(r'와의|간에|간\s*체결', relation) and not re.search(r'발급|발행|작성|로부터', relation):
                    continue
                if re.search(r'(?:입찰자|참가업체|당사|자사|본인)[^\n]{0,30}(?:작성|명의)', relation):
                    continue
                if len(relation) > 130:
                    continue
                for action in ACTION.finditer(q, target.end()):
                    between = q[target.end():action.start()]
                    # A coordinated second document (``확약서와 … 인증서를 제출``)
                    # shares the pledge's action; it is not a separate subject.
                    if OTHER_OBJECT.search(between) and not re.match(r'\s*(?:와|과|및|[,，·ㆍ])', between):
                        continue
                    times = [(m.start(), 'early', m.end()) for m in EARLY.finditer(q[:action.start()])]
                    times += [(m.start(), 'late', m.end()) for m in LATE.finditer(q[:action.start()])]
                    if not times or max(times)[1] != 'early':
                        continue
                    tail = re.sub(r'\s+', '', q[action.end():])
                    # Being able to submit a third-party document by a stated
                    # pre-bid deadline requires holding it before that deadline.
                    # Capability without such a date (or at contract) stays out.
                    latest = max(times)
                    if (re.match(r'(?:이)?가능한(?:업체|자)|(?:할|받을)수있는(?:업체|자)', tail)
                            and DEADLINE.search(q[latest[0]:latest[2]])):
                        return {'doc_index': di, 'doc_type': doc['type'], 'start': left, 'end': right,
                                'quote': q, 'binding': 'third_party_document_held_by_stated_pre_bid_deadline'}
                    if re.match(r'(?:이)?가능|(?:할|받을|을)수있|(?:할|받을|을)?(?:필요|의무)(?:가|는|도)?없|하지(?:않|아니)|(?:은|는)?(?:면제|불요)', tail):
                        continue
                    predicate = ACTION.split(tail, maxsplit=1)[0]
                    if re.search(r'것은아니|있지않|하여서는안|금지|필요(?:가)?없', predicate):
                        continue
                    required = bool(re.match(r'(?:하여야|해야|받아야|하여|하고|한다|합니다|함|할것|한업체|한자|어야)', tail))
                    required |= action[0] in ('내야', '붙여') and (action[0] == '내야' or tail.startswith('야'))
                    required |= action[0] == '갖춘' and bool(re.match(r'(?:업체|자)(?:만|에한)', tail))
                    # Explicit dated parenthetical checklist: '(마감 전 원본 제출)'.
                    required |= action[0] == '제출' and bool(re.match(r'[)）.。]*$', tail))
                    if required:
                        return {'doc_index': di, 'doc_type': doc['type'], 'start': left, 'end': right,
                                'quote': q, 'binding': 'third_party_document_required_at_bid_stage'}
    return None
