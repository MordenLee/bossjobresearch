import unittest
from independent_dom import validate_snapshot,validate_links,validate_transition,validate_detail_identity,validate_search_shell,validate_inline_expansion,inline_expand_selector,verified_search_can_resume


class IndependentValidationTests(unittest.TestCase):
    def setUp(self):
        self.config={'hosts':['jobs.example.test'],'company_names':['Example'],
                     'required_jd_labels':['职责','要求']}
        self.row={'id':'position-a','title':'分析师','url':'https://jobs.example.test/#position-a',
                  'description':'职责：分析业务数据并解释结果。'*4+'要求：掌握统计与数据库。'*4}

    def snapshot(self,**fields):
        return dict(url='https://jobs.example.test/',company_evidence='Example公司',rows=[dict(self.row)],**fields)

    def test_empty_page_is_not_zero_jobs(self):
        d=self.snapshot();d['rows']=[]
        with self.assertRaisesRegex(ValueError,'not proof of zero'):validate_snapshot(d,self.config)

    def test_foreign_page_and_detail_are_rejected(self):
        d=self.snapshot();d['url']='https://other.example/'
        with self.assertRaisesRegex(ValueError,'page host'):validate_snapshot(d,self.config)
        d=self.snapshot();d['rows'][0]['url']='https://other.example/job/1'
        with self.assertRaisesRegex(ValueError,'detail host'):validate_snapshot(d,self.config)

    def test_duplicate_identity_prevents_silent_merge(self):
        d=self.snapshot();d['rows'].append(dict(self.row))
        with self.assertRaisesRegex(ValueError,'duplicate'):validate_snapshot(d,self.config)

    def test_description_without_requirements_is_incomplete(self):
        d=self.snapshot();d['rows'][0]['description']='职责：分析数据。'*20
        with self.assertRaisesRegex(ValueError,'sections'):validate_snapshot(d,self.config)

    def test_optional_pending_body_keeps_identity_checks(self):
        d=self.snapshot();d['rows'][0]['description']='暂无'
        cfg=dict(self.config,allow_pending_inline_descriptions=True)
        validate_snapshot(d,cfg)
        with self.assertRaisesRegex(ValueError,'full description'):validate_snapshot(d,self.config)
        d['rows'].append(dict(d['rows'][0]))
        with self.assertRaisesRegex(ValueError,'duplicate'):validate_snapshot(d,cfg)

    def test_hidden_jd_needs_real_expansion_and_matching_id(self):
        d=self.snapshot();cfg=dict(self.config,require_description_visible=True)
        with self.assertRaisesRegex(ValueError,'collapsed'):validate_inline_expansion(d,self.row,cfg)
        d['rows'][0]['description_visible']=True
        self.assertEqual(validate_inline_expansion(d,self.row,cfg)['id'],self.row['id'])
        d['rows'][0]['id']='other'
        with self.assertRaisesRegex(ValueError,'identity mismatch'):validate_inline_expansion(d,self.row,cfg)

    def test_inline_selector_rejects_source_value_injection(self):
        cfg=dict(card_selector='.line',id_selector='input',id_attribute='value',inline_expand_selector='.post')
        self.assertIn(':has(input[value="123"])',inline_expand_selector({'id':'123'},cfg))
        with self.assertRaisesRegex(ValueError,'Unsafe'):inline_expand_selector({'id':'123"] .apply-btn'},cfg)

    def test_resume_search_requires_proven_same_filter_url_and_ids(self):
        current={'url':'https://jobs.example.test/','keyword':'数据','rows':[{'id':'1'}]}
        self.assertTrue(verified_search_can_resume(current,dict(current),'数据'))
        self.assertFalse(verified_search_can_resume(current,{},'数据'))
        self.assertFalse(verified_search_can_resume(current,dict(current,rows=[{'id':'2'}]),'数据'))
        self.assertFalse(verified_search_can_resume(current,dict(current,keyword='工程'),'数据'))

    def test_company_identity_must_come_from_dom(self):
        d=self.snapshot();d['company_evidence']='Other'
        with self.assertRaisesRegex(ValueError,'Company identity'):validate_snapshot(d,self.config)

    def test_inline_hash_id_must_match_and_stay_in_tenant(self):
        d=self.snapshot();d['rows'][0].update(id='abc',url='https://jobs.example.test/tenant/#/job/abc')
        cfg=dict(self.config,id_url_pattern=r'#/job/([a-z]+)$',detail_path_pattern='/tenant/')
        validate_snapshot(d,cfg)
        d['rows'][0]['url']='https://jobs.example.test/tenant/#/job/def'
        with self.assertRaisesRegex(ValueError,'identity mismatch'):validate_snapshot(d,cfg)
        d['rows'][0]['url']='https://jobs.example.test/other/#/job/abc'
        with self.assertRaisesRegex(ValueError,'path mismatch'):validate_snapshot(d,cfg)

    def test_list_rejects_same_host_unrelated_recommendation(self):
        d=self.snapshot();d['rows'][0]['url']='https://jobs.example.test/blog/news'
        cfg=dict(self.config,detail_path_pattern=r'/jobs/[0-9]+')
        with self.assertRaisesRegex(ValueError,'detail path'):validate_links(d,cfg)

    def test_list_allows_pending_jd_but_not_empty_list(self):
        d=self.snapshot();d['rows'][0].update(url='https://jobs.example.test/jobs/123',description='')
        cfg=dict(self.config,detail_path_pattern=r'/jobs/[0-9]+')
        self.assertEqual(len(validate_links(d,cfg)),1)
        d['rows']=[]
        with self.assertRaisesRegex(ValueError,'not confirmed zero'):validate_links(d,cfg)

    def test_changed_page_label_without_changed_jobs_is_rejected(self):
        old={'rows':[{'id':'1'}],'page':'1','keyword':'Engineer'}
        new=dict(old,page='2')
        with self.assertRaisesRegex(ValueError,'identities unchanged'):validate_transition(old,new,'Engineer',True)

    def test_pagination_must_retain_filter_and_advance_page(self):
        old={'rows':[{'id':'1'}],'page':'1','keyword':'Engineer'}
        new={'rows':[{'id':'2'}],'page':'1','keyword':'Engineer'}
        with self.assertRaisesRegex(ValueError,'advance'):validate_transition(old,new,'Engineer',True)
        new.update(page='2',keyword='')
        with self.assertRaisesRegex(ValueError,'filter'):validate_transition(old,new,'Engineer',True)
        new['keyword']='Engineer';validate_transition(old,new,'Engineer',True)

    def test_clicked_detail_id_must_match_card_even_with_same_title(self):
        cfg=dict(self.config,layout='click_details',detail_path_pattern='/detail',detail_id_query='id')
        d={'url':'https://jobs.example.test/detail?id=2','title':self.row['title'],
           'description':self.row['description'],'company_evidence':'Example'}
        with self.assertRaisesRegex(ValueError,'job ID'):validate_detail_identity(d,dict(self.row,id='1'),cfg)

    def test_clicked_card_identity_cannot_escape_selector(self):
        d=self.snapshot();d['rows'][0]['id']='1"] .other'
        with self.assertRaisesRegex(ValueError,'Unsafe'):validate_links(d,dict(self.config,layout='click_details'))

    def test_empty_search_shell_requires_visible_input_and_company(self):
        d=self.snapshot();d.update(rows=[],search_visible=True)
        validate_search_shell(d,self.config)
        d['search_visible']=False
        with self.assertRaisesRegex(ValueError,'control'):validate_search_shell(d,self.config)


if __name__=='__main__':unittest.main()
