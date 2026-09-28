"""v14-v18 (switch RTD4_BID_DECL): a line that only names the kind of bid states no participation qualification.

Such lines: "소기업·소상공인 간 제한경쟁 입찰입니다", "입찰방법: 제한경쟁(총액), 중소기업자 제한", "중·소기업 제한입찰입니다",
"1. 입찰에 부치는 사항[소기업 … 간 제한경쟁 …]", the 나라장터 tag "(국내입찰/…/제한경쟁_소기업*소상공인)". A line that says who
may bid ("…으로서", "…인 자", "…확인서를 소지한", "…에 한함", "…만 참가") or that cites 판로지원법 제2조의2 (or says the bid
"진행" under it) is read as a restriction. Talkboard (DEV-039): 입찰방법 표시·확인서 서류·조항호 등록 without a 참가자격 clause is
no restriction, so
- v16/v18: when every size line the documents hold is such a line and no 공고문 qualification line names a size class, the
  size restriction is absent;
- v14/v15/v17: when every qualification-section size line is such a line, no restriction class is stated.
"""
import re
from . import families, judge

SIZE = r'(소\s*기\s*업|소\s*상\s*공\s*인|중\s*소\s*기\s*업\s*자?|중\s*[·ㆍ・‧․⸱∙⋅,]?\s*소\s*기\s*업|중\s*기\s*업)'
BID_KIND = re.compile(SIZE + r'[^.。]{0,40}?(간\s*(의\s*)?)?(제\s*한\s*(경\s*쟁|입\s*찰)|경\s*쟁\s*입\s*찰)'
                      r'|(입\s*찰|계\s*약)\s*(방\s*법|방\s*식)\s*[:：]?[^.。]{0,60}?' + SIZE
                      + r'|입\s*찰\s*에\s*부\s*치\s*는\s*사\s*항[^.。]{0,60}?' + SIZE
                      + r'|제\s*한\s*\(?\s*경\s*쟁\s*\)?\s*[\[(（_][^\])）]{0,40}?' + SIZE)
WHO = re.compile(r'(으\s*로|로)\s*서|인\s*자(?![가-힣])|인\s*업\s*체|업\s*체\s*로|확\s*인\s*서|소\s*지|보\s*유|에\s*한\s*(함|하|정)'
                 r'|만\s*(참|입\s*찰)|참\s*가\s*(할\s*수|가\s*능|자\s*격\s*(은|을|이))|자\s*격\s*을\s*갖\s*춘|등\s*록\s*된\s*자|해\s*당\s*하\s*는\s*자'
                 r'|판\s*로|제\s*2\s*조\s*의\s*2|우\s*선\s*조\s*달|시\s*행\s*령|진\s*행')


def declaration(text):
    t = ' '.join(text.split())
    return bool(BID_KIND.search(t) or judge.SIZE_TAG.search(t) or judge.X5_TAG.search(t)) and not WHO.search(t)


def _decl(b, ln):
    return declaration(judge.clause_text(b.notice, ln))


def decl_only(b, lines):
    """v16/v18: every restriction clause is a bid-kind line and no 공고문 qualification-section line names a size class."""
    if not lines or not all(_decl(b, ln) for ln in lines):
        return False
    return not any(judge.SIZE_WORD_CHEAP.search(ln.text) and judge.qual_section(ln, b.notice) and not _decl(b, ln)
                   and families.size_words(families.size_normal(ln.text)) is not None
                   for ln in b.notice.lines if ln.doc_type == '공고문')


def positive_decl_only(b, lines):
    """v14/v15/v17: every qualification-section restriction clause is a bid-kind line."""
    return bool(lines) and all(_decl(b, ln) for ln in lines)
