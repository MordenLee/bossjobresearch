import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ats import Site, Moka, Beisen, collect, accept_detail, normalize, ATSError
from workflow import read

class ATSTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  self.site=Site('beisen','example-social','Example','https://example.zhiye.com')
 def tearDown(self):self.tmp.cleanup()
 def response(self, ids, total):
  return {'Code':200,'Count':total,'Data':[{'Id':x,'JobAdName':'Analyst'} for x in ids]}
 def test_partial_page_not_end(self):
  responses=iter([self.response(['1'],2),self.response(['2'],2)])
  result=collect(self.site,lambda *a:next(responses),self.root)
  self.assertTrue(result['complete']);self.assertEqual(result['pages'],2)
 def test_repeated_ids_not_complete(self):
  result=collect(self.site,lambda *a:self.response(['1'],2),self.root)
  self.assertEqual(result['status'],'PARTIAL');self.assertEqual(result['unique_fetched'],1)
 def test_failure_preserves_saved_jobs(self):
  calls=[]
  def fetch(*a):
   calls.append(1)
   if len(calls)>1:raise TimeoutError('timeout')
   return self.response(['1'],2)
  result=collect(self.site,fetch,self.root)
  self.assertEqual(len(read(self.root/'jobs.json')),1);self.assertFalse(result['complete'])
 def test_explicit_zero_vs_empty_error(self):
  result=collect(self.site,lambda *a:self.response([],0),self.root)
  self.assertTrue(result['complete'])
  result=collect(self.site,lambda *a:{'Code':500},self.root)
  self.assertEqual(result['status'],'FAILED')
 def test_unknown_total_not_complete(self):
  result=collect(self.site,lambda *a:self.response([],None),self.root)
  self.assertEqual(result['status'],'END_UNVERIFIED');self.assertFalse(result['complete'])
 def test_budget_not_complete(self):
  result=collect(self.site,lambda *a:self.response(['1'],10),self.root,max_pages=1)
  self.assertEqual(result['status'],'BUDGET_COMPLETED');self.assertFalse(result['complete'])
 def test_tenant_namespaces(self):
  other=Site('beisen','other','Example','https://other.zhiye.com')
  row={'Id':'1','JobAdName':'Analyst'}
  self.assertNotEqual(normalize(self.site,Beisen(),row)['job_key'],normalize(other,Beisen(),row)['job_key'])
 def test_moka_encoded_and_missing_list_rejected(self):
  for response in ({'data':'ciphertext'}, {'data':{}}):
   with self.assertRaises(ATSError):Moka().parse(response)
 def test_moka_request_and_parse(self):
  site=Site('moka','example','Example','https://app.mokahr.com',org_id='example',site_id=1)
  url,body=Moka().request(site,2,'')
  self.assertEqual(body['pagination']['pageIndex'],2)
  page=Moka().parse({'data':{'jobs':[{'id':'1','title':'Analyst'}],'jobStats':{'total':1}}})
  self.assertEqual(page.total,1)
  with self.assertRaises(ValueError):collect(site,lambda *a:None,self.root,keyword='AI')
 def test_full_detail_and_history_preserved(self):
  def detail(job):return dict(source_id=self.site.source_id,job_id=job['job_id'],
   url=self.site.host+'/job/detail?jobAdId=1',title='Analyst',company='Example',
   description='职责：SQL 分析。要求：Python。',truncated=False)
  collect(self.site,lambda *a:self.response(['1'],1),self.root,detail_reader=detail)
  first=read(self.root/'jobs.json')[0];self.assertEqual(first['detail_state'],'READY')
  collect(self.site,lambda *a:self.response(['1'],1),self.root)
  self.assertEqual(read(self.root/'jobs.json')[0]['jd_hash'],first['jd_hash'])
  wrong=dict(detail(first),job_id='2')
  with self.assertRaises(ATSError):accept_detail(self.site,first,wrong)
  wrong=dict(detail(first),company='Other')
  self.assertEqual(accept_detail(self.site,first,wrong)['detail_state'],'IDENTITY_MISMATCH')
 def test_interrupt_persists_and_propagates(self):
  def detail(job):raise KeyboardInterrupt()
  with self.assertRaises(KeyboardInterrupt):
   collect(self.site,lambda *a:self.response(['1'],1),self.root,detail_reader=detail)
  self.assertEqual(read(self.root/'coverage.json')['status'],'INTERRUPTED')
  self.assertEqual(len(read(self.root/'jobs.json')),1)
if __name__=='__main__':unittest.main()
