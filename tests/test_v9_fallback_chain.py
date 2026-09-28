"""Control-flow regression tests for the default-off v9 fallback chain."""
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'submission'))
from pps_c import judge,switches

@pytest.fixture
def flow(monkeypatch):
    for name,value in {'V9_READ2':False,'V9_STAGE2':False,'V9_X3':False,'V9_X3B':True,
                       'V9_BRAND_REQ':True,'V9_CODE_LISTING':True}.items():
        monkeypatch.setattr(switches,name,value)
    calls=[]
    values={'lines':[],'x3b':None,'brand':None,'code':None}
    def call(name):
        def run(b):
            calls.append(name)
            return values[name]
        return run
    for name,func in [('lines','v9_lines'),('x3b','x3b_line'),('brand','v9_brand_line'),('code','v9_code_listing')]:
        monkeypatch.setattr(judge,func,call(name))
    return calls,values

def test_default_is_off():
    assert switches.V9_FALLBACK_CHAIN is False

def test_off_preserves_early_none(flow,monkeypatch):
    calls,values=flow
    monkeypatch.setattr(switches,'V9_FALLBACK_CHAIN',False)
    values['brand']=object()
    assert judge.v9(object()) is None
    assert calls==['lines','x3b']

@pytest.mark.parametrize('enabled',[False,True])
def test_x3b_hit_keeps_priority(flow,monkeypatch,enabled):
    calls,values=flow
    monkeypatch.setattr(switches,'V9_FALLBACK_CHAIN',enabled)
    values['x3b']=object()
    values['brand']=object()
    assert judge.v9(object()) is values['x3b']
    assert calls==['lines','x3b']

def test_none_continues_to_brand(flow,monkeypatch):
    calls,values=flow
    monkeypatch.setattr(switches,'V9_FALLBACK_CHAIN',True)
    values['brand']=object()
    assert judge.v9(object()) is values['brand']
    assert calls==['lines','x3b','brand']

def test_none_continues_to_code_after_brand_miss(flow,monkeypatch):
    calls,values=flow
    monkeypatch.setattr(switches,'V9_FALLBACK_CHAIN',True)
    values['code']=object()
    assert judge.v9(object()) is values['code']
    assert calls==['lines','x3b','brand','code']

def test_all_misses_stay_none(flow,monkeypatch):
    calls,_=flow
    monkeypatch.setattr(switches,'V9_FALLBACK_CHAIN',True)
    assert judge.v9(object()) is None
    assert calls==['lines','x3b','brand','code']

def test_code_switch_still_controls_code(flow,monkeypatch):
    calls,values=flow
    monkeypatch.setattr(switches,'V9_FALLBACK_CHAIN',True)
    monkeypatch.setattr(switches,'V9_CODE_LISTING',False)
    values['code']=object()
    assert judge.v9(object()) is None
    assert calls==['lines','x3b','brand']

def test_brand_switch_still_gates_both_fallbacks(flow,monkeypatch):
    calls,values=flow
    monkeypatch.setattr(switches,'V9_FALLBACK_CHAIN',True)
    monkeypatch.setattr(switches,'V9_BRAND_REQ',False)
    values['brand']=values['code']=object()
    assert judge.v9(object()) is None
    assert calls==['lines','x3b']

@pytest.mark.parametrize('enabled',[False,True])
def test_x3b_off_is_unchanged(flow,monkeypatch,enabled):
    calls,values=flow
    monkeypatch.setattr(switches,'V9_FALLBACK_CHAIN',enabled)
    monkeypatch.setattr(switches,'V9_X3B',False)
    values['code']=object()
    assert judge.v9(object()) is values['code']
    assert calls==['lines','brand','code']

def test_existing_model_evidence_has_priority(flow,monkeypatch):
    calls,values=flow
    monkeypatch.setattr(switches,'V9_FALLBACK_CHAIN',True)
    values['lines']=[object()]
    assert judge.v9(object()) is values['lines'][0]
    assert calls==['lines']
