"""Read one already opened Huawei JD through DP, retaining source-ID limitations.

This is a detail reader, not a completed Huawei search/pagination adapter.
"""
import argparse,hashlib,json
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from collect import Guard,Proxy
from session import connect_owned
from independent_dom import wait_snapshot,normalize
from runtime import writer_lock
from workflow import read,write,now

DETAIL=r'''return {url:location.href,title:document.querySelector('.details-container .job-name')?.innerText.trim()||'',
 description:[...document.querySelectorAll('.details-container > .details-item')].filter(e=>['岗位职责','岗位要求'].includes(e.querySelector('.item-title')?.innerText.trim())).map(e=>e.innerText.trim()).join(String.fromCharCode(10,10)),
 location:document.querySelector('.details-container .locations')?.innerText.trim()||'',
 team:document.querySelector('.details-container .onboarding-department')?.innerText.trim()||'',
 experience:document.querySelector('.details-container .work-year')?.innerText.trim()||'',
 function:document.querySelector('.details-container .position-type')?.innerText.trim()||'',
 updated_at:document.querySelector('.details-container .update-time span:last-child')?.innerText.trim()||'',
 company_evidence:(document.body.innerText.match(/版权所有\s*©\s*华为技术有限公司[^\n]*/)||[])[0]||''};'''


def detail_id(url):
    u=urlsplit(url);ids=parse_qs(u.query).get('advertisementId',[])
    if u.scheme!='https' or u.hostname!='career.huawei.com' or u.path!='/cn/job-details' or len(ids)!=1 or not ids[0].isdigit():
        raise ValueError('Unexpected Huawei detail origin/path/ID')
    return ids[0]


def validate(d,source,navigation):
    if detail_id(d['url'])!=detail_id(navigation['url']):raise ValueError('Observed navigation ID mismatch')
    if normalize(d['title'])!=normalize(source['title']) or normalize(d['title'])!=normalize(navigation['source_title']):
        raise ValueError('Source title mismatch')
    if '华为技术有限公司' not in d['company_evidence']:raise ValueError('Company evidence missing')
    if len(d['description'])<80 or not all(x in d['description'] for x in ('岗位职责','岗位要求')):
        raise ValueError('Complete duty/requirement sections missing')
    metadata=normalize(source['location'])
    for field in ('location','team','experience','function'):
        if not d[field] or normalize(d[field]) not in metadata:raise ValueError('Source metadata mismatch: '+field)


def collect(run_dir,source_path,navigation_path,output):
    root=Path(run_dir).resolve();out=Path(output)
    with writer_lock(root),writer_lock(out):
        if read(root/'public-access.json').get('authorized') is not True:raise ValueError('Public-page permission required')
        source=read(source_path);nav=read(navigation_path);m=read(root/'browser.json')
        p=connect_owned(root,int(m['address'].split(':')[-1]));g=Guard(root,'independent',30,45,batch_size=5,batch_rest=90)
        g.ensure_can_resume();g.check(p,force=True);g.runtime.start('detail');q=Proxy(p,g)
        try:
            d=wait_snapshot(q,{},reader=DETAIL,validator=lambda value,unused:validate(value,source,nav))
        except Exception as e:
            write(out/'read-error.json',{'error_type':type(e).__name__,'error':str(e),'url':str(p.url),'at':now()})
            g.runtime.pause('READ_ERROR',url=str(p.url),message=str(e));raise
        ident=detail_id(d['url']);key='huawei:'+ident
        old=read(out/'jobs.json') if (out/'jobs.json').exists() else []
        if old and any(j['job_key']!=key for j in old):raise ValueError('Use separate output for another detail sample')
        job={k:v for k,v in (old[0] if old else {}).items() if k.startswith('user_')}
        job.update(source='huawei',job_key=key,company='华为',job_name=d['title'],job_url=d['url'],
                   source_id='',detail_job_id=ident,location=d['location'],team=d['team'],function=d['function'],
                   experience=d['experience'],updated_at=d['updated_at'],salary='',education='',published_at='',
                   jd_description=d['description'],jd_hash=hashlib.sha256(d['description'].encode()).hexdigest(),jd_read_at=now(),
                   company_evidence=d['company_evidence'],detail_state='COLLECTED_DETAIL_DOM',
                   identity_evidence={'mode':'source_card_title_metadata_and_observed_click_navigation','source_dom_job_id_available':False,
                                      'source_index':source['index'],'navigation':nav},
                   review_readiness='PENDING: source DOM job ID unavailable; not automatically READY')
        history=old[0].get('description_history',[]) if old else []
        if old and old[0]['jd_description']!=d['description']:
            history=history+[{'description':old[0]['jd_description'],'read_at':old[0].get('jd_read_at')}]
        if history:job['description_history']=history
        write(out/'detail-evidence.json',d);write(out/'source-card.json',source);write(out/'jobs.json',[job])
        coverage={'site':'huawei','reader_scope':'one already-open detail; search and pagination not implemented',
                  'with_jd':1,'full_site_adapter_verified':False,'checked_at':now(),'source_dom_job_id_available':False}
        write(out/'coverage.json',coverage);g.runtime.commit(task_state='BUDGET_COMPLETED',checkpoint=coverage)
        return coverage


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--run-dir',required=True);ap.add_argument('--source-card',required=True);ap.add_argument('--navigation',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();print(json.dumps(collect(a.run_dir,a.source_card,a.navigation,a.output),ensure_ascii=False))
