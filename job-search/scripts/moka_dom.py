"""Moka DOM search collector; login required, no HTTP or application actions."""
import argparse
import time
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from ats import Site
from ats_browser import LoginSession
from workflow import read, write, now
from export_csv import export, JOB_FIELDS

PLACEHOLDERS = {'', '暂无', '加载中', '加载中...', '暂无职位描述'}

def wait_stable(reader, valid, timeout=60, interval=2, stable_seconds=6,
                clock=time.monotonic, sleep=time.sleep):
    """Placeholder and stale identity never become a confirmed empty JD."""
    end = clock() + timeout
    previous = None
    since = clock()
    while clock() < end:
        try:
            value = reader()
        except Exception as exc:
            if type(exc).__name__ != 'ContextLostError':
                raise
            previous, since = None, clock()
            sleep(interval)
            continue
        if valid(value):
            if value != previous:
                previous, since = value, clock()
            elif clock() - since >= stable_seconds:
                return value
        else:
            previous, since = None, clock()
        sleep(interval)
    raise TimeoutError('页面在等待窗口内未提供身份匹配且稳定的正文/结果；保留待补读')

SNAPSHOT = r'''return {
 url:location.href,
 total:((document.body?.innerText||'').match(/(\d+)\s*(?:结果|results?\b)/i)||[])[1]??null,
 empty_result_text:(document.body?.innerText||'').split('\n').map(s=>s.trim()).find(s=>s==='尚无任何相关职位')||null,
 company:((document.body?.innerText||'').match(/©[^\n]*?([\u4e00-\u9fff]+有限公司)/)||[])[1]||'',
 rows:[...document.querySelectorAll('a[href^="#/job/"]')]
 .filter(a=>a.querySelector('[class^="card-content-"]')||(/^\/jobs(?:\/|\?|$)/.test(location.hash.slice(1))&&a.querySelector('[class^="title-"]'))).map(a=>({
 url:a.href,title:(()=>{const exact=a.querySelector('[class^="title-wrapper-"][title]')?.getAttribute('title');if(exact)return exact;const node=a.querySelector('[class^="title-"]')?.cloneNode(true);node?.querySelectorAll('[class^="prior-"]').forEach(e=>e.remove());return node?.textContent.trim()||'';})(),
 metadata:[...a.querySelectorAll(a.querySelector('[class^="info-"] .no-adaptive-tooltip')?'[class^="info-"] .no-adaptive-tooltip':'[class^="status-item-"]')].map(e=>e.innerText).filter(Boolean),
 published_at:a.querySelector('[class^="published-at-"]')?.innerText||'',
 jd:a.querySelector('[class^="job-description-"]')?.innerText||''
 }))};'''
DETAIL = r'''return {url:location.href,
 text:document.body.innerText,
 jd:[...document.querySelectorAll('[class^="job-description-"]')].map(e=>e.innerText).find(t=>t.trim() && t.trim()!=='暂无')||''};'''

def usable(text):
    return text.strip() not in PLACEHOLDERS

def search(session, keyword):
    q = session.page
    session.check('search')
    target = session.site.host + session.tenant_path + '#/jobs/'
    if '/jobs/' not in urlsplit(session.raw.url).fragment:
        q.get(target)
    box=q.ele('css:input[placeholder="搜索职位关键词"]')
    box.input(keyword+'\n', clear=True)
    def snapshot():
        session.check('search')
        return q.read_js(SNAPSHOT)
    def valid(d):
        query=parse_qs(urlsplit(d['url']).fragment.partition('?')[2])
        return (query.get('keyword')==[keyword] and d['total'] is not None
                and (bool(d['rows']) or d['total']=='0'))
    return wait_stable(snapshot, valid)

def collect_search(session, keyword, output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    data=search(session,keyword)
    write(output/'search-evidence.json',data)
    # Do not silently claim a partial page is complete.
    rows=data['rows']; total=int(data['total']); seen=set(); jobs=[]
    while True:
        for row in rows:
            if row['url'] in seen: continue
            seen.add(row['url'])
            meta=list(dict.fromkeys(row['metadata']))
            job=dict(source='moka',job_key='moka:'+session.site.tenant+':'+row['url'].split('/job/')[-1],
                company=data['company'],job_name=row['title'],job_url=row['url'],
                location=' | '.join(meta[2:]),employment_type=meta[0] if meta else '',
                function=meta[1] if len(meta)>1 else '',published_at=row['published_at'],
                keyword=keyword,jd_description=row['jd'] if usable(row['jd']) else '',
                detail_state='COLLECTED_LIST_DOM' if usable(row['jd']) else 'PENDING',
                salary='',experience='',education='',jd_read_at=now(),detail_read_error='')
            jobs.append(job)
        write(output/'jobs.json',jobs)
        if len(seen)>=total: break
        previous=[x['url'] for x in rows]
        session.page.ele('css:button[class*="Pagination-forward-"]').click()
        data=wait_stable(lambda:session.page.read_js(SNAPSHOT),lambda d:
            d['total'] is not None and [x['url'] for x in d['rows']]!=previous)
        session.check('search')
        if int(data['total'])!=total: raise ValueError('搜索总数改变，需重新核对覆盖')
        rows=data['rows']
        if all(x['url'] in seen for x in rows): raise ValueError('翻页未增加岗位')
    for job in jobs:
        if job['jd_description']: continue
        session.page.get(job['job_url'])
        def snapshot():
            session.check('detail')
            return session.page.read_js(DETAIL)
        try:
            d=wait_stable(snapshot,lambda d:d['url']==job['job_url'] and
                job['job_name'] in d['text'] and usable(d['jd']))
            job.update(jd_description=d['jd'],detail_state='COLLECTED_DETAIL_DOM',jd_read_at=now())
            write(output/(job['job_key'].split(':')[-1]+'.json'),d)
        except TimeoutError as exc:
            job.update(detail_state='PENDING',detail_read_error=str(exc))
        write(output/'jobs.json',jobs)
    fields=JOB_FIELDS+['employment_type','function','published_at']
    export({'todos':jobs},output/'jobs.csv',fields)
    coverage=dict(keyword=keyword,platform_total=total,collected=len(jobs),
        with_jd=sum(bool(j['jd_description']) for j in jobs),
        pending=sum(not j['jd_description'] for j in jobs),
        status='COMPLETE' if len(jobs)==total and all(j['jd_description'] for j in jobs) else 'PARTIAL',
        checked_at=now())
    write(output/'coverage.json',coverage)
    return coverage

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run-dir',required=True);p.add_argument('--keyword',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();root=Path(a.run_dir);cfg=read(root/'runtime-config.json');m=read(root/'browser.json')
    path=cfg['session']['tenant_path'];parts=path.strip('/').split('/')
    site=Site(platform='moka',tenant=parts[-2],company=cfg['company'],host='https://'+cfg['session']['hosts'][0],org_id=parts[-2],site_id=int(parts[-1]))
    s=LoginSession(site,root,int(m['address'].split(':')[-1]))
    print(collect_search(s,a.keyword,a.output))
