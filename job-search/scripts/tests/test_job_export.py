import csv,tempfile,unittest
from pathlib import Path
from export_csv import export_jobs
class JobExportTests(unittest.TestCase):
 def test_multiline_body_and_user_notes_survive(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'jobs.csv';row=dict(source='liepin',job_key='123',job_name='分析',jd_description='SQL,Python\n第二行',detail_state='READY',job_url='https://www.liepin.com/job/123.shtml')
   export_jobs([row],p)
   with p.open(encoding='utf-8-sig',newline='') as f:r=list(csv.DictReader(f))
   self.assertEqual(r[0]['jd_description'],row['jd_description']);r[0]['user_notes']='已读'
   with p.open('w',encoding='utf-8-sig',newline='') as f:w=csv.DictWriter(f,fieldnames=r[0].keys());w.writeheader();w.writerows(r)
   export_jobs([dict(row,salary='15-25k')],p)
   with p.open(encoding='utf-8-sig',newline='') as f:r=list(csv.DictReader(f))
   self.assertEqual(r[0]['user_notes'],'已读');self.assertEqual(r[0]['salary'],'15-25k')
 def test_machine_formula_escaped(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'a.csv';export_jobs([dict(source='liepin',job_key='1',job_name='=1+1')],p)
   with p.open(encoding='utf-8-sig',newline='') as f:self.assertEqual(next(csv.DictReader(f))['job_name'],"'=1+1")
