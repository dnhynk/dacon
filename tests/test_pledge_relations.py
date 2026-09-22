"""Pre-bid time, document issuer and mandatory action must describe one duty."""
import pytest

from submission.pps.other_checks import pledge_check
from submission.pps.rules import apply_rules
from tests.test_pledge_modality import notice


@pytest.mark.parametrize('text', [
    '입찰자는 제조사가 발급한 정품공급증명서를 제안서 제출 마감일까지 첨부하여야 한다.',
    '입찰등록 서류에는 공급업체가 발행한 공급확약서를 포함하여야 한다.',
    '제안사는 공급사의 기술지원확약서를 제안서 제출 마감일시까지 함께 내야 한다.',
    '유통업체가 견적을 제출할 때에는 제조업체의 공급보장 확약서를 함께 내야 한다.',
    '견적서에는 도서 공급을 보장하는 출판사 발행 확약을 붙여야 한다.',
    '물품 제조사의 정품인증서 및 A/S확약서를 전자입찰서 제출 마감일\n전일까지 보유하고, 계약체결 후 제출하여야 합니다.',
    '제조사의 기술지원확약서를 제출할 수 있는 업체(원본은 입찰서 제출 마감일 전일까지 제출)',
    '공급사의 물품공급확약서를 입찰서 접수 마감 전에 확보하여야 한다.',
    '공급사의 물품공급확약서를 입찰 전에 갖춘 업체만 참가한다.',
])
@pytest.mark.parametrize('model', [0, 1])
def test_positive_relation_recovers_model_zero_and_keeps_model_one(text, model):
    rec = notice(text)
    result = pledge_check(rec)
    assert result['value'] == 1
    row, _ = apply_rules(rec, {'v19': model, 'e19': text if model else ''}, items=(19,))
    assert row['v19'] == 1
    assert 0 < len(row['e19']) <= 500 and row['e19'] in text


@pytest.mark.parametrize('text', [
    '제조사의 정품공급증명서는 계약 체결 시 첨부하여야 한다.',
    '제조사의 정품공급증명서를 입찰 전에 제출할 수 있는 업체이어야 한다.',
    '제조사의 정품공급증명서를 입찰 전에 제출할 필요가 없다.',
    '제조사의 정품공급증명서를 입찰 전에 제출하지 않는다.',
    '제조사의 정품공급증명서를 입찰 전에 제출하여야 한다는 예시는 삭제한다.',
    '입찰자는 제조사와의 물품공급확약서를 제안서 제출 마감일까지 첨부하여야 한다.',
    '입찰자는 자사 명의로 작성한 물품공급확약서를 제안서 제출 마감일까지 첨부하여야 한다.',
    '공급업체는 사업자등록증명서를 입찰 전에 제출하여야 한다.',
    '제조사는 물품공급이 가능하다. 입찰자는 기술지원증명서를 입찰 전에 제출하여야 한다.',
    '제조사의 정품공급증명서는 계약 체결 시 제출한다. 보증서는 입찰 전에 보유하여야 한다.',
    '제조사의 정품공급증명서\n2. 입찰자는 보증서를 입찰 전에 제출하여야 한다.',
    '입찰서 제출 마감일까지 사업자등록증을 제출한다. 제조사의 정품공급증명서는 계약 후 제출한다.',
    '제조사의 정품공급증명서는 입찰 전에 제출하여야 하는 것은 아니다.',
    '제조사의 정품공급증명서는 입찰 전에 제출하여서는 안 된다.',
    '제조사의 정품공급증명서는 입찰 전에 보유하고 있지 않아도 된다.',
    '제조사의 정품공급증명서는 입찰 전에 제출함을 금지한다.',
    '제조사의 정품공급증명서를 보유한 입찰자가 직접 작성한 기술지원증명서를 입찰 전에 제출해야 한다.',
    '공급업체는 정품공급증명서를 입찰 전에 제출해야 한다.',
    '제조사는 자체 작성한 정품공급증명서를 입찰 전에 제출해야 한다.',
])
def test_legal_or_unresolved_relation_does_not_recover(text):
    assert pledge_check(notice(text))['value'] != 1


def test_no_recovery_preserves_model_positive():
    text = '제조사의 정품공급증명서 1부'
    row, _ = apply_rules(notice(text), {'v19': 1, 'e19': text}, items=(19,))
    assert row['v19'] == 1
