"""Offline regressions: no real browser, account, or external communication."""
import contextlib
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import collect
import workflow
import contact
from fixtures import sample


class Page:
    def __init__(self, text='ok', url='https://www.zhipin.com/'):
        self.text, self.url, self.closed, self.visits = text, url, False, []
        self.browser = types.SimpleNamespace(address='127.0.0.1:12345')
        self.tab_id = 'task-tab'
    def run_js(self, *args):
        return self.text
    def get(self, url):
        self.visits.append(url)
        self.url = url
    def ele(self, *args, **kwargs):
        return types.SimpleNamespace(states=types.SimpleNamespace(is_displayed=True))
    def quit(self):
        self.closed = True


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.test_base = (Path.cwd()/'work/skill-test-artifacts').resolve()
        self.test_base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=self.test_base)
        self.root = Path(self.temp.name)
    def tearDown(self):
        assert self.root.resolve().parent == self.test_base
        self.temp.cleanup()
    def args(self, **changes):
        keywords = self.root/'keywords.json'
        keywords.write_text(json.dumps({'keywords':['analysis']}), encoding='utf-8')
        result = dict(run_dir=str(self.root/'run'), keywords=str(keywords), sources=['boss'], resume=False,
                      target=5, pages=1, delay_min=0, delay_max=0, attach_port=None,
                      profile_dir=None, reuse_login=True, per_keyword=2, min_salary=None,
                      salary_range='lower', close_browser=False, batch_size=0, batch_rest=0)
        result.update(changes)
        return types.SimpleNamespace(**result)
    def run_collection(self, page, collector, **changes):
        adapter = types.SimpleNamespace(create_browser=lambda **kw: page, collect_jobs_by_keyword=collector)
        with patch.object(collect.importlib, 'import_module', return_value=adapter), contextlib.redirect_stdout(io.StringIO()):
            code = collect.collect(self.args(**changes))
        run = self.root/'run'
        return code, json.loads((run/'status.json').read_text(encoding='utf-8'))
    def test_verification_exits_without_sleep_and_keeps_window(self):
        page = Page('请完成验证')
        with patch.object(collect.time, 'sleep', side_effect=AssertionError('must not poll')):
            code, state = self.run_collection(page, lambda *a, **kw: [])
        self.assertEqual((code, state['state']), (2, 'WAITING_FOR_USER'))
        self.assertTrue(state['token'])
        self.assertFalse(page.closed)
    def test_security_query_is_detected(self):
        guard = collect.Guard(self.root, 'boss')
        self.assertTrue(guard.blocking_reason(Page(url='https://www.zhipin.com/?_security_check=1')))
    def test_waiting_requires_confirmation_before_browser_start(self):
        run=self.root/'run';run.mkdir()
        collect.atomic_json(run/'jobs.json', [])
        collect.atomic_json(run/'status.json', {'state':'WAITING_FOR_USER','token':'right','reason':'验证'})
        page=Page()
        code,state=self.run_collection(page, lambda *a, **kw: [], resume=True)
        self.assertEqual(code,2)
        self.assertEqual(state['token'],'right')
        self.assertEqual(page.visits,[])
    def test_confirmation_rechecks_and_renewed_challenge_stays_waiting(self):
        guard=collect.Guard(self.root,'boss')
        collect.atomic_json(self.root/'status.json',{'state':'WAITING_FOR_USER','token':'right'})
        collect.atomic_json(self.root/'continue.json',{'token':'right'})
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(collect.UserActionRequired):
            guard.check(Page('请完成验证'),force=True)
        state=json.loads((self.root/'status.json').read_text(encoding='utf-8'))
        self.assertEqual(state['state'],'WAITING_FOR_USER')
        self.assertNotEqual(state['token'],'right')
    def test_confirmation_clear_page_resumes_once(self):
        guard=collect.Guard(self.root,'boss')
        collect.atomic_json(self.root/'status.json',{'state':'WAITING_FOR_USER','token':'right'})
        collect.atomic_json(self.root/'continue.json',{'token':'right'})
        with contextlib.redirect_stdout(io.StringIO()):guard.check(Page(),force=True)
        self.assertFalse((self.root/'continue.json').exists())
        self.assertEqual(json.loads((self.root/'status.json').read_text(encoding='utf-8'))['state'],'RUNNING')
    def test_partial_failure_preserves_jobs_and_never_completes(self):
        def broken(page,keyword,**kw):
            rows=broken.__globals__['_streaming_list']()
            rows.append({'job_id':'abc','job_name':'Analyst','company':'A','location':'City','job_url':'https://www.zhipin.com/job_detail/abc.html','salary':'10-20K'})
            raise TimeoutError('adapter changed')
        page=Page()
        code,state=self.run_collection(page,broken)
        jobs=json.loads((self.root/'run/jobs.json').read_text(encoding='utf-8'))
        self.assertEqual((code,state['state']),(1,'SOURCE_STOPPED'))
        self.assertEqual(len(jobs),1)
        self.assertTrue(jobs[0]['job_key'].startswith('boss-'))
        self.assertFalse(page.closed)
    def test_same_title_employer_city_distinct_ids_remain_separate_hcs(self):
        def jobs(page,keyword,**kw):
            return [dict(job_id=key,job_name='Analyst',company='A',location='City',salary='10-20K',
                         job_url=f'https://www.zhipin.com/job_detail/{key}.html') for key in ('one','two')]
        code,state=self.run_collection(Page(),jobs)
        records=json.loads((self.root/'run/jobs.json').read_text(encoding='utf-8'))
        self.assertEqual(code,0)
        self.assertEqual(len(records),2)
        self.assertNotEqual(records[0]['job_key'],records[1]['job_key'])
    def test_empty_unverified_is_error_not_zero_or_completed(self):
        code,state=self.run_collection(Page('loading'),lambda *a,**kw: [])
        self.assertEqual((code,state['state']),(1,'SOURCE_STOPPED'))
        coverage=json.loads((self.root/'run/coverage.json').read_text(encoding='utf-8'))
        self.assertEqual(coverage[-1]['state'],'EMPTY_UNVERIFIED')
    def test_visible_zero_and_resume_keep_previous_exclusions(self):
        run=self.root/'run';run.mkdir()
        collect.atomic_json(run/'jobs.json',[])
        collect.atomic_json(run/'skipped.json',[{'job_name':'old exclusion'}])
        code,state=self.run_collection(Page('没有找到相关职位'),lambda *a,**kw: [],resume=True)
        self.assertEqual((code,state['state']),(0,'COMPLETED'))
        self.assertEqual(json.loads((run/'skipped.json').read_text())[0]['job_name'],'old exclusion')
        self.assertEqual(json.loads((run/'coverage.json').read_text())[-1]['state'],'ZERO_RESULTS')
    def test_rebuild_keeps_contact_when_keys_and_decision_change(self):
        old={'job_key':'job-00001','source':'boss','job_url':'https://www.zhipin.com/job_detail/a.html?ka=old','state':'contacted','execution':{'state':'contacted','evidence':'visible chat','at':'1'}}
        new={'job_key':'boss-new','source':'boss','job_url':'https://www.zhipin.com/job_detail/a.html?securityId=new','action':'skip','state':'pending'}
        bundle=workflow.merge_history({'todos':[new]}, {'todos':[old]})
        self.assertEqual(bundle['todos'][0]['state'],'contacted')
        self.assertEqual(bundle['todos'][0]['action'],'skip')
        self.assertEqual(len(bundle['todos'][0]['execution_history']),1)
    def test_same_sequential_key_different_hc_does_not_inherit_history(self):
        left={'job_key':'job-00001','source':'boss','job_url':'https://www.zhipin.com/job_detail/a.html'}
        right=dict(left,job_url='https://www.zhipin.com/job_detail/b.html')
        self.assertFalse(workflow.same_job(left,right))
    def test_removed_contact_is_archived_and_restored_later(self):
        old={'job_key':'job-00001','source':'boss','job_url':'https://www.zhipin.com/job_detail/a.html','state':'contacted','execution':{'state':'contacted','evidence':'chat'}}
        archived=workflow.merge_history({'todos':[]},{'todos':[old]})
        self.assertEqual(len(archived['archived_todos']),1)
        restored=workflow.merge_history({'todos':[dict(old,state='pending',execution=None)]},archived)
        self.assertEqual(restored['todos'][0]['state'],'contacted')
    def test_unfinished_intent_survives_rebuild_as_uncertain(self):
        todo={'source':'boss','job_key':'boss-a','job_url':'https://www.zhipin.com/job_detail/a.html','state':'pending'}
        event={'site':'boss','job_key':'boss-a','url':todo['job_url'],'state':'contact_intent','evidence':'click'}
        merged=workflow.merge_history({'todos':[todo]},ledger=[event])
        self.assertEqual(merged['todos'][0]['state'],'uncertain')
    def test_corrupt_ledger_fails_closed_without_rewriting(self):
        path=self.root/'ledger.jsonl';path.write_text('{broken\n',encoding='utf-8')
        with self.assertRaises(ValueError):workflow.read_ledger(path)
        self.assertEqual(path.read_text(),'{broken\n')
    def test_batch_rest_is_configurable_and_does_not_request_pages(self):
        guard=collect.Guard(self.root,'boss',0,0,batch_size=2,batch_rest=2)
        with patch.object(collect.time,'sleep') as sleep,contextlib.redirect_stdout(io.StringIO()):
            guard.delay();guard.delay();guard.delay()
        self.assertEqual(guard.actions,3)
        self.assertEqual(sum(call.args[0] for call in sleep.call_args_list),2)

    def contact_files(self):
        policy,candidate,job,review,screens,bundle=sample()
        row=dict(job,reason='matching facts',approved_for_contact=True,role_fit=review['role_fit'])
        workflow.write(self.root/'approved.json',[row])
        workflow.write(self.root/'todos.json',bundle)
        for name,value in [('candidate',candidate),('policy',policy),('source-jobs',[job]),
                           ('screening',screens),('reviews',[review]),('detail-selectors',{'source':'boss'})]:
            workflow.write(self.root/(name+'.json'),value)
        argv=['contact.py','--jobs',str(self.root/'approved.json'),'--todos',str(self.root/'todos.json'),
              '--ledger',str(self.root/'ledger.jsonl'),'--profile-dir',str(self.root/'profile'),
              '--run-dir',str(self.root/'contact'),'--delay-min','0','--delay-max','0']
        for name in ('candidate','policy','source-jobs','screening','reviews','detail-selectors'):
            argv.extend(['--'+name,str(self.root/(name+'.json'))])
        return argv

    def test_contact_connection_failure_never_reports_finished(self):
        argv=self.contact_files()
        with (patch.object(sys,'argv',argv), patch.object(contact,'create_browser',side_effect=RuntimeError('port unavailable')),
              contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as stopped):
            contact.main()
        self.assertEqual(stopped.exception.code,1)
        state=json.loads((self.root/'contact/status.json').read_text(encoding='utf-8'))
        self.assertEqual(state['state'],'CONTACT_STOPPED')
        self.assertEqual(workflow.read(self.root/'todos.json')['todos'][0]['state'],'pending')

    def test_contact_interrupted_click_keeps_intent_and_uncertain_todo(self):
        argv=self.contact_files();page=Page('Analyst A 10-20K');page.tab_ids=[]
        page.ele=lambda *args,**kw: types.SimpleNamespace(text='Analyst A 10-20K',states=types.SimpleNamespace(is_displayed=True))
        def click():raise RuntimeError('lost connection after click')
        button=types.SimpleNamespace(click=click)
        def optional(page,locator,**kwargs):return None if '继续沟通' in locator else button
        with (patch.object(sys,'argv',argv),patch.object(contact,'create_browser',return_value=page),
             patch.object(contact,'extract',return_value={'url':'https://www.zhipin.com/job_detail/a.html','title':'Analyst','company':'A','description':'metrics analysis'}),
             patch.object(contact,'optional_element',side_effect=optional),contextlib.redirect_stdout(io.StringIO()),
             self.assertRaises(SystemExit) as stopped):
            contact.main()
        self.assertEqual(stopped.exception.code,1)
        events=workflow.read_ledger(self.root/'ledger.jsonl')
        self.assertEqual(events[-1]['state'],'contact_intent')
        self.assertEqual(workflow.read(self.root/'todos.json')['todos'][0]['state'],'uncertain')
        self.assertEqual(workflow.read(self.root/'contact/communication.json')[-1]['state'],'CLICKED_RESULT_UNCERTAIN')


if __name__=='__main__':unittest.main()
