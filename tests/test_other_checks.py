"""Synthetic semantic controls; no development labels or record identities."""
import copy
import unittest
from submission.pps.other_checks import predict,pledge_check,sw_check,briefing_check,won,budget_facts,overlay


def notice(text,*,complete=True,law='국가계약법',authority='국가기관',award='협상에의한계약',budget=None,extra=()):
    return {'meta':{'적용계약법':law,'소관구분':authority,'낙찰방법':award,'업무구분':'일반용역','배정예산금액':budget},
            'docs':[{'type':'공고문','text':text}]+[{'type':kind,'text':body} for kind,body in extra],
            'input_completeness':{'완전관측':complete},'dropped_doc_counts':{} if complete else {'제안요청서':1}}


def test_withdrawn_attendance_requirement_and_conflicting_floor_clauses_abstain():
    withdrawn = notice('사업설명회 불참 업체는 참가 불가라는 조건은 삭제되었습니다.')
    assert briefing_check(withdrawn)['value'] is None
    contradictory = notice('소프트웨어진흥법 제48조에 따른 사업금액별 참여 제한을 적용한다.\n'
                           '소프트웨어진흥법 제48조의 참여 제한을 적용하지 않는다.')
    assert sw_check(contradictory)['value'] is None


class PledgeControls(unittest.TestCase):
    def test_early_possession_late_submission(self):
        self.assertEqual(pledge_check(notice('제조사의 기술지원 확약서는 입찰 전까지 보유하여야 하고 계약 시 제출한다.'))['value'],1)
    def test_explicit_bidder_written(self):
        self.assertEqual(pledge_check(notice('입찰자가 직접 작성한 기술지원 확약서는 입찰 전 제출한다.'))['value'],0)
    def test_mixed_issuer_clause_abstains(self):
        self.assertIsNone(pledge_check(notice('입찰자가 직접 작성한 기술지원 확약서와 제조사의 물품공급확약서는 입찰 전 제출한다.'))['value'])
    def test_form_first_person_is_unknown(self):
        self.assertIsNone(pledge_check(notice('기술지원 확약서: 당사는 기술지원을 약속합니다. 업체명, 대표이사.'))['value'])
    def test_unknown_issuer(self):
        self.assertIsNone(pledge_check(notice('기술지원 확약서는 입찰 전 제출한다.'))['value'])
    def test_list_does_not_prove_timing(self):
        self.assertIsNone(pledge_check(notice('제조사의 기술지원 확약서 각 1부'))['value'])
    def test_explicit_later_stages(self):
        for stage in ['낙찰 후','계약 시','계약 전','물품 납품 전','착수 전']:
            with self.subTest(stage=stage):self.assertEqual(pledge_check(notice('제조사의 기술지원 확약서를 '+stage+' 제출한다.'))['value'],0)
    def test_capability_not_possession(self):
        self.assertIsNone(pledge_check(notice('제조사의 기술지원 확약서를 제출할 수 있는 업체'))['value'])
    def test_capability_does_not_bind_unrelated_later_pledge(self):
        self.assertIsNone(pledge_check(notice('제조사의 기술지원 확약서를 제출할 수 있는 업체\n공급사의 정품 공급확약서는 계약 시 제출한다.'))['value'])
    def test_same_compound_bundle_can_bind(self):
        text='제조사의 물품공급확약서(규격서, 제품사진), 유지보수 및 사후관리확약서 제출이 가능한 업체'
        extra=(('규격서','공급업체는 제조사의 「물품공급확약서(규격서, 제품사진), 유지보수 및 사후관리확약서」를 계약 전 제출한다.'),)
        self.assertEqual(pledge_check(notice(text,extra=extra))['value'],0)
    def test_early_requirement_not_borrowed_from_next_clause(self):
        self.assertIsNone(pledge_check(notice('제조사의 기술지원 확약서\n입찰 전에는 사업자등록증을 제출한다.'))['value'])
    def test_issuer_not_borrowed_from_previous_clause(self):
        self.assertIsNone(pledge_check(notice('제조사는 물품을 공급한다.\n기술지원 확약서는 입찰 전 제출한다.'))['value'])
    def test_negated_bid_time_requirement(self):
        self.assertEqual(pledge_check(notice('제조사의 기술지원 확약서는 입찰 전 제출할 필요 없고 계약 시 제출한다.'))['value'],0)
    def test_deleted_requirement_abstains(self):
        self.assertIsNone(pledge_check(notice('제조사의 기술지원 확약서를 입찰 전 제출하는 규정은 삭제한다.'))['value'])
    def test_security_document_different_function(self):
        self.assertEqual(pledge_check(notice('자료 복사본 미보유 확약서를 입찰 전 제출한다.'))['value'],0)
    def test_certificate_not_automatically_pledge(self):
        self.assertIsNone(pledge_check(notice('제조자증명서와 판매대리점 계약서를 입찰 전 제출한다.'))['value'])
    def test_dropped_docs_block_negative(self):
        self.assertIsNone(pledge_check(notice('기술지원 확약서는 납품 전 제출한다.',complete=False))['value'])
    def test_visible_positive_survives_dropped_docs(self):
        self.assertEqual(pledge_check(notice('제조사의 기술지원 확약서는 입찰 전 제출한다.',complete=False))['value'],1)
    def test_unknown_law_abstains(self):
        self.assertIsNone(pledge_check(notice('제조사의 기술지원 확약서는 입찰 전 제출한다.',law=None))['value'])


class MoneyControls(unittest.TestCase):
    def test_won_units(self):
        for source,value in [('20억원',2_000_000_000),('40억원',4_000_000_000),('80억원',8_000_000_000),('8,000억원',800_000_000_000),('2억5천만원',250_000_000),('금29,060,000원',29_060_000),('1.5억원',150_000_000)]:
            with self.subTest(source=source):self.assertEqual(won(source),value)
    def test_invalid_amounts(self):
        for source in ['-20억원','20억원 미만','20달러','영원','0원','NaN']:
            with self.subTest(source=source):self.assertIsNone(won(source))
    def test_strict_threshold_boundaries(self):
        for value,band in [(1999999999,'below_20eok'),(2000000000,'20_to_below_40eok'),(3999999999,'20_to_below_40eok'),(4000000000,'40_to_below_80eok'),(7999999999,'40_to_below_80eok'),(8000000000,'at_least_80eok')]:
            with self.subTest(value=value):self.assertEqual(budget_facts(notice(f'사업예산: {value}원 (부가세 포함)'))['band'],band)
    def test_estimated_price_not_budget(self):
        self.assertIsNone(budget_facts(notice('추정가격: 2,000,000,000원 (부가세 별도)'))['effective_won'])
    def test_VAT_negations_not_inclusive(self):
        for tax in ['부가세 미포함','부가가치세 제외','VAT 별도']:
            with self.subTest(tax=tax):self.assertIsNone(budget_facts(notice('사업예산: 20억원 ('+tax+')'))['effective_won'])
    def test_one_won_meta_conflict_retained_with_official_notice_priority(self):
        facts=budget_facts(notice('사업예산: 20억원 (부가세 포함)',budget=2_000_000_001))
        self.assertTrue(facts['metadata_conflict'])
        self.assertEqual(facts['effective_won'],'2000000000')
    def test_base_price_not_project_budget(self):
        self.assertIsNone(budget_facts(notice('기초금액: 2,000,000,000원 (부가세 포함)'))['effective_won'])
    def test_metadata_tax_basis_unknown(self):
        self.assertIsNone(budget_facts(notice('',budget=2_000_000_000))['effective_won'])
    def test_body_metadata_conflict(self):
        b=budget_facts(notice('사업예산: 20억원 (부가세 포함)',budget=3_000_000_000))
        self.assertTrue(b['metadata_conflict']);self.assertEqual(b['effective_won'],'2000000000')
    def test_long_maintenance_annualization(self):
        b=budget_facts(notice('사업예산: 80억원 (부가세 포함)\n장기계속계약 소프트웨어 유지보수\n계약기간: 24개월'))
        self.assertTrue(b['annualized']);self.assertEqual(b['effective_won'],'4000000000')
    def test_system_build_not_annualized(self):
        b=budget_facts(notice('사업예산: 80억원 (부가세 포함)\n장기계속계약 정보시스템 구축\n계약기간: 24개월'))
        self.assertFalse(b['annualized']);self.assertEqual(b['effective_won'],'8000000000')
    def test_missing_maintenance_term_abstains(self):
        self.assertIsNone(budget_facts(notice('사업예산: 80억원 (부가세 포함)\n장기계속계약 소프트웨어 유지보수'))['effective_won'])
    def test_split_component_amount_abstains(self):
        self.assertIsNone(budget_facts(notice('사업예산: 80억원 (부가세 포함)\n소프트웨어사업은 비소프트웨어사업과 분리하여 발주한다.'))['effective_won'])
    def test_bundled_SW_lowest_component_unknown(self):
        self.assertIsNone(budget_facts(notice('사업예산: 80억원 (부가세 포함)\n둘 이상의 소프트웨어사업을 일괄 발주한다.'))['effective_won'])


class SoftwareControls(unittest.TestCase):
    def test_complete_actual_SW_missing_floor(self):
        self.assertEqual(sw_check(notice('본 사업은 소프트웨어사업이다.'))['value'],1)
    def test_dropped_actual_SW_missing_floor(self):
        self.assertIsNone(sw_check(notice('본 사업은 소프트웨어사업이다.',complete=False))['value'])
    def test_unknown_public_scope(self):
        self.assertIsNone(sw_check(notice('본 사업은 소프트웨어사업이다.',authority=None))['value'])
    def test_registration_alone_is_not_actual_work(self):
        self.assertIsNone(sw_check(notice('소프트웨어사업자(컴퓨터관련서비스사업) 등록 업체'))['value'])
    def test_incidental_AI_and_software_price_formula(self):
        self.assertIsNone(sw_check(notice('AI 교육 연구용역\n소프트웨어사업인 경우 평가점수 산정공식 적용'))['value'])
    def test_explicit_not_software(self):
        # The preserved highest-score consumer resolves an explicit declaration
        # only in a complete, non-conflicting source; the root legacy did not.
        decision = sw_check(notice('본 사업은 소프트웨어사업이 아니다.'))
        self.assertEqual(decision['value'], 0)
        self.assertEqual(decision['reason'], 'explicit_non_SW_scope_in_complete_source')
    def test_explicit_not_software_incomplete_is_unresolved(self):
        self.assertIsNone(sw_check(notice('본 사업은 소프트웨어사업이 아니다.', complete=False))['value'])
    def test_explicit_not_software_does_not_erase_conflicting_work(self):
        self.assertIsNone(sw_check(notice('본 사업은 소프트웨어사업이 아니다.\n본 사업은 소프트웨어사업이다.'))['value'])
    def test_license_no_blanket_exception(self):
        self.assertEqual(sw_check(notice('소프트웨어사업자(컴퓨터관련서비스사업) 등록\n라이선스 갱신 구매'))['value'],1)
    def test_applied_floor_and_basis(self):
        self.assertEqual(sw_check(notice('소프트웨어진흥법 제48조에 따른 소프트웨어사업자의 사업금액별 참여 제한'))['value'],0)
    def test_actual_exemption_claim_abstains(self):
        self.assertIsNone(sw_check(notice('본 사업은 소프트웨어사업이다.\n소프트웨어진흥법 제48조 제3항 예외 적용'))['value'])
    def test_security_is_not_automatic_exception(self):
        self.assertEqual(sw_check(notice('본 사업은 소프트웨어사업이다. 국가안보 분야에 사용한다.'))['value'],1)
    def test_conglomerate_ban_not_floor(self):
        self.assertIsNone(sw_check(notice('본 사업은 소프트웨어사업이다.\n소프트웨어진흥법 제48조 제4항에 따라 상호출자제한 대기업 참여 제한'))['value'])
    def test_negated_restriction_abstains(self):
        self.assertIsNone(sw_check(notice('소프트웨어진흥법 제48조에 따른 대기업 참여 제한하지 않는다.'))['value'])
    def test_claimed_budget_band_conflict(self):
        text='사업예산: 40억원 (부가세 포함)\n총 사업금액 20억 미만인 사업으로 소프트웨어진흥법 제48조에 따라 중소 소프트웨어사업자만 입찰참가 가능'
        self.assertIsNone(sw_check(notice(text))['value'])
    def test_floor_not_same_as_generic_law_reference(self):
        self.assertEqual(sw_check(notice('본 사업은 소프트웨어사업이다.\n소프트웨어진흥법 제48조를 참고한다.'))['value'],1)
    def test_explicit_floor_presence_survives_dropped_docs(self):
        self.assertEqual(sw_check(notice('소프트웨어진흥법 제48조에 따른 사업금액별 참여 제한',complete=False))['value'],0)


class BriefingControls(unittest.TestCase):
    def test_attendees_only_eligibility(self):
        self.assertEqual(briefing_check(notice('사업설명회 참석업체에 한하여 제안서 제출 자격을 부여한다.'))['value'],1)
    def test_proposal_nonreceipt_is_eligibility(self):
        self.assertEqual(briefing_check(notice('사업설명회 미참석 업체의 제안서는 접수하지 않음'))['value'],1)
    def test_undated_qualification(self):
        self.assertEqual(briefing_check(notice('3. 입찰참가자격\n발주기관 사업설명회에 참석한 자 (일정 추후 안내)'))['value'],1)
    def test_evaluation_presentation_not_prior_briefing(self):
        self.assertIsNone(briefing_check(notice('제안서 설명회 불참 업체는 입찰 참가 대상에서 제외한다.'))['value'])
    def test_evaluation_committee_event_not_prior_briefing(self):
        self.assertIsNone(briefing_check(notice('사업설명회에서 평가위원이 평가하며 불참 업체는 대상에서 제외한다.'))['value'])
    def test_scoring_only_not_eligibility(self):
        self.assertIsNone(briefing_check(notice('사업설명회 불참 업체는 평가점수 0점을 부여한다.'))['value'])
    def test_post_award_report_not_prior(self):
        self.assertIsNone(briefing_check(notice('계약 후 사업설명회는 최종 보고를 위해 개최한다.'))['value'])
    def test_explicit_optional(self):
        self.assertEqual(briefing_check(notice('사업설명회는 참석 여부와 상관없이 입찰 참가 가능'))['value'],0)
    def test_negated_exclusion(self):
        self.assertEqual(briefing_check(notice('사업설명회 불참 업체는 입찰 참가 대상에서 제외하지 않는다.'))['value'],0)
    def test_double_negation_abstains(self):
        self.assertIsNone(briefing_check(notice('사업설명회 미참석 업체의 제안서를 접수하지 않는 것은 아니다.'))['value'])
    def test_no_briefing(self):
        self.assertEqual(briefing_check(notice('현장설명회는 생략한다.'))['value'],0)
    def test_conflicting_attendance_conditions(self):
        self.assertIsNone(briefing_check(notice('사업설명회 참석업체에 한하여 제안서 제출 자격을 부여한다.\n사업설명회 참석 여부와 상관없이 입찰 참가 가능'))['value'])
    def test_nonnegotiated_abstains(self):
        self.assertIsNone(briefing_check(notice('사업설명회 불참 업체는 입찰 참가 대상에서 제외한다.',award='적격심사'))['value'])
    def test_body_meta_procedure_conflict(self):
        self.assertIsNone(briefing_check(notice('협상에 의한 계약\n사업설명회 불참 업체는 입찰 참가 대상에서 제외한다.',award='적격심사'))['value'])
    def test_body_procedure_can_resolve_missing_meta(self):
        self.assertEqual(briefing_check(notice('협상에 의한 계약\n사업설명회 불참 업체는 입찰 참가 대상에서 제외한다.',award=None))['value'],1)
    def test_unknown_law_abstains(self):
        self.assertIsNone(briefing_check(notice('사업설명회 불참 업체는 입찰 참가 대상에서 제외한다.',law=None))['value'])
    def test_incomplete_positive_still_visible(self):
        self.assertEqual(briefing_check(notice('사업설명회 불참 업체는 입찰 참가 대상에서 제외한다.',complete=False))['value'],1)


class InterfaceControls(unittest.TestCase):
    def test_unknown_preserves_every_baseline_column(self):
        r=notice('내용 없음');base={'id':'arbitrary','v19':'1','e19':'old','v20':'1','e20':'','v22':'1','e22':'old','v1':'1'}
        self.assertEqual(overlay(r,base),base)
    def test_input_immutable(self):
        r=notice('제조사의 기술지원 확약서는 입찰 전 제출한다.');before=copy.deepcopy(r)
        predict(r);self.assertEqual(r,before)
    def test_exact_original_offsets(self):
        r=notice('머리말\n제조사의 기술지원 확약서는 입찰 전 제출한다.\n끝')
        p=pledge_check(r);e=p['facts']['pledges'][0]['evidence']
        self.assertEqual(r['docs'][e['doc_index']]['text'][e['start']:e['end']],e['quote'])
    def test_positive_only_preserves_model_negative_override(self):
        base={'v19':'1','e19':'old','v20':'0','e20':'','v22':'0','e22':''}
        self.assertEqual(overlay(notice('제조사의 기술지원 확약서는 계약 시 제출한다.'),base,allow_negatives=False),base)
    def test_absence_evidence_blank(self):
        base={'v19':'0','e19':'','v20':'0','e20':'','v22':'0','e22':''}
        got=overlay(notice('본 사업은 소프트웨어사업이다.'),base)
        self.assertEqual((got['v20'],got['e20']),('1',''))


if __name__=='__main__':unittest.main(verbosity=2)
