"""Source-verified school transport / ordinary human guarding, per notice."""
import copy
import re

BUS = '7811189902'
GUARD = '9212159901'
GUARD_NOTE = '1. 경비업법상의 기계경비업, 특수경비업 제외 2. 공공기관이 자회사와 수의계약을 체결하는 경우 적용 대상에서 제외'
WRONG_SCOPE = re.compile(r'조사|연구|컨설팅|실태|교육용|훈련|개발|구축|구매|청소|방역|및|외\d+종|등\d+종')
NEGATED_ROLE = re.compile(r'예시|참고용|미적용|해당없음|수행하지|임차하지|운행하지|위탁하지|아님|아닌|아닙')

def norm(text):
    return re.sub(r'\s+', '', str(text))

def evidence(record, index, start, end):
    doc = record['docs'][index]
    return {'doc_index': index, 'doc_id': 'D' + str(index), 'document_role': doc['type'],
            'start': start, 'end': end, 'text': doc['text'][start:end]}

def find_source(record, pattern, before=0, after=0):
    found = []
    for index, doc in enumerate(record['docs']):
        for match in re.finditer(pattern, doc['text'], re.S):
            start, end = max(0, match.start() - before), min(len(doc['text']), match.end() + after)
            item = evidence(record, index, start, end)
            if not NEGATED_ROLE.search(norm(item['text'])):
                found.append(item)
    return found

def purchase_titles(record, fallback):
    """Keep wrapped title text inside an explicitly named purchase field."""
    found = []
    for index, doc in enumerate(record['docs']):
        for match in re.finditer(r'(?m)^\s*(?:[가나다]\.\s*)?(?:용\s*역\s*명|입찰\s*건명|사업\s*명)[\s:|]+', doc['text']):
            tail = doc['text'][match.end():match.end() + 260]
            boundary = re.search(r'(?m)^\s*(?:계약\s*기간|용역\s*기간|차량\s*규격|입찰\s*방식|입찰\s*방법|계약\s*방법|기초\s*금액|낙찰자|과업\s*내용|\d+\.)', tail)
            end = match.end() + (boundary.start() if boundary else len(tail))
            found.append(evidence(record, index, match.start(), end))
    return found or [evidence(record, x['doc_index'], x['start'], x['end']) for x in fallback
                     if len(x['text']) <= 350 and x.get('role') in ('title_or_scope_field', 'intro_title_candidate')]

def provide(record, model_facts, sme_product, qualification_facts, catalog):
    """Return a product packet or abstain; leave every nonproduct fact alone."""
    product = qualification_facts['product']
    qualification = qualification_facts['qualification']
    def reject(reason): return None, {'accepted': False, 'reason': reason}
    if product['status'] != 'unknown' or sme_product['status'] != 'unknown':
        return reject('original_state_already_resolved')
    if (product['products'] or sme_product['supported_products'] or
            product['uncertainty'] or sme_product['uncertainty']):
        return reject('existing_candidate_set_or_conflict_requires_review')
    if not qualification['complete'] or any(record.get('dropped_doc_counts', {}).values()):
        return reject('incomplete_source')
    claim = model_facts.get('실제구매대상_경쟁제품_고시조건', '')
    compact = norm(claim)
    codes = set(re.findall(r'(?<!\d)\d{10}(?!\d)', claim))
    if (len(codes) != 1 or not codes <= {BUS, GUARD} or
            not re.search(r'경쟁제품(?:에해당|임)', compact) or
            re.search(r'아니|않|불명|불확실|미확정|후보|판단불가|가능|일반제품|비경쟁|제외|하지만|다만', compact)):
        return reject('no_unambiguous_supported_model_candidate')
    code = next(iter(codes))
    row = catalog.get(code)
    if not row or norm(row['세부품명']) not in compact:
        return reject('catalog_identity_mismatch')
    direct = qualification['active_direct']
    direct_codes = {c for item in direct for c in item['codes']}
    if direct_codes != {code} or not set(sme_product['meta_codes']) <= {code}:
        return reject('code_not_corroborated_or_multiple_codes')
    certs = [item['evidence'] for item in direct if code in item['codes']]
    titles = []
    for ev in purchase_titles(record, product['scope_evidence']):
        t = norm(ev['text'])
        scope_text = re.sub(r'임차및(?:운행|운영)|운행및임차', '임차운행', t)
        if WRONG_SCOPE.search(scope_text) or NEGATED_ROLE.search(t):
            continue
        if code == BUS and re.search(r'(?:통학(?:버스|차량)|학생통학|등하교수송).{0,25}(?:임차|운행|운송|수송)(?:.{0,8}용역)?', t):
            titles.append(ev)
        elif code == GUARD and re.search(r'(?:보안인력|시설경비|인력경비|경비원|보안경비).{0,15}(?:위탁|용역|배치)', t):
            titles.append(ev)
    if not titles:
        return reject('no_affirmative_purchase_role_title')

    if code == BUS:
        if row['특이사항'].strip():
            return reject('unhandled_school_transport_catalog_condition')
        vehicles = find_source(record, r'(?:차량\s*규격|차량\s*대수|운행\s*차량).{0,180}?\d+\s*대')
        performance = find_source(record, r'통학\s*버스.{0,100}?용역.{0,180}?(?:당사|자사)\s*소유.{0,60}?직영\s*차량.{0,60}?운행.{0,50}?확약', before=40)
        if not performance:
            drivers = find_source(record, r'(?:운전원|운전기사)[^\r\n]{0,65}(?:제공|배치|포함)[^\r\n]{0,35}(?:하여야|해야|한다|합니다|함)')
            trips = find_source(record, r'(?:통학\s*노선|통학\s*차량|등하교)[^\r\n]{0,80}(?:운행|수송)[^\r\n]{0,40}(?:하여야|해야|한다|합니다|함)')
            if drivers and trips:
                performance = drivers[:1] + trips[:1]
        if not vehicles or not performance:
            return reject('no_vehicle_scope_and_actual_transport_commitment')
        condition = {'status': 'no_stated_condition'}
        proofs = titles + vehicles[:1] + performance[:1]
    else:
        if norm(row['특이사항']) != norm(GUARD_NOTE):
            return reject('unhandled_guarding_catalog_condition')
        if any(re.search(r'(?:기계|특수)\s*경비', d['text']) for d in record['docs']):
            return reject('machine_or_special_guarding_requires_abstention')
        licenses = []
        for section in qualification['eligibility_sections']:
            ev = section['evidence']
            if re.search(r'시설\s*경비업.{0,45}1164.{0,45}(?:등록|허가)', ev['text'], re.S):
                licenses.append(ev)
        performance = find_source(record, r'보안\s*인력\s*위탁\s*용역.{0,40}?수탁.{0,40}?업무.{0,40}?수행', before=60, after=40)
        if not performance:
            performance = find_source(record, r'(?:경비원|보안요원|보안인력)[^\r\n]{0,80}(?:배치|근무)[^\r\n]{0,40}(?:하여야|해야|한다|합니다|함)')
        methods = find_source(record, r'(?:입찰\s*방법|계약\s*방법|입찰\s*방식)[\s:|]*(?:제한경쟁|일반경쟁)')
        if not licenses or not performance:
            return reject('no_facility_license_and_actual_staffing_commitment')
        method = record.get('meta', {}).get('계약방법')
        if method not in ('제한경쟁', '일반경쟁') or not methods:
            return reject('subsidiary_negotiated_exception_not_negated_by_competitive_method')
        condition = {'kind': 'guarding_catalog_exclusions', 'status': 'met',
                     'note_preserved_verbatim': row['특이사항'],
                     'machine_special_guarding': 'ordinary_human_facility_service_affirmatively_supported',
                     'subsidiary_relationship': 'not_established',
                     'actual_contract_method': method,
                     'subsidiary_negotiated_exception': 'negotiated_contract_conjunct_false'}
        proofs = titles + licenses[:1] + performance[:1] + methods[:1]
    # All evidence is copied from actual source ranges. Certificate evidence
    # supplies the code association only after independent scope validation.
    proofs += certs
    for ev in proofs:
        assert record['docs'][ev['doc_index']]['text'][ev['start']:ev['end']] == ev['text']
    result = copy.deepcopy(product)
    result.update(status='competition', mechanism='automatic_source_verified_service_identity_v2',
                  products=[{'code': code, 'listed': True, 'name': row['세부품명'],
                             'note': row['특이사항'], 'condition': condition}],
                  identity_evidence=copy.deepcopy(proofs), uncertainty=[])
    return result, {'accepted': True, 'code': code, 'reason': 'model_candidate_source_role_and_catalog_conditions_verified',
                    'proofs': proofs, 'condition': condition}
