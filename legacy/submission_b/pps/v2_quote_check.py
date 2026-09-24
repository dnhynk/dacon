"""Supplied local small-quotation exceptions, with actual-route evidence."""
from .legal_context import applicable_law
import re
from .performance import compact

def check_item(record, performance, item):
    if item not in {2, 8}:
        raise ValueError('Local quotation applicability is implemented only for items2 and8')
    meta=record.get('meta',{})
    price=performance['prices']['estimated_price']
    if (applicable_law(record)!='지방계약법' or meta.get('계약방법')!='수의계약'
            or meta.get('업무구분')!='일반용역' or price['status']!='known'
            or price['value_won'] is None or not 0<price['value_won']<=100_000_000):
        return None
    # A legal reference to quotations is not evidence of the actual route.
    for doc in record['docs']:
        if doc['type']!='공고문': continue
        heading=''.join(doc['text'].splitlines()[:4])
        text=compact(heading)
        if re.search(r'참고|경우|가능|예시',text): continue
        if re.search(r'(?:수의계약|소액수의).{0,30}견적(?:서)?제출.{0,30}(?:안내|공고)',text):
            reason = ('local_actual_small_quote_proved_by_title_and_metadata' if item == 2
                      else 'local_actual_small_quote_allows_region_and_experience_combination')
            return {'item':item,'value':0,'evidence':'','source':'supplied_local_small_quote_exception',
                    'reason':reason,
                    'source_title':heading,'estimated_price':price['value_won']}
    return None


def check(record, performance):
    """Backward-compatible item2 entry point."""
    return check_item(record, performance, 2)
