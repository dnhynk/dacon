"""Size-clause readings, qualification headings and the 판로지원 exception (v13–v18)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pps_c import judge, record  # noqa: E402
from pps_c.families import size_words  # noqa: E402


def notice(*lines):
    return record.build({'id': 'T', 'meta': {}, 'docs': [{'type': '공고문', 'text': '\n'.join(lines)}]})


class Bundle:
    def __init__(self, n):
        self.notice = n


def test_sme_certificate_variants():
    assert size_words('중·소기업 및 소상공인으로서 발급된 “중·소기업(소상공인) 확인서”를 보유한 업체') == 'sme'
    assert size_words('「중소기업 범위 및 확인에 관한 규정에 따라“중기업, 소기업ㆍ소상공인 확인서"를 소지한 업체') == 'sme'
    assert size_words('중소기업제품 구매촉진 및 판로지원에 관한 법률 제2조에 따른 중소기업자로 소기업․소 상공인확인서를 소지하고') == 'small'


def test_law_titles_never_name_the_bidder():
    assert size_words('③ ｢중소기업기본법｣ 제2조에 따른 소기업 또는 ｢소상공인 보호 및 지원에 관 한 법률｣ 제2조에 따른 소상공인으로서 '
                      '｢중소기업 범위 및 확인에 관한 규 · 정｣에 따라 발급된 <소기업·소상공인 확인서>를 소지한 자') == 'small'
    assert size_words('※ 「소기업 ․ 소상공인 확인서」는 중소기업제품 공공구매 종합정보망에서 확인하며') == 'small'


def test_qualification_heading_variants():
    n = notice('1. 입찰에 부치는 사항', '2. 입찰방법', '3. 일정', '4. 입찰참가자의 자격에 관한 사항(아래 조건을 모두 갖추어야 함)',
               '가. 중소기업자', '5. 견적 제출 참가자의 자격에 관한 사항', '가. 소기업')
    assert [ln.sec for ln in n.lines][3:] == ['QUAL', 'QUAL', 'QUAL', 'QUAL']


def test_admission_is_not_a_waiver():
    def waives(*lines):
        n = notice(*lines)
        return judge.waives_size_limit(Bundle(n), n.lines[0])
    assert waives('▸ 본 입찰은「중소기업제품 구매촉진 및 판로지원에 관한 법률 시행령」제2조의3제1항제4호에 따라 소기업자 및 소상공인 제한을 하지 않습니다.')
    assert waives('※ 본 용역은 「중소기업제품 공공구매제도 운영요령」 제44조 제3호에 따라 중소기업자 우선조달', '가. 견적 개요')
    assert not waives('※ 「중소기업제품 구매촉진 및 판로지원에 관한 법률시행령」제2조의3 제1항제2호에 해당하는 비영리법인은 입찰참여가 가능합니다.')
    assert not waives('4)「중소기업기본법」제2조에 따른 중소기업이 아닌 경우')
    assert not waives('나. 계약상대자는 인지세법 제3조 및 같은 법 시행령 제2조의3에 의거 인지세를 납부하여야 합니다.')


def test_designated_class_limit():
    women = notice('3. 입찰참가자격', '가. 「여성기업지원에 관한 법률」 제2조 제1호에 따른 여성기업')
    assert judge.designated_class_limit(Bundle(women))
    rule_name = notice('3. 입찰참가자격', '가. 「중·소기업·소상공인 및 장애인기업 확인요령」에 따라 발급된 소기업·소상공인 확인서를 소지한 자')
    assert not judge.designated_class_limit(Bundle(rule_name))
