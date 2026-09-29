import ast
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))
from pps_c import catalog, facts, judge, switches


def bundle(lines, price, extra_docs=()):
    rec = {'id': 'SYNTHETIC-RTS', 'meta': {'적용계약법': '국가계약법',
        '업무구분': '일반용역', '계약방법': '제한경쟁', '입찰추정가격': price},
        'docs': [{'type': '공고문', 'text': '\n'.join(lines)}, *extra_docs]}
    return facts.build(rec, catalog.load())


def mark(b, prefix):
    ln = next(x for x in b.notice.lines if x.text.startswith(prefix))
    b.cands.setdefault('size', []).append(ln)
    b.readings.setdefault('size', {})[ln.i] = {'역할': '참가자격 제한'}
    return ln


@pytest.fixture
def config(monkeypatch):
    for name in ('AUDIT_FIXES', 'AUDIT_FIXES2', 'AUDIT_FIXES3'):
        monkeypatch.setattr(switches, name, False)
    for name in ('RTD_SIZE_TYPOGRAPHY', 'RTD4_SIZE_UNREAD', 'T4_SIZE_WIDE',
                 'T4_SIZE_WIDE_ABS', 'W4_SIZE_ARTICLE_TITLE'):
        monkeypatch.setattr(switches, name, True)
    return monkeypatch


def test_sw_midsize_admission_is_not_sme_limit(config):
    b = bundle(['입찰공고', '1. 입찰참가자격',
        '가. 중소 소프트웨어사업자의 사업 참여 지원에 관한 지침에 따라 중소기업이 중견기업에 해당되는 '
        '대기업이 된 경우 5년이 경과되지 않은 경우 입찰 참가 가능'], 4e8)
    mark(b, '가.')
    config.setattr(switches, 'RTS_V14_SW_ADMISSION', False)
    assert judge.v14(b) is not None
    config.setattr(switches, 'RTS_V14_SW_ADMISSION', True)
    assert judge.v14(b) is None


def test_independent_sme_requirement_is_preserved(config):
    b = bundle(['입찰공고', '1. 입찰참가자격',
        '가. 중소 소프트웨어사업자의 사업 참여 지원에 관한 지침에 따라 중소기업이 중견기업에 해당되는 '
        '대기업이 된 경우 5년이 경과되지 않은 경우 입찰 참가 가능',
        '나. 중소기업자로서 중소기업 확인서를 소지한 자'], 4e8)
    mark(b, '가.')
    mark(b, '나.')
    config.setattr(switches, 'RTS_V14_SW_ADMISSION', True)
    assert judge.v14(b) is not None
