"""PX v24 guards behind switches.V24P_GUARDS (runs/rebuild_c/transfer_20260925/audit/expert/PX/REPORT.md "V24W1P"): each test
shows the unguarded firing and the guarded silence, and one case per guard that must keep firing."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'submission'))
from pps_c import catalog, facts, judge, switches  # noqa: E402
from pps_c.v24 import compare, prompt, select, stage  # noqa: E402

CAT = catalog.load()
META = {'적용계약법': '지방계약법', '업무구분': '일반용역', '계약방법': '제한경쟁', '낙찰방법': '적격심사',
        '배정예산금액': 170000000, '입찰추정가격': 154545455, '지역제한여부': 'N', '제한지역코드목록': None,
        '업종제한여부': 'N', '면허업종제한목록': None, '공고게시일자': '2026-03-02'}


def bundle(*lines, **meta):
    m = dict(META, **meta)
    text = '\n'.join(('입찰공고', '1. 입찰에 부치는 사항') + lines + ('2. 입찰참가자격', '가. 자격을 갖춘 자'))
    return facts.build({'id': 'T', 'meta': m, 'docs': [{'type': '공고문', 'text': text}]}, CAT)


def fed(b, **fields):
    """모델 출력을 흉내 내어 b.v24p를 세운다. fields: 항목 → [(줄 텍스트 일부, 값)]."""
    by_text = {ln.text: ln for ln in b.notice.lines}
    obj = {name: [] for name in prompt.FIELD_NAMES}
    for name, items in fields.items():
        for hint, value in items:
            ln = next(x for t, x in by_text.items() if hint in t)
            obj[name].append({'줄': ln.i, '이름표': name, '값': value})
    assert stage.consume(b, select.excerpt(b.notice), json.dumps(obj, ensure_ascii=False))
    return b


def axis(b, guards, monkeypatch):
    monkeypatch.setattr(switches, 'V24P_GUARDS', guards)
    return compare.explain(b)[0]


def test_default_off():
    assert switches.V24P_GUARDS is False


def test_component_row_under_a_total_of_its_own_field(monkeypatch):
    b = fed(bundle('가. 학교별 추정가격', '[수요기관(초등학교)]', '83,747,950', '[기관(초등학교)]', '21,475,600', '계', '105,223,550',
                   배정예산금액=105223550, 입찰추정가격=105223550),
            추정가격=[('83,747,950', '83,747,950')], 추정금액=[('105,223,550', '105,223,550')])
    assert axis(b, False, monkeypatch) == 'amount'
    assert axis(b, True, monkeypatch) is None                   # one school's row beside the 계 that equals meta


def test_cell_beside_the_other_field_still_fires(monkeypatch):
    # 기초금액 | 추정가격 | 부가가치세 cells: the larger 72,600,000 is B (P × 1.1), not the 추정가격 total, so 59,400,000 stays a mismatch
    b = fed(bundle('기초금액', '추정가격', '부가가치세', '72,600,000', '59,400,000', '5,940,000',
                   배정예산금액=72600000, 입찰추정가격=66000000),
            추정가격=[('59,400,000', '59,400,000')])
    assert axis(b, False, monkeypatch) == 'amount'
    assert axis(b, True, monkeypatch) == 'amount'


def test_cell_beside_a_base_amount_still_fires(monkeypatch):
    # an edited 추정금액 cell (0.6 × B) beside the 기초금액 cell that still equals B: 기초금액 is no total of the 추정금액 field
    b = fed(bundle('기 초 금 액', '금330,000,000원', '추정금액', '금198,000,000원', 배정예산금액=330000000, 입찰추정가격=300000000),
            추정금액=[('금198,000,000원', '금198,000,000원')], 기초금액=[('금330,000,000원', '금330,000,000원')])
    assert axis(b, True, monkeypatch) == 'amount'


def test_estimate_note(monkeypatch):
    b = fed(bundle('다. 사업예산 : 67,000천원 ※ 2026년 예산액으로 추정금액이며 운영 및 사업비 지원상황에 따라 변동될 수 있습니다.',
                   배정예산금액=66300000, 입찰추정가격=60272727),
            예산=[('사업예산', '67,000천원')])
    assert axis(b, False, monkeypatch) == 'amount'
    assert axis(b, True, monkeypatch) is None
    b = fed(bundle('다. 사업예산 : 72,000,000원', 배정예산금액=66300000, 입찰추정가격=60272727), 예산=[('사업예산', '72,000,000원')])
    assert axis(b, True, monkeypatch) == 'amount'               # no note: the difference stands


def test_rounding_gap(monkeypatch):
    monkeypatch.setattr(switches, 'V24_AMOUNT_WIDE', True)
    b = bundle('용 역 명 | 용 역 금 액(원)', '폐기물 처리 용역 | 기초금액 | 55,452,000', '추정가격 | 50,411,400', '부 가 세 | 5,041,140',
               배정예산금액=55452000, 입찰추정가격=50410909)
    monkeypatch.setattr(switches, 'V24P_GUARDS', False)
    assert not judge.agrees(50411400, 50410909)
    assert judge.v24_amount_wide(b) is not None
    monkeypatch.setattr(switches, 'V24P_GUARDS', True)
    assert judge.agrees(50411400, 50410909)                     # 491원 of 50,410,909 (0.001%)
    assert judge.v24_amount_wide(b) is None
    assert not judge.agrees(50460000, 50410909)                 # 0.1% is beyond rounding
    b = bundle('나. 추정가격 : 414,545,460원', 배정예산금액=456000000, 입찰추정가격=414545455)
    assert judge.v24_amount(b) is not None                      # the P3a reader keeps its exact comparison


def test_alternative_eligibility(monkeypatch):
    lic = dict(업종제한여부='Y', 면허업종제한목록='[학술.연구용역(1169)]')
    lines = ('나. 나라장터에 학술.연구용역[업종코드 : 1169] 으로 등록한 업체', '라. 다음 각 호의 자격 요건 중 어느 하나에 해당하는 업체',
             '1)『지방자치단체출연 연구원의 설립 및 운영에 관한 법률』 제4조에 따라 설립된 연구기관',
             '2)『고등교육법』 제2조 각 호에 의한 교육기관 또는 산학협력단[업종코드 : 3178]', '3)『민법』 등 기타 법률에 의해 설립된 법인')
    b = fed(bundle(*lines, **lic), 업종=[('산학협력단', '산학협력단[업종코드 : 3178]')])
    ln = next(x for x in b.notice.lines if '3178' in x.text)
    assert judge.alternative_item(b.notice, ln)
    assert axis(b, False, monkeypatch) == 'licence'
    assert axis(b, True, monkeypatch) is None
    monkeypatch.setattr(switches, 'V24_LICENSE_WIDE', True)
    monkeypatch.setattr(switches, 'V24P_GUARDS', False)
    assert judge.v24_license_wide(b) is not None
    monkeypatch.setattr(switches, 'V24P_GUARDS', True)
    assert judge.v24_license_wide(b) is None


def test_list_of_required_items_is_no_alternative(monkeypatch):
    lic = dict(업종제한여부='Y', 면허업종제한목록='[학술.연구용역(1169)]')
    b = fed(bundle('가. 아래 조건을 모두 갖춘 업체', '1) 학술.연구용역[업종코드 : 1169] 으로 등록한 업체', '2) 산학협력단[업종코드 : 3178]', **lic),
            업종=[('3178', '산학협력단[업종코드 : 3178]')])
    ln = next(x for x in b.notice.lines if '3178' in x.text)
    assert not judge.alternative_item(b.notice, ln)
    assert axis(b, True, monkeypatch) == 'licence'
