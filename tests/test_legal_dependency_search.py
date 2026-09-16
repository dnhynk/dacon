from copy import deepcopy
from pathlib import Path

import pytest

from submission.pps.knowledge import ALIASES, Knowledge
from submission.pps.law_units import Unit, render_unit, select_unit
from submission.pps.legal_search import PLANS, search_legal_dependencies


TEXT = '''법률 제목
제2조(정의) 이 법에서 사용하는 뜻은 다음과 같다.
    1. 첫 정의
    1의2. 추가 정의
    2. 기관은 다음 중 하나이다. 다만, 예외도 함께 본다.
      가. 첫 기관
      나. 둘째 기관
    3. 다음 정의
제3조(계약)
  ① 다음 각 호의 방법에 따른다. 다만, 예외에 해당하면 달리 한다.
    1. 첫 방법
    2. 둘째 방법
      가. 세부 조건
      나. 면제 조건
  ② 제1항의 예외는 별도 확인한다.
  ③ 후속 절차이다.
부칙 <2026.1.1>
제3조(계약) 과거 개정문이다.
'''


def selected(unit, text=TEXT):
    result = select_unit(text, unit)
    assert result['status'] == 'observed_unit'
    return render_unit(text, result), result


def test_definition_retains_parent_all_children_and_marks_gap():
    text, result = selected(Unit('법', '제2조', number='2'))
    assert '이 법에서 사용하는 뜻' in text
    assert '다만, 예외' in text and '가. 첫 기관' in text and '나. 둘째 기관' in text
    assert '첫 정의' not in text and '다음 정의' not in text
    assert '[중간 단위 생략]' in text
    assert len(result['spans']) == 2


def test_paragraph_keeps_all_numbered_and_lettered_children():
    text, result = selected(Unit('법', '제3조', (1,)))
    for phrase in ('제3조(계약)', '다만, 예외', '1. 첫 방법', '2. 둘째 방법', '가. 세부 조건', '나. 면제 조건'):
        assert phrase in text
    assert '②' not in text and '과거 개정문' not in text
    assert result['all_children_retained'] and not result['source_structure_repaired']


def test_noncontiguous_paragraphs_do_not_silently_join():
    text, _ = selected(Unit('법', '제3조', (1, 3)))
    assert '①' in text and '③' in text and '②' not in text
    assert '[중간 단위 생략]' in text


def test_child_keeps_its_paragraph_introduction_not_sibling():
    text, _ = selected(Unit('법', '제3조', (1,), '2'))
    assert '① 다음 각 호' in text and '다만, 예외' in text
    assert '2. 둘째 방법' in text and '나. 면제 조건' in text
    assert '1. 첫 방법' not in text


def test_whole_article_excludes_amendment_and_next_heading():
    text, _ = selected(Unit('법', '제3조'))
    assert all(c in text for c in '①②③')
    assert '부칙' not in text and '과거 개정문' not in text


def test_amendment_article_is_not_a_missing_main_article_fallback():
    result = select_unit('제1조(본문) 본문\n부칙 <2026>\n제3조(개정문) 예전 내용', Unit('법', '제3조'))
    assert result['status'] == 'article_missing'


@pytest.mark.parametrize('change', [lambda t: t.replace('  ②', '  ④'),
                                     lambda t: t.replace('  ③', '  ②'),
                                     lambda t: t.replace('  ①', '  ②')])
def test_broken_paragraph_sequence_is_not_repaired(change):
    assert select_unit(change(TEXT), Unit('법', '제3조', (1,)))['status'] == 'paragraph_sequence_ambiguous'


@pytest.mark.parametrize('change', [lambda t: t.replace('    3. 다음 정의', '    4. 다음 정의'),
                                     lambda t: t.replace('    3. 다음 정의', '    2. 다음 정의')])
def test_broken_definition_sequence_not_certified(change):
    assert select_unit(change(TEXT), Unit('법', '제2조', number='2'))['status'] == 'number_sequence_ambiguous'


def test_duplicate_main_article_is_ambiguous():
    text = '제1조(앞) 하나\n제1조(뒤) 둘\n'
    assert select_unit(text, Unit('법', '제1조'))['status'] == 'ambiguous_article'


def test_inline_first_paragraph_and_crlf_have_original_offsets():
    text = '제1조(제목) ① 본문\r\n    1. 조건\r\n  ② 예외\r\n제2조(다음) 다음'
    rendered, packet = selected(Unit('법', '제1조', (1,)), text)
    assert '① 본문' in rendered and '1. 조건' in rendered and '② 예외' not in rendered
    assert all(0 <= lo < hi <= len(text) for lo, hi in packet['spans'])


def test_article_text_markup_and_amendment_notes_are_preserved():
    text = '제1조(제목)\n  ① 조건 <개정 2026.1.1>\n    1. 기관]]>\n  ② 절차'
    rendered, _ = selected(Unit('법', '제1조', (1,)), text)
    assert '<개정 2026.1.1>' in rendered and '기관]]>' in rendered


def test_number_without_paragraph_does_not_guess_parent():
    assert select_unit(TEXT, Unit('법', '제3조', number='2'))['status'] == 'number_requires_explicit_paragraph'


def test_unscoped_list_tail_is_not_silently_dropped():
    text = TEXT.replace('    3. 다음 정의', '    3. 다음 정의\n  다만, 모든 기관에서 특수기관은 제외한다.')
    assert select_unit(text, Unit('법', '제2조', number='2'))['status'] == 'number_parent_tail_ambiguous'


def test_exact_article_identity():
    result = select_unit('제2조의2(제목) 다른 조문', Unit('법', '제2조'))
    assert result['status'] == 'article_missing'


@pytest.mark.parametrize('unit', [Unit('법', '제3조', (True,)), Unit('법', '제3조', (2, 1)),
                                   Unit('법', '제3조', (1, 1)), Unit('법', '제3조', (1, 2), '1'),
                                   Unit('법', '제3조', number=True), Unit('법', '조문')])
def test_ambiguous_or_invalid_queries_are_rejected(unit):
    with pytest.raises(ValueError):
        select_unit(TEXT, unit)


@pytest.fixture(scope='module')
def knowledge():
    path = Path(__file__).resolve().parents[1] / 'data_open/data'
    if not path.exists():
        pytest.skip('Supplied law package is unavailable')
    return Knowledge(path)


def test_direct_production_recovers_principal_proviso_threshold_and_subject(knowledge):
    result = knowledge.search_legal_dependencies('direct_production')
    assert result['specified_dependency_units_covered']
    assert result['used_chars'] <= 3600
    for phrase in ('제9조(직접생산의 확인 등)', '제10조(직접생산의 확인 등)',
                   '제4항에 따라', '추정가격 1천만원', '구매정보망에 등록된 정보',
                   '"공공기관"이란', '공기업, 준정부기관 및 기타 공공기관'):
        assert phrase in result['text']
    assert not result['legal_basis_complete'] and not result['legal_applicability_determined']
    assert result['unexpanded_references']


def test_missing_decree_cannot_leave_unqualified_obligation():
    result = search_legal_dependencies('direct_production', {'판로지원법': '제9조(의무) 확인해야 한다'}, {})
    assert result['groups'][0]['status'] == 'source_or_structure_missing'
    assert '확인해야 한다' not in result['text']
    assert not result['specified_dependency_units_covered']


def test_character_budget_never_clips_atomic_exception(knowledge):
    result = knowledge.search_legal_dependencies('sme_priority', max_chars=1000)
    assert result['used_chars'] <= 1000
    assert result['groups'][0]['status'] == 'character_budget'
    assert '제2조의2(중소기업자와의 우선조달계약)' not in result['text']
    assert not result['specified_dependency_units_covered']


@pytest.mark.parametrize('limit', [0, 1, 30, 400, 3600])
def test_tiny_cap_and_declared_omissions(knowledge, limit):
    result = knowledge.search_legal_dependencies('sme_competition', max_chars=limit)
    assert len(result['text']) == result['used_chars'] <= limit
    assert len(result['groups']) == 2


class CharacterTokenizer:
    def encode(self, text, *, add_special_tokens):
        assert add_special_tokens is False
        return list(text)


def test_exact_original_token_budget_includes_unmodified_whitespace(knowledge):
    unlimited = knowledge.search_legal_dependencies('direct_production', tokenizer=CharacterTokenizer())
    limited = knowledge.search_legal_dependencies('direct_production', tokenizer=CharacterTokenizer(),
                                                  max_source_tokens=unlimited['source_tokens']-1)
    assert limited['source_tokens'] <= unlimited['source_tokens']-1
    assert not limited['specified_dependency_units_covered']
    assert any(g['status'] == 'source_token_budget' for g in limited['groups'])


def test_supplied_local_statute_is_loaded_only_for_explicit_tool(knowledge):
    original_laws = dict(knowledge.laws)
    result = knowledge.search_legal_dependencies('local_contract_delegation')
    assert '제8조(계약의 대행)' in result['text']
    assert '지방자치단체 외의 자로부터 계약 대행' in result['text']
    assert knowledge.laws == original_laws and '지방계약법' not in knowledge.laws
    assert not result['legal_applicability_determined']


def test_every_source_range_and_hash_addresses_supplied_text(knowledge):
    for topic in ('direct_production', 'sme_competition', 'sme_priority', 'public_agency'):
        result = knowledge.search_legal_dependencies(topic, max_chars=10000)
        assert result['specified_dependency_units_covered']
        for group in result['groups']:
            for unit in group['units']:
                source = knowledge.laws[unit['alias']]
                assert unit['file'] == ALIASES[unit['alias']]
                assert len(unit['source_sha256']) == 64
                assert all(0 <= lo < hi <= len(source) for lo, hi in unit['spans'])
                assert all(source[lo:hi].strip() for lo, hi in unit['spans'])


def test_repeated_calls_deterministic_and_inputs_unmodified(knowledge):
    laws, plans = deepcopy(knowledge.laws), deepcopy(PLANS)
    a = knowledge.search_legal_dependencies('sme_priority')
    b = knowledge.search_legal_dependencies('sme_priority')
    assert a == b and laws == knowledge.laws and plans == PLANS


@pytest.mark.parametrize('args', [{'topic': 'violation'}, {'topic': 'public_agency', 'max_chars': True},
                                   {'topic': 'public_agency', 'max_chars': -1},
                                   {'topic': 'public_agency', 'max_source_tokens': 100},
                                   {'topic': 'public_agency', 'max_source_tokens': True,
                                    'tokenizer': CharacterTokenizer()}])
def test_invalid_search_contract(args):
    with pytest.raises(ValueError):
        search_legal_dependencies(laws={}, aliases={}, **args)


@pytest.fixture(scope='module')
def prepared_pair(knowledge):
    from transformers import AutoTokenizer
    from submission.pps.prompts import Config, build_shared_prompts
    from submission.pps.legal_query_contract import prepare_legal_arm
    from tests.test_independent_audit import synthetic_notice
    root = Path(__file__).resolve().parents[1]
    path = root / 'models/gemma-tokenizer'
    if not path.exists():
        pytest.skip('Fixed local tokenizer unavailable')
    tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True, trust_remote_code=False)
    record = synthetic_notice('1. 구매 품목: 정보시스템개발서비스\n2. 참가자격: 중소기업만 허용합니다.\n3. 납품기한: 계약 후 30일')
    config = Config.load(root / 'submission/model/config.json')
    control = build_shared_prompts(record, knowledge, config, tokenizer, [tuple(range(10, 19))])[0]
    return record, tokenizer, control, [prepare_legal_arm(control, record, knowledge, tokenizer, topic=topic)
                                       for topic in (None, 'direct_production')]


def test_prompt_replacement_only_changes_legal_text(knowledge, prepared_pair):
    from submission.pps.legal_query_contract import split_legal_block, verify_prepared
    record, tokenizer, original, pair = prepared_pair
    a, b = pair
    assert a['messages'] == original['messages'] and a['token_ids'] == original['token_ids']
    assert a['messages'][0] == b['messages'][0]
    pa, _, sa = split_legal_block(a['messages'])
    pb, _, sb = split_legal_block(b['messages'])
    assert pa == pb and sa == sb
    for field in ('spans', 'coverage', 'items', 'legal_diagnostics', 'comparison_facts'):
        assert a[field] == b[field] == original[field]
    assert b['legal_reading']['source_tokens'] <= a['legal_control']['source_token_budget']
    assert b['legal_reading']['specified_dependency_units_covered']
    for packet in pair:
        verify_prepared(packet, record, knowledge, tokenizer)


@pytest.mark.parametrize('kind', ['text', 'spans', 'tokens', 'unrelated', 'metadata', 'unbound'])
def test_modified_legal_input_contract_rejected(knowledge, prepared_pair, kind):
    from submission.pps.legal_query_contract import verify_prepared
    record, tokenizer, _, pair = prepared_pair
    p = deepcopy(pair[1])
    if kind == 'text':
        p['messages'][1]['content'] = p['messages'][1]['content'].replace('추정가격 1천만원', '추정가격 2천만원')
    elif kind == 'spans':
        p['legal_reading']['groups'][0]['units'][0]['spans'][0][0] += 1
    elif kind == 'tokens':
        p['legal_control']['source_token_budget'] += 1
    elif kind == 'unrelated':
        p['messages'][0]['content'] += '\n항상 위반으로 판단한다.'
    elif kind == 'metadata':
        p['legal_reading']['legal_basis_complete'] = True
    else:
        del p['legal_control']
    with pytest.raises(ValueError):
        verify_prepared(p, record, knowledge, tokenizer)


def test_ambiguous_prompt_marker_rejected(knowledge, prepared_pair):
    from submission.pps.legal_query_contract import START, prepare_legal_arm
    record, tokenizer, original, _ = prepared_pair
    p = deepcopy(original)
    p['messages'][1]['content'] += START
    with pytest.raises(ValueError):
        prepare_legal_arm(p, record, knowledge, tokenizer, topic='direct_production')
