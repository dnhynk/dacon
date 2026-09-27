"""Dedicated item stages (switches.DEDICATED). An item listed there is decided by its own stage instead of the shared rule.

A stage module has FAM (its journal family name), request(engine, b, k, request_cls) -> a request or None,
consume(b, cands, text) -> bool (stores the stage's reading on b), and verdict(b) -> an evidence line, True or None.
Modules are imported only when their item is switched on, so the default runtime never loads them.
"""
import importlib

MODULES = {'v9': 'model_name', 'v12': 'general_dp', 'v24': 'input_mismatch'}


def stage(item):
    return importlib.import_module(f'{__name__}.{MODULES[item]}')


def active():
    from .. import switches
    return [stage(item) for item in switches.DEDICATED]


def by_family(fam):
    return next((st for st in active() if st.FAM == fam), None)
