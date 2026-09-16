"""Check a named property's local obligation, without inventing its value.

An obligation to provide an editing capability does not require every output
to use it. Conversely an optional model reading cannot erase an explicit must.
Relations retain offsets in the original physical source line. This is a
bounded contradiction check, not a complete Korean deontic parser.
"""
from __future__ import annotations

import re


_PARTICLES = (r'\s*(?:용)?\s*(?:(?:등)?\s*(?:으로|을|를|이|가|은|는|도))?\s*'
    r'(?:(?:반드시|필수로|의무적으로|별도로|실제로|일체|전혀|절대로)\s*){0,2}')
# These are implementation actions. Identifiability ("식별할 수 있는 정보")
# is itself a property, and must not be discarded by a generic "수 있다" rule.
_ACTION = r'(?:포함|삽입|표시|표기|노출|사용|활용|제작|적용|탑재|구비|설치|제공)'
_WEAK = re.compile(_PARTICLES + _ACTION + r'(?:'
    r'(?:할|될)\s*수\s*(?:있|없)|'
    r'(?:해도|하여도|되어도|돼도)\s*(?:된다|됨|됩니다|좋|무방)|'
    r'(?:하는|되는|할|될|을|하도록|하기를)?\s*(?:것을|것이|것은)?\s*(?:권장|권고|추천)|'
    r'(?:이|가|은|는)?\s*가능(?:하다|함|합니다|한|$)|'
    r'(?:을|를)?\s*허용|(?:이|가|은|는)?\s*선택\s*사항|'
    r'(?:하는|할)\s*(?:기능|능력|도구)|'
    r'(?:하는|할)\s*(?:것이|것은)?\s*(?:필수|의무)(?:가|는)?\s*아니|'
    r'(?:할)?\s*(?:필요|의무)(?:가|는)?\s*없)')
_REQUIRED = re.compile(_PARTICLES + r'(?:'
    + _ACTION + r'(?:하여야|해야|하여야만)|'
    r'(?:이|이어|이여)야\s*(?:한다|함|합니다|할\s*것))')
_WEAK_PREFIX = re.compile(r'^\s*(?:[○●□■※*-]|\d+[.)])?\s*'
    r'(?:권장|선택|참고|예시)\s*(?:사항|내용|조건)?\s*[:：]')


def review(text, claimed, relations):
    """Use property-bound relations, never another clause's optional action."""
    observations = []
    prefix = _WEAK_PREFIX.match(text)
    for relation in relations:
        start, end = relation['cue_end'], relation['end']
        tail = text[start:end]
        for kind, pattern in (('permitted_recommended_or_capability', _WEAK),
                              ('explicit_requirement', _REQUIRED)):
            match = pattern.match(tail)
            if match:
                observations.append({'kind': kind, 'start': start + match.start(),
                    'end': start + match.end(), 'text': match[0],
                    'property_start': relation['cue_start'], 'property_end': start})
    if prefix and any(r['start'] <= prefix.end() <= r['cue_start'] for r in relations):
        observations.append({'kind': 'nonmandatory_source_caption',
            'start': prefix.start(), 'end': prefix.end(), 'text': prefix[0]})
    kinds = {o['kind'] for o in observations}
    weak = bool(kinds - {'explicit_requirement'})
    issue = None
    if claimed == 'required' and weak:
        issue = 'original_property_does_not_establish_delivery_obligation'
    elif claimed in {'optional', 'example', 'negated'} and not weak and 'explicit_requirement' in kinds:
        issue = 'model_modality_conflicts_with_original_requirement'
    return {'issue': issue, 'relations': observations,
            'source_modality_overwrites_model': False, 'semantic_truth_certified': False}
