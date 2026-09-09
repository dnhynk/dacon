import os,sys,unittest
from pathlib import Path
sys.path.insert(0,os.environ.get('PPS_REVIEW_SOURCE',str(Path(__file__).resolve().parents[1] / 'source')))
from pps.performance import performance_facts
from pps.v2_quote_check import check

def record(title='통근 지원 용역 소액수의 견적제출 안내공고',price=80_000_000,law='지방계약법',method='수의계약'):
    return {'meta':{'적용계약법':law,'업무구분':'일반용역','계약방법':method,'입찰추정가격':price,'배정예산금액':price},'docs':[{'doc_id':'D0','type':'공고문','text':title+f'\n추정가격: {price}원\n1. 참가자격\n최근 3년 2억원 이상 수행 실적이 있는 업체'}]}

class Controls(unittest.TestCase):
    def test_actual_route_confirmed(self):
        r=record(); self.assertEqual(check(r,performance_facts(r))['value'],0)
    def test_only_local_small_actual_quote(self):
        for r in (record(law='국가계약법'),record(price=180_000_000),record(method='제한경쟁')):
            self.assertIsNone(check(r,performance_facts(r)))
    def test_meta_only_or_hypothetical_not_enough(self):
        for title in ('일반 용역 제한경쟁 공고','참고: 소액수의 견적제출 안내공고가 가능한 경우'):
            r=record(title=title); self.assertIsNone(check(r,performance_facts(r)))
    def test_price_conflict_not_erased(self):
        r=record(); r['meta']['입찰추정가격']=10_000_000
        self.assertIsNone(check(r,performance_facts(r)))

if __name__=='__main__': unittest.main()
