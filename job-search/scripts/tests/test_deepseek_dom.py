import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deepseek_dom import identity,normalized
class DeepSeekIdentityTests(unittest.TestCase):
 def test_correct_tenant(self):
  self.assertEqual(identity('https://app.mokahr.com/social-recruitment/high-flyer/140576#/job/a-b'),'a-b')
 def test_other_tenant(self):
  with self.assertRaises(ValueError):identity('https://app.mokahr.com/apply/moonshot/148506#/job/a-b')
 def test_other_origin(self):
  with self.assertRaises(ValueError):identity('https://evil.example/social-recruitment/high-flyer/140576#/job/a-b')
 def test_list_route_not_job(self):
  with self.assertRaises(ValueError):identity('https://app.mokahr.com/social-recruitment/high-flyer/140576#/jobs/')
 def test_whitespace_title(self):
  self.assertEqual(normalized('Agent  弹性计算研发工程师'),normalized('Agent 弹性计算研发工程师'))
