"""Read-only Workday hosted careers DOM collector using task-owned DrissionPage.

No CXS/API calls, application actions, login automation or generic body fallback.
Only myworkdayjobs.com hosted templates are supported in this adapter.
"""
import argparse
import re
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, unquote
from collect import Guard, Proxy, UserActionRequired, StopSite
from session import connect_owned
from runtime import writer_lock
from moka_dom import wait_stable, usable
from workflow import read, write, now
from export_csv import export, JOB_FIELDS

LIST_JS = r'''const text=id=>document.querySelector(`[data-automation-id="${id}"]`)?.innerText||'';
return {url:location.href,query:document.querySelector('[data-automation-id="keywordSearchInput"]')?.value,
total_text:text('jobFoundText'),range_text:text('jobOutOfText'),
rows:[...document.querySelectorAll('a[data-automation-id="jobTitle"]')].map(a=>{
const c=a.closest('li');return {url:a.href,title:a.innerText,summary:c?.innerText||''};})};'''
DETAIL_JS = r'''const boundary=document.querySelector('[data-automation-id="jobDetails"], [data-automation-id="jobPostingPage"]');
let root=boundary?.querySelector('[data-automation-id="jobPostingHeader"]');
while(root && !root.querySelector('[data-automation-id="jobPostingDescription"]'))root=root.parentElement;
if(!boundary?.contains(root))root=null;
const text=id=>root?.querySelector(`[data-automation-id="${id}"]`)?.innerText||'';
return {url:location.href,title:text('jobPostingHeader'),jd:text('jobPostingDescription'),
fields:[...(root?.querySelectorAll('dl')||[])].map(e=>({label:e.querySelector('dt')?.innerText||'',values:[...e.querySelectorAll('dd')].map(d=>d.innerText)})),
locations:text('locations'),posted_on:text('postedOn'),time_type:text('timeType'),requisition:text('requisitionId')};'''
FIELDS = JOB_FIELDS + ['job_id','requisition_id','employment_type','published_at','search_keyword']


def site_parts(url):
    p=urlsplit(url)
    if p.scheme!='https' or p.username or p.password or p.port or not (p.hostname or '').endswith('.myworkdayjobs.com'):
        raise ValueError('需要真实 Workday HTTPS 租户域名，不接受其他门户')
    parts=[unquote(x) for x in p.path.split('/') if x]
    if parts and re.fullmatch(r'[a-z]{2}-[A-Z]{2}',parts[0]):parts=parts[1:]
    if not parts or not re.fullmatch(r'[A-Za-z0-9_-]+',parts[0]):raise ValueError('缺少站点名称')
    return p.hostname,parts


def identity(url, config):
    host,parts=site_parts(url);base,site=site_parts(config['list_url'])
    if host!=base or parts[0]!=site[0] or len(parts)<3 or parts[1] not in ('job','details'):
        raise ValueError('详情跨租户/站点或不是岗位链接')
    tail=parts[-1].rsplit('_',1)
    if len(tail)!=2 or not re.fullmatch(r'[A-Za-z0-9-]+',tail[1]):raise ValueError('缺少岗位稳定ID')
    return tail[1]


def validate_config(c):
    _,parts=site_parts(c['list_url'])
    if len(parts)!=1:raise ValueError('list_url 必须是站点列表入口')
    if not c.get('company') or not c.get('tenant'):raise ValueError('缺少已核实公司/租户')
    if c.get('access_mode','authenticated') not in ('public','authenticated'):raise ValueError('未知访问模式')
    if c.get('access_mode')=='public' and not c.get('public_access',{}).get('evidence','').strip():
        raise ValueError('公开访问必须记录用户授权依据')
    pacing=c.get('pacing',{})
    if pacing.get('delay_min',30)<0 or pacing.get('delay_max',45)<pacing.get('delay_min',30):raise ValueError('无效等待范围')
    location=c.get('location_filter')
    if location is not None:
        if not isinstance(location,dict) or any(not isinstance(location.get(k),str) or not location[k].strip() for k in ('label','opener_selector','apply_text')):
            raise ValueError('location_filter requires observed label, opener_selector and apply_text')
        if not re.fullmatch(r'View Jobs|查看(?:职位|职务|工作)|显示(?:职位|职务|工作)',location['apply_text']):
            raise ValueError('Unsupported location-filter confirmation; application controls are not allowed')
    return c


class WorkdayGuard(Guard):
    def blocking_reason(self,page):
        reason=super().blocking_reason(page)
        if reason:return reason
        # A normal Sign In navigation button is NOT a login wall.
        d=page.run_js("return {text:document.body?.innerText||'',wall:[...document.querySelectorAll('[data-automation-id=\"signInContent\"],input[type=\"password\"]')].some(e=>e.getClientRects().length && getComputedStyle(e).visibility!=='hidden')}")
        t=d['text'].lower()
        if any(x in t for x in ('access denied','too many requests','verify you are human','verify that you are human')):
            self.detected_pause_reason='ACCESS_DENIED' if ('access denied' in t or 'too many requests' in t) else 'CHALLENGE'
            return 'Workday 访问限制或验证页面'
        if d['wall']:
            self.detected_pause_reason='LOGIN_REQUIRED';return 'Workday 登录表单已出现'
        return ''


def total_count(data):
    # Only dedicated result-count element, never arbitrary digits from the body.
    m=re.fullmatch(r'\s*([\d,]+)\s+JOBS? FOUND\s*',data.get('total_text',''),re.I)
    if not m:m=re.fullmatch(r'\s*找到\s*([\d,]+)\s*个职务\s*',data.get('total_text',''))
    return int(m[1].replace(',','')) if m else None


def valid_list(data,c,keyword):
    try:
        h,p=site_parts(data['url']);bh,bp=site_parts(c['list_url'])
        params=parse_qs(urlsplit(data['url']).query)
        selected=c.get('_selected_location_id')
        location_ok=not selected or any(selected in values for values in params.values())
        return h==bh and p==bp and data.get('query')==keyword and params.get('q',[''])[0]==keyword and location_ok and (bool(data['rows']) or total_count(data)==0)
    except (ValueError,KeyError):return False


def accept_detail(job,d,c):
    if identity(d['url'],c)!=job['job_id']:raise ValueError('岗位ID不符')
    normalize=lambda t:re.sub(r'\s+',' ',t).strip()
    if normalize(d['title'])!=normalize(job['job_name']):raise ValueError('岗位标题不符')
    if not usable(d['jd']) or d['jd'].strip().lower() in ('loading','loading...','no description available'):
        raise ValueError('正文仍为空/占位，需补读')
    fields={}
    for f in d.get('fields',[]):
        if f['label']:fields.setdefault(f['label'].strip().lower(),f['values'])
    def value(label,fallback):return '\n'.join(fields.get(label,[])) or d.get(fallback,'')
    return dict(jd_description=d['jd'],location=value('locations','locations'),
                employment_type=value('time type','time_type'),published_at=value('posted on','posted_on'),
                requisition_id=value('job requisition id','requisition'),detail_fields=fields,
                detail_state='COLLECTED_DETAIL_DOM',jd_read_at=now(),detail_read_error='')


def pending_details(jobs, limit, refresh=False):
    """The detail budget counts reads, not already-completed list positions."""
    if limit < 1:raise ValueError('limit must be positive')
    return [j for j in jobs if refresh or not j.get('jd_description')][:limit]


def choose_location_option(options, label):
    """Choose an actually rendered country/city checkbox, never invent its ID."""
    clean=lambda text:re.sub(r'\s*\([\d,]+\)\s*$','',str(text or '')).strip()
    matches=[o for o in options if o.get('type')=='checkbox' and not o.get('disabled') and clean(o.get('label'))==label]
    if len(matches)!=1 or not matches[0].get('id'):
        raise ValueError('Location label missing/ambiguous in current DOM: '+label)
    return matches[0]


def apply_location_filter(proxy, config, out, before):
    rule=config.get('location_filter')
    if not rule:return None
    label=rule['label'];apply_text=rule['apply_text']
    if not re.fullmatch(r'View Jobs|查看(?:职位|职务|工作)|显示(?:职位|职务|工作)',apply_text):raise ValueError('Unsupported filter confirmation label')
    write(out/'location-filter-progress.json',dict(stage='OPENING_FILTER',label=label,updated_at=now()))
    proxy.ele(rule['opener_selector'],timeout=3).click(by_js=False)
    js='''return [...document.querySelectorAll('input[type="checkbox"]')].filter(e=>e.getClientRects().length).map(e=>({type:e.type,id:e.id,label:[...(e.labels||[])].map(l=>l.innerText).join(' '),disabled:e.disabled,checked:e.checked}));'''
    options=wait_stable(lambda:proxy.read_js(js),lambda options:any(re.sub(r'\s*\([\d,]+\)\s*$','',o['label']).strip()==label for o in options))
    selected=choose_location_option(options,label)
    write(out/'location-options.json',dict(label=label,options=options,selected=selected))
    write(out/'location-filter-progress.json',dict(stage='SELECTING_OBSERVED_CHECKBOX',label=label,observed_id=selected['id'],updated_at=now()))
    escaped=proxy.read_js('return CSS.escape(arguments[0]);',selected['id'])
    if not selected['checked']:proxy.ele('css:input#'+escaped,timeout=3).click(by_js=False)
    checked=proxy.read_js('return document.getElementById(arguments[0])?.checked===true;',selected['id'])
    if not checked:raise ValueError('Native location checkbox did not become checked')
    write(out/'location-filter-progress.json',dict(stage='CONFIRMING_FILTER',label=label,observed_id=selected['id'],updated_at=now()))
    proxy.ele('xpath://button[normalize-space(.)="'+apply_text+'"]',timeout=3).click(by_js=False)
    def applied(data):
        params=parse_qs(urlsplit(data['url']).query)
        changed=([r['url'] for r in data['rows']]!=[r['url'] for r in before['rows']] or data['total_text']!=before['total_text'])
        return valid_list(data,config,config['_keyword']) and any(selected['id'] in values for values in params.values()) and changed
    result=wait_stable(lambda:proxy.read_js(LIST_JS),applied)
    write(out/'location-filter-evidence.json',dict(label=label,observed_checkbox_id=selected['id'],checked=True,actual_url=result['url'],before_total=before['total_text'],after_total=result['total_text'],visible_jobs=len(result['rows'])))
    write(out/'location-filter-progress.json',dict(stage='VERIFIED',label=label,updated_at=now()))
    result['_selected_location_id']=selected['id']
    return result


def collect(run_dir,output,keyword='',max_pages=1,limit=3,refresh=False):
    if max_pages<1 or limit<1:raise ValueError('max_pages 和 limit 必须为正数')
    root=Path(run_dir);out=Path(output);c=validate_config(read(root/'workday-dom.json'))
    with writer_lock(root):
        out.mkdir(parents=True,exist_ok=True)
        receipt=dict(config=c,keyword=keyword,max_pages=max_pages,limit=limit)
        if (out/'collection-config.json').exists() and read(out/'collection-config.json')!=receipt:
            raise ValueError('批次配置改变，请使用新输出目录')
        write(out/'collection-config.json',receipt)
        old={j['job_key']:j for j in read(out/'jobs.json')} if (out/'jobs.json').exists() else {}
        jobs={};pages=[];total=None;list_complete=False;error='';g=None
        def save(status):
            active=list(jobs.values());archived=[dict(j,in_current_view=False) for k,j in old.items() if k not in jobs]
            write(out/'jobs.json',active+archived)
            export({'todos':active,'archived_todos':archived},out/'jobs.csv',FIELDS)
            write(out/'coverage.json',dict(status=status,keyword=keyword,pages=len(pages),reported_total=total,
                list_unique=len(jobs),with_jd=sum(bool(j.get('jd_description')) for j in active),
                list_complete=list_complete,error=error,checked_at=now(),
                pause_reason=g.runtime.load().get('pause_reason') if g else None,
                location_filter=c.get('location_filter'),selected_location_id=c.get('_selected_location_id'),
                scope='当前关键词与列表URL筛选；详情受limit限制；不代表全公司/全部国家'))
        save('STARTING')
        try:
            m=read(root/'browser.json');p=connect_owned(root,int(m['address'].split(':')[-1]))
            p.set.load_mode.normal()
            pace=c.get('pacing',{});g=WorkdayGuard(root,'workday',pace.get('delay_min',30),pace.get('delay_max',45),batch_size=pace.get('batch_size',5),batch_rest=pace.get('batch_rest',90))
            g.ensure_can_resume();g.check(p,force=True);q=Proxy(p,g)
            q.get(c['list_url'],timeout=30,retry=0)
            if c.get('access_mode')!='public':g.preflight(p)
            g.runtime.start('search')
            save('SEARCHING')
            box=q.ele('css:input[data-automation-id="keywordSearchInput"]',timeout=30)
            if not box:raise ValueError('未识别 Workday 搜索框')
            box.input(keyword,clear=True);q.ele('css:button[data-automation-id="keywordSearchButton"]').click()
            if c.get('location_filter'):
                initial=wait_stable(lambda:q.read_js(LIST_JS),lambda d:valid_list(d,c,keyword))
                save('APPLYING_LOCATION_FILTER')
                filtered=apply_location_filter(q,dict(c,_keyword=keyword),out,initial)
                c=dict(c,_selected_location_id=filtered['_selected_location_id'])
            previous=None
            for number in range(1,max_pages+1):
                def list_snapshot():
                    if c.get('access_mode')!='public':g.preflight(p)
                    return q.read_js(LIST_JS)
                data=wait_stable(list_snapshot,lambda d:valid_list(d,c,keyword) and [r['url'] for r in d['rows']]!=previous)
                count=total_count(data)
                if pages and count!=total:raise ValueError('结果总数发生变化，请重开批次核查')
                total=count;previous=[r['url'] for r in data['rows']]
                write(out/f'page-{number}.json',data);pages.append(previous)
                new=0
                for row in data['rows']:
                    jid=identity(row['url'],c);key='workday:'+site_parts(c['list_url'])[0]+':'+site_parts(c['list_url'])[1][0]+':'+jid
                    if key in jobs:continue
                    new+=1;job=dict(old.get(key,{}));job.update(source='workday',job_key=key,job_id=jid,company=c['company'],company_source='task_verified_site_configuration',job_name=row['title'].strip(),job_url=row['url'],list_summary=row.get('summary',''),search_keyword=keyword,in_current_view=True)
                    job.setdefault('jd_description','');job.setdefault('detail_state','PENDING');jobs[key]=job
                if not new and total!=0:raise ValueError('重复页，停止而非假定已到底')
                list_complete=total is not None and len(jobs)==total
                save('COLLECTING_LIST')
                if list_complete or number==max_pages:break
                selector=c.get('next_selector')
                if not selector:raise ValueError('分页未配置，已保存当前列表')
                nxt=q.ele(selector,timeout=2)
                if not nxt or not nxt.states.is_enabled:raise ValueError('下一页不可用但数量尚未核齐')
                nxt.click()
            for job in pending_details(list(jobs.values()),limit,refresh):
                g.runtime.commit(checkpoint={'phase':'detail','job_key':job['job_key']})
                try:
                    q.get(job['job_url'],timeout=30,retry=0)
                    if c.get('access_mode')!='public':g.preflight(p)
                    def valid(d):
                        try:accept_detail(job,d,c);return True
                        except (ValueError,KeyError):return False
                    def detail_snapshot():
                        if c.get('access_mode')!='public':g.preflight(p)
                        return q.read_js(DETAIL_JS)
                    d=wait_stable(detail_snapshot,valid)
                    values=accept_detail(job,d,c)
                    if job.get('jd_description') and job['jd_description']!=values['jd_description']:
                        job.setdefault('jd_history',[]).append(dict(description=job['jd_description'],read_at=job.get('jd_read_at')))
                    job.update(values);write(out/(job['job_id']+'.json'),d)
                except (UserActionRequired,StopSite):
                    # A human checkpoint or an uninspectable page stops this site;
                    # never continue to the next job as if only one JD failed.
                    raise
                except Exception as exc:job['detail_read_error']=type(exc).__name__+': '+str(exc)
                save('COLLECTING_DETAILS')
            save('PARTIAL_ERROR' if any(j.get('detail_read_error') for j in jobs.values()) else 'COMPLETE' if list_complete and all(j.get('jd_description') for j in jobs.values()) else 'PARTIAL_BUDGET')
            g.runtime.commit(task_state='BUDGET_COMPLETED',checkpoint={'pages':len(pages),'jobs':len(jobs)})
        except (UserActionRequired,StopSite) as exc:
            error=type(exc).__name__+': '+str(exc)
            if g and g.runtime.load().get('task_state')!='PAUSED':
                g.runtime.pause('READ_ERROR',message=error)
            save('PAUSED_SITE');raise
        except Exception as exc:
            error=type(exc).__name__+': '+str(exc)
            if g and g.runtime.load().get('task_state')!='PAUSED':
                g.runtime.pause('READ_ERROR',message=error)
            save('READ_ERROR');raise
        except BaseException as exc:
            error=type(exc).__name__+': '+str(exc);save('INTERRUPTED');raise
        return read(out/'coverage.json')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir',required=True);p.add_argument('--output',required=True)
    p.add_argument('--keyword',default='');p.add_argument('--max-pages',type=int,default=1)
    p.add_argument('--limit',type=int,default=3);p.add_argument('--refresh',action='store_true')
    a=p.parse_args();print(collect(a.run_dir,a.output,a.keyword,a.max_pages,a.limit,a.refresh))
