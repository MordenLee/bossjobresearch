"""User-login-gated DrissionPage entry for company ATS DOM adapters.

Company-specific DOM callbacks must be verified on the actual tenant page.
No requests/httpx, cookie export, automatic login or embedded API calls.
"""
from pathlib import Path
from urllib.parse import urlsplit
from ats import collect
from collect import Guard, Proxy
from session import connect_owned, inspect_login
from workflow import read, write


class LoginSession:
    def __init__(self, site, run_dir, port, *, connector=connect_owned):
        self.site = site
        self.root = Path(run_dir)
        config = read(self.root/'runtime-config.json')
        self.session = config.get('session', {})
        # Exact configured tenant origin is mandatory; broad platform-wide hosts
        # must not allow another company's logged-in page to satisfy this preflight.
        host = urlsplit(site.host).hostname
        if self.session.get('hosts') != [host] or not self.session.get('signed_in_selectors'):
            raise ValueError('Configure the exact tenant host and verified visible login selectors')
        self.tenant_path = self.session.get('tenant_path', '').rstrip('/')
        if site.platform == 'moka' and not self.tenant_path.startswith('/'):
            raise ValueError('Moka shared host requires verified tenant_path')
        pacing = config.get('pacing', {})
        self.guard = Guard(self.root, site.platform,
                           delay_min=pacing.get('delay_min',30), delay_max=pacing.get('delay_max',45),
                           batch_size=pacing.get('batch_size',5), batch_rest=pacing.get('batch_rest',90))
        self.guard.ensure_can_resume()  # Never auto-acknowledge an old pause.
        self.raw = connector(self.root, port)
        self.page = Proxy(self.raw, self.guard)
        self.check('login')

    def check(self, phase, **checkpoint):
        self.guard.runtime.commit(checkpoint={'phase':phase, **checkpoint})
        self.guard.check(self.raw, force=True)
        current = urlsplit(self.raw.url)
        if (current.netloc != urlsplit(self.site.host).netloc or
                self.tenant_path and current.path.rstrip('/') != self.tenant_path):
            self.guard.pause('当前不是配置的企业招聘站；请返回该企业并确认登录', self.raw, 'LOGIN_UNKNOWN')
        result = inspect_login(self.raw, self.site.platform, self.session)
        write(self.root/'login-check.json', result)
        if result['status'] != 'authenticated':
            self.guard.pause('请在任务浏览器登录后明确回复继续；不会自动重试登录', self.raw,
                             'LOGIN_REQUIRED' if result['status']=='logged_out' else 'LOGIN_UNKNOWN')
        self.guard.runtime.start(phase if phase in ('login','search','detail') else 'search')


def collect_browser(site, run_dir, port, list_reader, detail_reader=None, *,
                    keyword='', max_pages=10, connector=connect_owned):
    """list_reader(proxy, page_number, keyword)->ats.Page.

    detail_reader(proxy, job)->ats.accept_detail evidence; return to the tenant
    list page before returning. Only DOM reads and guarded UI actions. Use
    proxy.read_js for read-only DOM, never fetch/XHR or page-internal API calls.
    Callbacks must not catch BaseException: user-login/challenge pauses propagate.
    """
    session = LoginSession(site, run_dir, port, connector=connector)

    def page_reader(number, query):
        session.check('search', page=number)
        page = list_reader(session.page, number, query)
        session.check('search', page=number)
        return page

    def read_detail(job):
        session.check('detail', job_key=job['job_key'])
        data = detail_reader(session.page, job)
        session.check('detail', job_key=job['job_key'])
        return data

    result = collect(site, None, run_dir, keyword=keyword, max_pages=max_pages,
                     page_reader=page_reader, detail_reader=read_detail if detail_reader else None)
    session.guard.runtime.commit(task_state='BUDGET_COMPLETED' if result['status'] in
                                 ('COMPLETE','BUDGET_COMPLETED','END_UNVERIFIED') else 'FAILED',
                                 checkpoint={'coverage_status':result['status'],
                                             'unique_fetched':result['unique_fetched']})
    return result
