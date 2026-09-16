"""Literal links from summary rows to nearby item-description sections.

This is a discovery inventory, not a repaired table or purchase classifier.
Every candidate keeps its original document coordinates. Shared/missing names
and semantic aliases are not filled in through list order or elimination.
"""
from __future__ import annotations

import re

from .products import normalized_map
from .retrieval import _source_units
from .table_structure import table_structures

_SECTION = re.compile(r'^[ \t]*(?:(?:[①-⑳]|\d+[.)]|[가-하][.)])[ \t]*)?'
    r'(?P<label>용[ \t]*도|규[ \t]*격|사[ \t]*양|구[ \t]*성[ \t]*품)'
    r'(?:[ \t]*[:：][ \t]*|[ \t]*$)')
_ROLES = {'용도': 'purpose', '규격': 'specification', '사양': 'specification', '구성품': 'components'}
_STOP = re.compile(r'^[ \t]*(?:※[ \t]*)?(?:공통[ \t]*(?:적용[ \t]*)?사항|'
                   r'(?:붙임|별첨|별지)[ \t]*(?:제[ \t]*)?\d)')
_CAPTION = re.compile(r'^[ \t]*[<〈《「\[]?[ \t]*참고[ \t]*용?[ \t]*예시[ \t]*[>〉》」\]]?[ \t]*$')
_FORM = re.compile(r'보고서|확인서|신청서|서약서|양식|서식')


def _ref(text, lo, hi):
    return {'start': lo, 'end': hi, 'text': text[lo:hi]}


def _name_key(text):
    # Parentheses can delimit a literally printed variant. No synonyms, unit
    # conversion, token reordering or fuzzy name repair participates in a link.
    return re.sub(r'[()]', '', normalized_map(text)[0])


def detail_cards(text, *, max_cards=64):
    if type(max_cards) is not int or max_cards < 1:
        raise ValueError('A positive detail-card limit is required')
    tables = table_structures(text)
    lines = _source_units(text)
    cards = []
    truncated = False
    for index, table in enumerate(tables):
        roles = {h['role'] for h in table['headers']}
        if (not {'name', 'unit'} <= roles or roles & {'quantity', 'unit_price', 'amount'}
                or len(table['rows']) != 1 or not table['rows'][0]['name_candidates']):
            continue
        if _FORM.search(text[max(0, table['start']-160):table['start']]):
            continue
        row = table['rows'][0]
        end = tables[index+1]['start'] if index+1 < len(tables) else len(text)
        tail = [(lo, hi) for lo, hi in lines if table['end'] <= lo < end]
        if not tail or not _SECTION.match(text[slice(*tail[0])]):
            continue  # Proximity to a distant specification is insufficient.
        headings = []
        captions = []
        stop = 'next_table_header' if index+1 < len(tables) else 'document_end'
        for lo, hi in tail:
            line = text[lo:hi]
            if _STOP.match(line):
                end, stop = lo, 'independent_common_section_or_attachment'
                break
            match = _SECTION.match(line)
            if match:
                label = re.sub(r'\s+', '', match['label'])
                headings.append((lo, lo + match.end(), _ROLES[label]))
            if _CAPTION.fullmatch(line):
                captions.append(_ref(text, lo, hi))
        if len({role for _, _, role in headings}) < 2:
            continue
        fields = []
        for number, (lo, body_start, role) in enumerate(headings):
            hi = headings[number+1][0] if number+1 < len(headings) else end
            # A caption may belong to a missing image. Preserve it separately,
            # rather than turning it into an operative product requirement.
            caption = next((c for c in captions if body_start <= c['start'] < hi), None)
            if caption:
                hi = caption['start']
            while body_start < hi and text[body_start].isspace():
                body_start += 1
            while hi > body_start and text[hi-1].isspace():
                hi -= 1
            if body_start < hi:
                fields.append({'role': role, 'header': _ref(text, lo, body_start),
                               'body': _ref(text, body_start, hi)})
        if len({f['role'] for f in fields}) < 2:
            continue
        if len(cards) == max_cards:
            truncated = True
            break
        preceding = next(((lo, hi) for lo, hi in reversed(lines) if hi <= table['start']), None)
        cards.append({'key': f'D{len(cards)+1}', 'source': _ref(text, table['start'], end),
            'table_source': _ref(text, table['start'], table['end']),
            'name_candidates': row['name_candidates'], 'unit': row['unit'], 'fields': fields,
            'trailing_captions': captions, 'end_basis': stop,
            'preceding_caption': (_ref(text, *preceding) if preceding and
                _CAPTION.fullmatch(text[slice(*preceding)]) else None),
            'literal_column_alignment': row['literal_column_alignment'],
            'operative_scope_certified': False, 'whole_purchase_certified': False,
            'source_modified': False})
    return {'cards': cards, 'candidate_search_truncated': truncated,
            'catalog_identity_certified': False, 'missing_names_inferred': False}


def detail_links(text, *, max_cards=64):
    inventory = detail_cards(text, max_cards=max_cards)
    links = []
    unmatched = []
    for table in table_structures(text):
        if not any(h['role'] == 'quantity' for h in table['headers']):
            continue
        for row in table['rows']:
            found = []
            prefix = text[row['start']:row['unit']['start']] if row['unit'] else ''
            for card in inventory['cards']:
                for name in row['name_candidates']:
                    for target in card['name_candidates']:
                        left, right = _name_key(name['text']), _name_key(target['text'])
                        basis = None
                        if left == right and len(left) >= 3:
                            basis = 'literal_name_equal_after_spacing_and_parentheses'
                        elif (len(left) >= 3 and right.startswith(left) and right != left
                                and _name_key(prefix).startswith(right)):
                            basis = 'name_and_variant_printed_in_row_prefix'
                        if basis:
                            found.append({'card': card['key'], 'summary_name': name, 'detail_name': target,
                                          'basis': basis})
            # Keep one strongest literal witness per target; ambiguity between
            # two differently described cards remains visible to the caller.
            by_card = {}
            for candidate in sorted(found, key=lambda x: (x['basis'], x['summary_name']['start'])):
                by_card.setdefault(candidate['card'], candidate)
            for candidate in by_card.values():
                links.append({**candidate, 'summary_row': _ref(text, row['start'], row['end']),
                    'summary_header': _ref(text, table['header_start'], table['header_end']),
                    'summary_unit': row['unit'], 'unique_literal_target': len(by_card) == 1,
                    'same_purchase_item_certified': False, 'property_agreement_checked': False,
                    'row_layout_certified': row['literal_column_alignment']})
            if not by_card:
                unmatched.append({'summary_row': _ref(text, row['start'], row['end']),
                    'reason': 'missing_name' if not row['name_candidates'] else 'no_literal_detail_name_link'})
    return {**inventory, 'links': links, 'unmatched_summary_rows': unmatched,
            'complete_purchase_identity_certified': False}
