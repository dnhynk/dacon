"""Expert audit X2 criteria behind switches X2_RECORD_NOISE, X2_HELD_RECORD_X, X2_ATTACH_QUAL, X2_V8_ADDS and V3_DISJUNCTIVE
(runs/rebuild_c/transfer_20260925/audit/expert/X2/REPORT.md). Each test shows the current behaviour and the switch's."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))

from pps_c import judge, record, switches  # noqa: E402


def notice_docs(*docs):
    return record.build({'id': 'T', 'meta': {}, 'docs': [{'type': t, 'text': '\n'.join(x)} for t, x in docs]})


class FakeBundle:
    def __init__(self, notice, **meta):
        self.notice, self.cands, self.titles = notice, {}, []
        base = {'P': 5e7, 'B': 5.5e7, 'law': '국가', 'region_sido': set(), 'region_basic': 0, 'local_private': False,
                'method': '제한경쟁', 'region_flag': 'N'}
        base.update(meta)
        self.meta = type('M', (), base)()

    def read(self, fam, ln):
        return {}


def line(n, start):
    return next(x for x in n.lines if x.text.startswith(start))


def test_switch_defaults_to_current():
    assert not (switches.X2_RECORD_NOISE or switches.X2_HELD_RECORD_X or switches.X2_ATTACH_QUAL or switches.X2_V8_ADDS
                or switches.V3_DISJUNCTIVE)


def test_record_noise_v2_v8(monkeypatch):
    n = notice_docs(('공고문', ['2. 입찰참가자격', '가. 숙박업체는 관광객 용역유경험자로서 실적이 우수하고 건실한 업체',
                               '나. 최근 1년 이상 공익활동실적이 있을 것', '다. 신인도 평가 시 실적 반영',
                               '라. 최근 3년 이내 동종 용역 수행 실적이 있는 업체']))
    perf = [line(n, '가.'), line(n, '나.'), line(n, '다.'), line(n, '라.')]
    monkeypatch.setattr(judge, 'perf_lines', lambda b: list(perf))
    monkeypatch.setattr(judge, 'region_restriction', lambda b: ([], set(), 0))
    b = FakeBundle(n, region_flag='Y')
    assert judge.v2(b) is perf[0] and judge.v8(b) is perf[0]
    monkeypatch.setattr(switches, 'X2_RECORD_NOISE', True)
    assert judge.v2(b) is perf[3] and judge.v8(b) is perf[3]


def test_unread_records_clause_and_attachment(monkeypatch):
    n = notice_docs(('공고문', ['2. 입찰참가자격', '가. 경쟁입찰 참가자격을 갖춘 자',
                               '다. 경남에 소재하고 「지원사업」 교육 수행 경력을 보유', '한 기관 및 대학']),
                    ('과업지시서', ['참가 자격', '◦ 최근 3년 이내 공연 실적이 있는 업체']))
    monkeypatch.setattr(judge, 'perf_lines', lambda b: [])
    b = FakeBundle(n)
    assert judge.v2(b) is None
    monkeypatch.setattr(switches, 'X2_HELD_RECORD_X', True)
    assert judge.v2(b) is line(n, '다. 경남')
    monkeypatch.setattr(switches, 'X2_HELD_RECORD_X', False)
    monkeypatch.setattr(switches, 'X2_ATTACH_QUAL', True)
    assert judge.v2(b) is line(n, '◦ 최근')


def test_v8_adds_cpu_region(monkeypatch):
    n = notice_docs(('공고문', ['2. 입찰참가자격', '가. 본점 소재지가 경상남도에 소재한 업체',
                               '나. 최근 3년 이내 동종 용역 수행 실적이 있는 업체']))
    monkeypatch.setattr(judge, 'perf_lines', lambda b: [line(n, '나.')])
    monkeypatch.setattr(judge, 'region_restriction', lambda b: ([], set(), 0))
    b = FakeBundle(n)
    assert judge.v8(b) is None
    monkeypatch.setattr(switches, 'X2_V8_ADDS', True)
    assert judge.v8(b) is line(n, '가.')


def test_v3_disjunctive_single_record(monkeypatch):
    n = notice_docs(('공고문', ['2. 입찰참가자격', '가. 단일 계약 1억원 이상 또는 누적 2억원 이상의 동종 실적이 있는 업체',
                               '나. 누적 계약금액 2억원 이상의 동종 실적이 있는 업체']))
    b = FakeBundle(n)
    assert judge.required_amount(b, line(n, '가.')) == 2e8
    monkeypatch.setattr(switches, 'V3_DISJUNCTIVE', True)
    assert judge.required_amount(b, line(n, '가.')) == 1e8
    assert judge.required_amount(b, line(n, '나.')) == 2e8      # a cumulative requirement alone is read as before
