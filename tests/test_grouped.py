import dataclasses
import gzip
import json
from pathlib import Path

import pytest

from pps.data import read_csv
from pps.pipeline import MockRunner, parse_output, run
from pps.prompts import Config, fact_fields, output_schema
from pps.retrieval import Span

ROOT = Path(__file__).resolve().parents[1]
GROUPS = ((1,2,3,4,5,6,7,8,9), (10,11,12,13,14,15,16,17,18), (19,20,21,22,23,24))


def test_thought_switch_is_passed_to_model_chat_template():
    from pps.prompts import token_ids

    class TemplateProbe:
        def apply_chat_template(self, messages, **kwargs):
            assert kwargs["tokenize"] and kwargs["add_generation_prompt"]
            return [1] if kwargs["enable_thinking"] else [0]

    assert token_ids(TemplateProbe(), [], True) == [1]
    assert token_ids(TemplateProbe(), []) == [0]


def factored(items):
    return {"facts": {k: "원문에 확인된 사실" for k in fact_fields(items)},
            "judgments": {f"v{k}": {"reason": "요구 조건 확인", "v": 1, "e": 1} for k in reversed(items)}}


def test_subset_facts_and_labels_keep_global_item_positions():
    items = GROUPS[1]
    obj = factored(items)
    labels, evidence = parse_output(json.dumps(obj), [Span(0,"공고문",0,4,"조건문구")], items)
    assert labels == [0]*9 + [1]*9 + [0]*6
    assert evidence == [""]*9 + ["조건문구"]*9 + [""]*6
    schema = output_schema("factored", 1, items)
    assert set(schema["properties"]["judgments"]["required"]) == {f"v{k}" for k in items}
    obj["judgments"]["v1"] = {"reason":"없는 항목", "v":0, "e":0}
    with pytest.raises(ValueError):
        parse_output(json.dumps(obj), [Span(0,"공고문",0,4,"조건문구")], items)


def test_empty_fact_is_not_a_complete_factored_response():
    obj = factored(GROUPS[0])
    obj["facts"][fact_fields(GROUPS[0])[0]] = ""
    with pytest.raises(ValueError, match="fact summary"):
        parse_output(json.dumps(obj), [], GROUPS[0])


@pytest.mark.skipif(not (ROOT/"data_open/data/항목표.json").exists(), reason="official material unavailable")
@pytest.mark.parametrize("shared_prefix", [False, True])
def test_group_pipeline_combines_every_item_without_erasing_other_groups(tmp_path, shared_prefix):
    recs = [{"id":str(n), "meta":{}, "docs":[{"doc_id":"a","type":"공고문","text":"조건문구"}]} for n in range(2)]
    source = tmp_path/"input.jsonl.gz"
    with gzip.open(source,"wt",encoding="utf-8") as f:
        f.write("\n".join(json.dumps(r,ensure_ascii=False) for r in recs))

    class FixtureRunner(MockRunner):
        def __init__(self):
            super().__init__()
            self.seen = []

        def generate(self, prompts, max_tokens=None):
            self.seen.extend(tuple(p["items"]) for p in prompts)
            return [{"text":json.dumps(factored(p["items"])),"finish_reason":"mock","output_tokens":0} for p in prompts]

    runner = FixtureRunner()
    cfg = Config(response_format="factored", judgment_groups=GROUPS, rubric_version="v4",
                 shared_prefix=shared_prefix, batch_size=1)
    report = run(source,tmp_path/"mock_submission.csv",ROOT/"data_open/data",cfg,runner)
    assert report["normal_model_calls"] == 0  # Fixture responses are never quality evidence.
    expected = list(GROUPS)*2 if shared_prefix else [g for group in GROUPS for g in (group,group)]
    assert runner.seen == expected
    rows = read_csv(tmp_path/"mock_submission.csv")
    assert len(rows) == 2 and all(int(row[f"v{k}"]) == 1 for row in rows for k in range(1,25))
    for groups in (GROUPS[:-1], ((1,1), *GROUPS[1:]), ((),*GROUPS)):
        with pytest.raises(ValueError,match="partition"):
            run(source,tmp_path/"invalid_mock.csv",ROOT/"data_open/data",dataclasses.replace(cfg,judgment_groups=groups),runner)


@pytest.mark.skipif(not (ROOT/"data_open/data/항목표.json").exists(), reason="official material unavailable")
def test_shared_source_and_evidence_index_fit_every_group_budget():
    from pps.knowledge import Knowledge
    from pps.prompts import build_shared_prompts

    class CharacterTokenizer:
        def apply_chat_template(self, messages, **kwargs):
            return list(map(ord, "\n".join(m["content"] for m in messages)))

    rec = {"id":"source-control","meta":{},"docs":[{"doc_id":"a","type":"공고문",
           "text":"입찰 참가조건과 직접생산 확인서, 공동수급 구성원 기준.\n"*800}]}
    cfg = Config(response_format="factored",rubric_version="v4",judgment_groups=GROUPS,
                 shared_prefix=True,legal_chars=0,span_overlap=0,document_chars=14000,
                 max_model_len=10000,max_output_tokens=2048)
    ps = build_shared_prompts(rec,Knowledge(ROOT/"data_open/data"),cfg,CharacterTokenizer(),GROUPS)
    assert len({p["document_budget"] for p in ps})==1
    assert ps[0]["spans"]==ps[1]["spans"]==ps[2]["spans"]
    common = [p["messages"][1]["content"].split("[이번 호출의 항목별 판단 안내]")[0] for p in ps]
    assert common[0]==common[1]==common[2]
    prefix = ps[0]["shared_prefix_tokens"]
    assert prefix>len(ps[0]["messages"][0]["content"])+100
    assert ps[0]["token_ids"][:prefix]==ps[1]["token_ids"][:prefix]==ps[2]["token_ids"][:prefix]
    assert all(len(p["token_ids"])+2048+32<=10000 for p in ps)
