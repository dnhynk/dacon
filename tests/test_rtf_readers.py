"""Reader input regressions: empty maker/model form labels."""
import pytest
from submission.pps_c import families as F, record, switches


def notice(text, other=None):
    docs=[{'type':'규격서','text':text}]
    if other is not None:docs.append({'type':'규격서','text':other})
    return record.build({'id':'SYNTHETIC','meta':{},'docs':docs})


@pytest.mark.parametrize('text',['모델명 :','• 제조원 :','응찰 모델명:','제조사/모델명:','물품명 : / 모델명 :','2.3. 제조사:','| 모델명: |'])
def test_empty_labels(text):
    assert F._empty_model_label(text)


@pytest.mark.parametrize('text',['모델명: Acme ZX900','제조사: 예시회사 / 모델명:', '모델명: ○○○','제조사 정품을 납품해야 한다','기존 모델명 : ZX200','장비: ZX200 / 모델명:'])
def test_nonempty_and_other_clause_not_removed(text):
    assert not F._empty_model_label(text)


def test_empty_labels_do_not_displace_real_model(monkeypatch):
    # Different numbered forms bypass the existing normalized-line deduplication.
    labels=[str(i)+'. 모델명 :' for i in range(1,13)]
    n=notice('\n'.join(labels+['제조사: Acme 모델명: ZX900']))
    monkeypatch.setattr(switches,'READ_SKIP_EMPTY_LABELS',False)
    assert 12 not in [ln.i for ln in F.model_select(n)]
    monkeypatch.setattr(switches,'READ_SKIP_EMPTY_LABELS',True)
    assert [ln.i for ln in F.model_select(n)]==[12]


def test_empty_labels_off_reproduces_original_selection(monkeypatch):
    n=notice('모델명 :\n제조사: Acme\n모델명: ZX900')
    monkeypatch.setattr(switches,'READ_SKIP_EMPTY_LABELS',False)
    assert [ln.i for ln in F.model_select(n)]==[0,1,2]
