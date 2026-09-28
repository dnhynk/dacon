import sys
from pathlib import Path
from types import SimpleNamespace
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))
from pps_c import judge, record, switches

def notice(*lines):
    return record.build({'id': 'synthetic', 'meta': {}, 'docs': [{'type': '공고문', 'text': '\n'.join(lines)}]})

from pps_c import rtq_v20_sme41 as rule, facts, catalog
from pps_c.dedicated import sw_participation as stage

CLAUSE='ㅇ 「소프트웨어 진흥법 시행령」 제41조(중소 소프트웨어사업자의 기준 등) 제1항 제1호 또는 제2호에 해당하는 자'

def test_explicit_sw_sme_clause_on_shared_and_stage(monkeypatch):
    b=facts.build({'id':'synthetic','meta':{
        '업무구분':'일반용역','계약방법':'제한경쟁','배정예산금액':90000000,
        '면허업종제한목록':'소프트웨어사업자(1468)'},
        'docs':[{'type':'공고문','text':'2. 입찰참가자격\n'+CLAUSE}]},catalog.load())
    b.sw_project='소프트웨어 개발·구축·유지관리·운영'
    setattr(b,stage.FAM,{'object_type':'sw_maintenance_or_operation','band_clause':'only_generic_large_firm_exclusion','quote':CLAUSE})
    monkeypatch.setattr(switches,'V20_STATEMENT_STRICT',True)
    monkeypatch.setattr(switches,'V20_CITATION_ONLY',True)
    monkeypatch.setattr(switches,'X3_V20_NEEDS_BASIS',True)
    monkeypatch.setattr(switches,'V20_SME41_STATEMENT',False)
    assert judge.v20(b) is True and stage.verdict(b) is True
    monkeypatch.setattr(switches,'V20_SME41_STATEMENT',True)
    assert judge.v20(b) is None and stage.verdict(b) is None

@pytest.mark.parametrize('text',[
    '「소프트웨어 진흥법 시행령」 제41조(중소 소프트웨어사업자의 기준 등)',
    '중소기업기본법 제2조에 따른 중소기업 확인서 소지자',
    '소프트웨어 진흥법 시행령 제41조에 의해 소프트웨어사업자로 신고된 자',
    CLAUSE.replace('해당하는 자','해당하지 않는 자'),
    CLAUSE.replace('소프트웨어 진흥법','다른 법률'),
])
def test_citation_registration_and_generic_sme_are_not_statement(text):
    assert not rule.statement(text)
