from __future__ import annotations

import gzip
import json
import pathlib

import pytest

from tools.independent_gold import full_record_context, qualification_context


ROOT = pathlib.Path(__file__).resolve().parents[1]
ORGANIZER_INPUT = ROOT / "data_open" / "train_unlabeled.jsonl.gz"


def _record(doc_count: int, doc_id_pad: int) -> dict:
    return {
        "id": "synthetic-mandatory-budget",
        "docs": [
            {
                "doc_id": f"D{index:03d}" + "x" * doc_id_pad,
                "type": "공고문",
                "text": "입찰공고입니다.",
            }
            for index in range(doc_count)
        ],
        "meta": {},
        "dropped_doc_counts": {},
        "input_completeness": {"완전관측": True},
    }


def test_ordinary_omitted_budget_is_identical_to_explicit_legacy_budget():
    record = _record(1, 0)
    automatic = qualification_context.build_qualification_context(
        record, "v10-13"
    )
    strict = qualification_context.build_qualification_context(
        record, "v10-13", max_chars=qualification_context.DEFAULT_MAX_CHARS
    )
    assert automatic == strict
    assert automatic["bounds"]["max_chars"] == 16_000


def test_explicit_budget_stays_strict_but_omitted_budget_preserves_mandatory_data():
    record = _record(50, 100)
    with pytest.raises(ValueError, match="smaller than mandatory"):
        qualification_context.build_qualification_context(
            record, "v10-13", max_chars=16_000
        )

    context = qualification_context.build_qualification_context(record, "v10-13")
    assert context["bounds"]["max_chars"] > 16_000
    assert context["bounds"]["rendered_chars"] <= context["bounds"]["max_chars"]
    assert qualification_context.validate_qualification_context(record, context) == []
    assert context["organizer_source"]["documents"][-1]["doc_id"] == record["docs"][-1]["doc_id"]


@pytest.mark.skipif(not ORGANIZER_INPUT.is_file(), reason="organizer unlabeled input unavailable")
def test_real_unlabeled_mandatory_overflow_is_admitted_without_full_context_truncation():
    record = None
    with gzip.open(ORGANIZER_INPUT, "rt", encoding="utf-8") as handle:
        for line in handle:
            candidate = json.loads(line)
            if candidate["id"] == "PPS-D-015386":
                record = candidate
                break
    assert record is not None

    with pytest.raises(ValueError, match="smaller than mandatory"):
        qualification_context.build_qualification_context(
            record, "v10-13", max_chars=16_000
        )
    context = full_record_context.build_full_record_context(record)
    assert context["bounds"]["source_chars"] == sum(
        len(document["text"]) for document in record["docs"]
    )
    assert context["bounds"]["truncated"] is False
    assert context["bounds"]["rendered_chars"] <= full_record_context.MAX_RENDERED_CHARS
    assert all(
        child["bounds"]["max_chars"] > 16_000
        for child in context["qualification_contexts"].values()
    )
    assert full_record_context.validate_full_record_context(record, context) == []
