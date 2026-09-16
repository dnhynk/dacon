"""Observe counted alphanumeric item syntax without inferring product identity."""
import re


_ENTRY = re.compile(r'(?:^|(?<=[,;]))[ \t]*(?P<name>[^,;\r\n]{2,110}?)'
                    r'[ \t*×]+(?P<quantity>\d+(?:,\d{3})*(?:\.\d+)?[ \t]*(?:개|대|점|세트|팩|쌍|식))'
                    r'(?=[ \t]*(?:[,;]|$))')
_PREFIX = re.compile(r'^[ \t]*(?:(?:[-○●□■•ㆍ]|\d{1,3}(?:-\d{1,3})*[.)]?)[ \t]+)?')
_ALPHA = re.compile(r'(?<![A-Za-z])[A-Za-z][A-Za-z0-9_-]{1,}(?![A-Za-z])')
_NONITEM = re.compile(r'https?://|www\.|@|사업자등록번호|계좌번호|전화번호|e-?mail', re.I)


def candidates(text):
    found, offset = [], 0
    for physical in text.splitlines(keepends=True):
        line = physical.rstrip('\r\n')
        for match in _ENTRY.finditer(line):
            name = match['name']; prefix = _PREFIX.match(name).end(); name = name[prefix:].strip()
            if not _ALPHA.search(name) or _NONITEM.search(name):
                continue
            lo = offset + match.start('name') + prefix
            while lo < offset + match.end('name') and text[lo].isspace():
                lo += 1
            hi = offset + match.end('name')
            while hi > lo and text[hi-1].isspace():
                hi -= 1
            end = offset + match.end()
            found.append({'source': {'start': lo, 'end': end, 'text': text[lo:end]},
                'value_source': {'start': lo, 'end': hi, 'text': text[lo:hi]},
                'quantity_source': {'start': offset+match.start('quantity'), 'end': offset+match.end('quantity'),
                                    'text': match['quantity']},
                'syntax': 'alphanumeric_name_and_printed_item_count',
                'unique_name_certified': False, 'purchase_role_certified': False})
        offset += len(physical)
    return found
