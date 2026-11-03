import unittest
from browser_tabs import close_owned_tabs

class Browser:
    def __init__(self):self.tab_ids=['keeper','user-tab','created-a','created-b']

class Tab:
    def __init__(self,browser,tab_id,fail=False):self.browser=browser;self.tab_id=tab_id;self.fail=fail
    def close(self):
        if self.fail:raise RuntimeError('close failed')
        self.browser.tab_ids.remove(self.tab_id)

class OwnedTabsTests(unittest.TestCase):
    def test_only_registered_tabs_are_closed(self):
        b=Browser();r=close_owned_tabs(b,[Tab(b,'created-a'),Tab(b,'created-a'),Tab(b,'created-b')])
        self.assertEqual(b.tab_ids,['keeper','user-tab']);self.assertEqual(len(r['closed']),2)
    def test_verification_keeps_owned_tabs_for_user(self):
        b=Browser();r=close_owned_tabs(b,[Tab(b,'created-a')],'CHALLENGE')
        self.assertIn('created-a',b.tab_ids);self.assertEqual(r['retained'],['created-a'])
    def test_read_error_does_not_keep_public_tabs(self):
        b=Browser();close_owned_tabs(b,[Tab(b,'created-a')],'READ_ERROR')
        self.assertNotIn('created-a',b.tab_ids)
    def test_close_failure_is_explicit_and_does_not_touch_other_tabs(self):
        b=Browser();r=close_owned_tabs(b,[Tab(b,'created-a',True)])
        self.assertEqual(r['retained'],['created-a']);self.assertEqual(len(r['errors']),1)
        self.assertIn('user-tab',b.tab_ids)

if __name__=='__main__':unittest.main()
