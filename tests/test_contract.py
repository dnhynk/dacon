import csv
import gzip
import json
import unicodedata
from pathlib import Path

import pytest

from submission.pps.data import ABSENCE, COLUMNS, clean_evidence, make_row, read_csv, records, validate_csv, write_csv
from submission.pps.pipeline import MockRunner, parse_output, run
from submission.pps.prompts import Config, build_prompt, output_schema
from submission.pps.retrieval import NoticeIndex, Span
from tools.evaluate import evaluate

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def record():
    return {"id": "case-1", "meta": {"text": "메타에만 있는 값"}, "docs": [
        {"doc_id": "a", "type": "공고문", "text": '공고문, 제목\n"필수" 실적 제한. 끝'},
        {"doc_id": "b", "type": "규격서", "text": "시작\n특정 모델 ZX99만 허용한다."}]}


def test_csv_round_trip_and_evidence_boundaries(tmp_path, record):
    values, evidence = [1] * 24, [record["docs"][0]["text"]] * 24
    row = make_row(record, values, evidence)
    path = tmp_path / "submission.csv"
    write_csv(path, [row])
    checked = validate_csv(path, [record])
    assert not path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert list(checked[0]) == COLUMNS
    assert checked[0]["e1"] == evidence[0]
    assert all(checked[0][f"e{k}"] == "" for k in ABSENCE)
    assert clean_evidence("끝\n시작", record) == ""
    assert clean_evidence("메타에만 있는 값", record) == ""


@pytest.mark.parametrize("bad", [True, 2, -1, "1", None])
def test_no_label_coercion(record, bad):
    with pytest.raises(ValueError):
        make_row(record, [bad] + [0] * 23, [""] * 24)


def test_forbidden_evidence_and_length(record):
    record["docs"][0]["text"] += "\n=SUM(A1:A3)\n" + "가" * 600
    repaired = clean_evidence("=SUM(A1:A3)", record)
    assert repaired in record["docs"][0]["text"] and "=SUM(A1:A3)" in repaired
    assert repaired[0] not in "=+@"
    assert len(clean_evidence("가" * 600, record)) == 500
    assert make_row(record, [0] * 24, ["실적 제한"] * 24)["e1"] == ""


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "bom", "invalid_evidence"])
def test_reject_invalid_submissions(tmp_path, record, mutation):
    row = make_row(record, [1] * 24, [""] * 24)
    path = tmp_path / "out.csv"
    if mutation == "invalid_evidence":
        row["e1"] = "문서에 없는 근거"
    write_csv(path, [row, row] if mutation == "duplicate" else [] if mutation == "missing" else [row])
    if mutation == "bom":
        path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    with pytest.raises(ValueError):
        validate_csv(path, [record])


def test_input_normalization_and_duplicate_rejection(tmp_path, record):
    record["docs"][0]["text"] = unicodedata.normalize("NFD", "입찰공고")
    path = tmp_path / "records.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n" + json.dumps(record) + "\n")
    assert list(records(path, limit=1))[0]["docs"][0]["text"] == "입찰공고"
    with pytest.raises(ValueError, match="duplicate"):
        list(records(path))
    with pytest.raises(ValueError):
        list(records(path, limit=0))


def test_compact_response_uses_exact_source_and_rejects_bad_references():
    spans = [Span(0, "공고문", 0, 4, "제한조건")]
    text = json.dumps({"v": [1] + [0] * 23, "e": [1] + [0] * 23})
    labels, evidence = parse_output(text, spans)
    assert labels[0] == 1 and evidence[0] == "제한조건"
    for obj in [{"v": [0], "e": [0]}, {"v": [0] * 24, "e": [2] * 24},
                {"v": [True] * 24, "e": [0] * 24}]:
        with pytest.raises(ValueError):
            parse_output(json.dumps(obj), spans)


def test_retrieval_keeps_late_attachment_without_other_notices(record):
    record["docs"][0]["text"] = "일반 안내사항입니다.\n" * 800
    first = NoticeIndex(record)
    selected = first.select(4000, items=(9,))
    assert any("ZX99" in s.text for s in selected)
    for span in selected:
        assert span.text == record["docs"][span.doc_index]["text"][span.start:span.end]
        assert len(span.text) <= 440
    NoticeIndex({"docs": [{"type": "공고문", "text": "다른 공고의 모델 특정 조건"}]})
    assert NoticeIndex(record).select(4000, items=(9,)) == selected


def test_named_judgments_align_by_item_number_and_reject_partial_responses():
    spans = [Span(0, "공고문", 0, 4, "제한조건")]
    # Reverse insertion order must not move v24 into the v1 CSV column.
    obj = {f"v{k}": {"reason": "해당 없음", "v": int(k == 24), "e": int(k == 24)}
           for k in reversed(range(1, 25))}
    labels, evidence = parse_output(json.dumps(obj), spans)
    assert labels == [0] * 23 + [1]
    assert evidence == [""] * 23 + ["제한조건"]
    for field, bad in (("v", True), ("e", 2), ("reason", ""), ("reason", "가" * 111)):
        invalid = json.loads(json.dumps(obj))
        invalid["v24"][field] = bad
        with pytest.raises(ValueError):
            parse_output(json.dumps(invalid), spans)
    del obj["v1"]
    with pytest.raises(ValueError):
        parse_output(json.dumps(obj), spans)


def test_evidence_grammar_excludes_nonexistent_source_numbers():
    for count in (0, 3, 30):
        named = output_schema("reasoned", count)
        compact = output_schema("compact", count)
        valid = list(range(count + 1))
        assert named["properties"]["v24"]["properties"]["e"]["enum"] == valid
        assert compact["properties"]["e"]["items"]["enum"] == valid


def test_macro_f1_uses_24_items_and_id_alignment(tmp_path, record):
    other = {**record, "id": "case-2"}
    truth = [make_row(record, [1] + [0] * 23, [""] * 24), make_row(other, [0] * 24, [""] * 24)]
    pred = [make_row(other, [1] + [0] * 23, [""] * 24), truth[0]]
    write_csv(tmp_path / "truth.csv", truth)
    write_csv(tmp_path / "pred.csv", pred)
    result = evaluate(tmp_path / "truth.csv", tmp_path / "pred.csv")
    assert result["per_item"]["v1"]["fp"] == 1
    assert result["macro_f1"] == pytest.approx((2 / 3) / 24)


@pytest.mark.skipif(not (ROOT / "data_open/data/test.jsonl.gz").exists(), reason="official data not downloaded")
def test_mock_pipeline_and_no_silent_model_failure(tmp_path):
    source = ROOT / "data_open/data/test.jsonl.gz"
    data_dir = ROOT / "data_open/data"
    report = run(source, tmp_path / "mock_submission.csv", data_dir, Config(), MockRunner(), limit=2)
    assert report["mock"] and report["normal_model_calls"] == 0
    assert report["estimated_1853_seconds_in_this_environment"] is None
    with pytest.raises(ValueError, match="Mock"):
        run(source, tmp_path / "submission.csv", data_dir, Config(), MockRunner(), limit=1)

    class Broken(MockRunner):
        is_mock = False

        def generate(self, prompts, max_tokens=None):
            return [{"text": "malformed", "finish_reason": "stop", "output_tokens": 1} for _ in prompts]

    target = tmp_path / "broken" / "submission.csv"
    with pytest.raises(ValueError):
        run(source, target, data_dir, Config(), Broken(), limit=1)
    assert not target.exists()
