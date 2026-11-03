"""DP public-career-page reader and native operator; not a full JD scraper.

CLI: sites | read | run --plan plan.json. See references/career-browser.md.
Requires DrissionPage plus the sibling job-search modules. No HTTP client/API.
"""
import argparse
import json
import random
import re
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from public_dom import PUBLIC_SNAPSHOT_JS
from career_navigation import detect_public_barrier
from browser_tabs import close_owned_tabs
from runtime import Runtime, writer_lock
from workflow import read, write, append_ledger, now

REGISTRY = Path(__file__).resolve().parent.parent/'assets/career-sites.json'
CONTROL_JS = r'''const vis=e=>!!e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden'&&getComputedStyle(e).display!=='none';
const path=e=>{let a=[];while(e&&e!==document.documentElement){let n=1;for(let p=e.previousElementSibling;p;p=p.previousElementSibling)if(p.tagName===e.tagName)n++;a.unshift(e.tagName.toLowerCase()+':nth-of-type('+n+')');e=e.parentElement;}return 'html>'+a.join('>');};
return [...document.querySelectorAll('a[href],button,input,select,[role="button"]')].filter(vis).map(e=>({
selector:path(e),tag:e.tagName.toLowerCase(),type:(e.type||'').toLowerCase(),name:e.getAttribute('name')||'',
value:e.tagName==='INPUT'&&['text','search'].includes((e.type||'').toLowerCase())?e.value:null,
text:(e.innerText||'').trim().slice(0,160),label:e.getAttribute('aria-label')||[...(e.labels||[])].map(x=>x.innerText).join(' ').trim()||e.getAttribute('placeholder')||e.title||e.name||'',
href:e.tagName==='A'?e.href:'',disabled:!!e.disabled||e.getAttribute('aria-disabled')==='true',
options:e.tagName==='SELECT'?[...e.options].map(o=>({text:o.text,value:o.value,disabled:o.disabled})):[],
form:e.form?{method:e.form.method,action:e.form.action,buttons:[...e.form.querySelectorAll('button')].filter(vis).map(b=>b.innerText||b.getAttribute('aria-label')||'').join(' ').slice(0,200)}:null
}));'''
BLOCKED = re.compile(r'申请|投递|登录|注册|订阅|上传|删除|接受全部|apply|sign\s*(?:in|up)|log\s*in|subscribe|upload|delete|accept\s+all', re.I)
SEARCH = re.compile(r'搜索|搜寻|关键词|关键字|职位名称|search|keyword|job title', re.I)
BUTTON = re.compile(r'搜索|筛选|下一页|上一页|更多职位|search|filter|next|previous|load more|^\d+$', re.I)


class CheckpointError(RuntimeError):
    pass


def registry(path=REGISTRY):
    data=read(path)
    if data.get('schema_version')!=1:raise ValueError('Unsupported career registry')
    return data['sites']


def fingerprint(control):
    return (tuple(control.get(k) for k in ('selector','tag','type','name','text','label','href','disabled')),
            json.dumps(control.get('form'),sort_keys=True),json.dumps(control.get('options'),sort_keys=True))


def rebind_search_after_input(original, controls):
    """Accept expected clear-button changes after typing, never a new field/form."""
    def identity(c):
        form=c.get('form')
        return (tuple(c.get(k) for k in ('selector','tag','type','name','text','label','href','disabled')),
                None if form is None else (form.get('method'),form.get('action')),
                json.dumps(c.get('options'),sort_keys=True))
    matches=[c for c in controls if c.get('selector')==original.get('selector')]
    if len(matches)!=1 or identity(matches[0])!=identity(original):
        raise ValueError('Search input or form changed after typing; inspect again')
    validate_action(matches[0],'search')
    return matches[0]


def validate_action(control, operation):
    if control.get('disabled'):raise ValueError('Control is disabled')
    label=control.get('text','')+' '+control.get('label','')
    href=control.get('href','');u=urlsplit(href)
    legacy_moka_read=(u.scheme=='https' and (u.hostname or '').endswith('.mokahr.com')
                      and re.fullmatch(r'/(?:m/)?apply/[A-Za-z0-9_-]+/\d+/?',u.path)
                      and re.fullmatch(r'/?|/jobs(?:/|\?.*)?|/job/[A-Za-z0-9-]+|/page/[^?]+(?:\?.*)?',u.fragment))
    if BLOCKED.search(label) or (BLOCKED.search(href) and not legacy_moka_read):
        raise ValueError('This reader does not perform application/account/subscription actions')
    if operation in ('input','search'):
        if control.get('tag')!='input' or control.get('type') not in ('text','search',''):
            raise ValueError('Only observed public search inputs are supported')
        form=control.get('form')
        if control.get('type')!='search' and not SEARCH.search(label+' '+(form or {}).get('buttons','')):
            raise ValueError('Input has no observed search purpose')
        if form and form.get('method','get').lower()!='get':
            raise ValueError('Search input belongs to a non-GET form; site adapter required')
    elif operation=='select':
        if control.get('tag')!='select':raise ValueError('Not a native select control')
    elif operation=='click':
        if control.get('tag')=='a':
            u=urlsplit(control.get('href',''))
            if u.scheme not in ('http','https') or u.username or u.password:
                raise ValueError('Only observed public HTTP navigation links are supported')
            if re.search(r'\.(?:zip|exe|pdf|docx?|xlsx?)(?:$|\?)',u.path,re.I):
                raise ValueError('Download links require a separate workflow')
        elif not BUTTON.search(label):
            raise ValueError('Button purpose is not verified search/filter/pagination')
    else:raise ValueError('Unsupported control operation')


class CareerBrowser:
    """One site, one task-owned tab group. Context manager closes ordinary tabs.

    Public reads are authorized by the caller. Human checkpoints retain the tab.
    Cross-host links require their exact hostname in allowed_hosts, from evidence.
    """
    def __init__(self, site, run_dir, browser_receipt, *, registry_path=REGISTRY,
                 delay_min=30, delay_max=45, wait_seconds=60, allowed_hosts=(), resume=False):
        sites=registry(registry_path)
        if site not in sites:raise ValueError('Unknown site: '+site)
        if not 0<=delay_min<=delay_max or not 1<=wait_seconds<=180:
            raise ValueError('Invalid waiting policy')
        self.site=sites[site];self.site_id=site;self.root=Path(run_dir).resolve()
        self.receipt_path=Path(browser_receipt).resolve()
        self.delay_min=delay_min;self.delay_max=delay_max;self.wait_seconds=wait_seconds
        self.hosts={urlsplit(self.site['url']).hostname,*allowed_hosts}
        self.browser=None;self.tab=None;self.owned=[];self.locks=[];self.last=None
        self.runtime=Runtime(self.root);self.actions=0;self.resume=resume

    def __enter__(self):
        try:
            receipt=read(self.receipt_path)
            profile=Path(receipt.get('profile','')).resolve()
            address=receipt.get('address','')
            if (not receipt.get('ownership_verified') or not profile.is_dir()
                    or not re.fullmatch(r'127\.0\.0\.1:\d+',address)
                    or Path(receipt.get('run_dir','')).resolve()!=self.receipt_path.parent):
                raise ValueError('Task browser receipt is not verified')
            for directory in (profile/'career-browser-lock',self.root):
                lock=writer_lock(directory);lock.__enter__();self.locks.append(lock)
            previous_state=self.runtime.ensure_ready()
            if previous_state['task_state']=='PAUSED' and not self.resume:
                raise ValueError('Acknowledged checkpoint requires resume=True / --resume')
            contract={'site':self.site_id,'url':self.site['url'],'address':address,'profile':str(profile)}
            path=self.root/'site-contract.json'
            if path.exists() and read(path)!=contract:raise ValueError('Run directory belongs to a different site/browser')
            write(path,contract)
            from DrissionPage import ChromiumOptions, ChromiumPage
            # DP eager mode issues Page.stopLoading at DOMContentLoaded and can
            # abort the deferred Workday/vendor bundle before the app renders.
            opts=ChromiumOptions().set_address(address).existing_only().set_load_mode('normal')
            opts.set_timeouts(base=10,page_load=25,script=15)
            self.browser=ChromiumPage(opts)
            if receipt.get('tab_id') not in self.browser.tab_ids:
                raise ValueError('Receipt keeper tab is no longer present')
            if self.resume:
                prior=read(self.root/'browser.json')
                if prior.get('address')!=address or prior.get('run_dir')!=str(self.root):
                    raise ValueError('Resume browser receipt mismatch')
                ids=prior.get('owned_tab_ids',[prior['tab_id']])
                if prior['tab_id'] not in self.browser.tab_ids:raise ValueError('Checkpoint tab is no longer open')
                self.owned=[self.browser.get_tab(i) for i in ids if i in self.browser.tab_ids]
                self.tab=self.browser.get_tab(prior['tab_id'])
                self.tab.set.load_mode.normal()
                self.read()  # Recheck verification/login BEFORE clearing the paused state.
                self.runtime.start('search')
            else:
                self.runtime.start('search')
                self.tab=self.browser.new_tab('about:blank');self.owned.append(self.tab)
                self.tab.set.load_mode.normal()
                self._save_browser(receipt)
                self._intent('open',url=self.site['url'])
                self._delay();self.tab.get(self.site['url'],timeout=25,retry=0)
                self.read()
            return self
        except BaseException as exc:
            self.__exit__(type(exc),exc,exc.__traceback__)
            raise

    def _intent(self,operation,**fields):
        append_ledger(self.root/'operations.jsonl',dict(at=now(),event='INTENT',operation=operation,**fields))

    def _save_browser(self,receipt=None):
        receipt=receipt or read(self.root/'browser.json')
        write(self.root/'browser.json',dict(receipt,tab_id=self.tab.tab_id,run_dir=str(self.root),
                                         owned_tab_ids=[t.tab_id for t in self.owned]))

    def _delay(self):
        if self.actions and self.actions%5==0:time.sleep(45)
        time.sleep(random.uniform(self.delay_min,self.delay_max));self.actions+=1

    def _pause(self,reason,message,snapshot=None):
        if snapshot:write(self.root/'checkpoint-page.json',snapshot)
        self.runtime.pause(reason,checkpoint={'tab_id':self.tab.tab_id,'url':str(self.tab.url)},message=message)
        raise CheckpointError(reason+': '+message)

    def _check(self,snapshot):
        reason=detect_public_barrier(snapshot)
        if reason:self._pause(reason,'Visible browser/service/verification barrier: '+snapshot.get('title','')+' '+snapshot.get('text','')[:160],snapshot)
        text=snapshot.get('text','')
        if re.search(r'^(?:安全验证|人机验证|请完成验证|请验证您是真人)[。！!\s]*$',snapshot.get('title','').strip()) or any(
                line.strip() in ('拖动滑块完成验证','请完成安全验证','请验证您是真人') for line in text.splitlines()[:10]):
            self._pause('CHALLENGE','Visible human-verification prompt',snapshot)
        password=any(i.get('visible') and re.search(r'type=[\"\x27]password[\"\x27]',i.get('html',''),re.I) for i in snapshot.get('inputs',[]))
        login_title=re.match(r'^(?:sign in|log in|login|登录|账号登录)(?:\s*$|\s*[|—-])',snapshot.get('title',''),re.I)
        if password or login_title or snapshot.get('login_dialogs') or re.search(r'请先登录|请登录后查看|扫码登录|please (?:sign|log) in to (?:view|continue)',text,re.I):
            self._pause('LOGIN_REQUIRED','Visible login requirement',snapshot)
        if urlsplit(snapshot['url']).hostname not in self.hosts:
            self._pause('READ_ERROR','Unexpected host; inspect the observed destination before allowing it',snapshot)

    def read(self):
        """Wait for rendered text stability, save every read under a unique name."""
        end=time.monotonic()+self.wait_seconds;previous=None;stable=None
        while True:
            try:snapshot=self.tab.run_js(PUBLIC_SNAPSHOT_JS)
            except Exception as exc:
                if type(exc).__name__=='ContextLostError' and time.monotonic()<end:
                    time.sleep(2);continue
                raise
            # Navigation can temporarily leave the newly-created tab on about:blank.
            # Give it the same loading budget instead of reporting an instant site error.
            if snapshot.get('url')=='about:blank' and time.monotonic()<end:
                time.sleep(min(2,max(0,end-time.monotonic())))
                continue
            self._check(snapshot)
            signature=(snapshot['url'],snapshot['text'])
            if signature!=previous:previous=signature;stable=time.monotonic()
            if (snapshot['text'].strip() and time.monotonic()-stable>=6) or time.monotonic()>=end:break
            time.sleep(min(2,max(0,end-time.monotonic())))
        snapshot['controls']=self.tab.run_js(CONTROL_JS)
        snapshot['snapshot_id']=uuid.uuid4().hex
        for i,c in enumerate(snapshot['controls']):c['id']=str(i)
        snapshot.update(site=self.site_id,read_at=now(),quality='PAGE_READABLE' if snapshot['text'].strip() else 'EMPTY_OR_LOADING',
                        full_jd_verified=False,capability_evidence=self.site['evidence_level'])
        filename='pages/'+snapshot['snapshot_id']+'.json'
        write(self.root/filename,snapshot);write(self.root/'latest.json',snapshot)
        self.last=snapshot
        append_ledger(self.root/'operations.jsonl',dict(at=now(),event='READ',snapshot_id=snapshot['snapshot_id'],url=snapshot['url'],file=filename))
        return snapshot

    def _control(self,control_id,snapshot_id):
        if not self.last or snapshot_id!=self.last['snapshot_id']:
            raise ValueError('Stale snapshot; inspect the current controls first')
        if str(self.tab.url)!=self.last['url']:raise ValueError('Page URL changed; read again')
        saved=next((c for c in self.last['controls'] if c['id']==str(control_id)),None)
        if saved is None:raise ValueError('Unknown control ID')
        fresh=self.tab.run_js(CONTROL_JS)
        matches=[c for c in fresh if c['selector']==saved['selector']]
        if len(matches)!=1 or fingerprint(matches[0])!=fingerprint(saved):
            raise ValueError('Control identity changed; read again')
        return saved

    def operate(self,operation,control_id,*,snapshot_id,value=None):
        """Native DOM operation; explicit current snapshot handles, no injected clicks."""
        self._check(self.tab.run_js(PUBLIC_SNAPSHOT_JS))
        c=self._control(control_id,snapshot_id);validate_action(c,operation)
        if operation=='click' and c['tag']=='a' and urlsplit(c['href']).hostname not in self.hosts:
            raise ValueError('Observed link leads to a new host; explicitly allow that host first')
        if operation=='select':
            if value not in [o['value'] for o in c['options'] if not o['disabled']]:
                raise ValueError('Select value not in current observed options')
        before_text=self.last['text'];before_url=self.last['url']
        self._intent(operation,control_id=c['id'],snapshot_id=snapshot_id,value=value)
        self._delay()
        # The delay may allow a re-render; revalidate immediately before native action.
        self._check(self.tab.run_js(PUBLIC_SNAPSHOT_JS));self._control(control_id,snapshot_id)
        element=self.tab.ele('css:'+c['selector'],timeout=1)
        if not element or not element.states.is_displayed:raise ValueError('Control is no longer visible')
        before=set(self.browser.tab_ids)
        try:
            if operation=='click':element.click(by_js=False)
            elif operation in ('input','search'):
                element.input(str(value or ''),clear=True)
                if operation=='search':
                    typed=self.read()
                    rebound=rebind_search_after_input(c,typed['controls'])
                    self._intent('search_input_rebound',before_snapshot=snapshot_id,after_snapshot=typed['snapshot_id'],control_id=rebound['id'])
                    self._delay();self._check(self.tab.run_js(PUBLIC_SNAPSHOT_JS))
                    # Revalidate the same input after waiting; no broad form submission.
                    self._control(rebound['id'],typed['snapshot_id'])
                    self.tab.ele('css:'+rebound['selector'],timeout=1).input('\n')
            else:element.select.by_value(value)
            time.sleep(2)
            new=set(self.browser.tab_ids)-before
            if len(new)>1:self._pause('RESULT_UNCERTAIN','Multiple new tabs; ownership requires inspection')
            if new:
                self.tab=self.browser.get_tab(new.pop());self.owned.append(self.tab)
                self._save_browser()
            result=self.read()
        except CheckpointError:raise
        except Exception as exc:
            self._pause('RESULT_UNCERTAIN',type(exc).__name__+': '+str(exc)[:200])
        append_ledger(self.root/'operations.jsonl',dict(at=now(),event='ACTION_OBSERVED',operation=operation,
                      before_snapshot=snapshot_id,after_snapshot=result['snapshot_id'],
                      content_changed=result['text']!=before_text,url_changed=result['url']!=before_url,
                      semantics_verified=False))
        return result

    def click_text(self,text):
        matches=[c for c in self.last['controls'] if c['text']==text or c['label']==text]
        # Responsive navigation often has duplicate links to the same target.
        unique={c['href'] or c['selector']:c for c in matches}
        if len(unique)!=1:raise ValueError('Text target absent or ambiguous; choose a current control ID')
        return self.operate('click',next(iter(unique.values()))['id'],snapshot_id=self.last['snapshot_id'])

    def __exit__(self,exc_type,exc,tb):
        try:
            if self.tab:
                state=self.runtime.load();reason=state.get('pause_reason') if state['task_state']=='PAUSED' else None
                if exc and not reason:
                    try:write(self.root/'error-page.json',self.tab.run_js(PUBLIC_SNAPSHOT_JS))
                    except Exception:pass
                    self.runtime.pause('READ_ERROR',message=str(exc)[:300])
                cleanup=close_owned_tabs(self.browser,self.owned,reason)
                write(self.root/'tab-cleanup.json',cleanup)
                if not exc:self.runtime.commit(task_state='BUDGET_COMPLETED',checkpoint={'last_snapshot':self.last['snapshot_id']})
        finally:
            for lock in reversed(self.locks):lock.__exit__(exc_type,exc,tb)
            self.locks=[]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=('sites','read','run'))
    p.add_argument('--registry',type=Path,default=REGISTRY)
    p.add_argument('--site');p.add_argument('--run-dir',type=Path);p.add_argument('--browser-receipt',type=Path)
    p.add_argument('--plan',type=Path);p.add_argument('--allow-host',action='append',default=[])
    p.add_argument('--resume',action='store_true')
    p.add_argument('--delay-min',type=float,default=30);p.add_argument('--delay-max',type=float,default=45)
    p.add_argument('--wait-seconds',type=float,default=60)
    a=p.parse_args()
    if a.command=='sites':print(json.dumps(registry(a.registry),ensure_ascii=False,indent=2));return
    if not all((a.site,a.run_dir,a.browser_receipt)):p.error('site, run-dir and browser-receipt required')
    plan=read(a.plan) if a.plan else []
    if a.command=='run' and not a.plan:p.error('run requires plan')
    try:
        with CareerBrowser(a.site,a.run_dir,a.browser_receipt,registry_path=a.registry,
                           delay_min=a.delay_min,delay_max=a.delay_max,wait_seconds=a.wait_seconds,allowed_hosts=a.allow_host,resume=a.resume) as page:
            for step in plan:
                if step['op']=='read':page.read()
                elif step['op']=='click_text':page.click_text(step['text'])
                else:
                    matches=[c for c in page.last['controls'] if c['label']==step.get('label') or c['text']==step.get('label')]
                    if len(matches)!=1:raise ValueError('Plan label absent or ambiguous; inspect latest.json')
                    page.operate(step['op'],matches[0]['id'],snapshot_id=page.last['snapshot_id'],value=step.get('value'))
            print(json.dumps({'site':a.site,'url':page.last['url'],'text_chars':len(page.last['text']),
                              'controls':len(page.last['controls']),'run_dir':str(page.root),'full_jd_verified':False},ensure_ascii=False))
    except CheckpointError as exc:
        print(json.dumps({'state':'PAUSED','reason':str(exc),'run_dir':str(a.run_dir)},ensure_ascii=False));raise SystemExit(2)


if __name__=='__main__':main()
