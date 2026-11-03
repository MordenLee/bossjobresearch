import unittest
from huawei_detail_dom import detail_id,validate


class HuaweiDetailTests(unittest.TestCase):
    def test_detail_id_rejects_foreign_or_duplicate_parameter(self):
        for url in ('https://other.test/cn/job-details?advertisementId=1',
                    'https://career.huawei.com/cn/job-details?advertisementId=1&advertisementId=2'):
            with self.assertRaises(ValueError):detail_id(url)

    def test_detail_title_alone_does_not_prove_metadata_identity(self):
        url='https://career.huawei.com/cn/job-details?advertisementId=1'
        d={'url':url,'title':'专家','company_evidence':'华为技术有限公司','description':'岗位职责：'+'研究知识工程。'*15+'岗位要求：三年经验。',
           'location':'深圳','team':'质量部','experience':'三年以上工作经验','function':'研发族'}
        source={'title':'专家','location':'北京 质量部 三年以上工作经验 研发族'}
        with self.assertRaisesRegex(ValueError,'metadata'):validate(d,source,{'url':url,'source_title':'专家'})
