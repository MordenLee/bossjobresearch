"""DP collector for verified legacy Beisen /zpdetail/<id> templates.

Public-page access requires explicit task authorization. No HTTP API requests,
login automation, or application actions. Other Beisen templates need adapters.
"""
import argparse
import re
from pathlib import Path
from urllib.parse import urlsplit
from collect import Guard, Proxy
from session import connect_owned
from runtime import writer_lock
from moka_dom import wait_stable, usable
from workflow import read, write, now
from export_csv import export, JOB_FIELDS

LIST_JS = '''return {url:location.href,rows:[...document.querySelectorAll('tr')].map(tr=>{const a=tr.querySelector('a[href*="/zpdetail/"]');return a?{url:a.href,title:a.innerText,cells:[...tr.querySelectorAll('td')].map(e=>e.innerText)}:null;}).filter(Boolean)};'''
DETAIL_JS = '''return {url:location.href,title:document.querySelector('.applyStatus')?.parentElement.innerText||'',jd:document.querySelector('.xiangqingtext')?.innerText||'',info:[...document.querySelectorAll('.xiangqinglist')].map(e=>e.innerText),sections:[...document.querySelectorAll('.xiangqingtext p.title')].map(e=>({label:e.innerText,text:e.nextElementSibling?.innerText||''}))};'''
LABELS = ('招聘类别','工作性质','薪资范围','招聘人数','发布时间','截止时间','工作地点')
FIELDS = JOB_FIELDS + ['responsibilities','qualifications','employment_type',
                       'published_at','deadline','recruitment_type','headcount','function']


def origin(url):
    p=urlsplit(url)
    return f'{p.scheme}://{p.netloc}'


def identity(url, host):
    p=urlsplit(url)
    if origin(url)!=host or p.scheme!='https' or p.username or p.password:
        raise ValueError('北森详情域名不匹配')
    m=re.fullmatch(r'/zpdetail/(\d+)/?',p.path)
    if not m:raise ValueError('缺少北森稳定岗位 ID')
    return m[1]


def title_key(text):
    text=re.sub(r'\s*\((?:未申请|已申请)\)\s*$','',text.strip())
    return re.sub(r'\s+','',text)


def parse_fields(lines):
    text=' '.join(lines)
    pattern='|'.join(map(re.escape,LABELS))
    matches=list(re.finditer(r'('+pattern+r')\s*[：:]',text))
    return {m[1]:text[m.end():matches[i+1].start() if i+1<len(matches) else len(text)].strip()
            for i,m in enumerate(matches)}


def accept_detail(job, data, host):
    if identity(data['url'],host)!=identity(job['job_url'],host):
        raise ValueError('北森岗位 ID 不匹配')
    if title_key(data['title'])!=title_key(job['job_name']):
        raise ValueError('北森岗位标题不匹配')
    if not usable(data['jd']):raise ValueError('正文为空或仍是占位内容')
    sections={s['label'].strip().rstrip('：:'):s['text'].strip() for s in data['sections']}
    fields=parse_fields(data['info'])
    values=dict(jd_description=data['jd'],responsibilities=sections.get('工作职责',''),
                qualifications=sections.get('任职资格',''),detail_state='COLLECTED_DETAIL_DOM',
                jd_read_at=now(),detail_read_error='',detail_fields=fields)
    for label,key in [('工作地点','location'),('薪资范围','salary'),('工作性质','employment_type'),
                      ('发布时间','published_at'),('截止时间','deadline'),
                      ('招聘类别','recruitment_type'),('招聘人数','headcount')]:
        values[key]=fields.get(label,'')
    return values


def validate_config(config):
    host=config['host'];p=urlsplit(host)
    if p.scheme!='https' or not p.hostname or not p.hostname.endswith('.zhiye.com') or host!=origin(host) or p.path:
        raise ValueError('配置需精确北森 HTTPS origin')
    if origin(config['list_url'])!=host or not config.get('tenant') or not config.get('company'):
        raise ValueError('列表域名、租户或公司配置无效')
    auth=config.get('public_access',{})
    if auth.get('authorized') is not True or not auth.get('evidence','').strip():
        raise ValueError('公开采集需保存真实用户授权；不会默认跳过登录')
    return config


def collect(run_dir, output, *, limit=None, refresh=False):
    root=Path(run_dir);out=Path(output)
    if limit is not None and limit<1:raise ValueError('limit must be positive')
    config=validate_config(read(root/'beisen-dom.json'))
    with writer_lock(root):
        out.mkdir(parents=True,exist_ok=True)
        receipt=out/'collection-config.json'
        if receipt.exists() and read(receipt)!=config:raise ValueError('输出目录配置已改变，请使用新批次')
        write(receipt,config)
        manifest=read(root/'browser.json')
        page=connect_owned(root,int(manifest['address'].split(':')[-1]))
        pacing=config.get('pacing',{})
        guard=Guard(root,'beisen',pacing.get('delay_min',30),pacing.get('delay_max',45),
                    batch_size=pacing.get('batch_size',5),batch_rest=pacing.get('batch_rest',90))
        guard.ensure_can_resume();guard.check(page,force=True);guard.runtime.start('search')
        proxy=Proxy(page,guard)
        if page.url!=config['list_url']:proxy.get(config['list_url'])
        data=wait_stable(lambda:proxy.read_js(LIST_JS),lambda d:origin(d['url'])==config['host'] and bool(d['rows']))
        write(out/'search-evidence.json',data)
        previous={j['job_key']:j for j in read(out/'jobs.json')} if (out/'jobs.json').exists() else {}
        active=[];seen=set();selected=data['rows'][:limit] if limit else data['rows']
        for row in selected:
            job_id=identity(row['url'],config['host']);key='beisen:'+config['tenant']+':'+job_id
            if key in seen:continue
            seen.add(key)
            job=dict(previous.get(key,{}))
            job.update(source='beisen',job_key=key,company=config['company'],
                       company_source='task_verified_site_configuration',job_name=row['title'].strip(),
                       job_url=row['url'],in_current_view=True)
            if not job.get('jd_description'):
                job.update(location=row['cells'][2].strip() if len(row['cells'])>2 else '',
                           function=row['cells'][1].strip() if len(row['cells'])>1 else '',
                           jd_description='',detail_state='PENDING')
            active.append(job)
        archived=[dict(j,in_current_view=False) for k,j in previous.items() if k not in seen]
        def save(status):
            write(out/'jobs.json',active+archived)
            export({'todos':active,'archived_todos':archived},out/'jobs.csv',FIELDS)
            write(out/'coverage.json',dict(status=status,visible_list_jobs=len(data['rows']),
                selected_unique=len(active),with_jd=sum(bool(j.get('jd_description')) for j in active),
                pending=sum(not j.get('jd_description') for j in active),
                scope='当前已加载表格列表；未自动翻页，不代表北森全站',checked_at=now()))
        save('RUNNING')
        try:
            for job in active:
                if job.get('jd_description') and not refresh:continue
                guard.runtime.commit(checkpoint={'phase':'detail','job_key':job['job_key']})
                try:
                    if page.url!=job['job_url']:proxy.get(job['job_url'])
                    def valid(d):
                        try:return identity(d['url'],config['host'])==identity(job['job_url'],config['host']) and title_key(d['title'])==title_key(job['job_name']) and usable(d['jd'])
                        except ValueError:return False
                    detail=wait_stable(lambda:proxy.read_js(DETAIL_JS),valid)
                    values=accept_detail(job,detail,config['host'])
                    if refresh and job.get('jd_description') and job['jd_description']!=values['jd_description']:
                        job.setdefault('jd_history',[]).append({'description':job['jd_description'],'read_at':job.get('jd_read_at')})
                    job.update(values)
                    write(out/(identity(job['job_url'],config['host'])+'.json'),detail)
                except Exception as exc:
                    job['detail_read_error']=type(exc).__name__+': '+str(exc)
                    if not job.get('jd_description'):job['detail_state']='PENDING'
                save('RUNNING')
        except BaseException:
            save('INTERRUPTED')
            raise
        status='VISIBLE_LIST_COLLECTED' if all(j.get('jd_description') for j in active) else 'PARTIAL'
        save(status)
        guard.runtime.commit(task_state='BUDGET_COMPLETED',checkpoint={'selected':len(active),'coverage_status':status})
        return read(out/'coverage.json')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir',required=True);p.add_argument('--output',required=True)
    p.add_argument('--limit',type=int);p.add_argument('--refresh',action='store_true')
    args=p.parse_args();print(collect(args.run_dir,args.output,limit=args.limit,refresh=args.refresh))
