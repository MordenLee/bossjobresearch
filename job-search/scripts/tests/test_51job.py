import sys,unittest,tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from adapters.extract_51job import search_url,job_id_from_url
from collect import validate_collected_detail,Guard,StopSite,Proxy
from workflow import canonical_url
class Job51Tests(unittest.TestCase):
 def test_search_preserves_filters(self):
  url=search_url('https://we.51job.com/pc/search?jobArea=090200&salary=02&pageNum=4','AI')
  self.assertIn('jobArea=090200',url);self.assertIn('salary=02',url);self.assertNotIn('pageNum',url)
 def test_wrong_host_rejected(self):
  with self.assertRaises(ValueError):search_url('https://example.com/pc/search','AI')
 def test_job_identity(self):
  self.assertEqual(job_id_from_url('https://jobs.51job.com/chengdu/123.html?s=x'),'123')
  self.assertEqual(job_id_from_url('https://jobs.51job.com/all/co123.html'),'')
  self.assertEqual(canonical_url('https://jobs.51job.com/chengdu/123.html?s=x'),'https://jobs.51job.com/chengdu/123.html')
 def test_detail_contract(self):
  d=dict(url='https://jobs.51job.com/chengdu/123.html',title='分析',company='公司',description='工作职责 SQL 分析',truncated=False)
  row=dict(job_name='分析',company='公司',job_url=d['url'],jd_source='detail_page',jd_description=d['description'],collected_detail_evidence=d)
  self.assertEqual(validate_collected_detail(row,'51job')['detail_state'],'READY')
  row['company']='另一公司'
  self.assertEqual(validate_collected_detail(row,'51job')['detail_state'],'IDENTITY_MISMATCH')
 def test_context_loss_retry_is_read_only_and_bounded(self):
  Error=type('ContextLostError',(Exception,),{})
  class Page:
   url='https://we.51job.com/pc/search'
   count=0
   def run_js(self,*args):
    self.count+=1
    if self.count<2:raise Error()
    return '正常页面'
  with tempfile.TemporaryDirectory() as d,patch('collect.time.sleep'):
   g=Guard(Path(d),'51job');p=Page();self.assertEqual(g.blocking_reason(p),'');self.assertEqual(p.count,2)
 def test_context_loss_stops_after_three_reads(self):
  Error=type('ContextLostError',(Exception,),{})
  class Page:
   url='https://we.51job.com/pc/search'
   count=0
   def run_js(self,*args):
    self.count+=1
    raise Error()
  with tempfile.TemporaryDirectory() as d,patch('collect.time.sleep'):
   g=Guard(Path(d),'51job');p=Page()
   with self.assertRaises(StopSite):g.blocking_reason(p)
   self.assertEqual(p.count,3)
 def test_missing_element_survives_proxy(self):
  class NoneElement:
   def __bool__(self):return False
   def __getattr__(self,key):raise RuntimeError('missing')
  class Page:url='https://we.51job.com/pc/search'
  el=NoneElement();self.assertIs(Proxy(Page(),None).wrap(el),el)
if __name__=='__main__':unittest.main()


