"""Structural v9 equivalence controls, independent of notice IDs and labels."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))
from pps_c import catalog, facts, judge


def bundle(*docs):
    return facts.build({'id': 'structural-equivalence', 'meta': {'업무구분': '물품'},
                        'docs': [{'type': '규격서', 'text': '\n'.join(lines)} for lines in docs]}, catalog.load())


def dropped(b, text):
    return judge.x3_drop(b, next(row for row in b.notice.lines if row.text == text))


@pytest.mark.parametrize('parent,child,sibling', [
    ('1. 장비 (동등 이상)', '가. 제조사: 알파전자 AX-900', '2. 제조사: 베타전자 BX-200'),
    ('가. 장비 (동등 이상)', '1) 제조사: 알파전자 AX-900', '나. 제조사: 베타전자 BX-200'),
    ('1) 장비 (동등 또는 이상)', '(1) 제조사: 알파전자 AX-900', '2) 제조사: 베타전자 BX-200'),
    ('(1) 장비 (동등 이상)', '① 제조사: 알파전자 AX-900', '(2) 제조사: 베타전자 BX-200'),
    ('1.2.3 장비는 다음 부재를 사용하여야', '1) 제조사: 알파전자 AX-900', '1.2.4 제조사: 베타전자 BX-200'),
])
@pytest.mark.parametrize('gap', [0, 12])
def test_parent_permission_is_inherited_until_its_sibling(parent, child, sibling, gap):
    lines = [parent]
    if '동등' not in parent:
        lines.append('한다. (동등 또는 이상)')
    lines += [''] * gap + [child, sibling]
    b = bundle(lines)
    assert dropped(b, child)
    assert not dropped(b, sibling)


@pytest.mark.parametrize('marker', ['1.', '가.', '1)', '(1)', '①', '-'])
def test_trailing_note_belongs_to_current_item_not_previous_sibling(marker):
    first = marker + ' 제조사: 알파전자 AX-900'
    second = marker + ' 제조사: 베타전자 BX-200'
    b = bundle([first, second, '※ 품질 성능은 동등 이상의 제품이어야 한다.'])
    assert not dropped(b, first)
    assert dropped(b, second)


def test_trailing_note_includes_wrapped_properties_of_the_item():
    line = '형식명: AX-900'
    b = bundle(['1. 발주 규격', '- 탑재 장치', line, '규격: 최대출력 80kW',
                '제조자: 알파전자', '※ 품질 성능은 동등 이상의 제품이어야 한다.'])
    assert dropped(b, line)


@pytest.mark.parametrize('permission,expected', [
    ('본 시방서에 명시된 장비 및 기기류의 사양은 설계기준이며 동등품으로 대체 할 수 있다.', True),
    ('본 규격서에 제시된 모든 제품의 규격은 동등 이상의 제품으로 대체할 수 있다.', True),
    ('본 시방서에 명시된 모니터의 사양은 동등품으로 대체할 수 있다.', False),
    ('본 시방서에 명시된 장비 및 기기류의 사양은 동등품으로 대체할 수 없다.', False),
    ('동등품이라 함은 성능이 같은 제품을 뜻한다.', False),
])
def test_explicit_general_permission_is_document_wide(permission, expected):
    target = '(1) 제조사: 알파전자 AX-900'
    b = bundle(['1. 일반사항', '(1) ' + permission, '2. 상세규격', target])
    assert dropped(b, target) is expected


def test_general_permission_does_not_cross_attachments():
    target = '1. 제조사: 알파전자 AX-900'
    b = bundle(['본 시방서에 명시된 장비 및 기기류의 사양은 동등품으로 대체할 수 있다.'], [target])
    assert not dropped(b, target)


def test_explicit_child_denial_is_not_a_permission_keyword():
    target = '(1) 제조사: 알파전자 AX-900, 동등 제품은 허용하지 않는다.'
    b = bundle(['1) 장비는 동등 이상 허용', target])
    assert not dropped(b, target)


def test_unanswered_equivalence_checkbox_is_not_permission():
    target = '1. 모델명: AX-900'
    b = bundle([target, '구매희망 모델과 동등 이상이면 납품 가능여부', '가능( ), 불가능( )'])
    assert not dropped(b, target)


def test_unstructured_text_is_not_one_document_wide_permission():
    target = '(1) 제조사: 알파전자 AX-900'
    b = bundle([target, *['일반 설치 요건을 준수한다.'] * 15,
                '배관의 단가는 평균 직경과 동등 이상의 규격으로 계산한다.'])
    assert not dropped(b, target)


@pytest.mark.parametrize('heading', ['2. 입찰참가자격', '2. 제출 절차\n입찰참가자격은 다음 각 호의 요건을 모두 갖추어야 합니다.'])
def test_general_specification_permission_does_not_waive_bidder_qualification(heading):
    target = '가. 제조사: 알파전자 AX-900 공급업체만 참여할 수 있다.'
    b = bundle(['1. 일반사항',
                '(1) 본 시방서에 명시된 장비 및 기기류의 사양은 동등품으로 대체할 수 있다.',
                heading, target])
    assert not dropped(b, target)
