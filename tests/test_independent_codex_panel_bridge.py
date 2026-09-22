from __future__ import annotations

import csv
import json
import pathlib

import pytest

from tools.independent_gold import codex_cli_annotator as RUNNER
from tools.independent_gold import codex_panel_bridge as BRIDGE
from tools.independent_gold import qualification_panel as PANEL


ROOT = pathlib.Path(__file__).resolve().parents[1]
QUOTE = "입찰참가자격 특정 문구"


def _record(record_id: str, variant: int) -> dict:
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


def _event_stream(raw: str, thread_id: str) -> str:
    events = [
        {"type": "thread.started", "thread_id": thread_id},
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {"id": "answer", "type": "agent_message", "text": raw},
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 100,
                "cached_input_tokens": 0,
                "output_tokens": 20,
                "reasoning_output_tokens": 5,
            },
        },
    ]
    return "".join(RUNNER.canonical_json(event) + "\n" for event in events)


def _write_inputs(root: pathlib.Path):
    records = [
        _record("P-A", 0),
        _record("P-B", 1),
        _record("N-A", 2),
        _record("N-B", 3),
    ]
    records_path = root / "dev.jsonl"
    records_path.write_text(
        "".join(RUNNER.canonical_json(record) + "\n" for record in records),
        encoding="utf-8",
    )
    labels_path = root / "dev_labels.csv"
    with labels_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", *PANEL.ITEMS])
        writer.writeheader()
        for record in records:
            positive = int(record["id"].startswith("P-"))
            writer.writerow({"id": record["id"], **{item: positive for item in PANEL.ITEMS}})
    labels = {
        record["id"]: {
            item: int(record["id"].startswith("P-")) for item in PANEL.ITEMS
        }
        for record in records
    }
    return records_path, labels_path, {record["id"]: record for record in records}, labels


def _wire_answer(group_items, item_labels):
    evidence = {}
    label_chars = []
    for item in group_items:
        label = item_labels[item]
        label_chars.append(str(label))
        evidence[item] = QUOTE if label == 1 and item not in PANEL.ABSENCE_ITEMS else None
    return RUNNER.canonical_json(
        {
            "labels": "".join(label_chars),
            "confidence": "H" * len(group_items),
            "evidence": evidence,
        }
    )


def _build_frozen_candidate(root: pathlib.Path, *, split_batches: bool):
    records_path, labels_path, records, labels = _write_inputs(root)
    panel_manifest = PANEL.build_manifest(records_path, labels_path)
    panel_path = root / "panel.json"
    panel_path.write_text(
        json.dumps(panel_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    fact_catalog = RUNNER.catalog_facts.CatalogIndex.load()
    qualification_catalog = (
        RUNNER.qualification_context.qualification_facts.CatalogReference.load()
    )
    fake_executable = root / "codex.exe"
    fake_executable.write_bytes(b"independent codex bridge fixture\n")
    rubric = RUNNER.RUBRIC_PATH.read_text(encoding="utf-8")
    cli_provenance = {
        "requested_executable": "codex",
        "resolved_executable": str(fake_executable.resolve()),
        "executable_sha256": RUNNER.file_sha256(fake_executable),
        "version_output": "codex-cli bridge-test",
        "version_stderr_sha256": RUNNER.sha256_text(""),
    }
    execution_batches = panel_manifest["execution_batches"]
    staged_batches = [[batch] for batch in execution_batches] if split_batches else [execution_batches]
    manifest_paths = []
    checkpoint_paths = []
    run_keys = set()
    for batch_index, grouped_batches in enumerate(staged_batches, 1):
        stage = root / (f"stage-{batch_index:02d}" if split_batches else "stage")
        checkpoint = root / (
            f"groups-{batch_index:02d}.jsonl" if split_batches else "groups.jsonl"
        )
        run_manifest = RUNNER.build_run_manifest(
            input_path=records_path,
            staging_dir=stage,
            model="gpt-independent-bridge-test",
            reasoning_effort="high",
            cli_provenance=cli_provenance,
            rubric=rubric,
            fact_catalog=fact_catalog,
            qualification_catalog=qualification_catalog,
            full_source=True,
        )
        RUNNER.persist_run_manifest(stage, run_manifest)
        manifest_paths.append(stage / "run_manifest.json")
        checkpoint_paths.append(checkpoint)
        run_keys.add(run_manifest["run_key"])

        for batch in grouped_batches:
            group_name = batch["target_group"]
            for record_id in batch["ids"]:
                record = records[record_id]
                prepared_facts = RUNNER.fact_context.prepare_fact_inputs(
                    record, catalog_index=fact_catalog
                )
                prepared_qualification = None
                if group_name in RUNNER.qualification_context.GROUP_FAMILIES:
                    prepared_qualification = (
                        RUNNER.qualification_context.prepare_qualification_input(
                            record, catalog=qualification_catalog
                        )
                    )
                task = RUNNER.prepare_group_task(
                    record,
                    group_name=group_name,
                    rubric=rubric,
                    staging_dir=stage,
                    run_manifest=run_manifest,
                    prepared_facts=prepared_facts,
                    fact_catalog=fact_catalog,
                    prepared_qualification=prepared_qualification,
                    qualification_catalog=qualification_catalog,
                    full_source=True,
                )
                raw = _wire_answer(task["target_items"], labels[record_id])
                events = _event_stream(raw, f"thread-{record_id}-{group_name}")
                invocation = {
                    "returncode": 0,
                    "stdout": events,
                    "stderr": "",
                    "raw_final": raw,
                    "timeout_error": None,
                    "elapsed_seconds": 1.0,
                }
                result = RUNNER.validate_invocation_result(
                    task,
                    run_manifest=run_manifest,
                    attempt=1,
                    invocation=invocation,
                )
                assert result["status"] == "ok", result.get("error")
                RUNNER.persist_attempt(task, attempt=1, invocation=invocation, result=result)
                RUNNER.append_checkpoint(checkpoint, result)

    assert len(run_keys) == 1

    return {
        "root": root,
        "records": records_path,
        "labels": labels_path,
        "panel": panel_path,
        "manifest": manifest_paths[0] if not split_batches else manifest_paths,
        "checkpoint": checkpoint_paths[0] if not split_batches else checkpoint_paths,
    }


@pytest.fixture(scope="module")
def frozen_candidate(tmp_path_factory):
    return _build_frozen_candidate(
        tmp_path_factory.mktemp("codex-panel-bridge"), split_batches=False
    )


@pytest.fixture(scope="module")
def frozen_multi_batch_candidate(tmp_path_factory):
    return _build_frozen_candidate(
        tmp_path_factory.mktemp("codex-panel-bridge-multi"), split_batches=True
    )


def _score(paths, checkpoint=None):
    return BRIDGE.bridge_score(
        panel_path=paths["panel"],
        records_path=paths["records"],
        labels_path=paths["labels"],
        run_manifest_path=paths["manifest"],
        checkpoint_path=checkpoint or paths["checkpoint"],
    )


def test_current_repository_panel_projects_exact_96_cells_62_ids():
    panel_path = ROOT / "runs" / "self_label_20000_20260918" / "qualification_panel_v1.json"
    manifest = json.loads(panel_path.read_text(encoding="utf-8"))
    records = PANEL.read_records(ROOT / "data_open" / "dev.jsonl.gz")
    targets, group_rows = BRIDGE._panel_targets(manifest, records)
    assert len(targets) == 96
    assert len({record_id for record_id, _ in targets}) == 62
    assert len(group_rows) == 82


def test_bridge_verifies_full_lineage_then_scores_only_posthoc_labels(frozen_candidate):
    report = _score(frozen_candidate)
    assert report["panel_diagnostic_pass"] is True
    assert report["panel_macro_positive_f1"] == 1.0
    assert report["minimum_item_positive_f1"] == 1.0
    assert report["counts"] == {
        "tp": 48,
        "fp": 0,
        "fn": 0,
        "tn": 48,
        "u": 0,
        "positive_f1": 1.0,
    }
    assert report["coverage"]["target_cells"] == 96
    assert report["coverage"]["required_group_rows"] == 32
    assert all(report["integrity"].values())
    assert report["qualified_for_gold_generation"] is False
    assert report["is_full_official_dev_evaluation"] is False
    assert report["information_boundary"]["model_invocations"] == 0


def test_repeatable_batch_cli_unions_disjoint_lineages_exactly(
    frozen_multi_batch_candidate, tmp_path
):
    report_path = tmp_path / "multi-batch-report.json"
    arguments = [
        "--panel",
        str(frozen_multi_batch_candidate["panel"]),
        "--records",
        str(frozen_multi_batch_candidate["records"]),
        "--labels",
        str(frozen_multi_batch_candidate["labels"]),
    ]
    for manifest, checkpoint in zip(
        frozen_multi_batch_candidate["manifest"],
        frozen_multi_batch_candidate["checkpoint"],
        strict=True,
    ):
        arguments.extend(
            ["--run-manifest", str(manifest), "--checkpoint", str(checkpoint)]
        )
    arguments.extend(["--report", str(report_path)])
    assert BRIDGE.main(arguments) == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["panel_diagnostic_pass"] is True
    assert report["coverage"]["lineage_batches"] == 8
    assert report["coverage"]["verified_group_rows"] == 32
    assert len(report["batch_lineages"]) == 8
    assert report["run_manifest_sha256"] is None
    assert report["checkpoint_sha256"] is None
    assert len({row["run_manifest_sha256"] for row in report["batch_lineages"]}) == 8
    assert {tuple(row["groups"]) for row in report["batch_lineages"]} == {
        (group_name,) for group_name, _, _ in RUNNER.annotate_groups.GROUPS
    }


def test_multi_batch_overlap_is_a_hard_failure(frozen_multi_batch_candidate):
    manifests = frozen_multi_batch_candidate["manifest"]
    checkpoints = frozen_multi_batch_candidate["checkpoint"]
    with pytest.raises(BRIDGE.BridgeError, match="overlapping/duplicate"):
        BRIDGE.bridge_score(
            panel_path=frozen_multi_batch_candidate["panel"],
            records_path=frozen_multi_batch_candidate["records"],
            labels_path=frozen_multi_batch_candidate["labels"],
            run_manifest_path=[manifests[0], manifests[0]],
            checkpoint_path=[checkpoints[0], checkpoints[0]],
        )


def test_multi_batch_manifest_checkpoint_counts_must_match(
    frozen_multi_batch_candidate
):
    with pytest.raises(BRIDGE.BridgeError, match="counts must be equal"):
        BRIDGE.bridge_score(
            panel_path=frozen_multi_batch_candidate["panel"],
            records_path=frozen_multi_batch_candidate["records"],
            labels_path=frozen_multi_batch_candidate["labels"],
            run_manifest_path=frozen_multi_batch_candidate["manifest"][:2],
            checkpoint_path=frozen_multi_batch_candidate["checkpoint"][:1],
        )


def test_multi_batch_mismatched_semantic_run_key_is_rejected_before_union(
    frozen_multi_batch_candidate, tmp_path
):
    manifests = frozen_multi_batch_candidate["manifest"]
    checkpoints = frozen_multi_batch_candidate["checkpoint"]
    changed = json.loads(manifests[1].read_text(encoding="utf-8"))
    changed_stage = tmp_path / "mixed-stage"
    changed["staging_dir"] = str(changed_stage.resolve())
    changed["semantic_config"]["model"] = "different-model-lineage"
    changed["semantic_config"]["codex_command_template"] = RUNNER.command_template(
        changed["semantic_config"]["model"],
        changed["semantic_config"]["reasoning_effort"],
    )
    changed["run_key"] = RUNNER.sha256_object(
        {
            "runner_schema_version": RUNNER.RUNNER_SCHEMA_VERSION,
            "record_input_sha256": changed["record_input"]["sha256"],
            "semantic_config": changed["semantic_config"],
            "cli_executable_sha256": changed["codex_cli"]["executable_sha256"],
            "cli_version_output": changed["codex_cli"]["version_output"],
        }
    )
    changed["manifest_sha256"] = BRIDGE._manifest_hash(changed)
    changed_stage.mkdir()
    (changed_stage / "THREAT_MODEL.md").write_text(
        RUNNER.THREAT_MODEL, encoding="utf-8"
    )
    changed_manifest = changed_stage / "run_manifest.json"
    changed_manifest.write_text(
        RUNNER.canonical_json(changed) + "\n", encoding="utf-8"
    )
    with pytest.raises(BRIDGE.BridgeError, match="mixed semantic lineage"):
        BRIDGE.bridge_score(
            panel_path=frozen_multi_batch_candidate["panel"],
            records_path=frozen_multi_batch_candidate["records"],
            labels_path=frozen_multi_batch_candidate["labels"],
            run_manifest_path=[manifests[0], changed_manifest],
            checkpoint_path=[checkpoints[0], checkpoints[1]],
        )


@pytest.mark.parametrize("mutation", ("missing", "duplicate", "extra"))
def test_missing_duplicate_and_extra_group_rows_are_hard_failures(
    frozen_candidate, tmp_path, mutation
):
    lines = frozen_candidate["checkpoint"].read_text(encoding="utf-8").splitlines()
    if mutation == "missing":
        lines = lines[1:]
    elif mutation == "duplicate":
        lines.append(lines[0])
    else:
        extra = json.loads(lines[0])
        extra["id"] = "OUT-OF-PANEL"
        lines.append(RUNNER.canonical_json(extra))
    changed = tmp_path / f"{mutation}.jsonl"
    changed.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(BRIDGE.BridgeError, match=mutation):
        _score(frozen_candidate, changed)


def test_mixed_or_tampered_run_lineage_is_a_hard_failure(frozen_candidate, tmp_path):
    rows = [
        json.loads(line)
        for line in frozen_candidate["checkpoint"].read_text(encoding="utf-8").splitlines()
    ]
    rows[0]["run_key"] = "0" * 64
    changed = tmp_path / "mixed-lineage.jsonl"
    changed.write_text(
        "".join(RUNNER.canonical_json(row) + "\n" for row in rows), encoding="utf-8"
    )
    with pytest.raises(BRIDGE.BridgeError, match="lineage"):
        _score(frozen_candidate, changed)


def test_unknown_target_is_retained_and_fails_no_abstention_gate(frozen_candidate):
    panel_manifest = json.loads(frozen_candidate["panel"].read_text(encoding="utf-8"))
    records = PANEL.read_records(frozen_candidate["records"])
    targets, _ = BRIDGE._panel_targets(panel_manifest, records)
    labels = PANEL.read_labels(frozen_candidate["labels"])
    verified = {}
    for record_id, item in targets:
        group = PANEL.ITEM_TO_GROUP[item]
        decisions = verified.setdefault((record_id, group), {})
        for group_item in PANEL.GROUP_BY_NAME[group][0]:
            label = labels[record_id][group_item]
            decisions.setdefault(
                group_item,
                {
                    "label": label,
                    "confidence": "high",
                    "evidence": "",
                    "evidence_locations": [],
                },
            )
    record_id, item = next(iter(targets))
    verified[(record_id, PANEL.ITEM_TO_GROUP[item])][item]["label"] = "U"
    score = BRIDGE._score_verified(targets, verified, labels)
    assert score["counts"]["u"] == 1
    assert score["no_abstentions"] is False
    assert score["panel_diagnostic_pass"] is False


def test_checkpoint_evidence_or_decision_shadow_cannot_override_raw_receipt(
    frozen_candidate, tmp_path
):
    rows = [
        json.loads(line)
        for line in frozen_candidate["checkpoint"].read_text(encoding="utf-8").splitlines()
    ]
    target = next(row for row in rows if row["id"].startswith("P-"))
    item = next(item for item, cell in target["decisions"].items() if cell["label"] == 1)
    target["decisions"][item]["evidence"] = "forged evidence"
    changed = tmp_path / "forged-evidence.jsonl"
    changed.write_text(
        "".join(RUNNER.canonical_json(row) + "\n" for row in rows), encoding="utf-8"
    )
    with pytest.raises(BRIDGE.BridgeError, match="receipt"):
        _score(frozen_candidate, changed)


def test_cli_writes_diagnostic_report_but_never_gold_qualification(
    frozen_candidate, tmp_path
):
    report_path = tmp_path / "report.json"
    assert (
        BRIDGE.main(
            [
                "--panel",
                str(frozen_candidate["panel"]),
                "--records",
                str(frozen_candidate["records"]),
                "--labels",
                str(frozen_candidate["labels"]),
                "--run-manifest",
                str(frozen_candidate["manifest"]),
                "--checkpoint",
                str(frozen_candidate["checkpoint"]),
                "--report",
                str(report_path),
            ]
        )
        == 0
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["panel_diagnostic_pass"] is True
    assert report["qualified_for_gold_generation"] is False


def test_module_has_no_competition_runtime_or_prediction_import():
    source = pathlib.Path(BRIDGE.__file__).read_text(encoding="utf-8").lower()
    forbidden = (
        "import " + "submission",
        "from " + "submission",
        "import pps",
        "from pps",
        "development" + "_predictions",
    )
    assert not any(token in source for token in forbidden)
