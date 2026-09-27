"""Literal-sweep group C switches (h08, 2026-09-27; commit b8c81cb): V24_REGION_CPU reads on the CPU a bidder-location
qualification clause the region family never selected (families.REGION_ANY misses a full 시·도 name followed by a particle),
and V19_QUAL_STAGE fires v19 on a third-party 확약서 demanded at the 적격심사 stage. Each test shows the switch-off behaviour
(current) and the switch-on behaviour."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))

from pps_c import families, judge, record, regions, switches  # noqa: E402

REGION_READING = {'역할': '참가자격', '대상': '입찰자 소재지 제한'}
THIRD_PARTY = '제3자(제조사·공급사·기술지원사)'
PRE_BID = '입찰 전 발급·보유 또는 입찰서와 함께 제출'
UNREAD_CLAUSE = '나. 입찰공고일 전일부터 입찰일(낙찰자는 계약체결일)까지 법인등기부상 본점 소재지를 경상남도에 둔 자이어야 합니다.'
QUAL_STAGE_PLEDGE = '3) 공급업체는 당해 물품 제조사의 물품공급 및 기술지원(A/S)확약서를 적격심사 시 제출하여야 하고 미제출시 낙찰대상에서 제외됨'


def notice_of(*texts, doc_type='공고문'):
    return record.build({'id': 'T', 'meta': {}, 'docs': [{'type': doc_type, 'text': '\n'.join(texts)}]})


class FakeBundle:
    def __init__(self, notice, readings=None, cands=None, titles=(), **meta):
        self.notice, self._readings, self.cands, self.titles = notice, readings or {}, cands or {}, list(titles)
        base = {'P': 5e7, 'B': 5.5e7, 'law': '지방', 'region_sido': set(), 'region_basic': 0, 'T_lo': 5e8, 'T_hi': 5e8,
                'local_private': False, 'private': False, 'method': '제한경쟁', 'award': '적격심사제', 'posted': None,
                'region_flag': None, 'license_flag': None, 'license': None, 'P_source': 'meta', 'codes': []}
        base.update(meta)
        self.meta = type('M', (), base)()

    def read(self, fam, ln):
        return self._readings.get((fam, ln.i), {})


def region_bundle(notice, *sido, **kw):
    return FakeBundle(notice, region_flag='Y', region_sido=set(sido), **kw)


def pledge_bundle(notice, ln, timing, issuer=THIRD_PARTY):
    return FakeBundle(notice, {('pledge', ln.i): {'발급 주체': issuer, '시점': timing}}, {'pledge': [ln]})


def test_sweep_c_switches_default_off():
    assert switches.V24_REGION_CPU is False
    assert switches.V19_QUAL_STAGE is False


def test_region_family_never_selects_a_particle_clause():
    # The defect the switch works around: a full 시·도 name followed by a particle is no region candidate, yet it names the 시·도.
    n = notice_of('2. 입찰참가자격', '가. 지방계약법 시행령 제13조에 따른 자격을 갖춘 자', UNREAD_CLAUSE)
    assert families.REGION_ANY.search(UNREAD_CLAUSE) is None
    assert not any(ln.text == UNREAD_CLAUSE for ln in families.region_select(n))
    assert regions.mentions(UNREAD_CLAUSE)['sido'] == {'경상남도'}
    assert n.lines[-1].sec == 'QUAL'


def test_v24_region_cpu_reads_the_unread_clause(monkeypatch):
    n = notice_of('2. 입찰참가자격', '가. 지방계약법 시행령 제13조에 따른 자격을 갖춘 자', UNREAD_CLAUSE)
    clause = n.lines[-1]
    differs, agrees = region_bundle(n, '경상북도'), region_bundle(n, '경상남도')
    assert judge.v24(differs) is None and judge.v24(agrees) is None          # current: the clause is never compared
    monkeypatch.setattr(switches, 'V24_REGION_CPU', True)
    assert judge.v24(differs) is clause                                     # stated 경남 vs registered 경북
    assert judge.v24(agrees) is None                                        # stated 경남 = registered 경남
    assert judge.v24(FakeBundle(n, region_flag='N', region_sido=set())) is None   # no registered 시·도 to compare with


def test_v24_region_cpu_skips_no_restriction_note_and_partner_location(monkeypatch):
    monkeypatch.setattr(switches, 'V24_REGION_CPU', True)
    note = notice_of('2. 입찰참가자격', '나. 지역제한 없음(본점 소재지가 경상남도인 업체도 참가 가능)')
    partner = notice_of('2. 입찰참가자격', '다. 면허보완을 위하여 경상남도에 소재한 업체와 공동도급이 가능합니다.')
    partner_only = notice_of('2. 입찰참가자격', '다. 경상남도에 소재한 업체와 공동도급이 가능합니다.')
    for n in (note, partner, partner_only):
        assert judge.v24(region_bundle(n, '경상북도')) is None, n.lines[-1].text
        assert judge.v24_cpu_region(region_bundle(n, '경상북도')) == ([], set())


def test_v24_region_cpu_yields_to_a_clause_the_family_read(monkeypatch):
    read_clause = '가. 본점 소재지가 경상남도에 소재한 업체'
    n = notice_of('2. 입찰참가자격', read_clause, '나. 주된 영업소를 전라남도에 둔 자이어야 합니다.')
    rl = n.lines[1]
    readings, cands = {('region', rl.i): REGION_READING}, {'region': [rl]}
    agrees = region_bundle(n, '경상남도', readings=readings, cands=cands)
    differs = region_bundle(n, '부산광역시', readings=readings, cands=cands)
    assert judge.region_restriction(agrees)[1] == {'경상남도'}
    assert judge.v24(agrees) is None and judge.v24(differs) is rl
    monkeypatch.setattr(switches, 'V24_REGION_CPU', True)
    # The family's 시·도 stands: the unread 전라남도 line adds nothing, the same line still carries the mismatch.
    assert judge.v24(agrees) is None and judge.v24(differs) is rl


def test_v19_qual_stage_off_and_on(monkeypatch):
    # The union's v19 reading: practitioner stage split (V19_STAGE) and the pledge-document checks (V19_PLEDGE_FIXES).
    monkeypatch.setattr(switches, 'V19_STAGE', True)
    monkeypatch.setattr(switches, 'V19_PLEDGE_FIXES', True)
    n = notice_of('2. 입찰참가자격', '1) 지방계약법 시행령 제13조에 따른 자격을 갖춘 자', QUAL_STAGE_PLEDGE)
    ln = n.lines[-1]
    assert judge.x6_pledge_stage(FakeBundle(n), ln) == 'QUAL_STAGE'
    for timing in ('불명', PRE_BID):                      # the model's time reading does not matter for this class
        assert judge.v19(pledge_bundle(n, ln, timing)) is None
    monkeypatch.setattr(switches, 'V19_QUAL_STAGE', True)
    assert judge.x6_qual_stage_demand(FakeBundle(n), ln)
    for timing in ('불명', PRE_BID):
        assert judge.v19(pledge_bundle(n, ln, timing)) is ln


def test_v19_qual_stage_without_the_practitioner_split(monkeypatch):
    # Without V19_STAGE a 시점 = 입찰 전 reading already fires; the switch adds only the untimed 적격심사 demand.
    n = notice_of('2. 입찰참가자격', QUAL_STAGE_PLEDGE)
    ln = n.lines[-1]
    assert judge.v19(pledge_bundle(n, ln, PRE_BID)) is ln
    assert judge.v19(pledge_bundle(n, ln, '불명')) is None
    monkeypatch.setattr(switches, 'V19_QUAL_STAGE', True)
    assert judge.v19(pledge_bundle(n, ln, '불명')) is ln


def test_v19_qual_stage_keeps_the_other_classes_out(monkeypatch):
    monkeypatch.setattr(switches, 'V19_STAGE', True)
    monkeypatch.setattr(switches, 'V19_PLEDGE_FIXES', True)
    monkeypatch.setattr(switches, 'V19_QUAL_STAGE', True)
    capability = notice_of('2. 입찰참가자격', '다. 제조사의 물품공급확약서 발급이 가능한 업체이어야 하며 적격심사 시 제출하여야 합니다.')
    lawful = notice_of('2. 입찰참가자격', '다. 물품공급확약서 발급은 제조사와 계약 대상자 간에 이루어지며 적격심사 시 사본을 제출합니다.')
    own = notice_of('2. 입찰참가자격', '3) 입찰자는 자체 기술지원확약서를 적격심사 시 제출하여야 합니다.')
    for n in (capability, lawful):
        ln = n.lines[-1]
        assert not judge.x6_qual_stage_demand(FakeBundle(n), ln)
        assert judge.v19(pledge_bundle(n, ln, '불명')) is None
    assert judge.v19(pledge_bundle(own, own.lines[-1], '불명', issuer='입찰자 자신')) is None
    # A bid-timed third-party demand fires through the existing path, switch or no switch.
    bid = notice_of('2. 입찰참가자격', '3) 입찰참가업체는 제조사로부터 물품공급 및 기술지원 확약서를 입찰 시 제출하여야 합니다.')
    bl = bid.lines[-1]
    assert judge.x6_pledge_stage(FakeBundle(bid), bl) == 'PRE'
    assert judge.v19(pledge_bundle(bid, bl, PRE_BID)) is bl
    monkeypatch.setattr(switches, 'V19_QUAL_STAGE', False)
    assert judge.v19(pledge_bundle(bid, bl, PRE_BID)) is bl
