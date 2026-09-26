"""Complete licence-field alternatives, with unrelated-number controls."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))
from pps_c import catalog, facts, judge, switches


def bundle(clause, registered='1234', qualification=True):
    head = '2. 입찰참가자격' if qualification else '1. 공고 개요'
    b = facts.build({'id': 'structural-licence-example', 'meta': {'업무구분': '용역'},
                     'docs': [{'type': '공고문', 'text': head + '\n가. ' + clause}]}, catalog.load())
    b.meta.license_flag, b.meta.license = 'Y', f'업종({registered})'
    return b


@pytest.mark.parametrize('form', [
    '물품공급업(세부업무)(1234) 또는 일반서비스업(5678)',
    '물품공급업(세부업무,1234) 또는 일반서비스업(5678)',
    '물품공급업（세부업무）（1234） 또는 일반서비스업(5678)',
    '입찰 업종코드: 5678 또는 1234',
    '입찰 업종번호: 5678, 1234',
    '입찰 업종코드: 5678 및 1234',
])
def test_registered_alternative_in_the_same_field_is_preserved(form, monkeypatch):
    monkeypatch.setattr(switches, 'V24_LICENSE_WIDE', True)
    assert judge.v24_license_wide(bundle(form + '로 등록한 업체')) is None
    assert judge.v24_license_wide(bundle(form + '로 등록한 업체', registered='9999')) is not None


def test_continued_codes_also_work_outside_a_qualification_heading(monkeypatch):
    monkeypatch.setattr(switches, 'V24_LICENSE_WIDE', True)
    assert judge.v24_license_wide(bundle('입찰 업종코드: 5678 또는 1234', qualification=False)) is None


@pytest.mark.parametrize('tail', ['담당부서(1234)', '연락처: 1234', '기준년도 1234', '등록번호 12345'])
def test_an_unrelated_number_cannot_complete_the_licence_field(tail, monkeypatch):
    monkeypatch.setattr(switches, 'V24_LICENSE_WIDE', True)
    assert judge.v24_license_wide(bundle('일반서비스업(5678)으로 등록한 업체; ' + tail)) is not None


def test_code_must_have_all_four_digits():
    assert judge.wide_license_codes('업종코드: 12345 또는 67890') == set()
    assert judge.wide_license_codes('일반서비스업(12345)') == set()


def test_common_licence_parser_is_unchanged_without_the_probe(monkeypatch):
    monkeypatch.setattr(switches, 'V24_LICENSE_WIDE', False)
    b = bundle('입찰 업종코드: 5678 또는 1234')
    assert judge.v24_license(b) is not None


def test_completing_alternatives_does_not_create_a_new_field_comparison(monkeypatch):
    monkeypatch.setattr(switches, 'V24_LICENSE_WIDE', True)
    # The audit completes a partially parsed licence field. Broadening to a new
    # field also needs shared-performance/qualification ownership, separately.
    assert judge.v24_license_wide(bundle('물품공급업(세부업무,5678)으로 등록한 업체')) is None
