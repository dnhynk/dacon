"""Structural counterexamples for the independent 2026-09-26 audit.

These examples do not use competition IDs, labels or measured scores. Each pair
changes one relationship (number boundary, article, list ownership, or subject).
"""
import gzip
import datetime as dt
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))
from pps_c import catalog, facts, families as F, judge, main, meta, record, switches
from pps_c.runner import MockEngine
from pps_c.v24 import compare, parse, prompt, select, stage


def bundle(*lines, work='일반용역', price=90_000_000, budget=99_000_000):
    return facts.build({
        'id': 'audit-example',
        'meta': {'적용계약법': '국가계약법', '업무구분': work,
                 '계약방법': '제한경쟁', '낙찰방법': '협상에의한계약',
                 '입찰추정가격': price, '배정예산금액': budget},
        'docs': [{'type': '공고문', 'text': '\n'.join(lines)}],
    }, catalog.load())


def reading(b, family, text, **fields):
    ln = next(x for x in b.notice.lines if x.text == text)
    b.cands.setdefault(family, [])
    if ln not in b.cands[family]:
        b.cands[family].append(ln)
    b.readings.setdefault(family, {})[ln.i] = fields
    return ln


@pytest.mark.parametrize('source,value', [
    ('사업예산: 150,000,000원', '50,000,000원'),
    ('사업예산: 150000000원', '50000000원'),
    ('사업예산: 170,000,000원', '170,000'),
    ('사업예산: 3.5억원', '5억원'),
    ('사업예산: 일억칠천만원', '칠천만원'),
    ('업종코드: 21169', '1169'),
    ('사업예산: １５０００００００원', '５０００００００원'),
])
def test_copied_value_cannot_be_a_fragment_of_a_number(source, value):
    b = bundle(source)
    assert stage.locate(b.notice, b.notice.lines, 0, value) is None


@pytest.mark.parametrize('source,value', [
    ('사업예산: 금 50,000,000원', '50,000,000원'),
    ('사업예산: 금일억칠천만원정', '일억칠천만원'),
    ('업종코드: 1169', '1169'),
    ('사업예산: 170,000,000원', '170,000,000'),
    ('사업예산: 금3.5억원', '3.5억원'),
])
def test_copied_complete_numbers_still_match(source, value):
    b = bundle(source)
    assert stage.locate(b.notice, b.notice.lines, 0, value) is b.notice.lines[0]


@pytest.mark.parametrize('text,expected', [
    ('170,000천원', [170_000_000]),
    ('1,200만원', [12_000_000]),
    ('2,500,000천원', [2_500_000_000]),
    ('100,000원 / 100,000원', [100_000, 100_000]),
])
def test_money_spans_respect_units_and_occurrences(text, expected):
    spans = parse.money_spans(text)
    assert [v for v, _, _ in spans] == expected
    assert all(a < z and text[a:z] for _, a, z in spans)
    assert all(z <= a2 for (_, _, z), (_, a2, _) in zip(spans, spans[1:]))


@pytest.mark.parametrize('article', ['제7조의2', '제 7 조 의 2', '제70조'])
def test_other_articles_are_not_article_seven_exceptions(article, monkeypatch):
    monkeypatch.setattr(switches, 'CODE_AUDIT_BASE', True)
    b = bundle(f'판로지원법 시행령 {article}에 따른 소기업 경쟁입찰')
    assert not judge.competition_exception(b)
    assert not judge.exception_anydoc(b)


@pytest.mark.parametrize('article', ['제7조', '제 7 조 제1항 제3호'])
def test_article_seven_exception_still_recognized(article):
    b = bundle(f'판로지원법 시행령 {article}에 따른 예외 적용')
    assert judge.competition_exception(b)
    assert judge.exception_anydoc(b)


def test_unrelated_waiver_does_not_exempt_enterprise_size(monkeypatch):
    monkeypatch.setattr(switches, 'X5_EXC_WORDING', True)
    b = bundle('1. 입찰에 부치는 사항', '용역명: 청사 시설물 유지관리 용역',
               '2. 입찰참가자격', '가. 경쟁입찰 참가자격을 갖춘 자',
               '3. 보험료 정산', '국민건강보험료 사후정산 의무적용 대상이 아닙니다.')
    assert judge.v18(b) is True
    c = bundle('본 건은 판로지원법 시행령에 의거한 제한경쟁입찰 의무적용 대상이 아닙니다.')
    assert judge.x5_absence_off(c)


def test_partner_region_is_removed_before_presence_tests(monkeypatch):
    monkeypatch.setattr(switches, 'AUDIT_FIXES3', True)
    loc = '가. 서울특별시 소재 업체와 분담이행을 허용합니다.'
    perf = '나. 유사 용역 수행 실적이 있는 업체이어야 합니다.'
    b = bundle('2. 입찰참가자격', loc, perf, price=300_000_000, budget=330_000_000)
    reading(b, 'region', loc, 역할='참가자격', 대상='입찰자 소재지 제한')
    reading(b, 'perf', perf, 역할='참가자격')
    assert judge.region_restriction(b) == ([], set(), set())
    assert judge.v5(b) is None
    assert judge.v8(b) is None


def test_mixed_partner_clause_keeps_the_bidders_own_location(monkeypatch):
    monkeypatch.setattr(switches, 'AUDIT_FIXES3', True)
    loc = '가. 주된 영업소가 서울특별시에 소재한 업체는 경기도 소재 업체와 분담이행을 허용합니다.'
    b = bundle('2. 입찰참가자격', loc, price=300_000_000, budget=330_000_000)
    ln = reading(b, 'region', loc, 역할='참가자격', 대상='입찰자 소재지 제한')
    assert judge.region_restriction(b) == ([ln], {'서울특별시'}, set())
    assert judge.v5(b) is ln


def test_institution_condition_does_not_borrow_an_unrelated_option_list(monkeypatch):
    monkeypatch.setattr(switches, 'V1_LEAD_OPTIONS', True)
    t = '가. 비영리법인에 한하여 입찰참가 가능'
    b = bundle('2. 입찰참가자격', t, '나. 아래 중 하나의 추가 요건을 갖춘 자',
               '① 직업소개사업자', '② 직업정보제공사업자')
    ln = reading(b, 'inst', t, 역할='참가자격', 요건='기관 유형 한정')
    assert not judge.x1_lead_options_commercial(b, ln)
    assert judge.v1(b) is ln


def test_a_licensed_business_noun_is_not_itself_an_alternative(monkeypatch):
    monkeypatch.setattr(switches, 'V1_LEAD_OPTIONS', True)
    t = '가. 연구기관에 한하며 직업소개사업자로 등록한 자이어야 합니다.'
    b = bundle('2. 입찰참가자격', t)
    ln = reading(b, 'inst', t, 역할='참가자격', 요건='기관 유형 한정')
    assert judge.v1(b) is ln


@pytest.mark.parametrize('condition,continuation', [
    ('가. 비영리법인만 참가할 수 있으며 아래 중 하나를 갖춘 자', []),
    ('가. 비영리법인만 참가할 수 있다.', ['아래 중 하나의 추가 자격을 갖추어야 한다.']),
    ('가. 연구기관에 한하며 직업소개사업자 또는 직업정보제공사업자로 등록한 자', []),
    ('가. 연구기관에 한하며 일반업체 또는 회사로 등록한 자', []),
])
def test_option_list_does_not_remove_a_cumulative_institution_requirement(monkeypatch, condition, continuation):
    monkeypatch.setattr(switches, 'AUDIT_FIXES', True)
    monkeypatch.setattr(switches, 'AUDIT_FIXES2', True)
    monkeypatch.setattr(switches, 'V1_LEAD_OPTIONS', True)
    b = bundle('2. 입찰참가자격', condition, *continuation, '① 직업소개사업자', '② 직업정보제공사업자')
    ln = reading(b, 'inst', condition, 역할='참가자격', 요건='기관 유형 한정')
    assert judge.v1(b) is ln


def test_equivalence_of_another_item_does_not_cancel_a_designation():
    a = '1. 제조사: 알파전자 AX-900만 납품하여야 한다.'
    z = '2. 모델명: 베타전자 BX-200 또는 동등 이상 허용'
    b = bundle(a, z, work='물품')
    assert not judge.x3_drop(b, b.notice.lines[0])
    assert judge.x3_drop(b, b.notice.lines[1])


def test_estimate_fallback_uses_the_amount_after_its_own_label(monkeypatch):
    monkeypatch.setattr(switches, 'CODE_AUDIT_BASE', True)
    b = bundle('기초금액: 220,000,000원 / 추정가격: 200,000,000원 / 부가세: 20,000,000원',
               price=None, budget=220_000_000)
    assert b.meta.P == 200_000_000
    assert b.meta.P_source == 'notice'


@pytest.mark.parametrize('label', ['추정가격(부가가치세 제외)', '추정가격 (부가세 별도)'])
def test_estimate_label_tax_annotation_is_not_a_new_amount_field(monkeypatch, label):
    monkeypatch.setattr(switches, 'CODE_AUDIT_BASE', True)
    b = bundle(f'기초금액: 220,000,000원 / {label}: 200,000,000원 / 부가세: 20,000,000원',
               price=None, budget=None)
    assert b.meta.P == 200_000_000


def test_runtime_rejects_incomplete_family_response_without_mutating_defaults():
    b = bundle('2. 입찰참가자격', '가. 비영리법인만 참여 가능')
    ln = b.notice.lines[-1]
    r = SimpleNamespace(fam='inst', cands=[ln], extra=())
    before = dict(b.readings.get('inst', {}).get(ln.i, {}))
    assert main.consume(b, r, '{}') is False
    assert main.consume(b, r, json.dumps({f'L{ln.i}': {'역할': '참가자격'}}, ensure_ascii=False)) is False
    assert b.readings.get('inst', {}).get(ln.i, {}) == before


def test_v24_incomplete_response_is_not_an_empty_success():
    b = bundle('사업예산: 170,000,000원')
    assert not stage.consume(b, b.notice.lines, '{}')
    assert not hasattr(b, 'v24p')
    out = {name: [] for name in prompt.FIELD_NAMES}
    assert stage.consume(b, b.notice.lines, json.dumps(out))


def test_successful_retry_is_journaled_and_counted(tmp_path, monkeypatch):
    class RetryEngine(MockEngine):
        attempts = 0

        def generate(self, batch):
            self.attempts += 1
            if self.attempts == 1:
                return [('invalid json', 'length', 1) for _ in batch]
            return super().generate(batch)

    monkeypatch.setattr(main, 'MockEngine', RetryEngine)
    inp = tmp_path / 'input.jsonl'
    inp.write_text(json.dumps({'id': 'audit-example', 'meta': {},
                              'docs': [{'type': '공고문', 'text': '2. 입찰참가자격\n가. 비영리법인만 참여 가능'}]},
                             ensure_ascii=False) + '\n', encoding='utf-8')
    journal = tmp_path / 'journal.jsonl.gz'
    assert main.main(['--mode', 'mock', '--input', str(inp), '--output-dir', str(tmp_path / 'output'),
                      '--families', 'inst', '--journal', str(journal)]) == 0
    with gzip.open(journal, 'rt', encoding='utf-8') as fh:
        rows = [json.loads(line) for line in fh]
    assert len(rows) == 2
    assert rows[0]['text'] == 'invalid json'
    assert json.loads(rows[1]['text'])
    stats = json.loads(Path(str(journal) + '.stats.json').read_text(encoding='utf-8'))
    assert stats['by_family']['inst'] == [1, 1]
    assert stats['answered'] == 1 and stats['parse_failed'] == 0


def test_first_pass_uses_available_attachment_when_no_notice_text():
    b = facts.build({'id': 'audit-example', 'meta': {},
                     'docs': [{'type': '규격서', 'text': '납품 규격에 따라 물품을 공급한다.'}]}, catalog.load())
    r = main.first_pass_request(MockEngine(), b, 0)
    assert r is not None and r.cands


@pytest.mark.parametrize('price,expected', [(1_800_000_000, True), (1_900_000_000, False),
                                         (1_818_181_818, True), (1_818_181_819, False), (None, False)])
def test_sw_band_uses_the_same_project_amount_basis_as_the_catalog(price, expected):
    b = bundle('소프트웨어 개발 사업', price=price, budget=None)
    b.sw_project = '소프트웨어 개발·구축·유지관리·운영'
    assert judge.sw_sme_band(b) is expected


def test_v24_keeps_own_region_when_the_same_line_also_names_a_partner(monkeypatch):
    monkeypatch.setattr(switches, 'AUDIT_FIXES', True)
    monkeypatch.setattr(switches, 'AUDIT_FIXES3', True)
    loc = '가. 주된 영업소가 서울특별시에 소재한 업체는 경기도 소재 업체와 분담이행을 허용합니다.'
    b = bundle('2. 입찰참가자격', loc)
    b.meta.region_flag, b.meta.region_sido = 'Y', {'경기도'}
    ln = reading(b, 'region', loc, 역할='참가자격', 대상='입찰자 소재지 제한')
    assert judge.v24_region(b) is ln
    b.meta.region_sido = {'서울특별시'}
    assert judge.v24_region(b) is None


@pytest.mark.parametrize('posted,deadline', [('2026-01-01', '2026-01-15'),
                                          ('2026-01-15', '2026-02-15')])
def test_zero_day_briefing_interval_is_not_sufficient(posted, deadline, monkeypatch):
    monkeypatch.setattr(switches, 'CODE_AUDIT_BASE', True)
    brief = '1. 사업설명회: 2026. 1. 15. 10:00'
    b = bundle(brief, f'2. 제안서 제출 마감: {deadline}', price=90_000_000)
    b.meta.law, b.meta.posted = '지방', dt.date.fromisoformat(posted)
    ln = reading(b, 'brief', brief, 참석='참석해야 입찰·제안 가능')
    assert judge.v23(b) is ln


def test_explicit_proposal_deadline_does_not_borrow_a_later_opening_date(monkeypatch):
    monkeypatch.setattr(switches, 'CODE_AUDIT_BASE', True)
    b = bundle('1. 제안서 제출 마감: 2026. 1. 15.', '2. 개찰 일시: 2026. 2. 15.')
    b.meta.posted = dt.date(2026, 1, 1)
    assert judge.proposal_deadline(b) == dt.date(2026, 1, 15)


def test_wrapped_deadline_date_is_not_a_numbered_section(monkeypatch):
    monkeypatch.setattr(switches, 'CODE_AUDIT_BASE', True)
    b = bundle('1. 제안서 제출 마감:', '2026. 1. 15.', '2. 개찰 일시: 2026. 2. 15.')
    b.meta.posted = dt.date(2026, 1, 1)
    assert judge.proposal_deadline(b) == dt.date(2026, 1, 15)


def test_value_outside_the_shown_excerpt_is_not_a_valid_copy():
    b = bundle('사업예산: 170,000,000원', '사업예산: 200,000,000원')
    assert stage.locate(b.notice, [b.notice.lines[0]], 1, '200,000,000원') is None
    assert stage.locate(b.notice, [b.notice.lines[0]], 1, '170,000,000원') is b.notice.lines[0]
    long = bundle('안내 ' * 200 + '사업예산: 170,000,000원')
    assert stage.locate(long.notice, long.notice.lines, 0, '170,000,000원') is None


def test_visible_wrapped_value_is_still_grounded():
    b = bundle('사업예산: 170,000,', '000원')
    assert stage.locate(b.notice, b.notice.lines, 0, '170,000,000원') is b.notice.lines[0]


@pytest.mark.parametrize('side,price,hit', [('이하', 100_000_000, False), ('이하', 120_000_000, True),
                                         ('초과', 100_000_000, True), ('초과', 120_000_000, False)])
def test_method_band_preserves_inclusive_and_strict_inequalities(side, price, hit):
    value = f'제한경쟁·1억원{side}'
    b = bundle(f'공고명: 시설 관리({value})', price=price, budget=price)
    ln = b.notice.lines[0]
    assert (compare.method(b, {'계약방법': [(ln, '공고명', value)]}) is not None) is hit


@pytest.mark.parametrize('family', ['inst', 'v24p'])
def test_long_metadata_cannot_escape_the_request_token_limit(family):
    class CharacterEngine(MockEngine):
        def token_ids(self, messages, thinking=False):
            return [0] * sum(len(m['content']) for m in messages)

    b = bundle('2. 입찰참가자격', '가. 비영리법인만 참여 가능', '사업예산: 170,000,000원')
    b.notice.meta['업무구분'] = '긴 비정상 메타데이터 ' * 10_000
    eng = CharacterEngine()
    req = (main.first_pass_request(eng, b, 0) if family == 'inst'
           else stage.request(eng, b, 0, main.Request))
    assert req is not None and req.cands
    assert len(req.token_ids) + req.max_tokens + req.budget <= main.PROMPT_TOKEN_LIMIT
    assert len(b.notice.meta['업무구분']) > main.PROMPT_TOKEN_LIMIT
