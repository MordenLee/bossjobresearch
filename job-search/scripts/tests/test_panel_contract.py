import unittest
from collect import validate_collected_detail
from runtime import digest

class PanelContractTests(unittest.TestCase):
    def row(self,**changes):
        result=dict(job_url='https://www.zhipin.com/job_detail/a.html',company='A',job_name='Analyst',jd_description='metrics',jd_source='search_panel',
                    panel_detail_evidence=dict(url='https://www.zhipin.com/job_detail/a.html',company='A',title='Analyst',description='metrics',truncated=False))
        result.update(changes);return result
    def test_verified_panel_is_ready_without_detail_navigation(self):
        row=validate_collected_detail(self.row(),'boss')
        self.assertEqual(row['detail_state'],'READY');self.assertEqual(row['jd_hash'],digest('metrics'))
    def test_legacy_verified_flag_does_not_bypass_contract(self):
        row=validate_collected_detail(self.row(panel_detail_evidence=None,detail_identity_verified=True),'boss')
        self.assertEqual(row['detail_state'],'PENDING');self.assertFalse(row['detail_identity_verified'])
    def test_wrong_company_title_and_hc_remain_unready(self):
        for key,value in [('company','Other'),('title','Other'),('url','https://www.zhipin.com/job_detail/b.html')]:
            row=self.row();row['panel_detail_evidence'][key]=value
            self.assertEqual(validate_collected_detail(row,'boss')['detail_state'],'IDENTITY_MISMATCH')
    def test_truncated_panel_needs_detail_fallback(self):
        row=self.row();row['panel_detail_evidence']['truncated']=True
        self.assertEqual(validate_collected_detail(row,'boss')['detail_state'],'INCOMPLETE')
    def test_other_sources_and_changed_body_cannot_use_panel_shortcut(self):
        self.assertEqual(validate_collected_detail(self.row(),'liepin')['detail_state'],'PENDING')
        self.assertEqual(validate_collected_detail(self.row(jd_description='different'),'boss')['detail_state'],'PENDING')
if __name__=='__main__':unittest.main()
