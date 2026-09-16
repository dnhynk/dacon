"""Meaningful source/fact dependency and execution contracts; no development labels."""
import copy
import dataclasses
import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from submission.b4_entry import B4Pipeline, digest, parse_error
from submission.pps.knowledge import Knowledge
from submission.pps.prompts import Config, fact_fields
from submission.pps import source_questions as questions
from submission.runtime import call_plan, execute
from submission.skips import validate as validate_skip

DATA = Path(__file__).resolve().parents[1] / 'data_open/data'


@pytest.fixture(scope='module')
def tokenizer():
    from transformers import AutoTokenizer
    return AutoTokenizer.from_pretrained(DATA.parents[1] / 'models/gemma-tokenizer',
                                         local_files_only=True, trust_remote_code=False)


def notice(*, resolved=False, complete=True):
    title = '통학버스 임차 및 운행 용역' if resolved else '업무 개선 지원 용역'
    body = f'1. 사업개요\n용역명: {title}\n추정가격: 8,000만원\n차량규격: 45인승 차량 2대\n'
    body += '2. 입찰참가자격\n가. 해당 업종을 등록한 업체\n'
    if resolved:
        body += ('나. 통학운송서비스(7811189902)의 직접생산확인증명서를 소지한 업체\n'
                 '다. 중소기업확인서를 소지한 업체\n')
    body += '3. 과업내용\n운전원을 제공하여야 한다.\n통학차량은 지정 노선을 운행하여야 한다.\n'
    return {'id': 'synthetic-source-questions',
        'meta': {'업무구분': '일반용역', '적용계약법': '국가계약법',
                 '계약방법': '제한경쟁', '입찰추정가격': 80_000_000},
        'docs': [{'type': '공고문', 'doc_id': 'test-notice', 'text': body}],
        'input_completeness': {'완전관측': complete}, 'dropped_doc_counts': {}}


def pipe(tokenizer, **options):
    return B4Pipeline(DATA, tokenizer, specification_review='current', catalog_review='current',
                      **options)


def response(items, value=0, claim='불명확'):
    return {'finish_reason': 'stop', 'output_tokens': 100,
        'text': json.dumps({'facts': {field: claim for field in fact_fields(items)},
                           'judgments': {'v': [value] * len(items), 'e': [0] * len(items)}})}


def test_unknown_initial_zeros_remain_model_questions():
    p = questions.plan(notice(complete=False), Knowledge(DATA))
    assert p['source_purchase_status'] == 'unknown'
    assert set(p['fixed']) == {'14', '15', '16'}  # Only the inapplicable price bands.
    assert p['model_items'] == [10, 11, 12, 13, 17, 18]


def test_source_service_resolution_does_not_require_a_model_claim():
    rec = notice(resolved=True)
    knowledge = Knowledge(DATA)
    source = knowledge.for_source(rec)
    model = knowledge.for_response(rec, response(questions.ITEMS, claim='경쟁제품이 아님'))
    assert source.packet == model.packet and source.packet['status'] == 'competition'
    assert source.provider_log['model_fact_used_for_identity'] is False
    p = questions.plan(rec, knowledge)
    assert p['model_items'] == [] and len(p['fixed']) == 9
    assert all(v['value'] == 0 for v in p['fixed'].values())


@pytest.mark.parametrize('claim', ['불명확', '경쟁제품에 해당하지 않음', '경쟁제품임'])
@pytest.mark.parametrize('value', [0, 1])
def test_projected_response_preserves_all_consumer_bits(tokenizer, claim, value):
    rec = notice()
    control_pipe = pipe(tokenizer)
    focused_pipe = pipe(tokenizer, a10_question_policy='source_questions')
    base = next(p for p in control_pipe.bundle(rec) if p['batch'] == 'A10')
    focused = next(p for p in focused_pipe.bundle(rec) if p['batch'] == 'A10')
    assert len(focused['items']) < 9
    full, _ = control_pipe.consume(rec, base, response(questions.ITEMS, value, claim))
    reduced, _ = focused_pipe.consume(rec, focused, response(focused['items'], value, claim))
    assert reduced == full


def test_questions_preserve_source_and_other_profiles(tokenizer):
    rec = notice()
    before = copy.deepcopy(rec)
    control = {p['batch']: p for p in pipe(tokenizer).bundle(rec)}
    focused = {p['batch']: p for p in pipe(tokenizer, a10_question_policy='source_questions').bundle(rec)}
    assert rec == before
    for profile in ('A1', 'A19', 'L19'):
        assert focused[profile] == control[profile]
    assert focused['A10']['spans'] == control['A10']['spans']
    assert focused['A10']['source_sha256'] == control['A10']['source_sha256']
    assert focused['A10']['generation']['thinking_budget'] == 768
    from submission.pps.legal_query_contract import verify_prepared
    verify_prepared(focused['A10'], rec, pipe(tokenizer).knowledge, tokenizer)
    assert parse_error(focused['A10'], response(focused['A10']['items'])) is None
    assert len(call_plan([rec], list(focused.values()))) == 4


@pytest.mark.parametrize('mutation', ['source', 'partition', 'fixed_type', 'skip_infers_normal'])
def test_wrong_source_partition_or_skip_refused(tokenizer, mutation):
    rec = notice(resolved=True)
    p = pipe(tokenizer, a10_question_policy='source_questions')
    packet = next(v for v in p.bundle(rec) if v['batch'] == 'A10')
    _, entry = questions.code_only(rec, packet, p.knowledge)
    if mutation == 'source':
        rec['docs'][0]['text'] += '추가 공고 문장'
        with pytest.raises(ValueError, match='plan changed'):
            questions.validate(rec, packet, p.knowledge)
    elif mutation == 'skip_infers_normal':
        entry['normality_from_missing_response'] = True
        with pytest.raises(ValueError):
            validate_skip(packet, entry)
    else:
        if mutation == 'partition':
            packet['source_questions']['model_items'] = [10]
        else:
            packet['source_questions']['fixed']['10']['value'] = False
        packet['source_questions_sha256'] = digest(packet['source_questions'])
        with pytest.raises(ValueError):
            questions.validate_structure(packet)


def test_all_fixed_call_is_accounted_without_a_model_response(tokenizer, tmp_path):
    rec = notice(resolved=True)
    source = tmp_path / 'input.jsonl.gz'
    with gzip.open(source, 'wt', encoding='utf8') as stream:
        stream.write(json.dumps(rec, ensure_ascii=False) + '\n')
    p = pipe(tokenizer, a10_question_policy='source_questions')
    runner = SimpleNamespace(config=p.config, tokenizer=tokenizer)
    observed = []
    def generate(packets, number, attempt):
        observed.extend(v['batch'] for v in packets)
        result = []
        for packet in packets:
            row = response(packet['items'])
            if packet['generation']['response_format'] == 'factored':
                obj = json.loads(row['text'])
                obj['judgments'] = {f'v{i}': {'reason': '불명확', 'v': 0, 'e': 0} for i in packet['items']}
                row['text'] = json.dumps(obj)
            result.append(row)
        return result
    out = tmp_path / 'execution'
    report = execute(source, DATA, out, runner, generation=generate,
                     specification_review='current', catalog_review='current',
                     a10_question_policy='source_questions')
    assert observed == ['A1', 'A19', 'L19']
    assert report['primary_requests'] == 3 and report['skipped_requests'] == 1
    skipped = json.loads((out / 'skipped_requests.json').read_text(encoding='utf8'))
    assert next(iter(skipped.values()))['rule'] == 'all_A10_items_source_fixed'
    with gzip.open(out / 'resolved_responses.jsonl.gz', 'rt', encoding='utf8') as stream:
        assert len(list(stream)) == 3
    assert (out / 'submission.csv').is_file()


def test_experimental_policy_requires_audited_input():
    with pytest.raises(ValueError):
        dataclasses.replace(Config(), a10_question_policy='source_questions')
    with pytest.raises(ValueError):
        dataclasses.replace(Config(), a10_question_policy='anything')
