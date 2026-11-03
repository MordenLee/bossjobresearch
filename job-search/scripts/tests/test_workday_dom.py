import sys
import unittest
import json
import tempfile
from unittest.mock import patch
from urllib.parse import quote
from types import SimpleNamespace
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import workday_dom as wd
from workday_dom import identity,validate_config,accept_detail,total_count,valid_list,WorkdayGuard,pending_details,choose_location_option
from collect import UserActionRequired,StopSite

class WorkdayTests(unittest.TestCase):
    def test_location_selection_uses_rendered_exact_country_not_similar_city(self):
        options=[dict(type='checkbox',label='China (1)',id='observed-country'),dict(type='checkbox',label='China, Beijing (2)',id='observed-city')]
        self.assertEqual(choose_location_option(options,'China')['id'],'observed-country')
        self.assertEqual(choose_location_option(options,'China, Beijing')['id'],'observed-city')
    def test_missing_ambiguous_disabled_or_unidentified_location_stops(self):
        option=dict(type='checkbox',label='China (1)',id='from-dom')
        for options in [[],[option,dict(option,id='another')],[dict(option,disabled=True)],[dict(option,id='')]]:
            with self.assertRaises(ValueError):choose_location_option(options,'China')
    def test_location_configuration_cannot_confirm_an_application(self):
        base=dict(list_url='https://example.wd5.myworkdayjobs.com/External',tenant='example',company='Example',access_mode='public',public_access={'evidence':'read-only'})
        for rule in [dict(label='China'),dict(label='China',opener_selector='css:button',apply_text='Apply')]:
            with self.assertRaises(ValueError):validate_config(dict(base,location_filter=rule))
    def setUp(self):
        self.c=dict(list_url='https://example.wd5.myworkdayjobs.com/External',tenant='example',company='Example',access_mode='public',public_access={'evidence':'User requested public collection'})
        self.url='https://example.wd5.myworkdayjobs.com/en-US/External/job/Shanghai/Analyst_R123-1?q=SQL'
        self.job=dict(job_id='R123-1',job_name='Data Analyst')
        self.d=dict(url=self.url,title='Data Analyst',jd='Responsibilities: build dashboards. Requirements: SQL.',fields=[{'label':'locations','values':['Shanghai','Beijing']},{'label':'locations','values':['Wrong related job']}])
    def test_locale_and_query_do_not_change_identity(self):
        self.assertEqual(identity(self.url,self.c),'R123-1')
        self.assertEqual(identity(self.url.replace('/en-US','').replace('/job/Shanghai/','/details/'),self.c),'R123-1')
    def test_tenant_and_board_boundaries(self):
        for url in (self.url.replace('example.wd5','other.wd5'),self.url.replace('/External/','/Internal/'),self.url.replace('myworkdayjobs.com','myworkdayjobs.com.evil.test')):
            with self.assertRaises(ValueError):identity(url,self.c)
    def test_require_id(self):
        with self.assertRaises(ValueError):identity(self.url.replace('_R123-1',''),self.c)
    def test_public_scope_requires_evidence(self):
        self.c['public_access']={}
        with self.assertRaises(ValueError):validate_config(self.c)
    def test_missing_company_rejected(self):
        self.c['company']=''
        with self.assertRaises(ValueError):validate_config(self.c)
    def test_count_not_body_digits(self):
        self.assertEqual(total_count({'total_text':'2,000 JOBS FOUND'}),2000)
        self.assertIsNone(total_count({'total_text':'Jobs 2000'}))
    def test_observed_chinese_count(self):
        self.assertEqual(total_count({'total_text':'找到 1 个职务'}),1)
        self.assertEqual(total_count({'total_text':'找到 0 个职务'}),0)
    def test_only_explicit_zero_is_empty(self):
        d=dict(url=self.c['list_url']+'?q=SQL',query='SQL',rows=[],total_text='0 JOBS FOUND')
        self.assertTrue(valid_list(d,self.c,'SQL'));d['total_text']='';self.assertFalse(valid_list(d,self.c,'SQL'))
    def test_stale_query_rejected(self):
        d=dict(url=self.c['list_url']+'?q=SQL',query='Agent',rows=[{}],total_text='1 JOB FOUND')
        self.assertFalse(valid_list(d,self.c,'Agent'))
    def test_next_page_cannot_silently_lose_verified_location_filter(self):
        config=dict(self.c,_selected_location_id='actual-dom-id')
        data=dict(url=self.c['list_url']+'?q=SQL&locationHierarchy1=actual-dom-id',query='SQL',rows=[{'url':'next-job'}],total_text='2 JOBS FOUND')
        self.assertTrue(valid_list(data,config,'SQL'))
        for url in [self.c['list_url']+'?q=SQL',self.c['list_url']+'?q=SQL&locationHierarchy1=other']:
            self.assertFalse(valid_list(dict(data,url=url),config,'SQL'))
    def test_first_detail_fields_win_over_related_jobs(self):
        d=accept_detail(self.job,self.d,self.c)
        self.assertEqual(d['location'],'Shanghai\nBeijing');self.assertEqual(d['published_at'],'')
    def test_detail_must_match_identity_and_title(self):
        for field,val in [('url',self.url.replace('R123-1','R222')),('title','Engineer'),('jd','loading...')]:
            with self.assertRaises(ValueError):accept_detail(self.job,dict(self.d,**{field:val}),self.c)
    def test_normal_signin_navigation_not_login_wall(self):
        class Page:
            url='https://example.wd5.myworkdayjobs.com/External'
            def run_js(self,s):return {'text':'Sign In', 'wall':False} if 'wall:' in s else 'Sign In'
        g=object.__new__(WorkdayGuard)
        self.assertEqual(g.blocking_reason(Page()),'')
    def test_password_wall_stops(self):
        class Page:
            url='https://example.wd5.myworkdayjobs.com/External'
            def run_js(self,s):return {'text':'Sign In', 'wall':True} if 'wall:' in s else 'Sign In'
        g=object.__new__(WorkdayGuard)
        self.assertTrue(g.blocking_reason(Page()));self.assertEqual(g.detected_pause_reason,'LOGIN_REQUIRED')
    def test_resume_budget_reaches_pending_jobs_after_completed_prefix(self):
        jobs=[dict(job_key='old',jd_description='complete'),dict(job_key='new1'),dict(job_key='new2')]
        self.assertEqual([j['job_key'] for j in pending_details(jobs,1)],['new1'])
        self.assertEqual([j['job_key'] for j in pending_details(jobs,2)],['new1','new2'])
        self.assertEqual([j['job_key'] for j in pending_details(jobs,1,refresh=True)],['old'])
    def test_detail_guard_pause_preserves_reason_and_never_visits_next_job(self):
        self._guard_stop(UserActionRequired('human verification'), 'CHALLENGE')
    def test_uninspectable_detail_saves_read_error_checkpoint(self):
        self._guard_stop(StopSite('verification state unreadable'), None)
    def _guard_stop(self, exception, existing_reason):
        config=self.c
        class Runtime:
            def __init__(self):self.state={'task_state':'RUNNING','pause_reason':None}
            def load(self):return self.state
            def start(self,*args):pass
            def commit(self,**kwargs):self.state.update(kwargs)
            def pause(self,reason,**kwargs):self.state.update(task_state='PAUSED',pause_reason=reason)
        class Guard:
            def __init__(self,*args,**kwargs):self.runtime=Runtime();guards.append(self)
            def ensure_can_resume(self):pass
            def check(self,*args,**kwargs):pass
        class Element:
            def input(self,value,**kwargs):page.keyword=value
            def click(self):page.url=config['list_url']+'?q='+quote(page.keyword)
        class Page:
            url=config['list_url'];keyword=''
        page=Page();guards=[];visited=[]
        page.set=SimpleNamespace(load_mode=SimpleNamespace(normal=lambda:setattr(page,'normal_load',True)))
        class Proxy:
            def __init__(self,page,guard):self.guard=guard
            def get(self,url,**kwargs):
                if not getattr(page,'normal_load',False):raise AssertionError('Workday navigation must not use eager stopLoading')
                page.url=url;visited.append(url)
            def ele(self,*args,**kwargs):return Element()
            def read_js(self,script):
                if script==wd.LIST_JS:
                    return dict(url=page.url,query=page.keyword,total_text='2 JOBS FOUND',rows=[dict(url=config['list_url']+'/job/X/Analyst_R'+str(i),title='Data Analyst',summary='China, Shenzhen') for i in [1,2]])
                if existing_reason:self.guard.runtime.pause(existing_reason)
                raise exception
        def stable(reader,valid):
            value=reader()
            if not valid(value):raise ValueError('Invalid simulated list')
            return value
        with tempfile.TemporaryDirectory() as temporary:
            run=Path(temporary)/'run';run.mkdir();out=Path(temporary)/'out'
            (run/'workday-dom.json').write_text(json.dumps(config),encoding='utf8')
            (run/'browser.json').write_text(json.dumps({'address':'127.0.0.1:1234'}),encoding='utf8')
            with patch.object(wd,'WorkdayGuard',Guard),patch.object(wd,'Proxy',Proxy),patch.object(wd,'connect_owned',return_value=page),patch.object(wd,'wait_stable',stable),patch.object(wd,'export'):
                with self.assertRaises(type(exception)):wd.collect(run,out,'SQL',1,2)
            coverage=json.loads((out/'coverage.json').read_text(encoding='utf8'))
            jobs=json.loads((out/'jobs.json').read_text(encoding='utf8'))
            self.assertEqual(coverage['status'],'PAUSED_SITE')
            self.assertEqual(coverage['pause_reason'],existing_reason or 'READ_ERROR')
            self.assertEqual(len(jobs),2)
            self.assertFalse(any(j.get('jd_description') for j in jobs))
            self.assertEqual(len(visited),2) # initial list and first detail only
            self.assertTrue(visited[-1].endswith('_R1'))

if __name__=='__main__':unittest.main()
