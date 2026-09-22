"""C3: relative basic-municipality scopes, order-free share abstention, the v8
region witness, and the default-off rule_anchor_selection excerpt switch."""
import dataclasses
import json
from pathlib import Path

from submission.pps.competitive_performance import checks as competitive_checks, nonoperative_quote_reference
from submission.pps.performance import performance_facts
from submission.pps.production_certificate import clause_coverage
from submission.pps.products import CODE
from submission.pps.prompts import Config
from submission.pps.qualification import inventory
from submission.pps.reference_coverage import eligibility_absence_coverage
from submission.pps.regions import relative_office_scope
from submission.pps.retrieval import NoticeIndex
from submission.pps.rules import joint_share_check, narrow_region_check


def record(body, *, work='일반용역', price=80_000_000, law='지방계약법',
           method='제한경쟁', extra=(), heading='2. 입찰 참가자격\n'):
    docs = [{'doc_id': 'notice', 'type': '공고문', 'text': heading + body}]
    docs.extend({'doc_id': f'attach{i}', 'type': '제안요청서', 'text': t}
                for i, t in enumerate(extra))
    return {'id': 'c3-case', 'meta': {
        '적용계약법': law, '업무구분': work, '계약방법': method,
        '입찰추정가격': price, '배정예산금액': int(price * 1.1)}, 'docs': docs,
        'input_completeness': {'공고문_실재': True, '추출_성공': True,
                               '무탈락': True, '완전관측': True}}


def test_basic_unit_of_the_authority_locality_is_read_in_its_written_forms():
    for clause in (
            '마. 입찰참가자는 계약체결일까지 본점을 [수요기관(초등학교)]가 위치한 자치구 안에 두고 있어야 합니다.',
            '바. 본점 소재지는 부산광역시 중에서도 [수요기관(중학교)]가 위치한 구·군 관내에 있어야 합니다.',
            '사. 본점 소재지는 전라남도 중 [수요기관(고등학교)]가 소재한 시·군 관내로 한정합니다.',
            '아. 본점 소재지가 [수요기관(초등학교)]와 같은 자치구 관할구역 안에 있는 자에 한하여 참가를 허용합니다.',
            '자. 본점 소재지를 [수요기관(공기업)] 본사가 위치한 시(市)의 관할구역 안에 둔 업체에 한함'):
        assert relative_office_scope(clause)['unit'] == 'basic', clause


def test_locative_verb_binds_to_the_nearest_unit():
    clause = ('자. 본점 소재지를 [수요기관(공기업)] 본사가 위치한 시(市)의 관할구역 안에 둔 업체에 한함'
              '(광역시·도 단위가 아니라 해당 기초자치단체로 한정함)')
    assert relative_office_scope(clause)['unit'] == 'basic'


def test_scope_split_at_a_connective_quotes_the_verbatim_clause():
    body = ('마. 본점 소재지는 부산광역시 중에서도 [수요기관(중학교)]가 위치한 구·군 관내에 있어야 하며, '
            '다른 구·군에 본점을 둔 자는 참가할 수 없습니다.\n')
    rec = record(body)
    found = narrow_region_check(rec)
    assert found['value'] == 1 and found['evidence'] in rec['docs'][0]['text']


def test_same_locality_relation_keeps_the_province_level():
    scope = relative_office_scope('본점 소재지가 [수요기관(대학)]과 같은 광역자치단체에 있는 업체만 참가할 수 있습니다.')
    assert scope['unit'] == 'province'


def test_authority_locality_without_a_bidder_office_is_not_a_restriction():
    assert relative_office_scope('납품장소는 [수요기관(초등학교)]가 위치한 자치구 안의 지정 장소로 합니다.') is None


def test_relative_basic_scope_proves_item6_only_below_the_ceiling():
    body = '마. 본점 소재지가 [수요기관(초등학교)]가 위치한 자치구 관내에 있는 자에 한하여 입찰에 참가할 수 있습니다.\n'
    assert narrow_region_check(record(body))['value'] == 1
    assert narrow_region_check(record(body, price=900_000_000)) is None


def test_jurisdiction_of_a_basic_authority_token_is_a_basic_restriction():
    body = '나. 법인등기부상 본점 소재지가 [수요기관(기초자치단체)|지역=r1] 관할 행정구역 안에 있는 업체\n'
    assert narrow_region_check(record(body))['value'] == 1


def test_unparsed_share_wording_in_an_earlier_document_cannot_hide_a_later_violation():
    notice = '다. 공동이행방식의 구성원 수 및 출자비율은 5인 이하, 10% 이상으로 한다.\n'
    later = '* 공동계약 적용 시 구성원별 계약참여 최소지분율은 3%이며, 기타사항은 예규를 따른다.'
    rec = record(notice, law='국가계약법', extra=(later,))
    assert joint_share_check(rec)['value'] == 1


def test_unparsed_share_wording_still_blocks_a_compliance_certificate():
    notice = '다. 공동이행방식의 구성원 수 및 출자비율은 5인 이하, 10% 이상으로 한다.\n'
    later = '* 공동계약 적용 시 구성원별 계약참여 최소지분율은 10%이며, 기타사항은 예규를 따른다.'
    assert joint_share_check(record(notice, law='국가계약법', extra=(later,))) is None


EXPERIENCE = '라. 최근 3년 이내 교육프로그램 운영 실적이 있는 업체\n'


def test_wrapped_statutory_office_anchor_is_a_region_for_the_duplicate_restriction():
    body = ('다. 입찰일까지 법인등기부상 본점 소재지(개인사업자의 경우 사업자등록증 등의 서류에\n\n'
            '기재된 사업장의 소재지)가 ‘서울특별시’인 자\n' + EXPERIENCE)
    facts = performance_facts(record(body), consumer=True)
    assert facts['operative_regions']
    assert facts['overlays']['v8']['value'] == 1
    assert not performance_facts(record(body))['operative_regions']  # prompt-side facts unchanged


def test_region_predicate_wrapped_to_the_next_line_is_bound_to_the_office_clause():
    body = ('다. 입찰일까지 주된 영업소가 ‘서울특별시’,\n\n‘경기도’, 또는 ‘인천광역시’에 소재하여야 합니다.\n'
            + EXPERIENCE)
    assert performance_facts(record(body), consumer=True)['operative_regions']


def test_business_place_of_performance_is_not_a_bidder_office():
    body = '다. 과업 수행 장소: 수요기관 사업장(서울특별시 [상세주소])\n' + EXPERIENCE
    assert not performance_facts(record(body), consumer=True)['operative_regions']


def test_category_mentions_of_private_contracts_do_not_declare_this_procedure():
    assert nonoperative_quote_reference(
        '발주하는 모든 공사, 물품 및 용역 등의 입찰(수의견적공고 포함) 및 수의계약에 참여할 때 당사 임직원은')
    assert nonoperative_quote_reference(
        '「이해충돌 방지제도 운영 규정」 시행에 따라 물품 등의 수의계약을 체결하려는 경우 '
        '수의계약 체결 제한 여부 확인서를 사실대로 제출하겠습니다.')
    assert not nonoperative_quote_reference('본 용역은 2인 이상으로부터 견적을 받는 소액수의계약입니다.')


def test_sanction_phrase_broken_by_a_line_wrap_is_read_whole():
    body = ('다. 입찰일까지 법인등기부상 본점 소재지가 경기도에 있는 업체\n' + EXPERIENCE
            + '5. 신인도 평가\n- 입찰공고일 기준 최근 3년 이내에 수의계약\n\n배제 업체로 등록된 이력이 있는 업체 | -4\n')
    rec = record(body)
    found = competitive_checks(rec, performance_facts(rec, consumer=True), (2, 8))
    assert {c['item'] for c in found} == {2, 8}


def pointer(text):
    return {'referenced_unavailable_docs': [{
        'referenced_role': '규격서', 'available_as_document_role': False, 'qualification_deferral': True,
        'evidence': {'doc_index': 0, 'start': 10, 'end': 10 + len(text), 'text': text}}]}


def test_reading_instruction_toward_a_missing_document_is_not_a_qualification_delegation():
    rec = record('가. 시행령 제13조의 자격요건을 갖춘 자\n')
    resolved = eligibility_absence_coverage(
        rec, [], pointer('입찰자는 입찰유의서, 시방서 등을 사전에 완전히 숙지한 후 입찰에 참가하여야 합니다'))
    assert resolved['size_and_direct_predicates_resolved']
    for text in ('입찰참가자격은 제안요청서를 따름', '중소기업확인서 등 제출서류는 시방서를 숙지하여 제출',
                 '제출 서류는 붙임 제안요청서를 참고'):
        assert not eligibility_absence_coverage(rec, [], pointer(text))['size_and_direct_predicates_resolved'], text


def certificate(text):
    rec = record('', heading=text)
    entry = {'evidence': {'doc_index': 0, 'start': 0, 'end': len(text), 'text': text},
             'codes': CODE.findall(text)}
    return clause_coverage(rec, entry)


def test_unclosed_statute_citation_does_not_quote_the_certificate_duty():
    found = certificate('라. 「중소기업제품 구매촉진 및 판로지원에 관한 법률 제9조 및 같은법 시행령 제10조에 의한 '
                        '직접생산확인증명서[세부품명: 통학운송서비스(7811189902)]를 소지한 업체')
    assert found['certificate_required_in_every_branch'] and found['guaranteed_codes'] == ['7811189902']
    quoted = certificate('다. “직접생산확인증명서를 소지한 업체')
    assert not quoted['certificate_required_in_every_branch']


def test_choice_among_item_names_of_one_certificate_still_requires_it():
    found = certificate('⑤ 판로지원법 제9조에 의한 직접생산확인증명서“세부품명: 회의기획및대행서비스, '
                        '국제행사기획및대행서비스(세부품명번호: 8014190201, 8014198901) 중 1개”를 소지한 자')
    assert found['certificate_required_in_every_branch'] and found['guaranteed_codes'] == []
    other = certificate('⑤ 직접생산확인증명서 또는 성능인증서 중 1개를 소지한 자')
    assert not other['certificate_required_in_every_branch']


def test_wrapped_item_code_is_not_a_list_number():
    body = ('가. 「중소기업제품 구매촉진 및 판로지원에 관한 법률」제9조 및 같은 법 시행령\n'
            '제10조에 의한 직접생산확인증명서(세부품명: 수확용건조기, 세부품명번호 10자리\n'
            '2110171001)를 소지한 업체\n')
    entries = inventory(record(body, work='물품(내자)'))[0]
    assert any(e['direct_requirement'] and '소지한 업체' in e['evidence']['text'] for e in entries)


def test_excluded_mid_size_firm_is_not_a_special_entity_branch():
    excluded = inventory(record('마. 중소기업·소상공인확인서를 가진 자만 입찰에 참가할 수 있습니다(대기업·중견기업 참가 불가).\n'))[0]
    assert all(e['status'] != 'special_entity_branch' for e in excluded if e['size'])
    allowed = inventory(record('마. 중소기업·소상공인확인서를 소지한 업체(초기중견기업도 참가 가능)\n'))[0]
    assert any(e['status'] == 'special_entity_branch' for e in allowed if e['size'])


def filler(n):
    return ''.join(f'{i}. 입찰참가 등록 서류는 전자조달시스템으로 제출하여야 합니다. 서식 {i}번을 작성합니다.\n'
                   for i in range(1, n + 1))


def anchored_record():
    body = (filler(40) + '41. 입찰참가 자격\n가. 최근 3년 이내 급식 위탁 운영 실적이 있는 자\n'
            + '나. 본점 소재지가 [수요기관(초등학교)]가 위치한 자치구 안에 있는 자에 한하여 참가할 수 있습니다.\n')
    return record(body, heading='')


def covered(spans, rec, needle):
    text = rec['docs'][0]['text']; lo = text.index(needle); hi = lo + len(needle)
    shown = set()
    for s in spans:
        shown.update(range(max(s.start, lo), min(s.end, hi)))
    return all(i in shown for i in range(lo, hi) if not text[i].isspace())


def test_rule_anchor_selection_is_off_by_default_in_code_and_enabled_in_shipped_config():
    rec = anchored_record()
    default = NoticeIndex(rec).select(1600, mode='evidence_first')
    explicit = NoticeIndex(rec).select(1600, mode='evidence_first', rule_anchor_selection=False)
    assert [(s.doc_index, s.start, s.end) for s in default] == [(s.doc_index, s.start, s.end) for s in explicit]
    config = Config.load(Path(__file__).resolve().parents[1] / 'submission/model/config.json')
    assert config.rule_anchor_selection is True


def test_rule_anchor_selection_reserves_complete_rule_read_conditions():
    rec = anchored_record()
    off = NoticeIndex(rec).select(1600, mode='evidence_first')
    on = NoticeIndex(rec).select(1600, mode='evidence_first', rule_anchor_selection=True)
    clause = '최근 3년 이내 급식 위탁 운영 실적이 있는 자'
    assert not covered(off, rec, clause) and covered(on, rec, clause)
    assert covered(on, rec, '본점 소재지가 [수요기관(초등학교)]가 위치한 자치구 안에 있는 자에 한하여 참가할 수 있습니다.')


def test_rule_anchor_selection_rejects_a_non_boolean_value():
    try:
        dataclasses.replace(Config(), rule_anchor_selection='yes')
    except ValueError:
        return
    raise AssertionError('non-boolean switch accepted')
