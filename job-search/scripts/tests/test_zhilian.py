import unittest
from unittest.mock import patch
from urllib.parse import urlsplit,parse_qs
from adapters import extract_zhilian as a
from collect import validate_collected_detail,UserActionRequired
from workflow import canonical_url
class Tests(unittest.TestCase):
 def row(self):return dict(card_index=2,job_name='分析师',company='A',salary='10-20K',location='成都')
 def panel(self,**kwargs):
  d=dict(url='https://www.zhaopin.com/jobdetail/ABC123.htm?x=1',title='分析师',company='A',description='SQL分析',selected_index=2,truncated=False);d.update(kwargs);return d
 def test_filters_preserved(self):
  q=parse_qs(urlsplit(a.search_url('https://www.zhaopin.com/jobs/?jl=801&salary=9&page=2','Agent')).query)
  self.assertEqual(q,dict(jl=['801'],salary=['9'],kw=['Agent']))
 def test_wrong_search_host_rejected(self):
  with self.assertRaises(ValueError):a.search_url('https://example.com/jobs/','x')
 def test_identity_uses_selection_title_company_and_host(self):
  self.assertTrue(a.panel_matches(self.row(),self.panel()))
  for k,v in [('selected_index',1),('title','Other'),('company','B'),('url','https://evil.test/jobdetail/a.htm'),('description','')]:
   self.assertFalse(a.panel_matches(self.row(),self.panel(**{k:v})))
 def test_tracking_dedup(self):
  self.assertEqual(canonical_url(self.panel()['url']),'https://www.zhaopin.com/jobdetail/ABC123.htm')
  self.assertNotEqual(canonical_url(self.panel()['url']),canonical_url('https://www.zhaopin.com/jobdetail/XYZ.htm'))
 def test_common_ready_and_truncation(self):
  row=dict(self.row(),job_url=self.panel()['url'],jd_source='search_panel',jd_description='SQL分析',collected_detail_evidence=self.panel())
  self.assertEqual(validate_collected_detail(row,'zhilian')['detail_state'],'READY')
  row['collected_detail_evidence']['truncated']=True
  self.assertEqual(validate_collected_detail(row,'zhilian')['detail_state'],'INCOMPLETE')
 def test_current_panel_needs_no_click(self):
  class P:
   def eles(self,*a,**k):raise AssertionError('no click')
  with patch.object(a,'read_panel',return_value=self.panel()),patch.object(a.time,'sleep'):
   self.assertEqual(a.extract_search_detail(P(),self.row())['jd_description'],'SQL分析')
 def test_guard_pause_not_swallowed(self):
  with patch.object(a,'read_panel',side_effect=UserActionRequired('verify')):
   with self.assertRaises(UserActionRequired):a.extract_search_detail(None,self.row())

 def test_switched_panel_cannot_reuse_old_link_or_body(self):
  row=self.row()
  class Card:
   def click(self):pass
  class Page:
   def eles(self,*args,**kwargs):return [Card(),Card(),Card()]
  old=self.panel(selected_index=1)
  for updated in [self.panel(description='new'),self.panel(url='https://www.zhaopin.com/jobdetail/NEW.htm')]:
   with patch.object(a,'read_panel',side_effect=[old,updated]),patch.object(a,'get_card_records',return_value=[{}, {},row]),patch.object(a.time,'monotonic',side_effect=[0,0,2]),patch.object(a.time,'sleep'):
    result=a.extract_search_detail(Page(),row,timeout=1)
    self.assertEqual(result['jd_description'],'');self.assertIn('detail_read_error',result)
