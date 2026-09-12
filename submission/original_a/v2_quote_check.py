"""Item2's supplied local small-quotation exception, with actual-route evidence."""
import re
from .performance import compact

def check(record,performance):
    meta=record.get('meta',{})
    price=performance['prices']['estimated_price']
    if (meta.get('적용계약법')!='지방계약법' or meta.get('계약방법')!='수의계약'
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
            return {'item':2,'value':0,'evidence':'','source':'supplied_v2_local_small_quote_exception',
                    'reason':'local_actual_small_quote_proved_by_title_and_metadata',
                    'source_title':heading,'estimated_price':price['value_won']}
    return None
