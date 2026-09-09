"""Equivalent Korean contract registers must retain the same operative facts."""
import pytest

from pps.retrieval import NoticeIndex


@pytest.mark.parametrize('ending', ['하여야 한다', '하여야 합니다', '해야 한다', '해야 합니다'])
def test_proof_condition_survives_administrative_submission_flood(ending):
    # Unrelated formatting instructions should not crowd out a participation fact.
    noise = '\n\n'.join(f'{n}. 참고자료는 지정한 글자 크기 {n}로 작성하여 제출한다.' for n in range(1, 36))
    fact = f'해외 거래 실적은 해당 계약의 증빙을 첨부{ending}.'
    exception = '다만, 발주자 확인을 받은 건은 계약서 첨부 의무가 없다.'
    text = noise + '\n\n' + fact + '\n' + exception
    selected = NoticeIndex({'docs': [{'type': '제안요청서', 'text': text}]}).select(850, mode='evidence_first')
    visible = '\n'.join(s.text for s in selected)
    assert fact in visible and exception in visible
    assert all(s.text == text[s.start:s.end] for s in selected)


@pytest.mark.parametrize('predicate', ['인정한다', '인정합니다', '허용한다', '허용합니다', '허용된다', '허용됩니다'])
def test_permitted_scope_remains_visible_in_both_registers(predicate):
    noise = '\n'.join(f'제출 서류 {n}번 자료를 작성해야 한다.' for n in range(30))
    fact = f'민간 발주 실적도 동등하게 {predicate}.'
    text = noise + '\n' + fact
    selected = NoticeIndex({'docs': [{'type': '공고문', 'text': text}]}).select(650, mode='evidence_first')
    assert fact in '\n'.join(s.text for s in selected)


@pytest.mark.parametrize('predicate', ['인정하지 않는다', '인정하지 않습니다'])
def test_negative_scope_is_kept_as_source_without_changing_polarity(predicate):
    noise = '\n'.join(f'제출 서류 {n}번 자료를 작성해야 한다.' for n in range(30))
    fact = f'해당 범위 밖의 실적은 {predicate}.'
    text = noise + '\n' + fact
    selected = NoticeIndex({'docs': [{'type': '공고문', 'text': text}]}).select(650, mode='evidence_first')
    assert fact in '\n'.join(s.text for s in selected)
