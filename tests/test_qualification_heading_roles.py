"""Qualification scope is not established by an incidental keyword occurrence."""
import copy

import pytest

from submission.pps import sme
from submission.pps.qualification import infer, inventory
from submission.pps.knowledge import Knowledge
from tests.test_service_identity import DATA


CLAUSE = ('가. 「중소기업기본법」 제2조에 따른 소기업자 또는 소상공인으로서\n'
    '「중소기업범위 및 확인에 관한 규정(중소벤처기업부 고시)」에 따라 발급된\n'
    '「소기업·소상공인 확인서」를 소지한 업체이어야만 합니다.')


def notice(header, clause=CLAUSE):
    return {'id':'synthetic-qualification-heading',
        'meta':{'업무구분':'일반용역','적용계약법':'국가계약법','계약방법':'제한경쟁',
                '입찰추정가격':150000000, '세부품명번호목록':'농림수산연구조사서비스[7010150001]'},
        'docs':[{'type':'공고문','doc_id':'notice','text':
            '1. 사업개요\n사업명: 농림수산연구조사서비스\n'+header+'\n'+clause+'\n5. 계약조건\n기한 준수'}],
        'input_completeness':{'완전관측':True}, 'dropped_doc_counts':{}}


@pytest.mark.parametrize('header', [
    '4. 입찰참가자격 (다음 조건을 모두 갖춘 자이어야 함)',
    '4. 입찰참가자격(다음 각 호의 요건을 모두 갖춘 업체)',
    '4. 입찰참가자격 : 다음 조건을 모두 갖춘 자이어야 함',
    '4. 입찰참가자격 [아래 조건을 모두 충족하는 업체에 한함]',
    '4. 견적제출 참가자격 (다음 요건을 모두 충족하여야 함)',
    '6. 견적제출 참가자격 (※아래 조건을 모두 갖춘 자)',
])
def test_attached_mandatory_governor_keeps_the_qualification_role(header):
    rec = notice(header); original = copy.deepcopy(rec)
    knowledge = Knowledge(DATA); knowledge.detailed_product_facts(rec)
    row, facts = infer(rec, {'v16':'1','e16':''}, knowledge._product_facts)
    assert facts['qualification']['allowed'] == ['micro','small']
    assert row['v16'] == '0'
    assert rec == original


def test_statutory_preamble_before_a_completed_all_conditions_governor_is_a_heading():
    header = ('2. 입찰참가자격 : 입찰참가등록 마감일 기준 지방계약법 시행령 제13조 및 '
              '시행규칙 제14조에 의한 경쟁입찰 참가자격을 갖춘 자로서, '
              '다음 사항을 모두 충족한 업체')
    rec = notice(header)
    entries, sections, *_ = inventory(rec)
    assert sections and sections[0]['closed']
    assert entries[0]['status'] == 'mandatory_eligibility'
    assert entries[0]['heading']['text'] == header
    assert rec['docs'][0]['text'][sections[0]['evidence']['start']:
                                sections[0]['evidence']['end']] == sections[0]['evidence']['text']


@pytest.mark.parametrize('text', [
    '입찰참가자격 제한 처분을 받겠습니다.',
    '- 발급된 확인서가 입찰참가자격의 기업구분과 다른 경우',
    '가. 나라장터 입찰참가자격 등록 내용에 따라 참가합니다.',
    '부정당업자의 입찰참가자격 제한',
    '4. 입찰참가자격 제한 처분',
])
def test_sanction_registration_and_verification_are_not_qualification_headers(text):
    assert sme.heading(sme.norm(text)) != 'eligibility'


def test_incidental_heading_cannot_supply_a_closed_absence_scope():
    rec = notice('입찰참가자격 제한 처분을 받겠습니다.', '자료 안내입니다.')
    parts = inventory(rec)
    assert not parts[1]


def test_ministry_in_a_law_title_is_not_an_alternative_bidder():
    entry = inventory(notice('4. 입찰참가자격'))[0][0]
    assert entry['size']['allowed'] == ['micro','small']
    assert entry['other_entity_options'] == []


def test_actual_venture_option_is_not_masked_with_the_ministry_name():
    text = ('가. 소기업 또는 벤처기업으로서 「소기업·소상공인 확인서」 또는 '
            '벤처기업확인서를 소지한 업체이어야 합니다.')
    entry = inventory(notice('4. 입찰참가자격', text))[0][0]
    assert '벤처기업' in entry['other_entity_options']
    assert entry['alternative_size_branch_unresolved']


def test_proven_certificate_cannot_migrate_from_a_submission_form():
    rec = notice('4. 제출서류 (다음 조건을 모두 갖춘 자)', CLAUSE)
    assert not any(e['status']=='mandatory_eligibility' for e in inventory(rec)[0])


def test_original_heading_and_evidence_offsets_are_retained():
    header = '4. 입찰참가자격 (다음 조건을 모두 갖춘 자이어야 함)'
    rec = notice(header)
    entry = inventory(rec)[0][0]
    assert entry['heading']['text'] == header
    for key in ('heading','evidence'):
        e = entry[key]
        assert rec['docs'][e['doc_index']]['text'][e['start']:e['end']] == e['text']


@pytest.mark.parametrize('header', [
    '4. 입찰참가자격: 다음 조건은 작성 예시이다.',
    '4. 입찰참가자격 (다음 조건을 모두 갖춘 자이어야 한다는 예시)',
    '4. 입찰참가자격: 다음 조건은 적용하지 않는다.',
    '4. 입찰참가자격: 다음 조건은 삭제한다.',
])
def test_nonoperative_heading_does_not_make_following_example_mandatory(header):
    rec = notice(header)
    rec['docs'][0]['text'] = rec['docs'][0]['text'].replace(header,
        '3. 입찰참가자격\n가. 적법하게 등록된 일반 업체\n'+header)
    entries = inventory(rec)[0]
    assert not any(e['status']=='mandatory_eligibility' and e['size'] for e in entries)


@pytest.mark.parametrize('header', [
    '3. 입찰서 제출 참가 자격',
    '4. 입찰(응모) 참가자격',
    '2. 공모·입찰 참가자격',
    'ParaShape="12" Style="3">3. 입찰 참가 자격',
    'Ⅳ | | 참가자격',
    '4 | 입찰참가자격',
    '❍ 입찰참가자격',
    '4. 견적 입찰 참가 자격',
    '4 입찰참가자격(아래의 자격을 모두 충족)',
    '4.1.1 제안(입찰) 참가자격',
    '3. 입찰(견적서 제출)참가자격',
    '【입찰 참가자격에 관련 공통사항】',
    '• 입찰참가자격(아래의 요건을 모두 충족하는 업체)',
    '5. 견적제출자 참가 자격',
    '2 | 참가 자격',
    '아래의 입찰참가자격을 모두 충족하여야 합니다.',
    '3. 입찰참가자격 ※ 공동수급 허용',
    '2. 입찰 참가자격 (제한경쟁입찰)',
    '2. 참가자격 (아래 기준을 모두 충족하는 사업자/증빙서류要)',
    '6. 입찰 참가자격(아래 자격을 모두 갖추어야 합니다.)',
    '3. 입찰 참가자격 및 방법',
    '4. 견적 제출 참가 자격(모두 해당)',
    '4. 입찰참가자격(아래 사항을 모두 갖출 것)',
    '2. 입찰참가자격: 다음 조건을 모두 충족하는 자 ※ 지점/지사 입찰 참가 및 계약 불가',
    '3. 참가자격: 아래의 입찰참가자격을 모두 갖춘 자이어야 합니다.',
    '계약방법 및 입찰참가자격',
    '4. 견적제출 자격',
    '※ 용역업체 참가자격',
    '라. 참가자격 : 하기 1), 2), 3)의 자격사항을 모두 갖추어야 함',
])
def test_formatting_does_not_drop_the_governing_qualification_heading(header):
    rec = notice(header)
    entries, sections, *_ = inventory(rec)
    assert any(e['status']=='mandatory_eligibility' and e['size']['allowed']==['micro','small']
               for e in entries if e['size'])
    assert any(s['closed'] for s in sections)
    assert entries[0]['heading']['text'] == header
    ev = entries[0]['heading']
    assert rec['docs'][ev['doc_index']]['text'][ev['start']:ev['end']] == header


@pytest.mark.parametrize('intervening', [
    '1234567890)로 입찰참가자격을 등록하고 시방서에 따라 납품 및 설치가 가능한 업체',
    '1) 국가종합전자조달시스템 입찰참가자등록 규정에 의하여 국가종합전자조달시스템 (나라장터)에\n'
    '입찰참가자격등록 마감일시까지 제조 물품으로 등록한 자',
    '2.5 GHz',
    '2026.4.15. 공고일',
    '5) 본 사업은 전북특별자치도 내 본사를 둔 사업자에 한해서 입찰 가능',
])
def test_nonheading_list_continuations_keep_the_original_parent(intervening):
    header = '3. 견적제출 참가자격'
    rec = notice(header, intervening+'\n'+CLAUSE)
    sized = [e for e in inventory(rec)[0] if e['size']]
    assert sized and all(e['status']=='mandatory_eligibility' for e in sized)
    assert all(e['heading']['text']==header for e in sized)


def test_parenthesized_qualification_members_do_not_become_peer_sections():
    clause = ('4) 본 사업은 공동수급 및 하도급 불가\n'
              '5) 본 사업은 전북특별자치도 내 본사를 둔 사업자에 한해서 입찰 가능\n'
              '6) 「중소기업기본법」제2조에 따른 소기업자 및 '
              '「소상공인 보호 및 지원에 관한 법률」제2조에 따른 소상공인으로서 '
              '「중소기업 범위 및 확인에 관한 규정」에 따라 발급된 '
              '<소기업 또는 소상공인 확인서>를 소지한 업체')
    rec = notice('3. 입찰참가자격\n가. 다음 각 호의 자격을 모두 갖춘 자', clause)
    sized = [entry for entry in inventory(rec)[0] if entry['size']]
    assert sized and sized[0]['status'] == 'mandatory_eligibility'
    assert sized[0]['heading']['text'].startswith('3. 입찰참가자격')


@pytest.mark.parametrize('text', [
    '입찰참가자격을 확인한 뒤 서류를 보관한다.',
    'ParaShape="12" Style="3">입찰참가자격 등록 내용에 따라 참가합니다.',
    '4 | 입찰참가자격 제한 처분',
    '아래의 입찰참가자격을 갖추지 않아도 된다.',
    '4. 입찰참가자격: 다음 조건을 모두 갖출 필요는 없다.',
    'Ⅳ. 참가자격 11',
])
def test_heading_decoration_cannot_promote_a_mention_or_a_table_of_contents(text):
    assert sme.heading(sme.norm(text)) != 'eligibility'


def test_unnumbered_submission_heading_closes_the_qualification_section():
    rec = notice('입찰 참가자격', '가. 등록한 일반업체\n입찰서 제출안내\n'+CLAUSE)
    entries, sections, *_ = inventory(rec)
    assert sections[0]['closed']
    assert not any(e['status']=='mandatory_eligibility' and e['size'] for e in entries)


def test_required_certificate_list_cannot_be_used_as_proof_of_absence():
    from submission.pps.qualification import qualification_facts
    rec = notice('입찰 참가자격', '가. 등록한 일반업체')
    rec['docs'].append({'type':'제안요청서','doc_id':'rfp','text':
        '5. 제출서류\n필수 제출 서류\n(※ 미\n제출시\n평가\n제외)\n'
        '조달청 경쟁입찰 참가자격 등록증 1부\n-\n중·소기업·소상공인 확인서\n-\n'
        '인권보호서약서 1부\n6. 기타사항'})
    q = qualification_facts(rec, inventory(rec))
    assert q['allowed'] is None  # Table membership alone does not certify holding.
    assert not q['no_size']
    assert q['unresolved_size'][0]['reason']=='required_certificate_submission_scope_unresolved'
    ev = q['unresolved_size'][0]['governor']
    assert rec['docs'][ev['doc_index']]['text'][ev['start']:ev['end']] == ev['text']


def test_an_example_certificate_form_does_not_become_a_required_list():
    from submission.pps.qualification import qualification_facts
    rec = notice('입찰 참가자격', '가. 등록한 일반업체')
    rec['docs'].append({'type':'제안요청서','doc_id':'rfp','text':
        '5. 필수 제출서류 작성 예시\n중·소기업·소상공인 확인서\n6. 기타사항'})
    q = qualification_facts(rec, inventory(rec))
    assert q['allowed'] is None
    assert q['no_size']
    assert not q['unresolved_size']


def test_postaward_size_certificate_cannot_support_size_restriction_violations():
    rec = notice('3. 입찰참가자격', '가. 법정 업종을 등록한 일반 업체')
    rec['docs'][0]['text'] += (
        '\n8. 계약체결\n낙찰자는 계약체결 시 다음 서류를 제출하여야 합니다.\n'
        '- 중·소기업·소상공인 확인서\n- 계약보증서\n9. 기타사항')
    knowledge = Knowledge(DATA)
    knowledge.detailed_product_facts(rec)
    row, facts = infer(rec, {'v13':'1','e13':'모델 근거',
                             'v14':'1','e14':'모델 근거',
                             'v15':'1','e15':'모델 근거',
                             'v17':'1','e17':'모델 근거'}, knowledge._product_facts)
    assert facts['qualification']['no_operative_size_prerequisite']
    assert all(row[f'v{item}'] == '0' and row[f'e{item}'] == ''
               for item in (13, 14, 15, 17))


def test_eligibility_size_mention_blocks_prerequisite_absence_guard():
    rec = notice('3. 입찰참가자격',
        '가. 중·소기업·소상공인 확인서는 유효기간을 별도로 확인합니다.')
    from submission.pps.qualification import qualification_facts
    facts = qualification_facts(rec, inventory(rec))
    assert not facts['no_operative_size_prerequisite']


def test_required_direct_certificate_list_also_blocks_absence_without_proving_holding():
    from submission.pps.qualification import qualification_facts
    rec = notice('입찰 참가자격', '가. 등록한 일반업체')
    rec['docs'].append({'type':'제안요청서','doc_id':'rfp','text':
        '5. 필수 제출서류\n직접생산확인증명서 1부\n6. 기타사항'})
    q = qualification_facts(rec, inventory(rec))
    assert not q['active_direct']
    assert not q['no_direct']
    assert q['unresolved_direct'][0]['reason']=='required_certificate_submission_scope_unresolved'


def test_required_list_retains_both_certificate_types_when_the_dump_joins_them():
    from submission.pps.qualification import qualification_facts
    rec = notice('입찰 참가자격', '가. 등록한 일반업체')
    rec['docs'].append({'type':'제안요청서','doc_id':'rfp','text':
        '5. 필수 제출서류\n중소기업확인서 및 직접생산확인증명서 각 1부\n6. 기타사항'})
    q = qualification_facts(rec, inventory(rec))
    assert q['unresolved_size'] and q['unresolved_direct']
    assert not q['no_size'] and not q['no_direct']
    assert not q['active_size'] and not q['active_direct']
