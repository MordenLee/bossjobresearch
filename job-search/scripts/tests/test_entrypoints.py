"""Exercise actual CLI handoffs plus form execution against local fake browser objects."""
import contextlib
import copy
import csv
import io
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fixtures import sample
from workflow import read,write
from runtime import digest as content_digest,Runtime
from execution import checked_bundle
import form


class Entrypoints(unittest.TestCase):
    def setUp(self):
        self.base=(Path.cwd()/'work/skill-test-artifacts').resolve();self.base.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=self.base);self.root=Path(self.temp.name)
        self.policy,self.candidate,self.job,self.review,self.screens,self.bundle=sample()
        self.scripts=Path(__file__).resolve().parents[1]
        for name,value in [('policy',self.policy),('candidate',self.candidate),('jobs',[self.job]),
                           ('reviews',[self.review]),('screening',self.screens),('todos',self.bundle)]:write(self.root/(name+'.json'),value)
        self.field={'field_id':'main:answer','selector':'css:#answer','type':'text','label':'Answer','required':True,
                    'disabled':False,'readonly':False,'supported':True,'multiple':False,'value':'',
                    'context':'main','name':'answer','options':[],'maxlength':100,'valid':True}
        self.snap={'schema_version':2,'url':'https://www.zhipin.com/form/a','contexts':{
                   'main':{'url':'https://www.zhipin.com/form/a','title':'Apply','text':'A Analyst'}},
                   'fields':[self.field], 'buttons':[{'button_id':'main:submit','selector':'css:#submit',
                   'type':'submit','label':'Submit','context':'main','disabled':False}],'unsupported':[]}
        self.snap['fingerprint']=form.digest(self.snap)
        self.plan={'site':'boss','job_key':'boss-a','url':self.snap['url'],
                   'actions':[{'field_id':'main:answer','answer_ref':'/cv/projects/0'}],
                   'next_action':{'button_id':'main:submit','kind':'submit'}}
        write(self.root/'snapshot.json',self.snap);write(self.root/'plan.json',self.plan)
    def tearDown(self):
        assert self.root.resolve().parent==self.base
        self.temp.cleanup()
    def cli(self,script,*args):
        result=subprocess.run([sys.executable,str(self.scripts/script),*map(str,args)],capture_output=True,
                              text=True,encoding='utf-8',env=dict(os.environ,PYTHONIOENCODING='utf-8'),timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        return result
    def common(self):
        args=[]
        for flag,name in [('candidate','candidate'),('policy','policy'),('source-jobs','jobs'),
                          ('screening','screening'),('reviews','reviews')]:args += ['--'+flag,self.root/(name+'.json')]
        return args
    def prepared(self):
        result=form.prepare(self.snap,self.plan,self.candidate,self.bundle)
        result['input_binding']=self.bundle['input_binding']
        result['review_hash']=content_digest(self.review)
        return result
    def test_actual_policy_build_and_csv_cli(self):
        self.cli('policy.py','--policy',self.root/'policy.json','--candidate',self.root/'candidate.json',
                 '--jobs',self.root/'jobs.json','--out',self.root/'packet.json')
        self.assertEqual(read(self.root/'packet.json')['jobs'][0]['input_binding'],self.review['input_binding'])
        self.cli('workflow.py','build','--site','boss','--jobs',self.root/'jobs.json','--screening',self.root/'screening.json',
                 '--reviews',self.root/'reviews.json','--candidate',self.root/'candidate.json','--policy',self.root/'policy.json',
                 '--out-dir',self.root/'review')
        self.cli('export_csv.py','--todos',self.root/'review/todos.json','--out',self.root/'view.csv')
        with (self.root/'view.csv').open(encoding='utf-8-sig',newline='') as stream:rows=list(csv.DictReader(stream))
        self.assertEqual(rows[0]['action'],'apply')
    def test_form_prepare_cli_accepts_v3_and_binds_review(self):
        self.cli('form.py','prepare',*self.common(),'--snapshot',self.root/'snapshot.json','--plan',self.root/'plan.json',
                 '--todos',self.root/'todos.json','--out-dir',self.root/'form')
        prepared=read(self.root/'form/prepared.json')
        self.assertEqual(prepared['state'],'READY')
        self.assertEqual(prepared['review_hash'],content_digest(self.review))
    def test_authorization_change_does_not_invalidate_qualification(self):
        self.candidate['authorization']['actions'].append('fill');write(self.root/'candidate.json',self.candidate)
        args=types.SimpleNamespace(candidate=self.root/'candidate.json',policy=self.root/'policy.json',
              source_jobs=self.root/'jobs.json',screening=self.root/'screening.json',reviews=self.root/'reviews.json')
        checked_bundle(args,self.bundle)
    def test_form_structure_hash_ignores_filled_values_but_detects_changed_options(self):
        filled=copy.deepcopy(self.snap);filled['fields'][0]['value']='metrics analysis'
        self.assertEqual(form.digest(filled),self.snap['fingerprint'])
        filled['fields'][0]['options']=[{'value':'new','label':'New'}]
        self.assertNotEqual(form.digest(filled),self.snap['fingerprint'])
    def test_form_rejects_wrong_employer_before_prepare(self):
        self.snap['contexts']['main']['text']='A different form'
        with self.assertRaises(ValueError):self.prepared()
    def form_args(self,command='fill'):
        prepared=self.prepared();write(self.root/'prepared.json',prepared)
        return types.SimpleNamespace(command=command,prepared=self.root/'prepared.json',todos=self.root/'todos.json',
               candidate=self.root/'candidate.json',policy=self.root/'policy.json',source_jobs=self.root/'jobs.json',
               screening=self.root/'screening.json',reviews=self.root/'reviews.json',attach_port=12345,
               authorized=True,run_dir=str(self.root/'form'),profile_dir=None,ledger=str(self.root/'ledger.jsonl'))
    def test_form_fill_rechecks_answers_and_reads_back(self):
        self.candidate['authorization']['actions']=['fill'];write(self.root/'candidate.json',self.candidate)
        args=self.form_args();actual=[]
        element=types.SimpleNamespace(input=lambda value,clear:actual.append(value))
        page=types.SimpleNamespace(url=self.snap['url'],browser=types.SimpleNamespace(address='127.0.0.1:12345',
             states=types.SimpleNamespace(is_headless=False)),tab_id='task',get_frames=lambda:[],ele=lambda *a,**k:element,
             run_js=lambda *a:'normal')
        after=copy.deepcopy(self.snap);after['fields'][0]['value']='metrics analysis'
        with (patch.object(form,'connect',return_value=page),patch('collect.Guard.preflight'),patch('collect.Guard.check'),
              patch.object(form,'snapshot',side_effect=[self.snap,after]),contextlib.redirect_stdout(io.StringIO())):
            form.execute(args)
        self.assertEqual(actual,['metrics analysis'])
        self.assertEqual(read(self.root/'form/filled.json')['state'],'FILLED_VERIFIED')
    def test_tampered_prepared_value_rejected_before_browser(self):
        self.candidate['authorization']['actions']=['fill'];write(self.root/'candidate.json',self.candidate)
        args=self.form_args();prepared=read(args.prepared);prepared['actions'][0]['value']='invented answer';write(args.prepared,prepared)
        with patch.object(form,'connect',side_effect=AssertionError('must not connect')):
            with self.assertRaises(ValueError):form.execute(args)
    def test_submit_stays_uncertain_until_explicit_result_verification(self):
        self.candidate['authorization']['actions']=['submit'];write(self.root/'candidate.json',self.candidate)
        args=self.form_args('advance');snap=copy.deepcopy(self.snap);snap['fields'][0]['value']='metrics analysis'
        clicks=[];element=types.SimpleNamespace(click=lambda:clicks.append('clicked'))
        page=types.SimpleNamespace(url=self.snap['url'],browser=types.SimpleNamespace(address='127.0.0.1:12345',
             states=types.SimpleNamespace(is_headless=False)),tab_id='task',get_frames=lambda:[],ele=lambda *a,**k:element,
             run_js=lambda *a:'normal')
        with (patch.object(form,'connect',return_value=page),patch('collect.Guard.preflight'),patch('collect.Guard.check'),
              patch('collect.Guard.delay'),patch.object(form,'snapshot',return_value=snap),
              contextlib.redirect_stdout(io.StringIO()),self.assertRaises(SystemExit) as stopped):
            form.execute(args)
        self.assertEqual(stopped.exception.code,2);self.assertEqual(clicks,['clicked'])
        self.assertEqual(Runtime(self.root/'form').load()['pause_reason'],'RESULT_UNCERTAIN')


if __name__=='__main__':unittest.main()
