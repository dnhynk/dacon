"""Dedicated item stages (switches.DEDICATED). An item listed there is decided by its own stage instead of the shared rule.

A stage module has FAM (its journal family name), request(engine, b, k, request_cls) -> a request or None,
consume(b, cands, text) -> bool (stores the stage's reading on b), and verdict(b) -> an evidence line, True or None.
A module that decides several items is listed as 'module:X' per item and takes the violation: verdict(b, 'X').
Modules are imported only when their item is switched on, so the default runtime never loads them.
"""
import importlib

MODULES = {'v9': 'model_name', 'v12': 'general_dp', 'v24': 'input_mismatch', 'v19': 'pledge', 'v20': 'sw_participation',
           'v10': 'competition_product:A', 'v11': 'competition_product:B', 'v13': 'competition_product:C',
           'v1': 'qualification:A', 'v4': 'qualification:B', 'v8': 'qualification:C'}


def stage(item):
    return importlib.import_module(f"{__name__}.{MODULES[item].partition(':')[0]}")


def verdict(item, b):
    tag = MODULES[item].partition(':')[2]
    return stage(item).verdict(b, tag) if tag else stage(item).verdict(b)


def active():
    from .. import switches
    return list(dict.fromkeys(stage(item) for item in switches.DEDICATED))


def by_family(fam):
    return next((st for st in active() if st.FAM == fam), None)
