"""Only explicit installed-maker acquisition restrictions survive same-maker X3."""
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'submission'))
from pps_c import catalog,facts,judge,switches
from pps_c.v9_new_same_maker import existing_maker,matches,find

REQUIRE='- 기존 백업 데이터 이관 및 호환성 유지를 위해 동일 제조사 장비 도입(현 운영 백업 장비 : NOVARA NX6300)'


def bundle(lines,work='물품(내자)',docs=None):
    return facts.build({'id':'synthetic','meta':{'업무구분':work,'계약방법':'일반경쟁','적용계약법':'국가계약법'},
                        'docs':docs or [{'type':'과업지시서','text':'\n'.join(lines)}]},catalog.load())


@pytest.mark.parametrize('text,maker',[
 ('현 운영 백업 장비 : NOVARA NX6300','NOVARA'),
 ('기존 장비(제조사: 가온테크, 모델: QX100)','가온테크'),
 ('기존 서버 제조사: NOVARA','NOVARA'),
 ('기존 장비: NX6300',None),
 ('기존 장비: VTL NX6300',None),
 ('기존 장비(모델: NX6300)',None),
 ('기존 장비 제조사: 미상',None),
 ('신규 장비: NOVARA NX6300',None),
])
def test_maker_must_be_written_in_source(text,maker):
    assert existing_maker(text)==maker


@pytest.mark.parametrize('text',[
 REQUIRE,
 '신규 장비는 기존 장비와 동일 제조사 제품으로 납품(기존 장비 제조사: NOVARA)',
 '동일한 제조사의 장비를 도입하여야 한다(기존 서버: NOVARA NX100)',
 '모든 신규 장비는 기존 장비와 동일 제조사 제품으로 납품(기존 장비: NOVARA NX100).',
])
def test_explicit_new_acquisition(text,monkeypatch):
    monkeypatch.setattr(switches,'V9_X3',True)
    monkeypatch.setattr(switches,'V9_X3_PLUS',True)
    monkeypatch.setattr(switches,'V9_EXISTING_COMPAT',False)
    b=bundle([text]);ln=b.notice.lines[0]
    monkeypatch.setattr(switches,'V9_NEW_SAME_MAKER',False)
    assert judge.x3_drop(b,ln)
    monkeypatch.setattr(switches,'V9_NEW_SAME_MAKER',True)
    assert matches(b,ln) and not judge.x3_plus_drop(ln)
    assert not judge.x3_drop(b,ln)
    assert judge.v9(b) is ln
    assert judge.judge(b)['v9']==(1,text)


@pytest.mark.parametrize('text',[
 REQUIRE.replace('NOVARA NX6300','NX6300'),
 REQUIRE.replace('동일 제조사 장비 도입','완벽한 호환 및 연동 지원'),
 '납품되는 모든 장비는 동일 제조사 제품으로 납품하여야 한다.',
 '신규 장비(NOVARA NX6300)와 동일 제조사 제품으로 납품하여야 한다.',
 REQUIRE.replace('장비 도입','장비 도입할 필요가 없음'),
 REQUIRE.replace('장비 도입','장비 도입은 권장 사항'),
 REQUIRE.replace('NOVARA NX6300','[제조사] NX6300'),
 '기존 장비: NOVARA NX100; 신규 납품품끼리 동일 제조사 제품으로 납품한다.',
 '신규 납품품끼리 동일 제조사 제품으로 납품한다(기존 장비: NOVARA NX100).',
 REQUIRE.replace('장비 도입','장비 도입을 검토'),
 REQUIRE.replace('장비 도입','장비 도입은 선택 사항'),
 REQUIRE.replace('장비 도입','장비 도입할 수도 있다'),
 '모든 신규 장비는 동일 제조사 제품으로 납품(기존 장비: NOVARA NX100).',
 '신규 납품품은 모두 동일 제조사 제품으로 납품(기존 장비: NOVARA NX100).',
])
def test_no_inferred_maker_or_compatibility_uniformity(text,monkeypatch):
    monkeypatch.setattr(switches,'V9_NEW_SAME_MAKER',True)
    b=bundle([text]);assert not matches(b,b.notice.lines[0])


def test_explicit_back_reference_can_use_adjacent_asset_field():
    b=bundle(['기존 장비 제조사: NOVARA','신규 장비는 기존 장비와 동일 제조사 제품으로 납품하여야 한다.'])
    assert find(b) is b.notice.lines[1]


def test_unrelated_nearby_name_cannot_fill_a_generic_uniformity_rule():
    b=bundle(['기존 장비 제조사: NOVARA','모든 납품품은 동일 제조사 제품으로 납품하여야 한다.'])
    assert find(b) is None


def test_other_document_cannot_supply_maker():
    b=bundle([],docs=[{'type':'규격서','text':'기존 장비 제조사: NOVARA'},
                     {'type':'과업지시서','text':'기존 장비와 동일 제조사 제품으로 납품하여야 한다.'}])
    assert find(b) is None


def test_service_contract_is_unchanged(monkeypatch):
    monkeypatch.setattr(switches,'V9_NEW_SAME_MAKER',True)
    b=bundle([REQUIRE],work='일반용역');assert find(b) is None


def test_other_x3_branches_are_not_exempted(monkeypatch):
    monkeypatch.setattr(switches,'V9_X3',True)
    monkeypatch.setattr(switches,'V9_NEW_SAME_MAKER',True)
    b=bundle([REQUIRE+'; 동등 이상 제품도 허용한다.'])
    assert judge.x3_drop(b,b.notice.lines[0])
