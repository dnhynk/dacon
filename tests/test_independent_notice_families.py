"""Organizer-only family assignments are deterministic audit blocks, not labels."""

from __future__ import annotations

import copy
import gzip
import json
from pathlib import Path

import pytest

from tools.independent_gold import notice_families


def _notice_lines(*, changed_line: int | None = None, number: int = 1) -> str:
    lines = []
    section_names = (
        "입찰자격", "계약조건", "제안방법", "기술평가", "가격평가", "서류제출",
        "현장설명", "과업범위", "사업기간", "질의답변", "보증금", "낙찰절차",
        "계약체결", "대금지급", "보안조치", "안전관리",
    )
    for index in range(16):
        word = "변경된 서로 다른 과업 조건" if index == changed_line else "공통 입찰 안내 양식"
        lines.append(
            f"{section_names[index]} 제{index}항 {word} 참여 자격과 제출 절차에 관한 이 문단은 충분히 긴 공고문 문장으로 구성됩니다. "
            f"입찰 담당자는 {number}회의 설명을 확인하고 문서의 내용에 따라 절차를 수행합니다."
        )
    return "\n".join(lines)


def _record(
    record_id: str,
    *,
    number: int = 1,
    changed_line: int | None = None,
    law: str = "국가계약법",
    doc_type: str = "공고문",
    text: str | None = None,
) -> dict:
    body = text if text is not None else _notice_lines(changed_line=changed_line, number=number)
    return {
        "id": record_id,
        "docs": [
            {"doc_id": "D0", "type": doc_type, "text": body, "n_chars": len(body)}
        ],
        "dropped_doc_counts": {},
        "input_completeness": {"all_documents_present": True},
        "meta": {
            "적용계약법": law,
            "업무구분": "일반용역",
            "계약방법": "제한경쟁",
            "낙찰방법": "협상에의한계약",
            "배정예산금액": 50_000_000,
            "입찰추정가격": 45_000_000,
        },
    }


def _write_records(path: Path, records: list[dict]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def test_near_template_family_and_replay_are_deterministic(tmp_path: Path) -> None:
    path = tmp_path / "organizer.jsonl.gz"
    records = [
        _record("r1"),
        _record("r2", number=2),  # Exact normalized announcement.
        _record("r3", number=3, changed_line=1),  # A genuinely near template.
        _record("r4", number=4, law="지방계약법"),  # A different legal scope.
        _record("r5", doc_type="과업지시서", text="첫 번째 공고문 누락 자료"),
        _record("r6", doc_type="과업지시서", text="두 번째 공고문 누락 자료"),
    ]
    _write_records(path, records)
    manifest = notice_families.build_family_manifest(path, expected_records=6)
    assert manifest["manifest_sha256"] == notice_families.sha256_object(
        {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    )
    assert notice_families.verify_family_manifest(
        manifest, path, expected_records=6
    ) == manifest["statistics"]
    family_by_id = {row["record_id"]: row["family_id"] for row in manifest["records"]}
    assert family_by_id["r1"] == family_by_id["r2"] == family_by_id["r3"]
    assert family_by_id["r4"] != family_by_id["r1"]
    assert family_by_id["r5"] != family_by_id["r6"]
    assert manifest["statistics"]["records_in_repeated_families"] == 3
    assert all(value.startswith("f-") and "|" not in value for value in family_by_id.values())

    _write_records(path, list(reversed(records)))
    reordered = notice_families.build_family_manifest(path, expected_records=6)
    assert reordered["records"] == manifest["records"]
    assert reordered["families"] == manifest["families"]


def test_source_binding_and_forbidden_label_rejection(tmp_path: Path) -> None:
    path = tmp_path / "organizer.jsonl.gz"
    records = [_record("r1"), _record("r2", number=2)]
    _write_records(path, records)
    manifest = notice_families.build_family_manifest(path, expected_records=2)
    forged = copy.deepcopy(manifest)
    forged["records"][0]["source_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="differs"):
        notice_families.verify_family_manifest(forged, path, expected_records=2)
    changed = copy.deepcopy(records)
    changed[0]["docs"][0]["text"] += " 변경"
    changed[0]["docs"][0]["n_chars"] = len(changed[0]["docs"][0]["text"])
    _write_records(path, changed)
    with pytest.raises(ValueError, match="differs"):
        notice_families.verify_family_manifest(manifest, path, expected_records=2)
    changed[0]["labels"] = {"v1": 1}
    _write_records(path, changed)
    with pytest.raises(ValueError, match="non-organizer|forbidden"):
        notice_families.build_family_manifest(path, expected_records=2)


def test_exact_coverage_rejects_missing_or_duplicate_record(tmp_path: Path) -> None:
    path = tmp_path / "organizer.jsonl.gz"
    _write_records(path, [_record("r1")])
    with pytest.raises(ValueError, match="count"):
        notice_families.build_family_manifest(path, expected_records=2)
    _write_records(path, [_record("r1"), _record("r1", number=2)])
    with pytest.raises(ValueError, match="duplicate"):
        notice_families.build_family_manifest(path, expected_records=2)
