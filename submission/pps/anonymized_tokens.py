"""Read anonymous token structure without recovering a hidden name or address."""
from __future__ import annotations

from dataclasses import dataclass
import re


_BRACKET = re.compile(r'\[(?:지역:|등록지역:|수요기관\()[^\[\]\r\n]*(?:\]|(?=$|[\r\n]))')


@dataclass(frozen=True)
class AnonymousToken:
    kind: str
    value: str
    attributes: tuple
    errors: tuple
    start: int
    end: int
    text: str
    document_key: tuple | None = None

    def attribute(self, key):
        if self.errors:
            return None
        return next((value for name, value in self.attributes if name == key), None)

    @property
    def region_key(self):
        """Identity is usable only inside an explicitly identified document."""
        if self.document_key is None or self.errors or self.kind == 'registered_region':
            return None
        symbol = self.value if self.kind == 'region' else self.attribute('지역')
        if not symbol or not re.fullmatch(r'r\d+', symbol):
            return None
        return (*self.document_key, symbol)


def anonymous_tokens(text, *, document_key=None):
    if document_key is not None and not (
            type(document_key) is tuple and len(document_key) == 2
            and type(document_key[0]) is str and bool(document_key[0])
            and type(document_key[1]) is int and document_key[1] >= 0):
        raise ValueError('Document key must contain record ID and document index')
    for match in _BRACKET.finditer(text):
        closed = match[0].endswith(']')
        head, *parts = (match[0][1:-1] if closed else match[0][1:]).split('|')
        kind = 'region' if head.startswith('지역:') else 'registered_region' if head.startswith('등록지역:') else 'institution'
        errors = [] if closed else ['unclosed_token']
        if kind in ('region', 'registered_region'):
            value = head.partition(':')[2].strip()
            if not value or re.search(r'\s|[()=]', value):
                errors.append('invalid_region_symbol')
        else:
            name = re.fullmatch(r'수요기관\(([^()]+)\)', head)
            value = name[1].strip() if name else ''
            if not value:
                errors.append('invalid_institution_type')
        attributes = []
        seen = set()
        for part in parts:
            key, separator, value_part = part.partition('=')
            key, value_part = key.strip(), value_part.strip()
            if not separator or not key or not value_part:
                errors.append('invalid_attribute')
            if key in seen:
                errors.append('duplicate_attribute:' + key)
            seen.add(key)
            attributes.append((key, value_part))
        yield AnonymousToken(kind, value, tuple(attributes), tuple(errors),
                             match.start(), match.end(), match[0], document_key)


def basic_notice_authority(record):
    """Only consistent, supplied notice institution types allow the higher band."""
    authorities = [token for doc in record.get('docs', []) if doc['type'] == '공고문'
                   for token in anonymous_tokens(doc['text']) if token.kind == 'institution']
    return (bool(authorities) and all(not token.errors for token in authorities)
            and {token.value for token in authorities} == {'기초자치단체'})


def registered_region_tokens(record):
    """Return a complete, well-formed structured metadata region list.

    ``r1``/``r2`` are deliberately kept as opaque record-local symbols.  This
    helper only exposes attributes that the input contract already supplies;
    it never joins them to a name in a document or another record.  A mixed,
    malformed or partly free-text list remains unresolved instead of silently
    dropping the part we could not parse.
    """
    raw = record.get('meta', {}).get('제한지역코드목록')
    if not isinstance(raw, str) or not raw.strip():
        return ()
    tokens = list(anonymous_tokens(raw))
    if (not tokens or any(token.kind != 'registered_region' or token.errors
                          for token in tokens)):
        return ()
    remainder = []
    cursor = 0
    for token in tokens:
        remainder.append(raw[cursor:token.start])
        cursor = token.end
    remainder.append(raw[cursor:])
    if re.sub(r'[\s,;]+', '', ''.join(remainder)):
        return ()
    return tuple(tokens)


def province_projection(text, *, allowed_provinces):
    """Project only a region token's explicit province; never join local IDs."""
    parts = []
    cursor = 0
    for token in anonymous_tokens(text):
        parts.append(text[cursor:token.start])
        province = token.attribute('광역') if token.kind in ('region', 'registered_region') else None
        if province not in allowed_provinces:
            province = None
        parts.append(' ' + (province or '') + ' ')
        cursor = token.end
    parts.append(text[cursor:])
    return ''.join(parts)
