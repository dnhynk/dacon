import copy

from tools.prepare_specification_contrast import relation_cohort


def records():
    texts = ['모델명: Arc A1\n기존 설비와 호환성 유지', '동등 성능 허용\nCPU: 처리장치',
             '소모품 안내', '사무용품 안내', '청소용품 안내', '운반용품 안내',
             '납품 일정 안내', '포장 안내', '모델명: B2', '동등품 허용', '모델명: C3']
    return [{'id': str(i), 'docs': [{'type': '규격서', 'text': text}]} for i, text in enumerate(texts)]


def test_source_relations_and_fixed_control_counts_are_selected():
    inputs = records()
    selected, inventory = relation_cohort(inputs)
    assert len(selected) == 6
    assert {r['id'] for r in inventory if r['relation_candidate']} == {'0', '1'}
    assert {'0', '1'} <= {r['id'] for r in selected}
    for entry in inventory:
        rec = inputs[int(entry['id'])]
        for span in entry['relation_witnesses']:
            assert rec['docs'][span['doc_index']]['text'][span['start']:span['end']] == span['text']


def test_metadata_labels_and_record_names_do_not_select_candidates():
    inputs = records()
    selected, _ = relation_cohort(inputs)
    changed = copy.deepcopy(inputs)
    for rec in changed:
        rec.update(id='renamed-' + rec['id'], meta={'v9': 1, 'random_score': 0.9})
    observed, _ = relation_cohort(changed)
    assert [r['docs'] for r in observed] == [r['docs'] for r in selected]


def test_plain_replacement_word_is_not_an_identity_or_permission_witness():
    selected, inventory = relation_cohort(records() + [
        {'id': 'unrelated', 'docs': [{'type': '규격서', 'text': '기존 소모품 교체 일정'}]}])
    entry = next(r for r in inventory if r['id'] == 'unrelated')
    assert entry['relation_witnesses'] and not entry['relation_candidate']


def test_numbered_component_field_is_a_source_relation_cue():
    rec = {'id': 'numbered', 'docs': [{'type': '규격서', 'text': '동등품 허용\n1. CPU : Atlas S1'}]}
    _, inventory = relation_cohort([rec])
    assert inventory[0]['relation_candidate']
    assert inventory[0]['relation_witnesses'][0]['text'] == '1. CPU :'
