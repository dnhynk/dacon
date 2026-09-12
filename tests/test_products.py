from pathlib import Path

import pytest

from submission.pps.products import ProductFacts

ROOT=Path(__file__).resolve().parents[1]


def test_catalog_price_condition_has_strict_boundary_and_preserves_unknown():
    note="추정가격 3억원 미만에 한함"
    assert ProductFacts.condition(note,299_999_999)["status"] == "met"
    assert ProductFacts.condition(note,300_000_000)["status"] == "not_met"
    assert ProductFacts.condition(note,None)["status"] == "unknown"
    assert ProductFacts.condition(note+". 별도 조건 적용",1)["status"] == "not_evaluated"


@pytest.mark.skipif(not (ROOT/"data_open/data/항목표.json").exists(),reason="official material unavailable")
def test_certificate_candidate_does_not_become_actual_purchase_class():
    catalog=ProductFacts(ROOT/"data_open/data/법령패키지/중기부고시/중기부고시_경쟁제품_세부품명.csv")
    text="사업명: 학술연구 위탁 사업\n참가자격: 직접생산확인증명서 [축제기획및대행서비스 9015189001]를 소지한 업체"
    rec={"id":"synthetic-probe","meta":{"입찰추정가격":310_000_000,"업무구분":"일반용역"},
         "docs":[{"doc_id":"a","type":"공고문","text":text}]}
    out=catalog.extract(rec,top_k=3)
    assert out["catalog"]["9015189001"]["condition"]["status"] == "not_met"
    assert any(m["role"]=="certificate_code_candidate" for m in out["body_code_mentions"])
    assert out["uncertainty"]["purchase_identity"] == "unresolved"
    assert out["uncertainty"]["no_match_is_general"] is False
    for s in out["sources"]:
        assert s["text"] == rec["docs"][s["doc_index"]]["text"][s["start"]:s["end"]]
