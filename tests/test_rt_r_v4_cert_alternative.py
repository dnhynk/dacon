"""RT-R: alternatives must not turn into mandatory buyer-specific records."""
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))
from pps_c import catalog, facts, judge, switches
from pps_c.rt_r_v4_cert import admits_certificate

ALT = '공공기관 납품실적(대상 물품) 또는 사전품질인증을 통해 품질적격판정을 받은 업체이어야 합니다.'


@pytest.mark.parametrize('text', [ALT, ALT.replace('또는', '혹은'), ALT.replace('통해', '입 찰 방 법 통해'), ALT.replace('품질적격', '품\n질적격')])
def test_complete_affirmative_alternative(text, monkeypatch):
    monkeypatch.setattr(switches, 'V4_CERT_ALTERNATIVE', False)
    assert judge.buyer_limit(text) == 'specific'
    monkeypatch.setattr(switches, 'V4_CERT_ALTERNATIVE', True)
    assert judge.buyer_limit(text) == 'open'


@pytest.mark.parametrize('text', [
    ALT.replace('또는', '및'),
    ALT.replace('또는 사전품질인증을 통해 품질적격판정을 받은', '보유'),
    ALT.replace('품질적격판정을 받은', '품질적격판정을 받지 못한'),
    ALT.replace('사전품질인증', '실적증명'),
    ALT + ' 공공기관의 설치 이력이 있는 업체이어야 한다.',
    ALT + ' 학교 납품실적을 별도로 보유하여야 한다.',
])
def test_does_not_remove_mandatory_or_independent_records(text, monkeypatch):
    monkeypatch.setattr(switches, 'V4_CERT_ALTERNATIVE', True)
    assert not admits_certificate(text)
    assert judge.buyer_limit(text) == 'specific'


def bundle(lines):
    rec = {'id': 'synthetic', 'meta': {'업무구분':'물품(내자)', '적용계약법':'국가계약법', '계약방법':'제한경쟁', '입찰추정가격':300000000},
           'docs':[{'type':'공고문','text':'1. 입찰참가자격\n'+'\n'.join(lines)}]}
    b = facts.build(rec, catalog.load())
    selected = [ln for ln in b.notice.lines if '실적' in ln.text]
    b.cands['perf'] = selected
    b.readings['perf'] = {ln.i:{'역할':'참가자격','형태':'기타','발주처':'특정 발주기관만'} for ln in selected}
    return b


def test_preserves_an_independent_restriction_on_a_different_line(monkeypatch):
    monkeypatch.setattr(switches, 'V4_CERT_ALTERNATIVE', True)
    monkeypatch.setattr(switches, 'V4_NAMED_BUYER', True)
    b = bundle(['가. '+ALT, '나. 공공기관에서 발주한 납품실적이 있는 업체이어야 한다.'])
    hit = judge.v4(b)
    assert hit is not None and hit.text.startswith('나.')


def test_wrapped_orderer_alternative_does_not_fire(monkeypatch):
    monkeypatch.setattr(switches, 'V4_CERT_ALTERNATIVE', True)
    monkeypatch.setattr(switches, 'V4_NAMED_BUYER', True)
    b = bundle(['가. [수요기관(공기업)] 납품실적(대상 물품) 또는 사전품질인증을',
                '통해 품질적격판정을 받은 업체이어야 합니다.'])
    assert judge.v4(b) is None
