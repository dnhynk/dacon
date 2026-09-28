"""Switch V13_FORMS2: small-only (v13) participation clauses in wordings and layouts that the V13_SMALL_ANYWHERE reader
(judge.small_only_any) misses.

판로지원법 시행령 제2조의2 ① limits a bid either to 중소기업자 or to 소기업·소상공인 (중소기업기본법 제2조②, 소상공인기본법
제2조). A clause names the small class as who may bid also when it
- starts with a bullet the older reader does not know (▣ ▶ ◎ ➂ ⑴ ㉮ *, a table row or a bracketed label), or follows or
  precedes a finished sentence, so the line before or after it is another clause;
- is a label and its value ("(입찰참가자격) 「소상공인기본법」에 따른 소상공인", "[참가자격] …", "입찰자격: 소기업/소상공인",
  "기업규모: 소기업, 소상공인", "| 기업규모 | 소기업, 소상공인 |");
- states the class as the rule ("…에 해당할 것", "…으로 한다", "…전용 입찰", "소기업·소상공인 제한(…)", "…제한경쟁 대상",
  "…으로 표시된 업체", "…확인서 소지 업체", "소기업 또는 소상공인(중기업은 입찰참가 제외 대상)");
- bars everybody else ("소기업·소상공인이 아닌 중소기업자는 입찰 참가 불가", "…이 아닌 경우 적격심사 대상에서 제외",
  "소기업·소상공인 확인서가 없는 업체의 입찰은 무효", "중기업 확인서를 소지한 업체는 입찰에 참가할 수 없습니다").
The evaluation and bidding-method sections of the 공고문 ("나. 소기업·소상공인 제한입찰입니다" declares the method), lines
scoring points (evaluation tables in any document), method statements ("입찰방법: 제한경쟁(…)", talkboard DEV-039),
statements of no limit ("…소상공인 제한은 없음"), headings between dashes, names of bodies ("소상공인시장진흥공단") and
certificate or conditional notes stay out, as in small_only_any.
"""
import re

from . import judge as J
from . import switches
from .u2_size_subset import medium_barred

ITEM2 = re.compile(r'^\W{0,3}([가-하]\s*[\.\)]|\(?\s*\d{1,2}\s*[\.\)]|[①-⑳➀-➉➊-➓⑴-⒇㉠-㉭㉮-㉻ⓐ-ⓩ]'
                   r'|[○●◦•ㅇ❍□■▫◐◈◉※\-*＊▣▶▷►◎◆◇★☆☞|\[<【〈《])')
WHO2 = re.compile(J.SMALL_ANY_WHO.pattern
                  + r'|해\s*당\s*할\s*것|(으\s*로|로)\s*한\s*다|전\s*용|제\s*한\s*경\s*쟁\s*(입\s*찰\s*)?대\s*상'
                  r'|(입\s*찰\s*참\s*가|참\s*가|입\s*찰)\s*자\s*격\s*(요\s*건)?\s*[:：]|자\s*격\s*요\s*건\s*[:：]|기\s*업\s*(규\s*모|구\s*분)\s*[:：|]'
                  r'|^\W{0,3}[\(\[【〈<]\s*(입\s*찰\s*)?(참\s*가\s*)?(자\s*격|대\s*상)[^)\]】〉>]{0,8}[\)\]】〉>]'
                  r'|(소\s*기\s*업|소\s*상\s*공\s*인)\s*(으\s*로\s*)?(제\s*한|한\s*정)(?!\s*(없|하\s*지|되\s*지|여\s*부))'
                  r'|아\s*닌\s*(경\s*우|업\s*체|자)[^.。]{0,30}(제\s*외|무\s*효|불\s*가|없)'
                  r'|(으\s*로|로)\s*표\s*시\s*된\s*(업\s*체|자)|(소\s*지|보\s*유)\s*(업\s*체|자)(?![가-힣])|미\s*소\s*지\s*업\s*체[^.。]{0,20}(무\s*효|불\s*가|없|제\s*외)')
BID2 = re.compile(J.SMALL_ANY_BID.pattern + r'|업\s*체|전\s*용|제\s*외|무\s*효|불\s*가|기\s*업\s*규\s*모|한\s*다|해\s*당\s*할\s*것')
# "소기업·소상공인이 아닌 중소기업자" names the barred rest of the SME class, not an admitted class.
NOT_SMALL = re.compile(r'(아\s*닌\s*)(중\s*[·ㆍ・․‧]?\s*소\s*기\s*업\s*자?|중\s*기\s*업|업\s*체)')
# "…확인서가 없는 업체", "…확인서를 소지하지 않은 자" is who is barred, not a note on the certificate.
# A parenthesis barring 중기업 ("소기업 또는 소상공인(중기업은 입찰참가 제외 대상)") names who may bid.
MID_BAR = re.compile(r'[\(\[]\s*중\s*기\s*업[^)\]]{0,20}?(불\s*가|제\s*외|없|제\s*한|[x×X✕])[^)\]]{0,6}[\)\]]')
# A bidding-method display ("총액입찰, 지역제한경쟁(…), 소기업·소상공인 제한 입찰, … 낙찰방식입니다", "견적제출 및 계약방식 : …")
# is no participation clause (talkboard DEV-039), nor is a clause conditioned on a case ("(1억 미만의 경우) …").
METHOD2 = re.compile(r'(총\s*액|단\s*가)\s*(입\s*찰|견\s*적)|전\s*자\s*(입\s*찰|견\s*적)|낙\s*찰\s*(방\s*식|자\s*결\s*정)|(계\s*약|입\s*찰)\s*방\s*(식|법)'
                     r'|견\s*적\s*제\s*출\s*(입\s*니\s*다|방\s*식|및)|수\s*의\s*([\(（][^)）]{0,6}[\)）]\s*)?계\s*약|[\(（][^)）]{0,24}경\s*우\s*[\)）]')
# A heading between dashes ("- 전북특별자치도소상공인희망센터 -") is no clause; names of bodies that carry the word
# ("소상공인시장진흥공단", "…소상공인희망센터", "소상공인연합회") name no bidder class; "…소상공인 제한은 없음" states no limit.
HEADING = re.compile(r'^\s*[-–—=~]+\s*\S.*\S\s*[-–—=~]+\s*$')
ORG = re.compile(r'소\s*상\s*공\s*인\s*(시\s*장\s*)?진\s*흥\s*공\s*단|소\s*상\s*공\s*인\s*[가-힣]{0,6}센\s*터|소\s*상\s*공\s*인\s*(연\s*합\s*회|정\s*책\s*자\s*금|공\s*제|방\s*송)')
NO_LIMIT = re.compile(r'제\s*한\s*(은|이|을)?\s*(없|두\s*지\s*않|하\s*지\s*않|적\s*용\s*하\s*지\s*않)|제\s*한\s*사\s*항\s*(은\s*)?없')
# Points, deductions and scores belong to evaluation tables, also in attachments.
SCORE = re.compile(r'\d+(\.\d+)?\s*점(?![가-힣])|점\s*수|감\s*점|가\s*산\s*점')
CERT_ABSENT = re.compile(r'확\s*인\s*서\s*(가|를|을)\s*(없\s*는|소\s*지\s*하\s*지\s*않\s*은|보\s*유\s*하\s*지\s*않\s*은|미\s*소\s*지\s*한)\s*(업\s*체|자)')


def small_only_any2(b):
    """small_only_any with the wider bullets, labels, rule shapes and bars above."""
    lines = [ln for ln in b.notice.lines if ln.text.strip()]
    for k, ln in enumerate(lines):
        if ln.doc_type == '공고문' and ln.sec in ('EVAL', 'BID') or J.METHOD_SUMMARY.search(ln.text) or HEADING.match(ln.text):
            continue
        barred = medium_barred(' '.join(ln.text.split()))
        if not J.SMALL_TEXT_CLASS.search(ORG.sub(' ', ln.text)) and not barred:
            continue
        parts = [ln.text]
        for nxt in lines[k + 1:k + 3]:
            if nxt.doc != ln.doc or ITEM2.match(nxt.text) or HEADING.match(nxt.text) or J.SENT_END.search(parts[-1]):
                break
            parts.append(nxt.text)
        before = lines[k - 1].text if k and lines[k - 1].doc == ln.doc and not ITEM2.match(ln.text) \
            and not J.SENT_END.search(lines[k - 1].text) else ''
        norm = lambda s: CERT_ABSENT.sub('확인서 미소지 업체', J.SMALL_ANY_CERT.sub(r'\1 \4', J.SMALL_ANY_STRONG.sub(
            ' ', J.SMALL_ANY_DOTS.sub('·', ' '.join(s.split())))))
        clause = ORG.sub(' ', NOT_SMALL.sub(r'\1업체', norm(' '.join(parts))))
        edge = norm(before.rstrip()[-12:] + ln.text.lstrip()[:4])
        core = J.SMALL_ANY_EXCLUDED.sub(' ', J.SMALL_TEXT_EXCLUDED.sub(' ', J.SMALL_TEXT_NAMES.sub(' ', clause)))
        prior = J.SMALL_ANY_EXCLUDED.sub(' ', J.SMALL_TEXT_EXCLUDED.sub(' ', J.SMALL_TEXT_NAMES.sub(' ', norm(before))))
        if switches.U2_V13_SUBSET_TEXT:
            from .u2_size_subset import subset_core, subset_who
            core, prior = subset_core(core), subset_core(prior)
        if J.SMALL_ANY_NOTE.search(clause) or J.SMALL_ANY_NOTE.search(core) or SCORE.search(clause) or METHOD2.search(clause) \
                or NO_LIMIT.search(clause) \
                or J.SMALL_TEXT_OTHER.search(prior) \
                or J.SMALL_TEXT_OTHER.search(J.SMALL_TEXT_NAMES.sub(' ', edge)):
            continue
        if barred and not J.SMALL_TEXT_OTHER.search(J.SMALL_ANY_EXCLUDED.sub(' ', core).replace('중기업', ' ')):
            return ln
        if J.SMALL_TEXT_OTHER.search(core):
            continue
        if switches.U2_V13_SUBSET_TEXT and J.SMALL_TEXT_CLASS.search(core) and subset_who(clause):
            return ln
        if J.SMALL_TEXT_CLASS.search(core) and (WHO2.search(core) and BID2.search(core) or MID_BAR.search(clause)):
            return ln
    return None
