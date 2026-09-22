from __future__ import annotations

import copy
import csv
import importlib.util
import json
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "independent_gold" / "qualification_panel.py"
SPEC = importlib.util.spec_from_file_location("independent_qualification_panel", MODULE_PATH)
PANEL = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(PANEL)


QUOTE = "입찰참가자격 특정 문구"


def record(record_id: str, *, variant: int):
    texts = (
        "대학 연구기관 서비스센터 수행실적 추정가격 본점 지역제한 모델명 제조사 "
        "경쟁제품 직접생산 세부품명번호 중소기업 소기업 소상공인 확약서 공급사 "
        "소프트웨어 정보시스템 유지관리 공동수급 지분율 설명회 제안서 계약방법 낙찰방법",
        "산학협력단 자체시설 납품실적 기초금액 주된 영업소 인접 브랜드 호환 "
        "중소기업자간 직생 물품분류번호 중기업 비영리 기술지원확약 제조사 "
        "대기업 참여 시스템 구축 공동이행 출자비율 현장설명 협상 지역제한",
        "대학 사업장 용역실적 예산 소재지 견적 model 동등이상 경쟁제품 직접생산 "
        "소상공인 공급확약 정보시스템 운영 공동도급 최소 5% 사업설명 제출마감",
        "연구기관 서비스센터 실적 억원 지역 업체 제조사 모델 판로지원법 직접생산 "
        "중소기업 소기업 확약서 기술지원사 소프트웨어 개발 공동수급 10% 설명회 긴급",
    )
    return {
        "id": record_id,
        "docs": [
            {
                "doc_id": "D0",
                "type": "공고문" if variant % 2 == 0 else "제안요청서",
                "text": f"{QUOTE}\n{texts[variant]}\n끝",
            }
        ],
        "meta": {
            "적용계약법": "국가계약법" if variant % 2 == 0 else "지방계약법",
            "업무구분": "물품" if variant < 2 else "용역",
            "계약방법": "제한경쟁" if variant % 2 == 0 else "일반경쟁",
            "낙찰방법": "협상" if variant in (1, 3) else "적격심사",
        },
        "input_completeness": {"완전관측": True},
        "dropped_doc_counts": {},
    }


def write_fixture_inputs(tmp_path):
    records = [
        record("P-A", variant=0),
        record("P-B", variant=1),
        record("N-A", variant=2),
        record("N-B", variant=3),
    ]
    records_path = tmp_path / "dev.jsonl"
    records_path.write_text(
        "".join(PANEL.canonical_json(value) + "\n" for value in records), encoding="utf-8"
    )
    labels_path = tmp_path / "labels.csv"
    with labels_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", *PANEL.ITEMS])
        writer.writeheader()
        for value in records:
            positive = value["id"].startswith("P-")
            writer.writerow(
                {"id": value["id"], **{item: int(positive) for item in PANEL.ITEMS}}
            )
    labels = {
        value["id"]: {item: int(value["id"].startswith("P-")) for item in PANEL.ITEMS}
        for value in records
    }
    return records_path, labels_path, {value["id"]: value for value in records}, labels


def exact_location(source):
    text = source["docs"][0]["text"]
    start = text.index(QUOTE)
    return {
        "origin": "source_packet",
        "segment_id": "segment-1",
        "doc_index": 0,
        "doc_id": "D0",
        "source_doc_sha256": PANEL.sha256_text(text),
        "start": start,
        "end": start + len(QUOTE),
        "segment_start": start,
        "segment_end": start + len(QUOTE),
        "evidence_sha256": PANEL.sha256_text(QUOTE),
    }


def checkpoint_row(source, group_name, labels, *, run_key="run-1"):
    target_items = PANEL.GROUP_BY_NAME[group_name][0]
    decisions = {}
    label_chars = []
    confidence_chars = []
    evidence = {}
    for item in target_items:
        label = labels[source["id"]][item]
        quote = QUOTE if label == 1 and item not in PANEL.ABSENCE_ITEMS else ""
        decisions[item] = {
            "label": label,
            "confidence": "high",
            "evidence": quote,
            "evidence_locations": [exact_location(source)] if quote else [],
        }
        label_chars.append(str(label))
        confidence_chars.append("H")
        if quote:
            evidence[item] = quote
    raw_content = PANEL.canonical_json(
        {
            "labels": "".join(label_chars),
            "confidence": "".join(confidence_chars),
            "evidence": evidence,
        }
    )
    return {
        "schema_version": PANEL.GROUP_SCHEMA_VERSION,
        "id": source["id"],
        "group": group_name,
        "target_items": list(target_items),
        "status": "ok",
        "run_key": run_key,
        "source_sha256": PANEL.sha256_object(source),
        "packet_sha256": PANEL.sha256_text(f"packet:{source['id']}:{group_name}"),
        "rubric_sha256": PANEL.sha256_text("rubric"),
        "system_prompt_sha256": PANEL.sha256_text(f"prompt:{group_name}"),
        "request_sha256": PANEL.sha256_text(f"request:{source['id']}:{group_name}"),
        "raw_response_sha256": PANEL.sha256_text(f"response:{source['id']}:{group_name}"),
        "content_sha256": PANEL.sha256_text(raw_content),
        "raw_content": raw_content,
        "model_provenance": {
            "requested_model": "independent-qwen-model",
            "declared_model_revision": "revision-1",
        },
        "evidence_errors": [],
        "decisions": decisions,
    }


def checkpoint_rows(manifest, records, labels):
    rows = []
    for batch in manifest["execution_batches"]:
        for record_id in batch["ids"]:
            rows.append(checkpoint_row(records[record_id], batch["target_group"], labels))
    return rows


def write_rows(path, rows):
    path.write_text(
        "".join(PANEL.canonical_json(value) + "\n" for value in rows), encoding="utf-8"
    )


def replace_decision(row, item, *, label, evidence="", locations=None):
    row["decisions"][item]["label"] = label
    row["decisions"][item]["evidence"] = evidence
    row["decisions"][item]["evidence_locations"] = list(locations or [])
    labels = []
    confidence = []
    evidence_map = {}
    for target in row["target_items"]:
        cell = row["decisions"][target]
        labels.append(str(cell["label"]))
        confidence.append({"high": "H", "medium": "M", "low": "L"}[cell["confidence"]])
        if cell["evidence"]:
            evidence_map[target] = cell["evidence"]
    row["raw_content"] = PANEL.canonical_json(
        {"labels": "".join(labels), "confidence": "".join(confidence), "evidence": evidence_map}
    )
    row["content_sha256"] = PANEL.sha256_text(row["raw_content"])


def find_row(rows, record_id, group_name):
    return next(value for value in rows if value["id"] == record_id and value["group"] == group_name)


def test_build_manifest_is_deterministic_diverse_and_never_claims_qualification(tmp_path):
    records_path, labels_path, _, _ = write_fixture_inputs(tmp_path)
    first = PANEL.build_manifest(records_path, labels_path)
    second = PANEL.build_manifest(records_path, labels_path)
    assert first == second
    assert first["qualified_for_gold_generation"] is False
    assert first["is_full_official_dev_evaluation"] is False
    assert first["labels_sha256"] == PANEL.file_sha256(labels_path)
    assert first["counts"] == {
        "unique_ids": 4,
        "target_cells": 96,
        "positive_target_cells": 48,
        "hard_negative_target_cells": 48,
    }
    for item in PANEL.ITEMS:
        spec = first["items"][item]
        assert spec["target_group"] == PANEL.ITEM_TO_GROUP[item]
        assert len(spec["positive_candidates"]) == 2
        assert len(spec["hard_negative_candidates"]) == 2
        assert len({row["pattern_signature"] for row in spec["positive_candidates"]}) == 2
        assert {row["id"] for row in spec["positive_candidates"]} == {"P-A", "P-B"}
        assert {row["id"] for row in spec["hard_negative_candidates"]} == {"N-A", "N-B"}
        assert not any("gold_label" in row for row in (*spec["positive_candidates"], *spec["hard_negative_candidates"]))
    assert all(set(batch) == {"target_group", "ids", "target_items"} for batch in first["execution_batches"])
    PANEL.validate_manifest(first, records_path, labels_path)


def test_score_reports_exact_target_cells_and_coordinate_validity(tmp_path):
    records_path, labels_path, records, labels = write_fixture_inputs(tmp_path)
    manifest = PANEL.build_manifest(records_path, labels_path)
    rows = checkpoint_rows(manifest, records, labels)
    checkpoint = tmp_path / "groups.jsonl"
    write_rows(checkpoint, rows)
    report = PANEL.score_panel(manifest, records_path, labels_path, checkpoint)
    assert report["qualified_for_gold_generation"] is False
    assert report["is_full_official_dev_evaluation"] is False
    assert report["target_cells"] == 96
    assert report["counts"] == {
        "tp": 48,
        "fp": 0,
        "fn": 0,
        "tn": 48,
        "u": 0,
        "invalid": 0,
        "diagnostic_positive_f1": 1.0,
    }
    assert report["all_target_cells_binary_and_valid"] is True
    positive_witness = next(
        cell
        for cell in report["cells"]
        if cell["outcome"] == "tp" and cell["item"] not in PANEL.ABSENCE_ITEMS
    )
    assert positive_witness["evidence_coordinate"]["status"] == "valid"
    location = positive_witness["evidence_coordinate"]["locations"][0]
    text = records[positive_witness["id"]]["docs"][location["doc_index"]]["text"]
    assert text[location["start"] : location["end"]] == QUOTE
    absence_positive = next(
        cell
        for cell in report["cells"]
        if cell["outcome"] == "tp" and cell["item"] in PANEL.ABSENCE_ITEMS
    )
    assert absence_positive["evidence_coordinate"]["status"] == "not_applicable_absence_item"


def test_score_separates_fp_fn_u_and_invalid_evidence(tmp_path):
    records_path, labels_path, records, labels = write_fixture_inputs(tmp_path)
    manifest = PANEL.build_manifest(records_path, labels_path)
    rows = checkpoint_rows(manifest, records, labels)
    v1_group = PANEL.ITEM_TO_GROUP["v1"]
    replace_decision(find_row(rows, "P-A", v1_group), "v1", label=0)
    replace_decision(
        find_row(rows, "N-A", v1_group),
        "v1",
        label=1,
        evidence=QUOTE,
        locations=[exact_location(records["N-A"])],
    )
    replace_decision(find_row(rows, "P-A", v1_group), "v2", label="U")
    v9_row = find_row(rows, "P-A", PANEL.ITEM_TO_GROUP["v9"])
    forged = exact_location(records["P-A"])
    forged["start"] += 1
    replace_decision(v9_row, "v9", label=1, evidence=QUOTE, locations=[forged])
    checkpoint = tmp_path / "groups.jsonl"
    write_rows(checkpoint, rows)
    report = PANEL.score_panel(manifest, records_path, labels_path, checkpoint)
    outcomes = {(cell["id"], cell["item"]): cell for cell in report["cells"]}
    assert outcomes[("P-A", "v1")]["outcome"] == "fn"
    assert outcomes[("N-A", "v1")]["outcome"] == "fp"
    assert outcomes[("P-A", "v2")]["outcome"] == "u"
    assert outcomes[("P-A", "v9")]["outcome"] == "invalid"
    assert "evidence:location_0_quote_mismatch" in outcomes[("P-A", "v9")]["invalid_reasons"]
    assert report["counts"]["fp"] == 1
    assert report["counts"]["fn"] == 1
    assert report["counts"]["u"] == 1
    assert report["counts"]["invalid"] == 1
    assert report["qualified_for_gold_generation"] is False


def test_score_rejects_ambiguous_runs_and_prohibited_model_provenance(tmp_path):
    records_path, labels_path, records, labels = write_fixture_inputs(tmp_path)
    manifest = PANEL.build_manifest(records_path, labels_path)
    rows = checkpoint_rows(manifest, records, labels)
    first = rows[0]
    alternate = copy.deepcopy(first)
    alternate["run_key"] = "run-2"
    first["model_provenance"]["requested_model"] = "same-" + "gem" + "ma-family"
    checkpoint = tmp_path / "groups.jsonl"
    write_rows(checkpoint, [*rows, alternate])
    report = PANEL.score_panel(manifest, records_path, labels_path, checkpoint)
    affected = [
        cell
        for cell in report["cells"]
        if cell["id"] == first["id"] and cell["target_group"] == first["group"]
    ]
    assert affected
    assert all(cell["outcome"] == "invalid" for cell in affected)
    assert all("ambiguous_run_keys" in cell["invalid_reasons"] for cell in affected)

    selected = PANEL.score_panel(
        manifest, records_path, labels_path, checkpoint, run_key="run-1"
    )
    affected = [
        cell
        for cell in selected["cells"]
        if cell["id"] == first["id"] and cell["target_group"] == first["group"]
    ]
    assert all("missing_or_prohibited_model_provenance" in cell["invalid_reasons"] for cell in affected)


def test_build_and_score_cli_write_artifacts_but_never_qualify(tmp_path):
    records_path, labels_path, records, labels = write_fixture_inputs(tmp_path)
    manifest_path = tmp_path / "panel.json"
    assert PANEL.main(
        [
            "--build",
            "--records",
            str(records_path),
            "--labels",
            str(labels_path),
            "--manifest",
            str(manifest_path),
        ]
    ) == 0
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    checkpoint = tmp_path / "groups.jsonl"
    write_rows(checkpoint, checkpoint_rows(manifest, records, labels))
    report_path = tmp_path / "report.json"
    assert PANEL.main(
        [
            "--score",
            "--records",
            str(records_path),
            "--labels",
            str(labels_path),
            "--manifest",
            str(manifest_path),
            "--group-results",
            str(checkpoint),
            "--report",
            str(report_path),
        ]
    ) == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["qualified_for_gold_generation"] is False
    assert report["is_full_official_dev_evaluation"] is False


def test_module_has_no_competition_runtime_or_prediction_dependency():
    source = MODULE_PATH.read_text(encoding="utf-8").lower()
    forbidden = (
        "import " + "submission",
        "from " + "submission",
        "import pps",
        "from pps",
        "gem" + "ma",
        "saved" + "_response",
        "development" + "_predictions",
    )
    assert not any(token in source for token in forbidden)
