from __future__ import annotations

import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[1]
RUBRIC = ROOT / "tools" / "independent_gold" / "rubric_v1.md"


def rubric_text() -> str:
    return RUBRIC.read_text(encoding="utf-8")


def test_v13_special_status_paths_do_not_cure_medium_enterprise_exclusion():
    text = rubric_text()
    section = text.split("### v13", 1)[1].split("### v14", 1)[0]
    assert "벤처기업·창업자·비영리법인·특별법인" in section
    assert "일반 중기업" in section
    assert "축소는 치유되지 않는다" in section


def test_closed_catalog_and_principal_object_boundary_is_explicit():
    text = rubric_text()
    common = text.split("## 항목별 경계", 1)[0]
    assert "중기부고시_경쟁제품_세부품명.csv" in common
    assert "주목적물의 정확한 10자리 코드" in common
    assert "직접생산확인 문구 안에만 나온 별도 코드·품명" in common


def test_v6_keeps_basic_region_tokens_and_meta_conflicts_separate():
    text = rubric_text()
    section = text.split("### v6", 1)[1].split("### v7", 1)[0]
    normalized = " ".join(section.split())
    assert "하나의 계층적 기초지역 표현" in normalized
    assert "meta.지역제한여부=N" in section
    assert "문서 기준 v6" in section
    assert "v24를 서로 독립적으로" in normalized


def test_v17_does_not_let_a_copied_qualification_code_redefine_the_object():
    text = rubric_text()
    section = text.split("### v17", 1)[1].split("### v18", 1)[0]
    normalized = " ".join(section.split())
    assert "무관한 경쟁제품 코드나 직접생산 품명" in section
    assert "주목적물로 치환하지 않는다" in normalized
    assert "v12에서 독립적으로 본다" in section


def test_v18_is_absence_of_any_size_restriction_not_absence_of_narrow_wording():
    text = rubric_text()
    section = text.split("### v18", 1)[1].split("### v19", 1)[0]
    normalized = " ".join(section.split())
    assert "중소기업자" in section
    assert "기업규모 제한의 존재" in normalized
    assert "v18=0" in section
    assert "v17과 v18을 동시에 1로 만들지 않는다" in normalized


def test_v23_needs_an_actual_briefing_date_to_prove_a_short_interval():
    text = rubric_text()
    section = text.split("### v23", 1)[1].split("### v24", 1)[0]
    normalized = " ".join(section.split())
    assert "일정·장소는 개별 안내" in section
    assert "측정 가능한 공고기간 미달" in normalized
    assert "v23은 0" in normalized
    assert "미래의 개별 안내가 위법한 날짜" in normalized


def test_presence_items_do_not_invent_violations_inside_unprovided_documents():
    text = rubric_text()
    common = text.split("## 항목별 경계", 1)[0]
    normalized = " ".join(common.split())
    assert "존재하는 제한·지정·충돌" in normalized
    assert "미제공 문서에 위반 문구가 있을 가능성" in normalized
    assert "양성 구성요건의 입증책임" in normalized


def test_v1_statutory_nonprofit_or_path_is_not_an_arbitrary_institution_limit():
    text = rubric_text()
    section = text.split("### v1", 1)[1].split("### v2", 1)[0]
    normalized = " ".join(section.split())
    assert "참가범위를 넓히는 법정 OR 경로" in normalized
    assert "일반 업체를 그 기관형태로 축소한 제한이 아니다" in normalized


def test_v9_missing_speculation_and_v19_generic_pledge_are_closed_negative():
    text = rubric_text()
    v9 = " ".join(text.split("### v9", 1)[1].split("### v10", 1)[0].split())
    v19 = " ".join(text.split("### v19", 1)[1].split("### v20", 1)[0].split())
    assert "별도 규격서가 있다는 일반 참조만으로" in v9
    assert "특정 모델이 있을 가능성을 상상해 U" in v9
    assert "확약서" in v19 and "제3자 발급요건이 있을 가능성만으로 U" in v19


def test_complete_law_and_catalog_receipts_are_not_reclassified_as_missing():
    text = rubric_text()
    common = " ".join(text.split("## 항목별 경계", 1)[0].split())
    v5 = " ".join(text.split("### v5", 1)[1].split("### v6", 1)[0].split())
    assert "전체 행 수·무누락" in common
    assert "원본 CSV가 프롬프트에 전행 재인쇄되지 않았다" in common
    assert "가능한 모든 관련 분기의 상한보다 낮으면" in v5


def test_v24_does_not_compare_document_dates_to_platform_posting_dates():
    text = rubric_text()
    section = " ".join(text.split("### v24", 1)[1].split("## 출력", 1)[0].split())
    assert "공고문 머리말의 작성일" in section
    assert "meta의 실제 나라장터 게시일" in section
    assert "v24 비교축도 아니다" in section


def test_optional_catalog_exception_requires_actual_application_and_scope():
    text = rubric_text()
    v12 = " ".join(text.split("### v12", 1)[1].split("### v13", 1)[0].split())
    v14 = " ".join(text.split("### v14", 1)[1].split("### v15", 1)[0].split())
    assert "예외 가능" in v12
    assert "실제 예외 채택과 그 범위" in v12
    assert "전체를 일반제품으로 재분류하지 않는다" in v14
    assert "분리 가능한 범위·금액" in v14


def test_v24_keeps_object_codes_separate_from_registered_industry_codes():
    text = rubric_text()
    v24 = " ".join(text.split("### v24", 1)[1].split("## 출력", 1)[0].split())
    assert "세부품명번호·품명은 면허·등록업종 코드와 다른" in v24
    assert "차이만으로 v24=1을 만들지 않는다" in v24
    assert "v10-v18의 품목 동일성·경쟁제품 판단에서 무시하지" in v24


def test_v16_distinguishes_operative_exception_adoption_from_abstract_citation():
    text = rubric_text()
    v16 = " ".join(text.split("### v16", 1)[1].split("### v17", 1)[0].split())
    assert "이번 입찰에" in v16
    assert "단정적으로 선언" in v16
    assert "추상적 법령 인용" in v16
    assert "세부 예외호·내부 판단서의 부재만으로" in v16


def test_v20_requires_positive_sw_signal_before_missing_scope_can_be_material():
    text = rubric_text()
    v20 = " ".join(text.split("### v20", 1)[1].split("### v21", 1)[0].split())
    assert "SW 과업이 숨어 있을 가능성만으로 U를 만들지 않는다" in v20
    assert "계약상대자의 SW 객체" in v20
    assert "홈페이지에서 첨부문서를 확인" in v20
    assert "구체적 양성 신호" in v20


def test_v24_requires_reconciled_tax_rounding_not_blanket_one_won_tolerance():
    text = rubric_text()
    v24 = " ".join(text.split("### v24", 1)[1].split("## 출력", 1)[0].split())
    assert "문서의 추정가격+VAT가 그 기초금액과 정확히" in v24
    assert "통상적인 반올림으로 복원" in v24
    assert "임의 허용오차를 주지 말고" in v24
