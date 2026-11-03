"""DeepSeek public careers search -> verified Moka detail pages via DP."""
import argparse
import re
from pathlib import Path
from urllib.parse import urlsplit
from collect import Guard, Proxy
from session import connect_owned
from moka_dom import wait_stable, usable
from workflow import read, write, now
from export_csv import export, JOB_FIELDS

SEARCH = '''return {url:location.href,keyword:[...document.querySelectorAll('input[placeholder="搜索职位关键词"]')].filter(e=>e.getClientRects().length).map(e=>e.value)[0], text:document.body.innerText,rows:[...document.querySelectorAll('a[href*="app.mokahr.com/"]')].map(e=>({url:e.href,lines:e.innerText.split('\\n').map(s=>s.trim()).filter(Boolean)}))};'''
DETAIL = '''return {url:location.href,title:document.querySelector('div[class^="title-"]')?.innerText||'',jd:[...document.querySelectorAll('div[class^="job-description-"]')].map(e=>e.innerText).find(t=>t.trim()&&t.trim()!=='暂无')||'',fields:[...document.querySelectorAll('[class*="info-row-"]')].map(e=>({label:e.querySelector('.no-adaptive-tooltip')?.innerText,value:e.querySelector('[id="clamp-container"]')?.innerText||e.querySelector('[class*="value-"]')?.innerText})),text:document.body.innerText};'''

def identity(url):
    p=urlsplit(url)
    if p.scheme!='https' or p.hostname!='app.mokahr.com' or p.path.rstrip('/')!='/social-recruitment/high-flyer/140576':
        raise ValueError('Detail origin/tenant mismatch')
    m=re.fullmatch(r'/job/([A-Za-z0-9-]+)',p.fragment)
    if not m:raise ValueError('Missing stable Moka job ID')
    return m[1]

def normalized(s):return re.sub(r'\s+','',s)

def collect(run_dir,keyword,output):
    root=Path(run_dir);auth=read(root/'public-access.json')
    if auth.get('authorized') is not True:raise ValueError('Explicit task public-page authorization required')
    manifest=read(root/'browser.json');p=connect_owned(root,int(manifest['address'].split(':')[-1]))
    g=Guard(root,'moka',30,45,batch_size=5,batch_rest=90);g.ensure_can_resume();g.check(p,force=True);g.runtime.start('search');q=Proxy(p,g)
    if urlsplit(p.url).hostname!='talent.deepseek.com':raise ValueError('Start on official DeepSeek careers page')
    box=next(e for e in q.eles('css:input[placeholder="搜索职位关键词"]') if e.states.is_displayed)
    if box.attr('value')!=keyword:g.delay();box.input(keyword+'\n',clear=True)
    search=wait_stable(lambda:q.read_js(SEARCH),lambda d:d['keyword']==keyword and bool(d['rows']))
    out=Path(output);out.mkdir(parents=True,exist_ok=True);write(out/'search-evidence.json',search)
    company=re.search(r'©\s*\d{4}\s+(.+?)\s*版权所有',search['text'])
    if not company:raise ValueError('Company identity missing')
    jobs=[];seen=set()
    for row in search['rows']:
        job_id=identity(row['url'])
        if job_id in seen:continue
        seen.add(job_id);lines=row['lines']
        job=dict(source='deepseek',job_key='deepseek:'+job_id,company=company[1],job_name=lines[0],function=lines[1] if len(lines)>1 else '',location=lines[2] if len(lines)>2 else '',job_url=row['url'],keyword=keyword,jd_description='',detail_state='PENDING',detail_read_error='',salary='',experience='',education='',jd_read_at='')
        jobs.append(job);write(out/'jobs.json',jobs)
        g.runtime.commit(checkpoint={'job_key':job['job_key'],'phase':'detail'});q.get(row['url'])
        try:
            d=wait_stable(lambda:q.read_js(DETAIL),lambda d:identity(d['url'])==job_id and normalized(d['title'])==normalized(job['job_name']) and usable(d['jd']))
            fields={x['label']:x['value'] for x in d['fields'] if x.get('label')}
            job.update(jd_description=d['jd'],detail_state='COLLECTED_DETAIL_DOM',jd_read_at=now(),detail_fields=fields)
            for label,key in [('工作地点','location'),('职能类型','function'),('职位性质','employment_type'),('薪资范围','salary'),('发布日期','published_at')]:
                if fields.get(label) not in (None,'','-'):job[key]=fields[label]
            write(out/(job_id+'.json'),d)
        except (TimeoutError,ValueError) as exc:job['detail_read_error']=str(exc)
        write(out/'jobs.json',jobs)
    export({'todos':jobs},out/'jobs.csv',JOB_FIELDS+['function','employment_type','published_at'])
    coverage=dict(keyword=keyword,search_rows=len(search['rows']),unique_jobs=len(jobs),with_jd=sum(bool(j['jd_description']) for j in jobs),pending=sum(not j['jd_description'] for j in jobs),scope='Official visible keyword search results',checked_at=now())
    write(out/'coverage.json',coverage);g.runtime.commit(task_state='BUDGET_COMPLETED',checkpoint=coverage)
    return coverage

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run-dir',required=True);p.add_argument('--keyword',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    print(collect(a.run_dir,a.keyword,a.output))
