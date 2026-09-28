"""V12 split: alternative permission, denial, and qualification controls."""
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))
from pps_c.v12_x3_split import alternative_match, verification_match, exclusion_match

@pytest.mark.parametrize('text', [
    '직접생산확인증명서 또는 정품 공급증명서를 제출할 수 있는 자',
    '나. 직접생산여부를 확인 할 수 있는 증명서류(직접생산확인증명서) 또는 정품 공급증명서를 제출할 수 있는 자',
    '직접생산확인증명서 또는 제조사 공급확약서를 보유한 업체',
])
def test_substantive_certificate_alternative(text):
    assert alternative_match(text)
    assert exclusion_match(text, text, alt=True)[0] == 'ALT_CERT_ROUTE'
    assert exclusion_match(text, text, verify=True) is None
    assert exclusion_match(text, text) is None

@pytest.mark.parametrize('text', [
    '직접생산여부를 확인',
    '직접생산확인증명서를 제출할 수 있는 자',
    '직접생산확인증명서 또는 정품 공급증명서를 제출할 수 없는 자',
    '직접생산확인증명서 또는 정품 공급증명서를 모두 제출할 수 있는 자',
    '직접생산확인증명서는 필수이며 또는 정품 공급증명서를 제출할 수 있는 자',
    '직접생산확인증명서 또는 정품 공급증명서를 제출할 수 있는 자는 제외한다.',
    '직접생산확인증명서가 필요하다. 별도 사업은 계약서 또는 정품 공급증명서를 제출할 수 있는 자',
    '정품 공급증명서를 제출할 수 있는 자',
])
def test_keyword_or_denied_alternative_does_not_filter(text):
    assert alternative_match(text) is None

@pytest.mark.parametrize('ending', [
    '입찰참가자격이 없음.', '입찰참가자격이 없습니다.',
    '입찰에 참가할 수 없습니다.', '입찰은 무효입니다.',
    '직접생산확인증명서를 소지하여야 합니다.',
])
def test_nonconfirmation_with_qualification_is_not_verification_only(ending):
    text = '직접생산확인증명서가 종합정보망에서 확인이 안 되거나 기업 구분과 다른 경우에는 ' + ending
    assert verification_match(text) is None
    assert exclusion_match(text, text, verify=True) is None


def test_independent_verification_filter():
    text = '직접생산확인증명서가 시스템에서 확인이 안 되거나 조회에 오류가 있으면 담당자에게 문의 바랍니다.'
    assert verification_match(text).group(0) == '확인이 안 되거나'
    assert exclusion_match(text, text, verify=True)[0] == 'VERIFY_UNCONFIRMED'
    assert exclusion_match(text, text, alt=True) is None


def test_confirmation_word_never_suffices():
    text = '직접생산여부를 확인'
    assert exclusion_match(text, text, alt=True, verify=True) is None
