"""Clause detector for v9 (item definition, docs/REBUILD_BASIS.md and TALKBOARD_QA.md).

v9: the specification designates a manufacturer or model: a 제조사·모델명·상표·브랜드 label with a named value, or a
    brand + model + 시리즈 designation. Numeric specification values, '동등' and maintenance of existing equipment
    are not v9.

The detector returns (doc index, source line, kind) so a gate can quote a substring of the notice.
"""
import re
import unicodedata

EVAL = re.compile(r'평가|배점|가점|감점|점수|심사\s*항목|평점|채점')
V9_LABEL = re.compile(r'(?:^|[\s·ㆍ\-*○▷□◦•①-⑳)(])(제조사|제조원|제조업체|모델명|상표명?|브랜드)\s*[·ㆍ/및]?\s*(모델명?|제품명)?\s*[:：]\s*(?P<value>[^\n]+)')
V9_SERIES = re.compile(r'[A-Z]{2,}\s+[A-Za-z]+\s*[A-Za-z]*\d+[A-Za-z0-9/\-]*\s*시리즈')
SPEC_VALUE = re.compile(r'이상|이하|이내|미만|초과|\d+\s*(W|V|mm|cm|kg|%|기통)|동등|무관|자유')
V9_EXCLUDE = re.compile(r'동등|유지\s*보수|유지\s*관리|기존\s*(장비|설비|시스템)|호환')


def norm(text):
    return unicodedata.normalize('NFKC', text or '')


def _lines(record, doc_types=None):
    for di, doc in enumerate(record.get('docs', [])):
        if doc_types and doc.get('type') not in doc_types:
            continue
        for line in doc.get('text', '').splitlines():
            s = line.strip()
            if 8 <= len(s) <= 500:
                yield di, s


def v9_lines(record):
    out = []
    for di, s in _lines(record, ('공고문', '규격서', '시방서', '과업지시서', '제안요청서')):
        n = norm(s)
        if V9_EXCLUDE.search(n) or EVAL.search(n):
            continue
        m = V9_LABEL.search(n)
        if m and not SPEC_VALUE.search(m.group('value')) and len(m.group('value').strip()) >= 2:
            out.append((di, s, 'label'))
        elif V9_SERIES.search(n):
            out.append((di, s, 'series'))
    return out
