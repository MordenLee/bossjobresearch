import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from beisen_dom import identity,parse_fields,accept_detail,validate_config
class BeisenDOMTests(unittest.TestCase):
 def setUp(self):
  self.host='https://example.zhiye.com'
  self.job={'job_url':self.host+'/zpdetail/123?d=-1','job_name':'数据分析师(J100)'}
  self.detail={'url':self.host+'/zpdetail/123?d=-1','title':'数据分析师(J100) (未申请)','jd':'工作职责：使用 SQL 分析。任职资格：本科。','info':['招聘类别： 社会招聘 工作性质： 全职 薪资范围： 面议 招聘人数： 若干','发布时间： 2026-10-25 截止时间： ','工作地点： 北京市,上海市'],'sections':[{'label':'工作职责：','text':'使用 SQL 分析。'},{'label':'任职资格：','text':'本科。'}]}
 def test_identity_ignores_tracking(self):self.assertEqual(identity(self.job['job_url'],self.host),'123')
 def test_cross_tenant_rejected(self):
  with self.assertRaises(ValueError):identity('https://other.zhiye.com/zpdetail/123',self.host)
 def test_wrong_id_rejected(self):
  self.detail['url']=self.host+'/zpdetail/456'
  with self.assertRaises(ValueError):accept_detail(self.job,self.detail,self.host)
 def test_wrong_title_rejected(self):
  self.detail['title']='数据工程师(J100)'
  with self.assertRaises(ValueError):accept_detail(self.job,self.detail,self.host)
 def test_placeholder_rejected(self):
  self.detail['jd']='暂无'
  with self.assertRaises(ValueError):accept_detail(self.job,self.detail,self.host)
 def test_labels_not_positional(self):
  fields=parse_fields(self.detail['info']);self.assertEqual(fields['薪资范围'],'面议');self.assertEqual(fields['截止时间'],'');self.assertEqual(fields['工作地点'],'北京市,上海市')
 def test_full_sections(self):
  d=accept_detail(self.job,self.detail,self.host);self.assertEqual(d['qualifications'],'本科。');self.assertEqual(d['published_at'],'2026-10-25')
 def test_public_authorization_required(self):
  c={'host':self.host,'list_url':self.host+'/social','tenant':'example','company':'Example'}
  with self.assertRaises(ValueError):validate_config(c)
  c['public_access']={'authorized':True,'evidence':'用户授权公开采集'};self.assertEqual(validate_config(c),c)
