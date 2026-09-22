import importlib.util
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "independent_gold" / "annotate.py"
SPEC = importlib.util.spec_from_file_location("independent_gold_annotate", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_annotation_module_does_not_import_submission():
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "import submission" not in source
    assert "from submission" not in source
    assert "import pps" not in source
    assert "from pps" not in source


def test_evidence_is_located_by_exact_source_coordinates():
    record = {
        "id": "fixture",
        "docs": [{"doc_id": "D0", "type": "공고문", "text": "앞 문장\n정확한 근거\n뒤 문장"}],
    }
    _, docs = MODULE.canonical_sources(record)
    decisions = {
        item: {"label": 0, "confidence": "high", "evidence": "", "reason": ""}
        for item in MODULE.ITEMS
    }
    decisions["v1"] = {
        "label": 1,
        "confidence": "high",
        "evidence": "정확한 근거",
        "reason": "fixture",
    }
    assert MODULE.locate_evidence(decisions, docs) == []
    location = decisions["v1"]["evidence_locations"][0]
    assert record["docs"][0]["text"][location["start"] : location["end"]] == "정확한 근거"


def test_positive_without_evidence_is_rejected():
    record = {"id": "fixture", "docs": [{"doc_id": "D0", "type": "공고문", "text": "x"}]}
    _, docs = MODULE.canonical_sources(record)
    decisions = {
        item: {"label": 0, "confidence": "high", "evidence": "", "reason": ""}
        for item in MODULE.ITEMS
    }
    decisions["v9"]["label"] = 1
    assert "v9: positive_without_evidence" in MODULE.locate_evidence(decisions, docs)


def test_annotation_source_has_no_production_runtime_dependency():
    for path in (ROOT / "tools" / "independent_gold").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "submission/" not in source
        assert "submission\\" not in source
        assert "saved_response" not in source
        assert "development_predictions" not in source


def test_compact_output_normalizes_to_twenty_four_cells():
    raw = {
        "labels": "1" + "0" * 22 + "U",
        "confidence": "H" * 23 + "L",
        "evidence": {"v1": "정확한 근거"},
    }
    decisions = MODULE.normalize_decision(raw)
    assert len(decisions) == 24
    assert decisions["v1"]["label"] == 1
    assert decisions["v1"]["evidence"] == "정확한 근거"
    assert decisions["v24"]["label"] == "U"
