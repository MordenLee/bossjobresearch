"""Behavioral regressions for v3. All inputs and browser surfaces are local fakes."""
import copy
import csv
import io
import contextlib
import json
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fixtures import sample
from runtime import Runtime, writer_lock
from workflow import read, write, build, merge_history
from policy import validate_policy, validate_review, binding
from export_csv import export
from execution import authorize, checked_bundle
from session import inspect_login, connect_owned
from detail import apply_detail
from collect import Guard, UserActionRequired
from salary import salary_gate


class Contracts(unittest.TestCase):
    def setUp(self):
        self.test_base = (Path.cwd()/'work/skill-test-artifacts').resolve()
        self.test_base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=self.test_base)
        self.root = Path(self.temp.name)
        self.policy,self.candidate,self.job,self.review,self.screens,self.bundle=sample()
    def tearDown(self):
        assert self.root.resolve().parent == self.test_base
        self.temp.cleanup()
    def rule(self, category='hard_constraints', rule_id='rule-1'):
        rule={'id':rule_id,'statement':'A sourced requirement',
              'source':{'kind':'user_instruction','ref':'turn:1','quote':'explicit requirement'}}
        self.policy[category].append(rule)
        return rule
    def rebind(self): self.review['input_binding']=binding(self.job,self.candidate,self.policy)
    def result(self, rule_id='rule-1', status='pass'):
        return {'rule_id':rule_id,'status':status,'reason':'facts or missing information',
                'evidence':{'jd_quotes':['metrics'],'candidate_refs':[]}}
    def validate(self): validate_review(self.review,self.job,self.candidate,self.policy)

    def test_empty_policy_has_no_implicit_profession_or_salary(self):
        self.validate(); validate_policy(self.policy)
    def test_candidate_template_does_not_choose_business_preferences(self):
        template=read(Path(__file__).resolve().parents[2]/'assets/candidate.template.json')
        prefs=template['preferences']
        self.assertEqual(prefs['search_languages'],[])
        self.assertIsNone(prefs['city_mode'])
        self.assertIsNone(prefs['commute_scoring'])
        self.assertTrue(all(value is None for value in prefs['salary'].values()))
        self.assertEqual(prefs['target_roles'],[])
        self.assertEqual(prefs['excluded_industries'],[])
        self.assertEqual(prefs['excluded_roles'],[])
    def test_salary_without_explicit_threshold_does_not_filter(self):
        self.assertEqual(salary_gate('10-20K',None),{'status':'unset'})
    def test_unknown_risk_severity_fails_at_reviewer_boundary(self):
        self.review['risks']=[{'severity':'warning','finding':'gap','evidence':'facts'}]
        with self.assertRaises(ValueError):self.validate()
    def test_rule_without_source_rejected(self):
        self.rule().pop('source')
        with self.assertRaises(ValueError): validate_policy(self.policy)
    def test_cross_category_duplicate_ids_rejected(self):
        self.rule(); self.rule('soft_preferences')
        with self.assertRaises(ValueError): validate_policy(self.policy)
    def test_tradeoff_cannot_relax_hard_constraint(self):
        self.rule(); rule=self.rule('tradeoffs','trade')
        rule['may_relax_rule_ids']=['rule-1']
        with self.assertRaises(ValueError): validate_policy(self.policy)
    def test_tradeoff_can_reference_only_named_soft_preferences(self):
        self.rule('soft_preferences'); self.rule('tradeoffs','trade')['may_relax_rule_ids']=['rule-1']
        validate_policy(self.policy)
    def test_omitted_rule_cannot_pass_review(self):
        self.rule(); self.rebind()
        with self.assertRaises(ValueError): self.validate()
    def test_hard_failure_cannot_be_overridden(self):
        self.rule(); self.review['rule_results']=[self.result(status='fail')]
        self.review['hard_constraints']=[{'name':'rule-1','status':'fail','evidence':'metrics'}]; self.rebind()
        with self.assertRaises(ValueError): self.validate()
        self.review['decision']='skip'; self.validate()
    def test_unknown_hard_condition_blocks_apply(self):
        self.rule(); self.review['rule_results']=[self.result(status='unknown')]
        self.review['hard_constraints']=[{'name':'rule-1','status':'unknown','evidence':'missing'}]; self.rebind()
        with self.assertRaises(ValueError): self.validate()
        self.review.update(decision='verify',questions=['Confirm requirement?']); self.validate()
    def test_inapplicable_hard_rule_remains_blocking_and_can_be_exported_for_verification(self):
        self.rule(); self.review['rule_results']=[self.result(status='not_applicable')]
        self.review['hard_constraints']=[{'name':'rule-1','status':'not_applicable','evidence':'rule needs clarification'}]
        self.rebind()
        with self.assertRaises(ValueError): self.validate()
        self.review.update(decision='verify',questions=['Clarify whether the rule applies?'])
        result=build('boss',[self.job],self.screens,[self.review],self.candidate,self.policy)
        self.assertEqual(result['todos'][0]['action'],'verify')
    def test_foreign_jd_quote_rejected(self):
        self.rule('soft_preferences'); self.review['rule_results']=[self.result()]
        self.review['rule_results'][0]['evidence']['jd_quotes']=['a different job']; self.rebind()
        with self.assertRaises(ValueError): self.validate()
    def test_company_rule_can_cite_collected_company_field(self):
        self.rule('exclusions'); self.review['rule_results']=[self.result()]
        self.review['rule_results'][0]['evidence']={'jd_quotes':[],'candidate_refs':[],'job_refs':['/company']}
        self.rebind(); self.validate()
        self.review['rule_results'][0]['evidence']['job_refs']=['/nonexistent']
        with self.assertRaises(KeyError): self.validate()
    def test_unconfirmed_resume_reference_rejected(self):
        self.candidate['evidence']['/cv/projects/0']['confirmed']=False; self.rebind()
        with self.assertRaises(ValueError): build('boss',[self.job],self.screens,[self.review],self.candidate,self.policy)
    def test_jd_change_invalidates_old_review(self):
        self.job['jd_description']+=' changed'
        with self.assertRaises(ValueError): self.validate()
    def test_same_version_policy_content_change_invalidates_review(self):
        self.policy['policy_id']='changed'
        with self.assertRaises(ValueError): self.validate()
    def test_candidate_change_invalidates_review(self):
        self.candidate['cv']['projects'].append('new fact')
        with self.assertRaises(ValueError): self.validate()
    def test_partial_jd_cannot_be_reviewed(self):
        self.job['detail_state']='INCOMPLETE'; self.rebind()
        with self.assertRaises(ValueError): self.validate()
    def test_mismatched_identity_stays_unready(self):
        data={'url':self.job['job_url'],'title':'Analyst','company':'different','description':'metrics'}
        self.assertEqual(apply_detail(self.job,data)['detail_state'],'IDENTITY_MISMATCH')
    def test_changed_jd_preserves_prior_version(self):
        data={'url':self.job['job_url'],'title':'Analyst','company':'A','description':'new metrics'}
        result=apply_detail(self.job,data)
        self.assertEqual(result['detail_state'],'READY')
        self.assertEqual(result['jd_history'][0]['jd_description'],'metrics analysis')
    def test_visible_truncation_blocks_ready(self):
        data={'url':self.job['job_url'],'title':'Analyst','company':'A','description':'metrics','truncated':True}
        self.assertEqual(apply_detail(self.job,data)['detail_state'],'INCOMPLETE')
    def test_csv_preserves_human_fields_across_reordering(self):
        path=self.root/'todos.csv'; export(self.bundle,path)
        with path.open(encoding='utf-8-sig',newline='') as f: rows=list(csv.DictReader(f))
        rows[0].update(user_choice='apply',user_notes='中文, newline\nand quote " retained',user_custom='my field')
        with path.open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
        self.bundle['todos'][0]['priority']=3; export(self.bundle,path)
        with path.open(encoding='utf-8-sig',newline='') as f: actual=list(csv.DictReader(f))[0]
        self.assertEqual(actual['user_notes'],rows[0]['user_notes']);self.assertEqual(actual['user_custom'],'my field')
        self.assertEqual(actual['priority'],'3')
    def test_csv_removed_row_is_retained(self):
        path=self.root/'todos.csv';export(self.bundle,path);export({'todos':[]},path)
        with path.open(encoding='utf-8-sig',newline='') as f: row=list(csv.DictReader(f))[0]
        self.assertEqual(row['in_current_view'],'false')
    def test_csv_duplicate_input_does_not_replace_old_file(self):
        path=self.root/'todos.csv';export(self.bundle,path);before=path.read_bytes()
        self.bundle['todos']*=2
        with self.assertRaises(ValueError):export(self.bundle,path)
        self.assertEqual(path.read_bytes(),before)
    def test_machine_formula_is_exported_as_text(self):
        self.bundle['todos'][0]['company']='=CMD()';path=self.root/'todos.csv';export(self.bundle,path)
        with path.open(encoding='utf-8-sig',newline='') as f:row=list(csv.DictReader(f))[0]
        self.assertEqual(row['company'],"'=CMD()")
    def test_journal_recovers_snapshot_after_crash(self):
        runtime=Runtime(self.root);runtime.start('search');write(self.root/'task-state.json',{'stale':True})
        self.assertEqual(Runtime(self.root).load()['phase'],'search')
    def test_corrupt_journal_stops_without_rewriting(self):
        path=self.root/'task-events.jsonl';path.write_text('{bad\n')
        with self.assertRaises(ValueError):Runtime(self.root).load()
        self.assertEqual(path.read_text(),'{bad\n')
    def test_pause_ack_is_not_login_proof(self):
        runtime=Runtime(self.root);state=runtime.pause('LOGIN_UNKNOWN')
        runtime.acknowledge(state['token'],'user says done')
        page=types.SimpleNamespace(url='https://www.zhipin.com/',run_js=lambda *a:'normal page',ele=lambda *a,**k:None)
        with contextlib.redirect_stdout(io.StringIO()),self.assertRaises(UserActionRequired):Guard(self.root,'boss').preflight(page)
        self.assertEqual(runtime.load()['pause_reason'],'LOGIN_UNKNOWN')
    def test_old_pause_token_cannot_resume_new_pause(self):
        runtime=Runtime(self.root);old=runtime.pause('USER_PAUSE');runtime.pause('USER_PAUSE')
        with self.assertRaises(ValueError):runtime.acknowledge(old['token'],'continue')
    def test_upgrade_requires_specific_decision(self):
        runtime=Runtime(self.root);state=runtime.request_upgrade('expensive reader','may consume many tokens')
        with self.assertRaises(ValueError):runtime.acknowledge(state['token'],'continue')
        runtime.decide_upgrade(state['token'],True,'explicit user approval');runtime.check_upgrade('expensive reader')
        with self.assertRaises(ValueError):runtime.check_upgrade('different program')
    def test_upgrade_does_not_transfer_to_another_task(self):
        runtime=Runtime(self.root);state=runtime.request_upgrade('reader','high cost');runtime.decide_upgrade(state['token'],True,'yes')
        with self.assertRaises(ValueError):Runtime(self.root/'other').check_upgrade('reader')
    def test_declined_upgrade_is_recorded_and_not_approved(self):
        runtime=Runtime(self.root);state=runtime.request_upgrade('reader','high cost');runtime.decide_upgrade(state['token'],False,'no')
        self.assertEqual(runtime.load()['task_state'],'READY')
        with self.assertRaises(ValueError):runtime.check_upgrade('reader')
    def test_concurrent_writer_rejected(self):
        with writer_lock(self.root):
            with self.assertRaises(ValueError):
                with writer_lock(self.root):pass
    def test_stale_quota_snapshot_pauses(self):
        write(self.root/'runtime-config.json',{'quota':{'max_age_seconds':10}})
        write(self.root/'quota-state.json',{'source':'mcp__codex_app__get_usage_limits','checked_at_epoch':time.time()-30,
                                        'remaining_percent':50,'checked_processed_total':0})
        runtime=Runtime(self.root)
        with self.assertRaises(ValueError):runtime.quota_gate(0)
        self.assertEqual(runtime.load()['pause_reason'],'QUOTA_CHECK')
    def test_low_quota_cannot_be_cleared_by_continue(self):
        write(self.root/'runtime-config.json',{'quota':{ 'minimum_remaining_percent':10}})
        write(self.root/'quota-state.json',{'source':'mcp__codex_app__get_usage_limits','checked_at_epoch':time.time(),
                                        'remaining_percent':2,'checked_processed_total':0})
        runtime=Runtime(self.root)
        with self.assertRaises(ValueError):runtime.quota_gate(0)
        with self.assertRaises(ValueError):runtime.acknowledge(runtime.load()['token'],'continue')
    def test_login_unknown_and_hidden_signal_not_authenticated(self):
        page=types.SimpleNamespace(url='https://www.zhipin.com',run_js=lambda *a:'normal',
             ele=lambda *a,**k:types.SimpleNamespace(states=types.SimpleNamespace(is_displayed=False)))
        self.assertEqual(inspect_login(page,'boss')['status'],'unknown')
    def test_wrong_browser_manifest_rejected_before_connect(self):
        write(self.root/'browser.json',{'address':'127.0.0.1:1','tab_id':'tab','ownership_verified':True,'run_dir':str(self.root)})
        with self.assertRaises(ValueError):connect_owned(self.root,2)
    def test_apply_does_not_authorize_upload(self):
        authorize(self.candidate,'boss',['contact'],self.job['job_key'])
        with self.assertRaises(ValueError):authorize(self.candidate,'boss',['upload'],self.job['job_key'])
    def test_later_submit_intent_not_hidden_by_earlier_contact(self):
        events=[dict(site='boss',job_key='boss-a',state='contacted',at='1'),
                dict(site='boss',job_key='boss-a',state='submit_intent',at='2')]
        bundle=merge_history(self.bundle,ledger=events)
        self.assertEqual(bundle['todos'][0]['state'],'uncertain')
    def test_end_to_end_offline_policy_build_history_csv(self):
        self.rule();self.review['rule_results']=[self.result()]
        self.review['hard_constraints']=[{'name':'rule-1','status':'pass','evidence':'metrics'}];self.rebind()
        bundle=build('boss',[self.job],self.screens,[self.review],self.candidate,self.policy)
        bundle=merge_history(bundle,ledger=[dict(site='boss',job_key='boss-a',state='contacted',at='1',evidence='fake success')])
        path=self.root/'deliverable.csv';export(bundle,path)
        with path.open(encoding='utf-8-sig',newline='') as f:row=list(csv.DictReader(f))[0]
        self.assertEqual((row['action'],row['state']),('apply','contacted'))


if __name__=='__main__':unittest.main()
