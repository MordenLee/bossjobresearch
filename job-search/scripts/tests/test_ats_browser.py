import contextlib,io,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ats import Site,Page
from ats_browser import collect_browser
from collect import UserActionRequired
from workflow import write,read

class Browser:
 url='https://example.zhiye.com/social/jobs'
 text='职位列表'
 logged_in=True
 def run_js(self,*a):return self.text
 def ele(self,*a,**kw):return SimpleNamespace(states=SimpleNamespace(is_displayed=self.logged_in))

class BrowserATSTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  self.site=Site('beisen','example','Example','https://example.zhiye.com')
  self.browser=Browser();self.calls=[]
  write(self.root/'runtime-config.json',{'session':{'hosts':['example.zhiye.com'], 'signed_in_selectors':['css:.verified-user']}})
 def tearDown(self):self.tmp.cleanup()
 def run_collection(self,reader):
  with contextlib.redirect_stdout(io.StringIO()):
   return collect_browser(self.site,self.root,1234,reader,connector=lambda *a:self.browser)
 def reader(self,p,n,k):
  self.calls.append(n);return Page([{'Id':'1','JobAdName':'Analyst'}],1)
 def test_authenticated_can_collect(self):
  self.assertTrue(self.run_collection(self.reader)['complete']);self.assertEqual(self.calls,[1])
 def test_logged_out_never_reads_jobs(self):
  self.browser.text='请先登录';self.browser.logged_in=False
  with self.assertRaises(UserActionRequired):self.run_collection(self.reader)
  self.assertEqual(self.calls,[]);self.assertEqual(read(self.root/'task-state.json')['task_state'],'PAUSED')
 def test_hidden_marker_is_unknown(self):
  self.browser.logged_in=False
  with self.assertRaises(UserActionRequired):self.run_collection(self.reader)
  self.assertEqual(read(self.root/'task-state.json')['pause_reason'],'LOGIN_UNKNOWN')
 def test_expiry_preserves_previous_page(self):
  def reader(p,n,k):
   if n==2:self.browser.logged_in=False
   return Page([{'Id':str(n),'JobAdName':'Analyst'}],3)
  with self.assertRaises(UserActionRequired):self.run_collection(reader)
  self.assertEqual(len(read(self.root/'jobs.json')),1)
  self.assertEqual(read(self.root/'coverage.json')['status'],'INTERRUPTED')
 def test_login_alone_does_not_auto_resume(self):
  self.browser.logged_in=False
  with self.assertRaises(UserActionRequired):self.run_collection(self.reader)
  self.browser.logged_in=True
  with self.assertRaises(UserActionRequired):self.run_collection(self.reader)
  self.assertEqual(self.calls,[])
 def test_other_tenant_rejected(self):
  self.browser.url='https://other.zhiye.com/social/jobs'
  with self.assertRaises(UserActionRequired):self.run_collection(self.reader)
  self.assertEqual(self.calls,[])
 def test_unverified_selector_configuration_rejected(self):
  write(self.root/'runtime-config.json',{'session':{'hosts':['zhiye.com'],'signed_in_selectors':['css:.user']}})
  with self.assertRaises(ValueError):self.run_collection(self.reader)
if __name__=='__main__':unittest.main()
