import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

path=Path(__file__).resolve().parents[1]/'adapters/extract_boss.py'
spec=importlib.util.spec_from_file_location('panel_adapter',path)
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
def snap(jid,body='SQL分析正文',title='数据分析师'):
    return dict(more_info_url=f'https://www.zhipin.com/job_detail/{jid}.html',jd_description=body,detail_title=title)
class Card:
    def __init__(self):self.clicks=0
    def ele(self,*args,**kwargs):return self
    def click(self,**kwargs):self.clicks+=1
class Page:
    def __init__(self,values):self.values=iter(values);self.last={};self.calls=0
    def read_js(self,*args):
        self.calls+=1
        self.last=next(self.values,self.last)
        return self.last.copy()
    def run_js(self,*args):raise AssertionError('must use read-only path')
    def get(self,*args):raise AssertionError('must not navigate')
    def new_tab(self,*args):raise AssertionError('must not open tabs')
class PanelTests(unittest.TestCase):
    record={'job_id':'target','href':'https://www.zhipin.com/job_detail/target.html'}
    def extract(self,values,card=None,timeout=.01):
        p=Page(values);c=card or Card()
        with patch.object(a,'find_card_by_href',return_value=c),patch.object(a.time,'sleep'):
            return a.extract_search_detail(p,self.record,timeout),c,p
    def test_existing_panel_needs_no_click(self):
        result,c,p=self.extract([snap('target')]*3)
        self.assertTrue(result['detail_identity_verified']);self.assertEqual(c.clicks,0)
        self.assertEqual(result['jd_source'],'search_panel')
    def test_old_same_title_cannot_be_assigned_to_target(self):
        result,c,p=self.extract([snap('old','旧正文'),snap('old','旧正文'),snap('target','新正文'),snap('target','新正文')])
        self.assertEqual(result['jd_description'],'新正文');self.assertEqual(c.clicks,1)
    def test_mismatch_never_keeps_previous_body(self):
        result,c,p=self.extract([snap('old')],timeout=0)
        self.assertEqual(result['detail_read_error'],'PANEL_ID_MISMATCH')
        self.assertEqual(result['jd_description'],'')
    def test_new_link_with_old_body_is_not_ready(self):
        result,_,_=self.extract([snap('old','旧正文'),snap('target','旧正文')],timeout=0)
        self.assertEqual(result['detail_read_error'],'PANEL_BODY_UNCHANGED_AFTER_SELECTION')
    def test_empty_and_missing_have_different_errors(self):
        result,_,_=self.extract([snap('target','')],timeout=0)
        self.assertEqual(result['detail_read_error'],'PANEL_BODY_EMPTY_OR_UNSTABLE')
        result,_,_=self.extract([{}],timeout=0)
        self.assertEqual(result['detail_read_error'],'PANEL_MISSING')
    def test_no_stable_id_rejects_even_matching_title(self):
        result,_,_=self.extract([{'detail_title':'数据分析师','jd_description':'正文'}],timeout=0)
        self.assertFalse(result['detail_identity_verified'])
    def test_missing_card_records_error(self):
        with patch.object(a,'find_card_by_href',return_value=None):
            result=a.extract_search_detail(Page([{}]),self.record)
        self.assertEqual(result['detail_read_error'],'CARD_NOT_FOUND')
    def test_read_error_is_not_silent_success(self):
        with patch.object(a,'read_search_detail',side_effect=RuntimeError('DOM failed')):
            result=a.extract_search_detail(Page([]),self.record)
        self.assertIn('PANEL_READ_FAILED',result['detail_read_error'])
    def test_verification_stop_propagates(self):
        class Stop(BaseException):pass
        with patch.object(a,'read_search_detail',side_effect=Stop()),self.assertRaises(Stop):
            a.extract_search_detail(Page([]),self.record)
    def test_does_not_remove_normal_english_words(self):
        self.assertEqual(a.clean_desc_text('Use boss reports and kanzhun data'),'Use boss reports and kanzhun data')
    def test_only_panel_failures_enter_fallback(self):
        self.assertTrue(a.needs_detail_read({'jd_source':'search_panel','detail_read_error':'PANEL_ID_MISMATCH'}))
        self.assertFalse(a.needs_detail_read({'jd_source':'search_panel','detail_read_error':'详情链接跳转首页'}))
        self.assertFalse(a.needs_detail_read({'jd_description':'已有正文','detail_read_error':'PANEL_MISSING','jd_source':'search_panel'}))
if __name__=='__main__':unittest.main()
