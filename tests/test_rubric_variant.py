"""Rubric experiments must be confined to the A1/A19 suffix and opt-in."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from submission.pps.prompts import Config, build_prompt, build_shared_prompts
from submission.pps.rubrics import FACT_JUDGMENT_CONSISTENCY, FACT_JUDGMENT_RUBRIC

ROOT = Path(__file__).resolve().parents[1]
GROUPS = (tuple(range(1, 10)), tuple(range(10, 19)), tuple(range(19, 25)))
MARKER = '\n\n[이번 호출의 항목별 판단 안내]\n'


class Tokenizer:
    def apply_chat_template(self, messages, **kwargs):
        return list(map(ord, json.dumps(messages, ensure_ascii=False, separators=(',', ':'))))


class Knowledge:
    table = {f'v{i}': {'항목명': f'항목{i}'} for i in range(1, 25)}

    def legal_context(self, *args):
        return ''

    def product_matches(self, rec):
        return []


def fixture():
    rec = {'id': 'rubric-structural-fixture', 'meta': {}, 'docs': [
        {'doc_id': 'D0', 'type': '공고문', 'text':
         '2. 입찰참가자격\n가. 연구시설을 보유한 기관이어야 한다.\n3. 계약조건\n계약 체결 시 서류 제출.'}]}
    cfg = Config(shared_prefix=True, rubric_version='v6', response_format='fact_compact',
                 judgment_groups=GROUPS, legal_chars=0)
    return rec, Knowledge(), cfg, Tokenizer()


def test_current_is_default_and_packaged_default():
    assert Config().rubric_variant == 'current'
    assert Config.load(ROOT / 'submission/model/config.json').rubric_variant == 'current'
    rec, knowledge, cfg, tokenizer = fixture()
    implicit = build_shared_prompts(rec, knowledge, cfg, tokenizer, GROUPS)
    explicit = build_shared_prompts(rec, knowledge, replace(cfg, rubric_variant='current'), tokenizer, GROUPS)
    assert implicit == explicit
    # Hashes of pre-change module's chat tokens on this fixed, data-free fixture.
    hashes = [hashlib.sha256(json.dumps(p['token_ids']).encode()).hexdigest() for p in implicit]
    assert hashes == BASELINE_TOKEN_HASHES


BASELINE_TOKEN_HASHES = [
    'cdcf014f4522bf6e2fd04732419618883d6bcde104766962f9116d3e1048a58d',
    'c42cfd25a2d39a76fdd04c9cd42f82064cd5c41fc593a85c0b1028699a896312',
    '726d5046b8d8ac80f69f5db70293120540344a42ff775710ee6987471ec71357',
]


@pytest.mark.parametrize('variant', ['unknown', '', None, False])
def test_unknown_variant_rejected(variant):
    with pytest.raises(ValueError, match='Unknown rubric variant'):
        Config(rubric_variant=variant)


def test_variant_changes_only_target_suffixes_and_retains_output_contract():
    rec, knowledge, cfg, tokenizer = fixture()
    off = build_shared_prompts(rec, knowledge, cfg, tokenizer, GROUPS)
    on = build_shared_prompts(rec, knowledge, replace(cfg, rubric_variant='fact_judgment'), tokenizer, GROUPS)
    for i, (a, b) in enumerate(zip(off, on)):
        assert a['messages'][0] == b['messages'][0]
        assert a['spans'] == b['spans']
        assert a['document_budget'] == b['document_budget']
        assert a['shared_prefix_tokens'] == b['shared_prefix_tokens']
        n = a['shared_prefix_tokens']
        assert a['token_ids'][:n] == b['token_ids'][:n]
        assert a['messages'][1]['content'].split(MARKER)[0] == b['messages'][1]['content'].split(MARKER)[0]
        if i == 1:
            assert a == b
        else:
            assert a['token_ids'] != b['token_ids']
            _, rest = a['messages'][1]['content'].split('\n이번 호출에서 검토할 항목: ', 1)
            assert b['messages'][1]['content'].endswith('\n이번 호출에서 검토할 항목: ' + rest)
            assert FACT_JUDGMENT_CONSISTENCY in b['messages'][1]['content']
            assert len(b['token_ids']) + cfg.max_output_tokens + 32 <= cfg.max_model_len


@pytest.mark.parametrize('items', [(9,), (20,), tuple(range(10, 19)), tuple(range(1, 25))])
def test_other_groups_are_unchanged(items):
    rec, knowledge, cfg, tokenizer = fixture()
    off = build_shared_prompts(rec, knowledge, cfg, tokenizer, [items])
    on = build_shared_prompts(rec, knowledge, replace(cfg, rubric_variant='fact_judgment'), tokenizer, [items])
    assert off == on


def test_nonshared_specialist_route_unchanged():
    rec, knowledge, cfg, tokenizer = fixture()
    cfg = replace(cfg, shared_prefix=False, response_format='factored')
    assert build_prompt(rec, knowledge, cfg, tokenizer, (20,)) == build_prompt(
        rec, knowledge, replace(cfg, rubric_variant='fact_judgment'), tokenizer, (20,))


def test_supplement_preserves_exception_boundaries():
    assert '해당 항목의 위반유형' in FACT_JUDGMENT_CONSISTENCY
    assert '예외·금액대 비적용' in FACT_JUDGMENT_CONSISTENCY
    assert '평가 배점·우대는 제외' in FACT_JUDGMENT_RUBRIC[1]
    assert '지방 소액수의의 명시적 예외' in FACT_JUDGMENT_RUBRIC[2]
    assert '같은 품목에 동등 이상' in FACT_JUDGMENT_RUBRIC[9]
    assert '낙찰 후 확보·계약시 제출' in FACT_JUDGMENT_RUBRIC[19]
    assert '제출 가능 능력만이면 제외' in FACT_JUDGMENT_RUBRIC[19]


def test_variant_never_reallocates_source_budget():
    rec, knowledge, cfg, tokenizer = fixture()
    rec['docs'][0]['text'] *= 100
    cfg = replace(cfg, max_model_len=9000)
    off = build_shared_prompts(rec, knowledge, cfg, tokenizer, GROUPS)
    on = build_shared_prompts(rec, knowledge, replace(cfg, rubric_variant='fact_judgment'), tokenizer, GROUPS)
    assert all(a['spans'] == b['spans'] and a['document_budget'] == b['document_budget']
               for a, b in zip(off, on))


def test_variant_overflow_fails_without_silent_source_truncation():
    # Earlier rubric versions can be shorter than the compact v6 variant.
    rec, knowledge, cfg, tokenizer = fixture()
    cfg = replace(cfg, rubric_version='v3')
    old = build_shared_prompts(rec, knowledge, cfg, tokenizer, [GROUPS[0]])[0]
    tight = replace(cfg, max_model_len=len(old['token_ids']) + cfg.max_output_tokens + 32,
                    rubric_variant='fact_judgment', document_chars=880)
    with pytest.raises(ValueError, match='Rubric variant exceeds model context without changing the shared source'):
        build_shared_prompts(rec, knowledge, tight, tokenizer, [GROUPS[0]])


@pytest.mark.skipif(not (ROOT / 'models/gemma-tokenizer').is_dir(), reason='Local tokenizer unavailable')
def test_full_default_bundle_preserves_a10_q10_s9_l19_with_fixed_encoder():
    import numpy as np
    from transformers import AutoTokenizer
    from submission.b4_entry import B4Pipeline
    from submission.pps.data import records

    class Encoder:
        # A fixed retrieval-vector fixture, not a model or accuracy measurement.
        def encode(self, texts):
            return np.tile(np.array([[1., 0.]], dtype=np.float32), (len(texts), 1))

    rec = next(r for r in records(ROOT / 'data_open/dev.jsonl.gz') if r['id'] == 'PPS-DEV-22')
    tokenizer = AutoTokenizer.from_pretrained(ROOT / 'models/gemma-tokenizer', local_files_only=True)
    pipe = B4Pipeline(ROOT / 'data_open/data', tokenizer, encoder=Encoder())
    off = {p['batch']: p for p in pipe.bundle(rec)}
    pipe.config = replace(pipe.config, rubric_variant='fact_judgment')
    on = {p['batch']: p for p in pipe.bundle(rec)}
    assert off.keys() == on.keys()
    for group in ('A10', 'Q10', 'S9', 'L19'):
        assert off[group] == on[group]
    assert pipe.config.catalog_source_policy == 'task_hybrid'
    assert all(off[g]['token_ids'] != on[g]['token_ids'] for g in ('A1', 'A19'))
