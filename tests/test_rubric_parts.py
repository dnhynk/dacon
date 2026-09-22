"""W4 ablations: selector validation and exact suffix boundaries."""
from dataclasses import replace
from itertools import combinations

import pytest

from submission.pps.prompts import Config, build_shared_prompts, rubric_parts
from test_rubric_variant import fixture, GROUPS, MARKER


@pytest.mark.parametrize('variant', ['parts:', 'parts:wat', 'parts:v1+', 'parts:+v1',
                                   'parts:v1+v1', 'parts:v1+v1b', 'parts:v19b+v19',
                                   'parts: v1', [], {}])
def test_bad_parts(variant):
    with pytest.raises(ValueError):
        Config(rubric_variant=variant)


def test_all_original_subsets_change_only_selected_item_lines():
    rec, knowledge, cfg, tokenizer = fixture()
    off = build_shared_prompts(rec, knowledge, cfg, tokenizer, GROUPS)
    names = ('consistency', 'v1', 'v2', 'v9', 'v19')
    for size in range(1, 6):
        for selected in combinations(names, size):
            variant = 'parts:' + '+'.join(selected)
            on = build_shared_prompts(rec, knowledge, replace(cfg, rubric_variant=variant), tokenizer, GROUPS)
            reverse = build_shared_prompts(rec, knowledge, replace(cfg, rubric_variant='parts:' + '+'.join(reversed(selected))), tokenizer, GROUPS)
            assert on == reverse
            assert on[1] == off[1]
            for a, b in zip(off, on):
                assert a['spans'] == b['spans']
                assert a['messages'][0] == b['messages'][0]
                assert a['messages'][1]['content'].split(MARKER)[0] == b['messages'][1]['content'].split(MARKER)[0]
                for line in a['messages'][1]['content'].splitlines():
                    if line.startswith('v') and ': ' in line:
                        item = line.split(' ', 1)[0]
                        assert (line in b['messages'][1]['content'].splitlines()) == (item not in selected or a is off[1])
    all_parts = build_shared_prompts(rec, knowledge, replace(cfg, rubric_variant='parts:' + '+'.join(names)), tokenizer, GROUPS)
    old = build_shared_prompts(rec, knowledge, replace(cfg, rubric_variant='fact_judgment'), tokenizer, GROUPS)
    assert all_parts == old


@pytest.mark.parametrize('name,item,index', [('v1b', 1, 0), ('v19b', 19, 2)])
def test_revised_parts_are_isolated(name, item, index):
    rec, knowledge, cfg, tokenizer = fixture()
    off = build_shared_prompts(rec, knowledge, cfg, tokenizer, GROUPS)
    on = build_shared_prompts(rec, knowledge, replace(cfg, rubric_variant='parts:' + name), tokenizer, GROUPS)
    assert rubric_parts('parts:' + name) == {name}
    for i in range(3):
        if i != index:
            assert off[i] == on[i]
        else:
            a = off[i]['messages'][1]['content'].splitlines()
            b = on[i]['messages'][1]['content'].splitlines()
            changed = [(x, y) for x, y in zip(a, b) if x != y]
            assert len(a) == len(b) and len(changed) == 1
            assert all(s.startswith(f'v{item} ') for s in changed[0])
