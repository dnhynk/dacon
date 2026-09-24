"""The opt-in reserve preserves complete clauses, caps and the off contract."""
from dataclasses import replace
from pathlib import Path

import pytest

from submission.pps.briefing_region_selection import anchor_candidates, reserve_anchors
from submission.pps.prompts import Config
from submission.pps.retrieval import Span


class CharacterTokenizer:
    def encode(self, text, **kwargs):
        return list(text)


def record(text):
    return {'id': 'source-fixture', 'meta': {}, 'docs': [{'type': '공고문', 'doc_id': 'D0', 'text': text}]}


def shown(spans, text):
    return any(text in s.text for s in spans)


@pytest.mark.parametrize('clause', [
    '사업설명회에 참석한 업체(미참석 업체는 제안 참가 자격 없음)',
    '입찰설명회에 참석한 경우에 한함.(참석업체 사업자등록증 지참)',
    '설명회에 불참한 업체도 입찰 참가가 가능하다.',
])
def test_undated_briefing_condition_reserved_with_its_polarity(clause):
    text = '배경 정보와 공지사항입니다.\n' * 100 + '\n2. 입찰참가자격\n가. ' + clause + '\n3. 안내\n끝'
    rec = record(text)
    original = [Span(0, '공고문', 0, 1200, text[:1200])]
    selected, _ = reserve_anchors(rec, original, CharacterTokenizer(), (22,))
    assert shown(selected, clause)
    assert sum(len(s.text) for s in selected) <= 1200
    assert all(s.text == text[s.start:s.end] for s in selected)


def test_split_event_heading_kept_with_attendance_and_exception():
    clause = '4. 사업설명회\n참석한 업체만 입찰 참가 가능하다.\n다만, 온라인 참석도 인정한다.'
    rec = record('안내사항입니다.\n' * 180 + clause)
    text = rec['docs'][0]['text']
    selected, _ = reserve_anchors(rec, [Span(0, '공고문', 0, 1200, text[:1200])], CharacterTokenizer(), (22,))
    assert shown(selected, clause)


def test_late_office_region_item_keeps_wrapped_condition_and_exception():
    clause = '나. 법인등기부상 본점 또는 영업소가\n광역시와 인접 지역에 소재한 업체\n다만, 공동수급 대표자는 다른 지역도 허용한다.'
    text = '안내사항입니다.\n' * 180 + '2. 입찰참가자격\n가. 등록 업체\n' + clause + '\n3. 기타사항'
    rec = record(text)
    original = [Span(0, '공고문', 0, 1200, text[:1200])]
    selected, _ = reserve_anchors(rec, original, CharacterTokenizer(), (6, 7))
    assert shown(selected, clause)
    assert sum(len(s.text) for s in selected) <= 1200


def test_task_and_contact_addresses_are_not_eligibility_anchors():
    rec = record('1. 과업범위\n가. 본점 소재지에 물품을 납품한다.\n2. 문의처\n영업소 소재지 연락처')
    assert anchor_candidates(rec, (6, 7)) == []


def test_oversized_condition_is_not_clipped_or_invented():
    text = '안내사항입니다.\n' * 180 + '2. 입찰참가자격\n가. 본점이 소재한 지역에\n' + '긴 조건의 내용이 이어지며\n' * 80 + '등록한 업체\n3. 안내'
    rec = record(text)
    original = [Span(0, '공고문', 0, 1200, text[:1200])]
    selected, _ = reserve_anchors(rec, original, CharacterTokenizer(), (6, 7))
    assert selected == original


def test_no_missing_anchor_is_exact_noop():
    text = '2. 입찰참가자격\n가. 사업설명회에 참석한 업체만 참가 가능하다.\n3. 안내'
    rec = record(text)
    spans = [Span(0, '공고문', 0, len(text), text)]
    selected, _ = reserve_anchors(rec, spans, CharacterTokenizer(), (22,))
    assert selected is spans


def test_defaults_and_type_contract():
    assert Config().briefing_region_anchor_selection is False
    config = Config.load(Path(__file__).resolve().parents[1] / 'submission/model/config.json')
    assert config.briefing_region_anchor_selection is False
    with pytest.raises(ValueError, match='must be boolean'):
        replace(config, briefing_region_anchor_selection=1)
