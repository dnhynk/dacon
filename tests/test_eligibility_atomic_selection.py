"""Opt-in source selection must preserve complete conditions and old packets."""
from dataclasses import replace
from pathlib import Path

import pytest

from submission.pps.knowledge import Knowledge
from submission.pps.prompts import Config, build_shared_prompts
from submission.pps.retrieval import NoticeIndex


def record(text):
    return {'id': 'structural-fixture', 'meta': {}, 'docs': [{'type': '공고문', 'text': text}]}


def represented(text, spans, clause):
    lo = text.index(clause)
    needed = {i for i in range(lo, lo + len(clause)) if not text[i].isspace()}
    shown = {i for s in spans for i in range(s.start, s.end)}
    return needed <= shown


def candidates(text):
    _, groups = NoticeIndex(record(text))._operative_candidates(True)
    return [c for group in groups for c in group]


@pytest.mark.parametrize('heading', ['2. 입찰참가자격', '2. 입 찰 참 가 자 격',
                                    '2. 입찰참가자격 (각호 모두 해당되는 업체)',
                                    '2. 입찰 참가 자격 : 아래 조건을 모두 갖추어야 합니다.'])
def test_all_siblings_get_heading_role_and_stop_at_next_section(heading):
    clauses = ['가. 설립목적에 따른 법인', '나. 지정 장소에서 활동하는 단체', '다. 전국에 지사를 둔 기관']
    outside = '가. 문의는 담당 부서로 연락'
    text = '\n'.join([heading, *clauses, '3. 문의처', outside])
    cs = candidates(text)
    for clause in clauses:
        lo = text.index(clause)
        assert any(c.start <= lo and c.end >= lo + len(clause) and 'qualification' in c.roles for c in cs)
    assert not any(c.start <= text.index(outside) < c.end for c in cs)


def test_numeric_children_do_not_end_the_qualification_section():
    text = ('2. 입찰참가자격\n2-1. 가공시설을 운영하는 법인\n'
            '2-2. 활동구역에 사무소가 있는 단체\n2-2-1. 보조 설명\n'
            '2-3. 연구기관\n3. 문의처\n담당 부서')
    cs = candidates(text)
    assert any(c.start == text.index('2-3.') for c in cs)
    assert not any(c.start >= text.index('3. 문의처') for c in cs)


def test_different_numeric_parent_ends_section_even_without_parent_heading():
    text = '2. 입찰참가자격\n2-1. 연구기관\n3-1. 배경 설명\n가. 담당 부서'
    assert not any(c.start >= text.index('3-1.') for c in candidates(text))


def test_bullet_heading_does_not_close_on_its_first_child():
    text = '○ 입찰참가자격\n○ 연구기관\n○ 활동 장소가 있는 단체\n3. 기타사항\n문의처'
    assert any(c.start == text.index('○ 활동') for c in candidates(text))


def test_nested_qualification_heading_and_date_retain_parent_section():
    text = ('2. 입찰참가자격\n2-1. 자격요건\n가. 연구기관\n'
            '2026.01.30. 기준\n2-2. 활동 장소가 있는 단체\n3. 기타사항')
    assert any(c.start == text.index('2-2.') for c in candidates(text))


@pytest.mark.parametrize('clause', [
    '나. 최근 4년간 단일계약 기준 3억원 이상의\n연구 용역 수행실적이 있는 업체',
    '나. 국가 및 공공기관에서 발주한\n조사 용역을 수행한 실적이 있는 기관',
])
def test_wrapped_amount_and_purchaser_are_atomic_before_background(clause):
    text = '연혁 소개 문장\n' * 100 + '2. 입찰참가자격\n가. 연구법인\n' + clause + '\n3. 안내\n문의처'
    index = NoticeIndex(record(text))
    spans = index.select(440, mode='evidence_first', eligibility_atomic_selection=True)
    assert represented(text, spans, clause)
    assert spans.diagnostics['charged_characters'] <= 440
    assert not represented(text, spans, '연혁 소개 문장\n' * 100)
    assert all(s.text == text[s.start:s.end] for s in spans)


def test_oversized_item_cannot_leak_through_another_role_or_background():
    clause = '가. 단일계약 금액은 3억원 이상의\n' + '긴 조건 설명이 계속되는 구절\n' * 45 + '수행실적을 보유한 업체'
    text = '2. 입찰참가자격\n' + clause + '\n나. 연구기관\n3. 안내\n문서 끝'
    spans = NoticeIndex(record(text)).select(440, mode='evidence_first', eligibility_atomic_selection=True)
    lo, hi = text.index(clause), text.index(clause) + len(clause)
    assert not any(s.start < hi and s.end > lo for s in spans)
    assert represented(text, spans, '나. 연구기관')


def test_exception_stays_with_item_across_allocation_roles():
    clause = '가. 시설을 보유한 업체만 참가 가능하다.\n다만, 공동 사용 시설도 인정한다.'
    text = '2. 입찰참가자격\n' + clause + '\n나. 연구기관\n3. 안내'
    cs = candidates(text)
    lo, hi = text.index(clause), text.index(clause) + len(clause)
    assert all(c.context_start <= lo and c.context_end >= hi
               for c in cs if c.start < hi and c.end > lo)


def test_switch_off_and_on_caches_do_not_contaminate_each_other():
    text = '배경 문장\n' * 100 + '2. 입찰참가자격\n가. 연구기관\n나. 전국 지사를 둔 단체\n3. 끝'
    index = NoticeIndex(record(text))
    before = index.select(440, mode='evidence_first')
    index.select(440, mode='evidence_first', eligibility_atomic_selection=True)
    after = index.select(440, mode='evidence_first', eligibility_atomic_selection=False)
    assert before == after
    assert before.diagnostics == after.diagnostics
    assert Config().eligibility_atomic_selection is False
    config = Config.load(Path(__file__).resolve().parents[1] / 'submission/model/config.json')
    assert config.eligibility_atomic_selection is False


def test_on_does_not_expand_overlapping_bundles_outside_eligibility():
    text = ('제품은 동등 이상으로 납품한다.\n다만, 크기는 동일하여야 한다.\n'
            '제품은 지정 규격으로 납품한다.\n배경 자료\n') * 8
    index = NoticeIndex(record(text))
    off = index.select(600, mode='evidence_first')
    on = index.select(600, mode='evidence_first', eligibility_atomic_selection=True)
    assert on == off
    assert on.diagnostics == off.diagnostics


def test_default_and_explicit_off_have_identical_prompt_tokens():
    class CharacterTokenizer:
        def apply_chat_template(self, messages, **kwargs):
            return [ord(c) for message in messages for c in message['role'] + message['content']]

    rec = record('2. 입찰참가자격\n가. 연구기관\n나. 시설을 보유한 업체\n3. 안내')
    groups = ((1, 2), (19, 22))
    config = Config(mode='evidence_first', rubric_version='v6', response_format='fact_compact',
                    shared_prefix=True, max_model_len=40000)
    knowledge = Knowledge(Path(__file__).resolve().parents[1] / 'data_open/data')
    implicit = build_shared_prompts(rec, knowledge, config, CharacterTokenizer(), groups)
    explicit = build_shared_prompts(rec, knowledge, replace(config, eligibility_atomic_selection=False),
                                    CharacterTokenizer(), groups)
    assert [p['token_ids'] for p in implicit] == [p['token_ids'] for p in explicit]
