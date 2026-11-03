import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from moka_dom import wait_stable,usable
class WaitingTests(unittest.TestCase):
 def test_transient_navigation_context_loss_is_retried_within_wait_budget(self):
  class ContextLostError(Exception):pass
  t=[0];calls=[0]
  def reader():
   calls[0]+=1
   if calls[0]==1:raise ContextLostError('navigation')
   return '完整正文'
  def sleep(s):t[0]+=s
  self.assertEqual(wait_stable(reader,usable,timeout=20,interval=2,stable_seconds=4,clock=lambda:t[0],sleep=sleep),'完整正文')
 def test_non_navigation_error_is_not_swallowed(self):
  def reader():raise ValueError('identity mismatch')
  with self.assertRaises(ValueError):wait_stable(reader,usable)
 def test_placeholder_then_body(self):
  t=[0]; values=iter(['暂无','暂无','真实职责','真实职责','真实职责','真实职责'])
  def sleep(s):t[0]+=s
  self.assertEqual(wait_stable(lambda:next(values),usable,timeout=20,interval=2,stable_seconds=4,clock=lambda:t[0],sleep=sleep),'真实职责')
 def test_placeholder_timeout_is_not_missing(self):
  t=[0]
  def sleep(s):t[0]+=s
  with self.assertRaises(TimeoutError):wait_stable(lambda:'暂无',usable,timeout=6,clock=lambda:t[0],sleep=sleep)
 def test_stale_identity_not_accepted(self):
  t=[0]
  def sleep(s):t[0]+=s
  with self.assertRaises(TimeoutError):wait_stable(lambda:{'id':'old','jd':'正文'},lambda d:d['id']=='new',timeout=6,clock=lambda:t[0],sleep=sleep)
