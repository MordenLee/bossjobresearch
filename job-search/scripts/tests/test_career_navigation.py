import unittest
from career_navigation import choose_job_link,detect_public_barrier

def link(text,url,**kwargs):return dict(text=text,url=url,raw_href=url,visible=True,**kwargs)

class NavigationTests(unittest.TestCase):
    def test_observed_combined_moka_more_jobs_card(self):
        entry=link('其他职位\n查看更多职位','https://app.mokahr.com/apply/example/1#/jobs/')
        self.assertIs(choose_job_link('https://app.mokahr.com/apply/example/1#/',[entry]),entry)

    def test_social_entry_is_preferred_and_original_link_is_preserved(self):
        entry=link('社会招聘','https://jobs.example.test/social?tenant=abc')
        self.assertIs(choose_job_link('https://example.test/careers',[link('Search jobs','https://example.test/list'),entry]),entry)
    def test_only_observed_visible_navigation_labels(self):
        self.assertIsNone(choose_job_link('https://example.test/',[link('Apply now','https://example.test/apply'),dict(link('Search jobs','https://example.test/jobs'),visible=False)]))
    def test_existing_search_and_filters_are_not_reset(self):
        links=[link('Search jobs','https://example.test/all')]
        for url in ['https://example.test/search-results','https://example.test/careers?country=CHN','https://example.test/jobs/123']:
            self.assertIsNone(choose_job_link(url,links))
    def test_no_script_download_campus_or_same_page_navigation(self):
        for target in ['javascript:showJobs()','https://example.test/jobs.pdf','https://example.test/campus','https://example.test/#jobs']:
            self.assertIsNone(choose_job_link('https://example.test/',[link('Search jobs',target)]))
    def test_no_url_is_guessed_from_text(self):
        self.assertIsNone(choose_job_link('https://example.test/',[dict(text='Search jobs',visible=True)]))
    def test_observed_spa_social_route_is_navigation(self):
        entry=link('社会招聘','https://example.test/social-recruitment/company/1#/page/social')
        self.assertIs(choose_job_link('https://example.test/social-recruitment/company/1#/',[entry]),entry)
        self.assertIsNone(choose_job_link(entry['url'],[entry]))
    def test_bilingual_social_label_and_hash_search_protection(self):
        entry=link('社会招聘\nExperienced Recruitment','https://example.test/custom/social')
        self.assertIs(choose_job_link('https://example.test/custom/index',[entry]),entry)
        self.assertIsNone(choose_job_link('https://example.test/tenant#/jobs?keyword=Python',[entry]))

    def test_observed_cloudflare_unicode_heading_is_a_checkpoint(self):
        self.assertEqual(detect_public_barrier({'url':'https://example.test/jobs','title':'Verifying your browser…','text':'Just a quick security check…\nWe are verifying the security of your connection.'}),'CHALLENGE')
    def test_security_job_prose_is_not_a_verification_prompt(self):
        self.assertIsNone(detect_public_barrier({'url':'https://example.test/jobs','title':'Security Engineer','text':'岗位职责：分析Access denied日志，实施security check。'*8}))
    def test_cf_marker_is_not_alone_proof_after_public_content_loads(self):
        d={'url':'https://example.test/jobs?__cf_chl_rt_tk=abc','title':'Careers','text':'公开职位与职责。'*40}
        self.assertIsNone(detect_public_barrier(d))
        d['text']='';self.assertEqual(detect_public_barrier(d),'CHALLENGE')
    def test_request_error_is_separate_from_access_denied(self):
        self.assertEqual(detect_public_barrier({'url':'https://example.test','title':'Request Unavailable','text':''}),'READ_ERROR')
        self.assertEqual(detect_public_barrier({'url':'https://example.test','title':'Access Denied','text':''}),'ACCESS_DENIED')

if __name__=='__main__':unittest.main()
