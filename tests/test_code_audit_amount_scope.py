"""A nearby statement about a different base amount owns its own unit cue."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))
from pps_c import catalog, facts, judge


def bundle(*lines):
    return facts.build({'id': 'structural-unit-example',
                        'meta': {'업무구분': '일반용역', '배정예산금액': 110_000_000,
                                 '입찰추정가격': 100_000_000},
                        'docs': [{'type': '공고문', 'text': '\n'.join(lines)}]}, catalog.load())


def test_unit_note_can_explain_its_own_base_amount():
    b = bundle('1. 기초금액: 60,000,000원', '※ 기초금액은 품목별 단가총액으로 산정합니다.')
    assert judge.v24_base_zone(b) is None


def test_unit_note_for_another_section_cannot_erase_total_amount():
    b = bundle('1. 기초금액: 60,000,000원', '2. 별도 유지보수 계약',
               '※ 기초금액은 품목별 단가총액으로 산정합니다.')
    assert judge.v24_base_zone(b) is not None
