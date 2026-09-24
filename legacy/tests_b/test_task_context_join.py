"""A fresh abstention must not inherit an old specialist's decision."""
import pytest

from submission.b4_entry import assemble
from tools.consume_task_context import replacement_packets


def packet(key, family, record):
    return dict(request_key=key, family=family, record_id=record)


def test_new_abstention_restores_original_a10_and_preserves_other_routes():
    records = [dict(id='notice')]
    a = packet('A', 'A', 'notice')
    law = packet('L', 'L', 'notice')
    old = packet('old-Q', 'Q', 'notice')
    fresh = packet('new-Q', 'Q', 'notice')
    rows = dict(A={**{f'v{i}': 0 for i in range(1, 25)},
                   **{f'e{i}': '' for i in range(1, 25)}, 'v10': 1, 'e10': 'original A10'},
                L=dict(v20=1, e20='L evidence'))
    rows.update({'old-Q': dict(v10=0, e10=''), 'new-Q': None})
    _, prior = assemble(records, [a, law, old], rows)
    assert prior['notice']['v10'] == 0
    _, replaced = assemble(records, replacement_packets([a, law, old], [fresh]), rows)
    assert replaced['notice']['v10'] == 1
    assert replaced['notice']['e10'] == 'original A10'
    assert replaced['notice']['v20'] == 1
    assert replaced['notice']['e20'] == 'L evidence'


@pytest.mark.parametrize('new', [[], [packet('new', 'Q', 'other')],
    [packet('new', 'Q', 'notice'), packet('duplicate', 'Q', 'notice')],
    [packet('new', 'A', 'notice')]])
def test_incomplete_or_wrong_replacement_is_not_a_complete_arm(new):
    with pytest.raises(ValueError, match='complete identical cohort'):
        replacement_packets([packet('old', 'Q', 'notice')], new)
