import unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from career_browser import validate_action, fingerprint, CareerBrowser, CheckpointError,rebind_search_after_input
from tempfile import TemporaryDirectory
from runtime import Runtime


class CareerBrowserContractTests(unittest.TestCase):
    def test_search_typing_can_add_clear_button_without_changing_input(self):
        field=dict(selector='#keyword',tag='input',type='text',name='base_query',label='Search jobs',form=dict(method='get',action='https://example.org/search',buttons='Search jobs'))
        typed=dict(field,form=dict(field['form'],buttons='Clear keyword search Search jobs'),id='4')
        self.assertNotEqual(fingerprint(field),fingerprint(typed))
        self.assertEqual(rebind_search_after_input(field,[typed])['id'],'4')

    def test_search_rebind_rejects_field_swaps_and_application_form(self):
        field=dict(selector='#keyword',tag='input',type='text',name='query',label='Search jobs',form=dict(method='get',action='https://example.org/search',buttons='Search jobs'))
        changes=[dict(field,name='email'),dict(field,type='password'),dict(field,label='Email'),dict(field,form=dict(field['form'],method='post')),dict(field,form=dict(field['form'],action='https://example.org/apply'))]
        for changed in changes:
            with self.assertRaises(ValueError):rebind_search_after_input(field,[changed])
        with self.assertRaises(ValueError):rebind_search_after_input(field,[field,field])

    def test_search_does_not_accept_contact_or_password_fields(self):
        for c in ({'tag':'input','type':'password','label':'Search'},
                  {'tag':'input','type':'text','label':'Email'},
                  {'tag':'input','type':'search','form':{'method':'post'}}):
            with self.assertRaises(ValueError):validate_action(c,'search')

    def test_public_search_and_next_are_supported(self):
        validate_action({'tag':'input','type':'search'},'search')
        validate_action({'tag':'button','text':'Next'},'click')

    def test_legacy_moka_catalog_is_not_application_submission(self):
        url='https://app.mokahr.com/apply/cambricon/1113#/jobs'
        validate_action({'tag':'a','text':'查看更多职位','href':url},'click')
        for control in ({'tag':'a','text':'申请职位','href':url},
                        {'tag':'a','text':'查看更多职位','href':url.replace('#/jobs','#/apply')},
                        {'tag':'a','text':'查看更多职位','href':url.replace('app.mokahr.com','other.example')}):
            with self.assertRaises(ValueError):validate_action(control,'click')

    def test_account_and_application_controls_are_not_reader_actions(self):
        for text in ('Apply now','Sign in','上传简历','订阅职位'):
            with self.assertRaises(ValueError):validate_action({'tag':'button','text':text},'click')

    def test_script_download_and_disabled_links_are_rejected(self):
        for c in ({'tag':'a','href':'javascript:alert(1)'},
                  {'tag':'a','href':'https://example.org/resume.pdf'},
                  {'tag':'button','text':'Next','disabled':True}):
            with self.assertRaises(ValueError):validate_action(c,'click')

    def test_reused_selector_with_different_destination_has_new_identity(self):
        a={'selector':'html>body>a','tag':'a','href':'https://example.org/job/1'}
        self.assertNotEqual(fingerprint(a),fingerprint(dict(a,href='https://example.org/job/2')))
        self.assertNotEqual(fingerprint(dict(a,form={'method':'get'})),fingerprint(dict(a,form={'method':'post'})))

    def test_stale_snapshot_id_rejected_before_any_browser_access(self):
        page=object.__new__(CareerBrowser);page.last={'snapshot_id':'new'}
        with self.assertRaisesRegex(ValueError,'Stale snapshot'):page._control('0','old')

    def test_full_page_login_and_challenge_pause(self):
        from types import SimpleNamespace
        for title,reason in [('Sign in | Company','LOGIN_REQUIRED'),('安全验证','CHALLENGE')]:
            with TemporaryDirectory() as tmp:
                p=object.__new__(CareerBrowser);p.root=__import__('pathlib').Path(tmp)
                p.runtime=Runtime(tmp);p.hosts={'example.org'}
                p.tab=SimpleNamespace(tab_id='fixture',url='https://example.org/')
                with self.assertRaises(CheckpointError):
                    p._check({'url':p.tab.url,'title':title,'text':''})
                self.assertEqual(p.runtime.load()['pause_reason'],reason)


if __name__=='__main__':unittest.main()
