"""Offline robustness and semantic controls; no development labels used here."""
import copy,unittest
from unittest.mock import patch
from submission.pps.temporal import predict,v23,v24


def rec(text,**meta):
    m={'적용계약법':'지방계약법','낙찰방법':'협상에의한계약','입찰추정가격':50000000,'공고게시일자':'20260101','배정예산금액':55000000,'계약방법':'제한경쟁','지역제한여부':None,'제한지역코드목록':None,'업종제한여부':None,'면허업종제한목록':None}
    m.update(meta)
    return {'meta':m,'docs':[{'doc_id':'notice','type':'공고문','text':text}],'dropped_doc_counts':{},'input_completeness':{'완전관측':True}}


class Dates(unittest.TestCase):
    def test_briefing_shortfall(self):
        r=rec('사업설명회 : 2026. 1. 15.\n\n제안서 제출 마감 : 2026. 1. 20.')
        self.assertEqual(v23(r)['value'],1)
    def test_evaluation_is_not_briefing(self):
        r=rec('제안서 평가회 : 2026. 1. 15.\n\n제안서 제출 마감 : 2026. 1. 20.')
        self.assertIsNone(v23(r)['value'])
    def test_bid_opening_not_proposal_deadline(self):
        r=rec('사업설명회 : 2026. 1. 15.\n\n개찰일 : 2026. 1. 20.',개찰예정일자='20260120')
        self.assertIsNone(v23(r)['value'])
    def test_price_only_deadline_not_proposal(self):
        r=rec('사업설명회 : 2026. 1. 15.\n\n가격입찰서 제출 마감 : 2026. 1. 20.')
        self.assertIsNone(v23(r)['value'])
    def test_explicit_no_briefing(self):
        self.assertEqual(v23(rec('제안요청서 설명회 : 제안요청서로 갈음'))['value'],0)
    def test_unknown_date_not_next_field_date(self):
        r=rec('사업설명회 : 추후 통보\n\n제안서 제출 마감 : 2026. 1. 20.')
        self.assertIsNone(v23(r)['value'])
    def test_exact_day_boundary_abstains(self):
        r=rec('사업설명회 : 2026. 1. 15.\n\n제안서 제출 마감 : 2026. 1. 25.')
        self.assertIsNone(v23(r)['value'])
    def test_both_intervals_clearly_sufficient(self):
        r=rec('사업설명회 : 2026. 1. 15.\n\n제안서 제출 마감 : 2026. 1. 31.')
        self.assertEqual(v23(r)['value'],0)
    def test_estimated_amount_boundary_changes_band(self):
        text='사업설명회 : 2026. 1. 15.\n\n제안서 제출 마감 : 2026. 1. 31.'
        self.assertEqual(v23(rec(text,입찰추정가격=100000000))['value'],1)
        self.assertEqual(v23(rec(text,입찰추정가격=99999999))['value'],0)
    def test_announcement_leg_alone_suffices(self):
        self.assertEqual(v23(rec('사업설명회 : 2026. 1. 5.'))['value'],1)
    def test_conflicting_deadlines_abstain(self):
        r=rec('사업설명회 : 2026. 1. 15.\n\n제안서 제출 마감 : 2026. 1. 20.\n\n제안서 접수 : 2026. 1. 22.')
        self.assertIsNone(v23(r)['value'])
    def test_date_range_last_endpoint(self):
        r=rec('사업설명회 : 2026. 1. 15.\n\n제안서 접수기간 : 2026. 1. 20. ~ 1. 31.')
        self.assertEqual(v23(r)['value'],0)
    def test_notice_estimate_priority_preserves_registration_conflict(self):
        r=rec('추정가격 : 100,000,000원\n\n사업설명회 : 2026. 1. 15.\n\n제안서 제출 마감 : 2026. 1. 20.')
        decision=v23(r)
        self.assertEqual(decision['value'],1)
        calc=next(f for f in decision['facts'] if f['kind']=='calculation')
        self.assertEqual(calc['required_days'],20)
        self.assertTrue(calc['price_resolution']['source_conflict'])
    def test_invalid_estimate_abstains_without_crash(self):
        text='사업설명회 : 2026. 1. 15.\n\n제안서 제출 마감 : 2026. 1. 20.'
        for price in ['알 수 없음','미입력',None,True,'NaN']:
            with self.subTest(price=price):self.assertIsNone(v23(rec(text,입찰추정가격=price))['value'])
    def test_unknown_law_and_conflicting_law(self):
        self.assertIsNone(v23(rec('사업설명회:2026.1.15.',적용계약법=None))['value'])
        self.assertEqual(v23(rec('본 계약은 국가계약법을 적용합니다.\n사업설명회:2026.1.15.'))['value'],0)
    def test_national_outside_scope(self):
        self.assertEqual(v23(rec('사업설명회:2026.1.15.',적용계약법='국가계약법'))['value'],0)
    def test_nonnegotiated_outside_scope(self):
        self.assertEqual(v23(rec('현장설명회:2026.1.15.',낙찰방법='적격심사제'))['value'],0)
    def test_workshop_after_award_is_not_tender_event(self):
        r=rec('주민 홍보 사업설명회 : 2026. 1. 15.\n\n제안서 접수 : 2026. 1. 20.')
        self.assertIsNone(v23(r)['value'])


class Metadata(unittest.TestCase):
    def test_explicit_budget_mismatch(self):
        v=v24(rec('사업예산 : 70,000,000원 (부가가치세 포함)'))
        self.assertEqual(v['value'],1)
        self.assertEqual(v['value_mismatches'][0]['field'],'budget_including_vat')
    def test_same_budget_not_global_negative(self):
        self.assertIsNone(v24(rec('사업예산 : 55,000,000원 (부가가치세 포함)'))['value'])
    def test_one_won_rounding_abstains(self):
        self.assertIsNone(v24(rec('사업예산 : 55,000,001원 (부가가치세 포함)'))['value'])
    def test_base_price_is_not_allocated_budget(self):
        self.assertIsNone(v24(rec('기초금액 : 40,000,000원 (부가가치세 포함)'))['value'])
    def test_estimated_price_is_not_allocated_budget(self):
        self.assertIsNone(v24(rec('추정가격 : 50,000,000원'))['value'])
    def test_vat_excluded_budget_unresolved(self):
        self.assertIsNone(v24(rec('사업예산 : 50,000,000원 (부가세 별도)'))['value'])
    def test_annual_budget_not_full_budget(self):
        self.assertIsNone(v24(rec('1차년도 사업예산 : 20,000,000원 (부가세 포함)'))['value'])
    def test_unknown_meta_not_mismatch(self):
        self.assertIsNone(v24(rec('사업예산 : 70,000,000원 (부가세 포함)',배정예산금액=None))['value'])
        self.assertIsNone(v24(rec('계약방법 : 제한경쟁',계약방법='미입력'))['value'])
    def test_negotiation_and_restricted_are_distinct_fields(self):
        self.assertIsNone(v24(rec('계약방법 : 협상에 의한 계약'))['value'])
    def test_normalized_quote_procedures(self):
        for t in ['입찰방법 : 전자입찰(소액수의견적제출, 총액입찰, 제한경쟁)','계약방법: 전자계약, 소액(총액)수의 견적입찰, 제한경쟁','입 찰 방 법 : 수의견적, 제한경쟁, 총액입찰']:
            with self.subTest(t=t):self.assertIsNone(v24(rec(t,계약방법='수의계약'))['value'])
    def test_real_contract_mismatch(self):
        self.assertEqual(v24(rec('계약방법 : 일반경쟁'))['value'],1)
    def test_conflicting_body_contracts_abstain(self):
        self.assertIsNone(v24(rec('계약방법 : 일반경쟁\n계약방법 : 제한경쟁'))['value'])
    def test_region_flag_is_diagnostic_only(self):
        v=v24(rec('주된 영업소 소재지가 경기도에 있는 업체',지역제한여부='N'))
        self.assertIsNone(v['value']);self.assertEqual(v['diagnostic_value'],1)
    def test_region_null_not_negative(self):
        v=v24(rec('주된 영업소 소재지가 경기도에 있는 업체',지역제한여부=None))
        self.assertIsNone(v['diagnostic_value'])
    def test_region_extra_province_audit(self):
        v=v24(rec('본점 소재지를 경기도 또는 제주도에 둔 업체',제한지역코드목록='경기도',지역제한여부='Y'))
        self.assertIsNone(v['value']);self.assertEqual(v['value_comparison_value'],1)
    def test_delivery_location_not_bidder_region(self):
        v=v24(rec('납품장소 : 경기도 내 공공시설',지역제한여부='N'))
        self.assertIsNone(v['diagnostic_value'])
    def test_exact_industry_mismatch(self):
        v=v24(rec('식품판매업(업종코드:5210)으로 등록한 자',면허업종제한목록='식품판매업(5246)',업종제한여부='Y'))
        self.assertEqual(v['value'],1)
    def test_optional_industry_not_hard_mismatch(self):
        v=v24(rec('관련 자격은 식품판매업(업종코드:5210) 또는 다른 업종으로 등록한 업체',면허업종제한목록='식품판매업(5246)',업종제한여부='Y'))
        self.assertIsNone(v['value'])
    def test_no_filesystem_or_record_mutation(self):
        r=rec('사업설명회 : 2026. 1. 15.\n제안서 제출 마감 : 2026. 1. 20.')
        saved=copy.deepcopy(r)
        with patch('builtins.open',side_effect=AssertionError('Predictor accessed filesystem')):
            a=predict(r);b=predict(copy.deepcopy(r))
        self.assertEqual(a,b);self.assertEqual(r,saved)


if __name__=='__main__':unittest.main(verbosity=2)
