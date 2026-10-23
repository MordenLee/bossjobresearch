import unittest
from unittest.mock import patch
from urllib.parse import parse_qs,urlsplit
from adapters import extract_liepin as a
from collect import validate_collected_detail, UserActionRequired
from workflow import canonical_url
class LiepinTests(unittest.TestCase):
 def test_search_preserves_filters(self):
  q=parse_qs(urlsplit(a.search_url('https://www.liepin.com/zhaopin/?city=123&compScale=3&currentPage=9','AI 应用')).query)
  self.assertEqual(q,dict(city=['123'],compScale=['3'],currentPage=['0'],key=['AI 应用']))
 def test_search_rejects_wrong_host(self):
  with self.assertRaises(ValueError): a.search_url('https://example.com/zhaopin/','a')
 def test_tracking_dedup_and_kind_separation(self):
  self.assertEqual(canonical_url('https://www.liepin.com/job/123.shtml?ckId=a'),canonical_url('https://www.liepin.com/job/123.shtml?ckId=b'))
  self.assertNotEqual(canonical_url('https://www.liepin.com/a/123.shtml'),canonical_url('https://www.liepin.com/job/123.shtml'))
  self.assertIn('?id=2',canonical_url('https://example.com/job?id=2'))
 def row(self):
  e=dict(url='https://www.liepin.com/job/123.shtml?track=2',title='分析师',company='公司A',description='SQL分析',truncated=False)
  return dict(job_url='https://www.liepin.com/job/123.shtml',job_name='分析师',company='公司A',jd_description='SQL分析',jd_source='detail_page',collected_detail_evidence=e)
 def test_full_detail_ready(self):
  self.assertEqual(validate_collected_detail(self.row(),'liepin')['detail_state'],'READY')
 def test_identity_and_truncation(self):
  row=self.row();row['collected_detail_evidence']['company']='匿名公司'
  self.assertEqual(validate_collected_detail(row,'liepin')['detail_state'],'IDENTITY_MISMATCH')
  row=self.row();row['collected_detail_evidence']['truncated']=True
  self.assertEqual(validate_collected_detail(row,'liepin')['detail_state'],'INCOMPLETE')
 def test_error_not_empty_success(self):
  with patch.object(a,'open_detail_tab',side_effect=RuntimeError('network')):
   self.assertIn('network',a._fetch_detail(None,'url')['detail_read_error'])
 def test_challenge_propagates(self):
  with patch.object(a,'open_detail_tab',side_effect=UserActionRequired('login')):
   with self.assertRaises(UserActionRequired):a._fetch_detail(None,'url')
 def test_search_empty_is_explicit(self):
  class Page:
   search_home='https://www.liepin.com/zhaopin/'
   def get(self,*args,**kwargs):pass
   def read_js(self,*args):return {'count':0,'empty':True}
  p=Page();self.assertIs(a.search_jobs(p,'agent'),p)
 def test_blank_list_without_zero_message_is_error(self):
  class Page:
   def read_js(self,*args):return False
  with patch.object(a,'search_jobs',return_value=Page()),patch.object(a,'extract_job_cards',return_value=[]):
   with self.assertRaisesRegex(RuntimeError,'LIST_NOT_READY'):a.collect_jobs_by_keyword(None,'test')
 def test_pagination_waits_for_changed_cards(self):
  class Button:
   def click(self):pass
  class Page:
   def ele(self,*args,**kwargs):return Button()
  with patch.object(a,'has_next_page',return_value=True),patch.object(a,'extract_job_cards',side_effect=[[{'job_url':'old'}],[{'job_url':'old'}],[{'job_url':'new'}]]),patch.object(a.time,'sleep'):
   self.assertTrue(a.go_next_page(Page()))
if __name__=='__main__':unittest.main()
