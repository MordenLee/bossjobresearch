import tempfile
import unittest
from collect import Guard


class Page:
    def __init__(self,text,url='https://careers.example.test/jobs'):
        self.text=text;self.url=url
    def run_js(self,script):return self.text


class GuardChallengeContextTests(unittest.TestCase):
    def reason(self,text,url='https://careers.example.test/jobs'):
        with tempfile.TemporaryDirectory() as root:
            guard=Guard(root,'independent')
            result=guard.blocking_reason(Page(text,url))
            return result,guard.detected_pause_reason

    def test_job_requirement_is_not_a_challenge(self):
        self.assertEqual(self.reason('岗位要求：主导测试体系搭建、工具开发、安全验证项目实施，以及对外技术合作。')[0],'')

    def test_standalone_heading_still_pauses(self):
        self.assertTrue(self.reason('安全验证\n请拖动下方图块')[0])

    def test_explicit_instruction_still_pauses(self):
        self.assertTrue(self.reason('当前访问需要完成安全验证后才能继续')[0])

    def test_verification_url_still_pauses(self):
        self.assertTrue(self.reason('加载中','https://careers.example.test/verify')[0])

    def test_actual_login_prompt_is_not_suppressed(self):
        reason,kind=self.reason('岗位职责含安全验证项目。\n请先登录后继续。')
        self.assertTrue(reason);self.assertEqual(kind,'LOGIN_REQUIRED')


if __name__=='__main__':unittest.main()
