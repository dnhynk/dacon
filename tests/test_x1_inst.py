"""Expert audit X1 criteria for v1 and v4 (runs/rebuild_c/transfer_20260925/audit/expert/X1/REPORT.md), each behind its own
switch; every test shows the current behaviour with the switch off and the audit's reading with it on."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))

from pps_c import judge, record, switches  # noqa: E402


class FakeBundle:
    def __init__(self, notice, readings=None, cands=None, **meta):
        self.notice, self._readings, self.cands = notice, readings or {}, cands or {}
        base = {'P': 5e7, 'B': 5.5e7, 'law': '국가', 'region_sido': set(), 'region_basic': 0, 'T_lo': 2.3e8, 'T_hi': 2.3e8,
                'local_private': False, 'method': '제한경쟁', 'posted': None}
        base.update(meta)
        self.meta = type('M', (), base)()

    def read(self, fam, ln):
        return self._readings.get((fam, ln.i), {})


def notice_of(*texts):
    return record.build({'id': 'T', 'meta': {}, 'docs': [{'type': '공고문', 'text': '\n'.join(texts)}]})


def inst_bundle(texts, fired, **meta):
    n = notice_of('2. 입찰참가자격', *texts)
    ln = next(x for x in n.lines if x.text.startswith(fired))
    return FakeBundle(n, {('inst', ln.i): {'역할': '참가자격', '요건': '기관 유형 한정'}}, {'inst': [ln]}, **meta), ln


def perf_bundle(text):
    n = notice_of('2. 입찰참가자격', text)
    ln = n.lines[-1]
    return FakeBundle(n, {('perf', ln.i): {'역할': '참가자격'}}, {'perf': [ln]}), ln


@pytest.fixture
def af(monkeypatch):
    monkeypatch.setattr(switches, 'AUDIT_FIXES', True)
    monkeypatch.setattr(switches, 'AUDIT_FIXES2', True)

    def turn(name, on):
        monkeypatch.setattr(switches, name, on)
    return turn


def test_x1_switches_default_to_current():
    assert not any(getattr(switches, k) for k in ('V1_REGISTERED_TYPE2', 'V1_LEAD_OPTIONS', 'V1_LOCAL_PRIVATE_INST', 'V1_INST_LIST',
                                                   'V4_PRIVATE_ENUM', 'V4_TOKEN_BUYER'))


def test_v1_type_obtained_by_statutory_approval(af):
    text = '가. 평생교육시설로 관할청의 인가를 득하고 운영 중인 법인'
    assert judge.institution_limit(text)
    af('V1_REGISTERED_TYPE2', True)
    assert not judge.institution_limit(text)
    assert judge.institution_limit('가. 평생교육시설로 관할청의 인가를 득한 법인만 참가 가능')      # stated as the only bidder


def test_v1_lead_in_option_list_with_a_licensed_business(af):
    b, ln = inst_bundle(['가. 아래 ①~② 중 하나를 갖춘 자', '① 대학 또는 연구기관', '② 직업안정법에 따른 직업소개사업자'], '① 대학')
    assert judge.v1(b) is ln
    af('V1_LEAD_OPTIONS', True)
    assert judge.v1(b) is None
    # cumulative conditions of another marker kind end the list: a later "(가) 업체" item is not an option
    c, ln2 = inst_bundle(['가. 아래 ①~② 중 하나를 갖춘 자', '① 대학 또는 연구기관', '② 국공립 연구기관', '(가) 업체 소재지 증빙 제출'], '① 대학')
    assert judge.v1(c) is ln2


def test_v1_local_small_private_quotation(af):
    b, ln = inst_bundle(['가. 대학 또는 연구기관에 한함'], '가. 대학', law='지방', method='수의계약', local_private=True)
    assert judge.v1(b) is ln
    af('V1_LOCAL_PRIVATE_INST', True)
    assert judge.v1(b) is None


def test_v1_institution_enumeration_dev058_form(af):
    n = notice_of('2. 입찰참가자격', '가. 학술연구용역 수행이 가능한 대학, 연구기관')
    b = FakeBundle(n)
    assert judge.v1(b) is None
    af('V1_INST_LIST', True)
    assert judge.v1(b) is n.lines[-1]
    for text in ('가. 학술연구용역 수행이 가능한 대학, 연구기관 또는 업체', '가. 대학, 연구기관 등도 입찰참가 가능'):
        assert judge.v1(FakeBundle(notice_of('2. 입찰참가자격', text))) is None
    assert judge.v1(FakeBundle(n, method='지명경쟁')) is None


def test_v4_private_party_in_the_buyer_enumeration():
    b, ln = perf_bundle('가. 반도체 기업 또는 공공기관에서 수행한 실적이 있는 업체')
    assert judge.buyer_limit(ln.text) == 'specific' and judge.v4(b) is ln
    switches.V4_PRIVATE_ENUM = True
    try:
        assert judge.v4(b) is None
        c, ln2 = perf_bundle('가. 국가기관 또는 공공기관에서 수행한 실적이 있는 업체')
        assert judge.v4(c) is ln2
    finally:
        switches.V4_PRIVATE_ENUM = False


def test_v4_anonymised_buyer_kind_token():
    b, ln = perf_bundle('가. 직장 [수요기관(보육시설)] 납품 실적이 있는 업체')
    assert judge.v4(b) is None
    switches.V4_TOKEN_BUYER = True
    try:
        assert judge.v4(b) is ln
    finally:
        switches.V4_TOKEN_BUYER = False
