import json
from pathlib import Path

from pps.knowledge import Knowledge
from pps.pipeline import _response_row
from pps.prompts import Config, build_shared_prompts
from pps.retrieval import Span
from pps.retrieval import NoticeIndex

ROOT = Path(__file__).resolve().parents[1]
GROUPS = (tuple(range(1, 10)), tuple(range(10, 19)), tuple(range(19, 25)))


def test_group_specific_law_preserves_shared_sources_and_records_omissions():
    rec = {'id': 'synthetic-scope', 'meta': {'적용계약법': None}, 'docs': [
        {'type': '공고문', 'text': '본 계약은 지방계약법을 적용한다.\n입찰 참가자격\n중소기업인 업체만 참가 가능하다.'},
        {'type': '규격서', 'text': '규격: 동등 이상 제품의 납품을 허용한다.'}]}
    cfg = Config(mode='evidence_first', legal_context_version='v2', legal_chars=3600,
                 response_format='factored', rubric_version='v4', judgment_groups=GROUPS,
                 shared_prefix=True)
    prompts = build_shared_prompts(rec, Knowledge(ROOT/'data_open/data'), cfg, None, GROUPS)
    assert len(prompts) == 3
    prefixes = [p['messages'][1]['content'].split('[이번 항목의 배포 법령 참고 발췌]')[0] for p in prompts]
    assert prefixes[0] == prefixes[1] == prefixes[2]
    assert prompts[0]['spans'] == prompts[1]['spans'] == prompts[2]['spans']
    assert all(p['legal_diagnostics']['scope']['status'] == 'local' for p in prompts)
    assert all(p['legal_diagnostics']['used_chars'] <= 3600 for p in prompts)
    assert '판로지원' in prompts[1]['messages'][1]['content']
    assert any(g['group'] == 'sw_floor' for g in prompts[2]['legal_diagnostics']['selected'])
    assert all(p['coverage']['operative_candidates']['unshown_candidates'] == 0 for p in prompts)


def test_qualification_overlay_cannot_modify_unrequested_group():
    text = '중소기업인 업체만 참가 가능하다.'
    rec = {'id': 'synthetic-group', 'meta': {}, 'docs': [{'type': '공고문', 'text': text}]}
    prompt = {'spans': [Span(0, '공고문', 0, len(text), text)]}
    class Facts:
        calls = 0
        def qualification_decisions(self, record, row):
            self.calls += 1
            return {**row, 'v1': '1', 'e1': text, 'v17': '1', 'e17': text}, {'decisions': {}}
    knowledge = Facts()
    cfg = Config(qualification_checks=True)
    def response(items):
        return {'text': json.dumps({'v': [0]*len(items), 'e': [0]*len(items)})}
    for group in (GROUPS[0], GROUPS[2]):
        row, details = _response_row(rec, response(group), prompt, group, cfg, knowledge, group)
        assert row['v17'] == row['v1'] == 0 and details == []
    assert knowledge.calls == 0
    row, details = _response_row(rec, response(GROUPS[1]), prompt, GROUPS[1], cfg, knowledge, GROUPS[1])
    assert knowledge.calls == 1 and row['v17'] == 1 and row['v1'] == 0
    assert row['e17'] == text and details[0]['items'] == list(GROUPS[1])


def test_new_retrieval_evidence_keeps_operator_and_negation_in_final_row():
    text = '배정예산금액: +120000000원 이하이며 이를 초과하지 않는다.'
    rec = {'id': 'synthetic-evidence', 'meta': {}, 'docs': [{'type': '공고문', 'text': text}]}
    start = text.index('+')
    prompt = {'spans': [Span(0, '공고문', start, len(text), text[start:])]}
    response = {'text': json.dumps({'v': [1], 'e': [1]})}
    row, _ = _response_row(rec, response, prompt, (24,), Config(), None, (24,))
    assert row['v24'] == 1 and row['e24'] in text
    assert '+120000000원 이하이며 이를 초과하지 않는다.' in row['e24']
    assert row['e24'][0] not in '=+@'


def test_adjacent_source_lines_share_headers_without_losing_whitespace_or_polarity():
    text = '\n'.join(f'배정예산금액: {amount}원' for amount in range(100000001, 100000011))
    rec = {'docs': [{'type': '공고문', 'text': text}]}
    selected = NoticeIndex(rec).select(max(440, len(text) + 40), mode='evidence_first')
    assert len(selected) == 1 and selected[0].text == text
    assert selected[0].start == 0 and selected[0].end == len(text)
    assert selected.diagnostics['unshown_candidates'] == 0


def test_range_packing_never_bridges_omitted_words():
    from pps.retrieval import _merge_ranges
    text = '참가 허용\n제외 조건\n참가 제한'
    left, right = (0, 5), (13, len(text))
    assert _merge_ranges([left, right], text) == [left, right]
