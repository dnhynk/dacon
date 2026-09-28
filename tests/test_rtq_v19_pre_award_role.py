import sys
from pathlib import Path
from types import SimpleNamespace
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))
from pps_c import judge, record, switches

def notice(*lines):
    return record.build({'id': 'synthetic', 'meta': {}, 'docs': [{'type': '공고문', 'text': '\n'.join(lines)}]})

@pytest.mark.parametrize('role', ['제안업체', '제안자', '제안사'])
def test_actor_is_not_submission_time(monkeypatch, role):
    text = '제조사 공급확약서를 요구하는 경우 ' + role + '는 즉시 이를 제출하여야 함'
    monkeypatch.setattr(switches, 'V19_PRE_AWARD_ROLE', False)
    assert judge.before_award(text)
    monkeypatch.setattr(switches, 'V19_PRE_AWARD_ROLE', True)
    assert not judge.before_award(text)

@pytest.mark.parametrize('time', ['입찰 시', '제안서와 함께', '제안서 제출 시', '적격심사 시'])
def test_real_submission_time_survives(monkeypatch, time):
    monkeypatch.setattr(switches, 'V19_PRE_AWARD_ROLE', True)
    assert judge.before_award('제조사 공급확약서를 ' + time + ' 제출하여야 함')
