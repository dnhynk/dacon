"""Literal sweep B switches for v10·v11·v13 (commit b8c81cb; scratchpad h08/B_v10_v11_v13/REPORT.md). Each test shows the
switch-off behaviour (current) and the switch-on behaviour on a small synthetic notice built through facts.build."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'submission'))

from pps_c import catalog, facts, judge, switches  # noqa: E402

UNION_GOODS = ('v10', 'v11', 'v13')      # the union package's COMPETITIVE_GOODS (CG: v10·v11·v13 judged on goods too)
SME_QUAL = '나. 「중소기업기본법」 제2조에 따른 중소기업자로서 「중소기업 범위 및 확인에 관한 규정」에 따라 발급된 중소기업확인서를 소지한 자'
SMALL_QUAL = ('나. 「중소기업기본법」 제2조 제2항에 따른 소기업 또는 「소상공인 보호 및 지원에 관한 법률」 제2조에 따른 소상공인으로서 '
              '「중소기업 범위 및 확인에 관한 규정」에 따라 발급된 소기업·소상공인 확인서를 소지한 자')
# An SME-level clause whose dot variant (⸱, U+2E31) is outside families.DOT: without size_normal, "중⸱소기업자" reads as 소기업.
DOT_SME_QUAL = ('나. 「중소기업기본법」 제2조에 따른 중⸱소기업자 또는 「소상공인 보호 및 지원에 관한 법률」 제2조에 따른 소상공인으로서 '
                '「중소기업 범위 및 확인에 관한 규정」에 따라 발급된 중⸱소기업⸱소상공인 확인서를 소지한 자')
DP_QUAL = '가. 「중소기업제품 구매촉진 및 판로지원에 관한 법률」 제9조에 따라 직접생산확인증명서[컴퓨터서버(4321150102)]를 소지한 자'


def notice(m, item_line, qual):
    text = '\n'.join(['입찰공고', '1. 입찰에 부치는 사항', item_line, '2. 입찰참가자격', qual, '3. 입찰방법', '가. 전자입찰'])
    return facts.build({'id': 'T', 'meta': m, 'docs': [{'type': '공고문', 'text': text}]}, catalog.load())


def goods(codes, item_line, qual, P, method='제한경쟁', award='적격심사제', clause='[판로지원법 시행령] 중기업,소기업,소상공인 제한'):
    m = {'적용계약법': '지방계약법', '업무구분': '물품(내자)', '계약방법': method, '낙찰방법': award, '입찰추정가격': str(P),
         '배정예산금액': str(P * 1.1), '세부품명번호목록': codes, '조항호내용': clause}
    return notice(m, item_line, qual)


def service(title_line, qual='가. 경쟁입찰 참가자격을 갖춘 자', clause='None'):
    m = {'적용계약법': '지방계약법', '업무구분': '일반용역', '계약방법': '제한경쟁', '낙찰방법': '협상에의한계약',
         '입찰추정가격': '90000000', '배정예산금액': '99000000', '조항호내용': clause}
    return notice(m, title_line, qual)


def test_sweep_b_switches_default_off():
    for k in ('V13_ANY_SECTION', 'V13_REGISTERED', 'V13_PRIVATE', 'V10_FLOOR_PRIVATE_ONLY'):
        assert getattr(switches, k) is False, k
    assert switches.COMP_REGISTERED == ()
    assert switches.COMP_MIXED == ()


# ---------------------------------------------------------------- V10_FLOOR_PRIVATE_ONLY
def test_v10_floor_applies_to_private_contracts_only(monkeypatch):
    monkeypatch.setattr(switches, 'COMPETITIVE_GOODS', UNION_GOODS)
    bid = goods('컴퓨터서버[4321150102]', '가. 품명: 컴퓨터서버 구매', SME_QUAL, 9_000_000)            # 제한경쟁 below 1천만원, no 직생 clause
    quote = goods('컴퓨터서버[4321150102]', '가. 품명: 컴퓨터서버 구매', SME_QUAL, 9_000_000, method='수의계약', award='소액수의견적')
    with_dp = goods('컴퓨터서버[4321150102]', '가. 품명: 컴퓨터서버 구매', DP_QUAL + '\n' + SME_QUAL, 9_000_000)
    assert bid.scope.competitive is True and bid.meta.P < 1e7 and not bid.meta.private and quote.meta.private
    assert judge.v10(bid) is None and judge.v10(quote) is None and judge.v10(with_dp) is None
    monkeypatch.setattr(switches, 'V10_FLOOR_PRIVATE_ONLY', True)
    assert judge.v10(bid) is True                 # 판로지원법 제9조①: the 1천만원 floor is a 수의계약 condition; a competitive bid is judged
    assert judge.v10(quote) is None               # a 소액수의 견적 below 1천만원 stays out
    assert judge.v10(with_dp) is None             # a stated 직생 requirement keeps v10 quiet
    assert judge.v11(bid) is None                 # the SME clause is present: nothing else changes


# ---------------------------------------------------------------- COMP_REGISTERED
def test_comp_registered_takes_the_registered_designation(monkeypatch):
    designated = service('가. 용역명: 정책 홍보물 편집 및 제작 용역', clause='중소기업청장이 지정.고시한 제품')
    plain = service('가. 용역명: 정책 홍보물 편집 및 제작 용역', clause='행정안전부령 금액 미만 본점소재지')
    assert designated.scope.basis == 'service:none' and designated.scope.competitive is False
    assert judge.v10(designated) is None and judge.v11(designated) is None and judge.v13(designated) is None
    monkeypatch.setattr(switches, 'COMP_REGISTERED', UNION_GOODS)
    assert judge.v10(designated) is True and judge.v11(designated) is True     # no 직생 clause, no SME clause
    assert judge.v13(designated) is None                                        # no small-only clause either
    assert judge.v10(plain) is None and judge.v11(plain) is None               # another 조항호 registers no designation
    monkeypatch.setattr(switches, 'COMP_REGISTERED', ('v11',))
    assert judge.v10(designated) is None and judge.v11(designated) is True     # item-gated


# ---------------------------------------------------------------- v13 under the audit fixes (AF_ITEMS containing 'v13')
def test_v13_under_audit_fixes_reads_the_sme_level_clause(monkeypatch):
    monkeypatch.setattr(switches, 'COMPETITIVE_GOODS', UNION_GOODS)
    b = goods('컴퓨터서버[4321150102]', '가. 품명: 컴퓨터서버 구매', DP_QUAL + '\n' + DOT_SME_QUAL, 150_000_000)
    ln = next(x for x in b.notice.lines if x.text.strip().startswith('나.'))
    assert ln.sec == 'QUAL' and b.read('size', ln).get('역할') == '참가자격 제한'
    out = judge.judge(b)
    assert out['v13'][0] == 1 and out['v13'][1].startswith('나.')             # current: "중⸱소기업자" read as the small class
    assert out['v10'] == (0, '') and out['v11'] == (0, '')
    monkeypatch.setattr(switches, 'AF_ITEMS', ('v13',))
    out = judge.judge(b)
    assert out['v13'] == (0, '')                                                # size_normal: 중⸱소기업 = 중·소기업, an SME-level clause
    assert out['v10'] == (0, '') and out['v11'] == (0, '')
    small = goods('컴퓨터서버[4321150102]', '가. 품명: 컴퓨터서버 구매', DP_QUAL + '\n' + SMALL_QUAL, 150_000_000)
    assert judge.judge(small)['v13'][0] == 1                                    # a genuine small-only clause still fires
