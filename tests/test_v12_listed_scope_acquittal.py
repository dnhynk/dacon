"""v12 listed-scope acquittal (config v12_listed_scope_acquittal, default off): the Q10 consumer's withheld overlay
still sets v12 to 0 when the model's whole-task scope names a listed service whose catalog conditions are met."""
import pytest

from submission.pps import catalog_scope
from submission.pps.prompts import Config


def _patch_conditions(monkeypatch, status):
    import submission.pps.qualification as qualification
    import submission.pps.prices as prices
    monkeypatch.setattr(qualification, 'catalog_condition', lambda *a, **k: {'status': status})
    monkeypatch.setattr(qualification, 'software_catalog_prices', lambda record, p: p)
    monkeypatch.setattr(prices, 'project_prices', lambda record: {'budget': {}, 'estimated_price': {}})


BY_CODE = {'8014199001': {'code': '8014199001', 'name': '행사대행', 'condition': '추정가격 10억원 미만'}}
ORIGINAL = {'estimate_won': 150_000_000, 'budget_won': 165_000_000}


def test_acquittal_needs_listed_whole_link_with_met_conditions(monkeypatch):
    _patch_conditions(monkeypatch, 'met')
    listed = {'catalog_relation': 'listed_category', 'relationships': [{'code': '8014199001', 'role': 'whole'}]}
    rows = catalog_scope._listed_scope_acquittal(listed, BY_CODE, ORIGINAL, {'docs': [], 'meta': {}})
    assert rows == [{'code': '8014199001', 'name': '행사대행', 'condition': 'met'}]
    outside = dict(listed, catalog_relation='outside_all_listed_service_categories')
    assert catalog_scope._listed_scope_acquittal(outside, BY_CODE, ORIGINAL, {}) is None
    component = dict(listed, relationships=[{'code': '8014199001', 'role': 'component'}])
    assert catalog_scope._listed_scope_acquittal(component, BY_CODE, ORIGINAL, {}) is None
    unknown_code = dict(listed, relationships=[{'code': '9999999999', 'role': 'whole'}])
    assert catalog_scope._listed_scope_acquittal(unknown_code, BY_CODE, ORIGINAL, {}) is None


def test_unmet_condition_keeps_the_general_purchase_reading(monkeypatch):
    _patch_conditions(monkeypatch, 'not_met')
    listed = {'catalog_relation': 'listed_category', 'relationships': [{'code': '8014199001', 'role': 'whole'}]}
    assert catalog_scope._listed_scope_acquittal(listed, BY_CODE, ORIGINAL, {'docs': [], 'meta': {}}) is None


def test_config_switch_defaults_off():
    assert Config().v12_listed_scope_acquittal is False
    assert Config(v12_listed_scope_acquittal=True).v12_listed_scope_acquittal is True
    with pytest.raises(ValueError):
        Config(v12_listed_scope_acquittal=1)
