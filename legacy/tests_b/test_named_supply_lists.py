from tools.audit_named_supply_lists import candidates


def test_multiple_named_and_generic_list_entries_keep_exact_source_positions():
    text='3-1 구성품\n  ○ ACME Raven 4 본체 1개, USB-C to USB-C 케이블 2개, ACME 배터리 3개\n'
    values=candidates(text)
    assert len(values)==3
    assert values[0]['value_source']['text']=='ACME Raven 4 본체'
    assert values[1]['value_source']['text']=='USB-C to USB-C 케이블'
    assert all(not v['unique_name_certified'] and not v['purchase_role_certified'] for v in values)
    for value in values:
        for name in ('source','value_source','quantity_source'):
            ref=value[name]
            assert text[ref['start']:ref['end']]==ref['text']


def test_missing_count_and_separate_physical_lines_do_not_create_a_link():
    text='ACME Raven 4\n\n1개\n규격: USB-C 5V\nhttps://supplier.example/items 2개\n'
    assert candidates(text)==[]


def test_printed_thousands_are_one_count_and_never_an_inferred_product_property():
    values=candidates('ACME battery 1,000개, microSD*64GB*1개')
    assert len(values)==2
    assert values[0]['quantity_source']['text']=='1,000개'
    assert values[1]['value_source']['text']=='microSD*64GB'
