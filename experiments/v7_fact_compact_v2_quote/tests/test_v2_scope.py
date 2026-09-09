import copy,os,sys,unittest
from pathlib import Path
sys.path.insert(0,os.environ.get('PPS_REVIEW_SOURCE',str(Path(__file__).resolve().parents[1] / 'source')))
from pps.rules import apply_rules

def record(price=340_000_000,required=500_000_000,law='지방계약법',work='일반용역',authority='기초자치단체'):
    return {'id':'new-synthetic','meta':{'입찰추정가격':price,'배정예산금액':price,'적용계약법':law,'업무구분':work,'소관구분':authority,'계약방법':'제한경쟁'},
            'docs':[{'doc_id':'D0','type':'공고문','text':f'추정가격: {price}원\n사업예산: {price}원\n1. 입찰참가자격\n가. 최근 5년간 단일 용역 {required}원 이상의 수행 실적을 보유한 업체\n2. 계약조건\n본 용역을 수행한다.'}],
            'input_completeness':{'완전관측':True},'dropped_doc_counts':{}}
def row(): return {**{f'v{k}':1 if k==2 else 0 for k in range(1,25)},**{f'e{k}':'' for k in range(1,25)}}

class Controls(unittest.TestCase):
    def test_above_notice_does_not_clear_excessive_experience(self):
        out,checks=apply_rules(record(),row())
        self.assertEqual(int(out['v2']),0); self.assertEqual(int(out['v3']),1)
        self.assertTrue(any(c['source']=='supplied_performance_applicability' for c in checks))
    def test_below_notice_remains_violation(self):
        out,_=apply_rules(record(price=120_000_000,required=50_000_000),row())
        self.assertEqual(int(out['v2']),1)
    def test_conflicting_price_not_cleared(self):
        r=record(); r['meta']['입찰추정가격']=100_000_000
        self.assertEqual(int(apply_rules(r,row())[0]['v2']),1)
    def test_goods_and_unresolved_authority_not_cleared(self):
        for r in (record(work='물품(내자)'),record(law='국가계약법',authority='공공기관')):
            self.assertEqual(int(apply_rules(r,row())[0]['v2']),1)
    def test_resolved_national_authority_and_id_independence(self):
        r=record(law='국가계약법',authority='국가기관'); a=apply_rules(r,row())[0]
        r['id']='different-name'; self.assertEqual(apply_rules(r,row())[0],a)
        self.assertEqual(int(a['v2']),0)
    def test_scoring_only_is_not_a_proven_whole_item_negative(self):
        r=record(); r['docs'][0]['text']=r['docs'][0]['text'].replace('1. 입찰참가자격','1. 정량평가 배점').replace('실적을 보유한 업체','실적은 5점')
        self.assertEqual(int(apply_rules(r,row())[0]['v2']),1)

if __name__=='__main__': unittest.main()
