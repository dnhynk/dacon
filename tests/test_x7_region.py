"""Expert audit X7 switches (runs/rebuild_c/transfer_20260925/audit/expert/X7/REPORT.md): v6 recall of 시·군 restrictions the
qualification-section reading misses, v6's office-setup duty, and v7's site spanning the restricted 시·도. Each test shows the
switch-off behaviour (current) and the switch-on behaviour."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))

from pps_c import judge, record, switches  # noqa: E402

REGION_READING = {'역할': '참가자격', '대상': '입찰자 소재지 제한'}


def notice_of(*texts, doc_type='공고문'):
    return record.build({'id': 'T', 'meta': {}, 'docs': [{'type': doc_type, 'text': '\n'.join(texts)}]})


class FakeBundle:
    def __init__(self, notice, readings=None, cands=None, titles=(), **meta):
        self.notice, self._readings, self.cands, self.titles = notice, readings or {}, cands or {}, list(titles)
        base = {'P': 5e7, 'B': 5.5e7, 'law': '지방', 'region_sido': set(), 'region_basic': 0, 'T_lo': 5e8, 'T_hi': 5e8,
                'local_private': False, 'method': '제한경쟁', 'posted': None, 'region_flag': None}
        base.update(meta)
        self.meta = type('M', (), base)()

    def read(self, fam, ln):
        return self._readings.get((fam, ln.i), {})


def test_x7_switches_default_off():
    for k in ('V6_LABEL_BASIC', 'V6_ORDERER_ANY', 'V6_OFFICE_DUTY', 'V7_SITE_SPAN'):
        assert getattr(switches, k) is False, k


def test_label_names_are_read_only_under_the_label():
    assert judge.x7_label_basic('- 전자입찰, 총액입찰, 제한입찰(지역제한: 여주, 양평)')
    assert judge.x7_label_basic('| 입찰참가자격 | 전자입찰, 총액입찰, 제한입찰(지역제한: 여주, 양평) |')
    assert judge.x7_label_basic('지역제한 : 여주시, 양평군 (필수)')
    for t in ('지역제한: 경기도', '지역제한: 없음', '지역제한: 예산 범위 내', '예산 범위 내에서 여주, 양평 지역 행사'):
        assert not judge.x7_label_basic(t), t


def test_v6_label_basic(monkeypatch):
    n = notice_of('1. 입찰에 부치는 사항', '- 전자입찰, 총액입찰, 제한입찰(지역제한: 여주, 양평)')
    b = FakeBundle(n, P=1.36e8)
    assert judge.v6(b) is None
    monkeypatch.setattr(switches, 'V6_LABEL_BASIC', True)
    assert judge.v6(b) is n.lines[-1]
    assert judge.v6(FakeBundle(n, P=6e8)) is None                          # at or above T it is v5's question
    assert judge.v6(FakeBundle(n, P=1.36e8, local_private=True)) is None   # 지방 소액수의 (비고)


def test_v6_orderer_any_section(monkeypatch):
    # A notice with its own qualification section: the BID-section line is outside it (as in the DEV-071 para placements).
    n = notice_of('2. 입찰참가자격', '가. 경쟁입찰 참가자격을 갖춘 자', '3. 입찰방법',
                  '가. 입찰공고일 전날부터 입찰일까지 법인등기부상 본점소재지가 [수요기관(기초자치단체)]내에 소재하고 입찰마감일 전일까지 다음의 '
                  '자격을 모두 갖춘 자')
    other = notice_of('2. 입찰참가자격', '가. 경쟁입찰 참가자격을 갖춘 자', '3. 입찰방법', '가. 납품장소는 [수요기관(기초자치단체)] 청사 내 지정 장소')
    assert judge.v6(FakeBundle(n)) is None
    monkeypatch.setattr(switches, 'V6_ORDERER_ANY', True)
    assert judge.v6(FakeBundle(n)) is n.lines[-1]
    assert judge.v6(FakeBundle(other)) is None
    assert judge.x7_v6_extra(FakeBundle(notice_of('가. 본점 소재지가 [수요기관(광역자치단체)] 관내인 업체'))) is None


def test_v6_office_setup_duty_is_no_location(monkeypatch):
    n = notice_of('2. 입찰참가자격', '① [지역:r1|단위=기초|광역=충청북도] 소재 사무실 설치 필수이며 착수일로부터 업무 수행이 가능해야 함')
    ln = n.lines[-1]
    b = FakeBundle(n, {('region', ln.i): REGION_READING}, {'region': [ln]})
    assert judge.v6(b) is ln
    monkeypatch.setattr(switches, 'V6_OFFICE_DUTY', True)
    assert judge.v6(b) is None
    kept = notice_of('2. 입찰참가자격', '가. 주된 영업소가 [지역:r1|단위=기초|광역=충청북도]에 소재한 업체')
    kl = kept.lines[-1]
    assert judge.v6(FakeBundle(kept, {('region', kl.i): REGION_READING}, {'region': [kl]})) is kl


def test_v7_site_span(monkeypatch):
    n = notice_of('2. 입찰참가자격', '가. 본점 소재지가 부산광역시 또는 울산광역시 또는 경상남도에 소재한 업체',
                  '4.1. 과업대상: 부산광역시, 울산광역시, 경상남도 소재 지상기상관측장소 72개소')
    ln = n.lines[1]
    b = FakeBundle(n, {('region', ln.i): REGION_READING}, {'region': [ln]}, law='국가', T_lo=2.3e8, T_hi=2.3e8)
    widened = notice_of('2. 입찰참가자격', '가. 본점 소재지가 부산광역시 또는 울산광역시 또는 경상남도에 소재한 업체',
                        '4.1. 과업대상: 부산광역시 소재 관측장소 5개소')
    wl = widened.lines[1]
    w = FakeBundle(widened, {('region', wl.i): REGION_READING}, {'region': [wl]}, law='국가', T_lo=2.3e8, T_hi=2.3e8)
    assert judge.v7(b) is ln and judge.v7(w) is wl
    monkeypatch.setattr(switches, 'V7_SITE_SPAN', True)
    assert judge.v7(b) is None and judge.v7(w) is wl
    reg = FakeBundle(notice_of('4.1. 과업대상: 부산광역시, 울산광역시 소재 관측장소'), law='국가', T_lo=2.3e8, T_hi=2.3e8, region_flag='Y',
                     region_sido={'부산광역시', '울산광역시'})
    monkeypatch.setattr(switches, 'V7_META_MULTI', True)
    assert judge.v7(reg) is None
    monkeypatch.setattr(switches, 'V7_SITE_SPAN', False)
    assert judge.v7(reg) is True
