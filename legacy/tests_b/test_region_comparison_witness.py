"""A fully named extra province can prove mismatch without resolving a district."""
import pytest

from submission.pps.comparison import compare, positive_decision
from tests.test_comparison import record


def restriction(place):
    return '법인등기부상 본점 소재지를 계속 ' + place + '에 둔 업체이어야 한다.'


def test_explicit_outside_province_branch_is_a_complete_difference_witness():
    text = restriction('경기도 [지역:r1|단위=기초|광역=경기도] 또는 제주도')
    rec = record(text, 제한지역코드목록='경기도')
    result = positive_decision(rec, compare(rec))
    assert result['value'] == 1 and result['evidence'] in text
    assert result['comparison']['outside_registered_provinces'] == ['제주특별자치도']


@pytest.mark.parametrize('place,metadata', [
    ('경기도 [지역:r1|단위=기초|광역=경기도]', '경기도'),
    ('경기도 [지역:r1|단위=기초|광역=경기도] 또는 제주도', '경기도,제주특별자치도'),
    ('경기도 [지역:r1|단위=기초|광역=경기도] 또는 제주도', None),
    ('경기도 [지역:r1|단위=기초|광역=경기도] 또는 제주도', '[지역:r2|단위=기초|광역=경기도]'),
])
def test_equal_or_unparsed_projection_never_proves_full_region_identity(place, metadata):
    rec = record(restriction(place), 제한지역코드목록=metadata)
    assert positive_decision(rec, compare(rec)) is None


def test_a_delivery_location_or_example_is_not_an_outside_bidder_branch():
    for text in [restriction('경기도') + '\n납품 장소: 제주도',
                 '예시: ' + restriction('경기도 [지역:r1|단위=기초|광역=경기도] 또는 제주도')]:
        rec = record(text, 제한지역코드목록='경기도')
        assert positive_decision(rec, compare(rec)) is None


def test_unresolved_other_document_does_not_become_a_region_value_difference():
    rec = record(restriction('경기도 [지역:r1|단위=기초|광역=경기도] 또는 제주도'),
                 restriction('경기도'), 제한지역코드목록='경기도')
    assert positive_decision(rec, compare(rec)) is None
