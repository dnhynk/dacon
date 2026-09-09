import copy,json,os,sys,unittest
from pathlib import Path
sys.path.insert(0,os.environ.get('PPS_REVIEW_SOURCE',str(Path(__file__).resolve().parents[1] / 'source')))
from pps.qualification import inventory,qualification_facts
from pps.model_fact_overlay import overlay,PRODUCT_FIELD

def record(clause,heading='입찰참가자격',code='1234'):
    text=('1. '+heading+'\n가. 아래의 자격을 갖춘 업체\n'
          '7) 기타자유업종(업종코드 : '+code+')을 등록한 업체\n8) '+clause+
          '\n2. 계약조건\n계약 후 정산한다.\n')
    return {'id':'synthetic-new-notice','docs':[{'doc_id':'D0','type':'공고문','text':text}],
            'meta':{'업무구분':'일반용역','적용계약법':'국가계약법'},
            'input_completeness':{'완전관측':True},'dropped_doc_counts':{}}

class Controls(unittest.TestCase):
    def test_numbered_registration_keeps_eligibility_role(self):
        for code in ('1234','5678'):
            r=record("우선조달계약 대상으로 '소기업' 또는 '소상공인'",code=code)
            q=qualification_facts(r,inventory(r))
            self.assertFalse(q['no_size'])
            self.assertTrue(any(e['section_role']=='eligibility' for e in q['inventory']))
    def test_submission_list_is_not_eligibility(self):
        r=record('중소기업확인서 1부를 제출합니다.',heading='제출서류')
        q=qualification_facts(r,inventory(r))
        self.assertFalse(q['active_size'])
    def test_optional_or_negative_clause_does_not_prove_required_size(self):
        for clause in ('소기업도 참여할 수 있다.','소기업 확인서는 제출하지 않아도 된다.'):
            q=qualification_facts(record(clause),inventory(record(clause)))
            self.assertFalse(q['active_size'])
    def fixture(self):
        r=record('일반 사업자로서 법정 등록 자격을 갖춘 업체')
        row={**{f'v{k}':'0' for k in range(1,25)},**{f'e{k}':'' for k in range(1,25)}}
        q=qualification_facts(r,inventory(r))
        p={'status':'unknown','uncertainty':[],'products':[],'estimate_won':150_000_000}
        rsp={'finish_reason':'stop','text':json.dumps({'facts':{PRODUCT_FIELD:'경쟁제품에 해당하지 않는 일반용역'}})}
        return r,row,rsp,{'product':p,'qualification':q}
    def test_categorical_fact_and_complete_absence_can_join(self):
        r,row,rsp,f=self.fixture(); result,log=overlay(r,row,rsp,f,range(10,19))
        self.assertEqual(result['v16'],'1'); self.assertFalse(log['source_scope_promoted'])
        r['id']='independently-renamed'; self.assertEqual(overlay(r,row,rsp,f,range(10,19))[0],result)
    def test_competition_conflict_uncertainty_and_missing_input_preserved(self):
        for change in ('competition','conflict','component','incomplete','exception'):
            r,row,rsp,f=self.fixture()
            if change=='competition': f['product']['status']='competition'
            if change=='conflict': f['product']['uncertainty']=['mixed_purchase']
            if change=='component': f['product']['products']=[{'listed':True,'condition':{'status':'met'}}]
            if change=='incomplete': f['qualification']['complete']=False
            if change=='exception': f['qualification']['exceptions']=[{'kind':'nonprofit_alternative'}]
            self.assertEqual(overlay(r,row,rsp,f,range(10,19))[0],row)
    def test_model_uncertainty_or_truncation_cannot_create_violation(self):
        for fact in ('경쟁제품 여부 불확실','경쟁제품에 해당함','경쟁제품에 해당하지 않을 가능성'):
            r,row,rsp,f=self.fixture(); rsp['text']=json.dumps({'facts':{PRODUCT_FIELD:fact}})
            self.assertEqual(overlay(r,row,rsp,f,range(10,19))[0],row)
        r,row,rsp,f=self.fixture(); rsp['finish_reason']='length'
        self.assertEqual(overlay(r,row,rsp,f,range(10,19))[0],row)
    def test_size_present_is_not_absence(self):
        r,row,rsp,f=self.fixture(); f['qualification']=qualification_facts(record('소기업확인서를 소지한 업체이어야 합니다.'),inventory(record('소기업확인서를 소지한 업체이어야 합니다.')))
        self.assertEqual(overlay(r,row,rsp,f,range(10,19))[0],row)

if __name__=='__main__': unittest.main()
