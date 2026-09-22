"""CPU-only invariants for dev-vs-pool edit mining; no model call."""

import gzip
import json

from tools.independent_gold import cell_edit_mining as mining


def _record(record_id, lines, meta=None):
    return {"id": record_id, "docs": [{"type": "공고문", "text": "\n".join(lines)}], "meta": meta or {}}


BASE_LINES = [
    "가. 용역금액: 금38,129,000원(추정가격 34,662,728원, 부가가치세 3,466,272원)",
    "나. 위치: [수요기관(기초자치단체)|지역=r1] 일원에서 수행하는 용역입니다.",
    "다. 중소기업자로서 중기업·소기업·소상공인 확인서를 소지한 업체이어야 합니다.",
    "라. 직접생산확인증명서를 소지한 업체이어야 하며 유효기간 내에 있어야 합니다.",
    "마. 공동수급은 허용하지 않습니다. 입찰참가자격 등록을 마친 자이어야 합니다.",
]


def test_blocks_separate_perturbation_from_violation_creating_edits():
    edited = [
        "가. 용역금액: 금37,930,000원(추정가격 34,481,818원, 부가가치세 3,448,182원)",
        "나. 위치: [수요기관(기초자치단체)|지역=r9] 일원에서 수행하는 용역입니다.",
        "다. 중소기업자로서 소기업·소상공인 확인서를 소지한 업체이어야 합니다.",
        "마. 공동수급은 허용하지 않습니다. 입찰참가자격 등록을 마친 자이어야 합니다.",
        "바. 최근 3년 이내 공공기관 실적이 2억원 이상 있는 업체만 참가할 수 있습니다.",
    ]
    diff = mining.diff_pair(_record("P", BASE_LINES, {"입찰추정가격": 1}), _record("D", edited, {"입찰추정가격": 2}))
    kinds = [block["kind"] for block in diff["blocks"]]
    assert kinds == ["numeric_only", "token_only", "replace", "delete", "insert"]
    assert diff["blocks"][2]["fragments"] == [{"from": "중기업·", "to": ""}]
    assert diff["meta_changes"] == {"입찰추정가격": [1, 2]}
    row = {item: "0" for item in mining.ITEMS} | {"e" + item[1:]: "" for item in mining.ITEMS}
    row.update({"v4": "1", "e4": "최근 3년 이내 공공기관 실적이 2억원 이상 있는 업체만 참가할 수 있습니다.", "v11": "1"})
    links = mining.link_evidence(row, diff["blocks"])
    assert links["v4"] == {"has_evidence": True, "block": 4, "block_kind": "insert"}
    assert links["v11"] == {"has_evidence": False, "block": None, "block_kind": None}


def test_sibling_is_the_pool_notice_sharing_the_most_rare_lines(tmp_path):
    pool_path = tmp_path / "pool.jsonl.gz"
    other = [f"전혀 다른 공고의 {number}번째 조항으로 길이를 채우는 문장입니다." for number in range(5)]
    with gzip.open(pool_path, "wt", encoding="utf-8") as handle:
        for record in (_record("POOL-OTHER", other), _record("POOL-BASE", BASE_LINES)):
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    dev = {"DEV": _record("DEV", BASE_LINES[:4] + ["바. 새로 주입된 실적 제한 조항으로서 충분히 긴 문장입니다."])}
    match = mining.find_siblings(dev, pool_path)["DEV"]
    assert match["pool_id"] == "POOL-BASE"
    assert (match["shared_rare_lines"], match["rare_lines"]) == (4, 5)
