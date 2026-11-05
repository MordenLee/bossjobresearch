import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from page_wait import wait_page, PageNotReady, page_state


class Clock:
    def __init__(self):self.t=0
    def now(self):return self.t
    def sleep(self,n):self.t+=n


class ReadinessTests(unittest.TestCase):
    def run_wait(self,reader,valid,**kw):
        clock=Clock()
        return wait_page(reader,valid,timeout=12,interval=2,stable_seconds=4,
                         clock=clock.now,sleep=clock.sleep,**kw)

    def test_service_recovers_without_becoming_zero_jobs(self):
        values=iter([{'text':''},{'text':'Workday is currently unavailable.'},
                     {'text':'Real job','jd':'Full job description'}])
        last={}
        def read():
            nonlocal last
            last=next(values,last);return last
        self.assertEqual(self.run_wait(read,lambda s:bool(s.get('jd')))['text'],'Real job')

    def test_error_footer_never_accepted_even_by_weak_validator(self):
        for text,status in [("The page you are looking for doesn't exist.",'NOT_FOUND'),
                            ('Workday is currently unavailable.','SERVICE_UNAVAILABLE'),('','EMPTY_SHELL')]:
            with self.subTest(status=status),self.assertRaises(PageNotReady) as cm:
                self.run_wait(lambda:{'text':text},lambda s:True)
            self.assertEqual(cm.exception.status,status)
            self.assertEqual(cm.exception.elapsed,12)

    def test_navigation_context_loss_recovers(self):
        class ContextLostError(Exception):pass
        n=0
        def read():
            nonlocal n
            n+=1
            if n==1:raise ContextLostError()
            return {'text':'Job','jd':'Ready'}
        self.assertEqual(self.run_wait(read,lambda s:bool(s.get('jd')))['jd'],'Ready')

    def test_human_gates_stop_without_wait_or_retry(self):
        for s in [{'text':'Verify you are human'},{'text':'Sign In','login_wall':True},{'text':'Access denied'}]:
            with self.assertRaises(PageNotReady) as cm:self.run_wait(lambda:s,lambda s:True)
            self.assertEqual(cm.exception.elapsed,0)

    def test_nav_signin_link_is_not_login_wall(self):
        self.assertEqual(page_state({'text':'Sign In\nA real job'}),'LOADING_OR_UNVERIFIED')

    def test_unexpected_reader_failure_not_masked(self):
        def read():raise ValueError('bad selector')
        with self.assertRaises(ValueError):self.run_wait(read,lambda s:True)

    def test_changing_footer_does_not_block_stable_verified_jd(self):
        n=0
        def read():
            nonlocal n
            n+=1
            return {'text':f'Job and dynamic footer {n}','id':'R1','jd':'Full JD'}
        result=self.run_wait(read,lambda s:s['id']=='R1',
                             fingerprint=lambda s:(s['id'],s['jd']))
        self.assertEqual(result['id'],'R1')

    def test_stale_wrong_job_never_passes(self):
        with self.assertRaises(PageNotReady):
            self.run_wait(lambda:{'text':'Full but stale JD','id':'R2'},lambda s:s['id']=='R1')


if __name__=='__main__':unittest.main()
