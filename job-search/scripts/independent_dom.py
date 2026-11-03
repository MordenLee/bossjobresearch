"""DP-only collection for independently verified corporate career DOM profiles.

Profiles describe observed selectors, not guessed APIs. Unknown fields stay empty.
Reading a page does not imply that its JD or the whole company was covered.
"""
import argparse
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from collect import Guard, Proxy
from session import connect_owned
from workflow import read, write, now
from runtime import writer_lock


SNAPSHOT = r'''const c=arguments[0];
const text=(root,selector)=>{const e=root.querySelector(selector);return e?e.innerText.trim():'';};
return {url:location.href,title:document.title,company_evidence:text(document,c.company_selector),
 keyword:c.search_selector?document.querySelector(c.search_selector)?.value:null,
 search_visible:c.search_selector?!!document.querySelector(c.search_selector)?.getClientRects().length:false,
 page:c.active_page_selector?text(document,c.active_page_selector):null,
 rows:[...document.querySelectorAll(c.card_selector)].map(e=>{
 const idNode=c.id_selector?e.querySelector(c.id_selector):e;
 const rawId=idNode?.getAttribute(c.id_attribute||'id')||'';
 const id=c.id_pattern?(rawId.match(new RegExp(c.id_pattern))?.[1]||''):rawId;
 return {
 id:id,title:text(e,c.title_selector),
 description:text(e,c.description_selector),location:c.location_selector?text(e,c.location_selector):'',
 description_visible:!!e.querySelector(c.description_selector)?.getClientRects().length,
 url:c.job_url_scope==='list_page'?location.href:(c.link_self?e.href:(c.link_selector?(e.querySelector(c.link_selector)?.href||''):location.href.split('#')[0]+'#'+id)),
 fields:Object.fromEntries(Object.entries(c.list_fields||{}).map(([k,s])=>[k,text(e,s)])),
 raw_text:e.innerText
};}),text:document.body.innerText};'''

LINK_LIST = r'''const c=arguments[0];return {url:location.href,title:document.title,
 keyword:c.search_selector?document.querySelector(c.search_selector)?.value:null,
 search_visible:c.search_selector?!!document.querySelector(c.search_selector)?.getClientRects().length:false,
 page:c.active_page_selector?document.querySelector(c.active_page_selector)?.getAttribute(c.page_attribute||'data-page'):null,
 company_evidence:document.querySelector(c.company_selector)?.innerText||'',text:document.body.innerText,
 rows:[...document.querySelectorAll(c.card_selector)].map(e=>{const a=c.link_selector?e.querySelector(c.link_selector):null;
 const rawId=e.getAttribute(c.id_attribute||'id')||'';const m=c.id_pattern?rawId.match(new RegExp(c.id_pattern)):null;
 const u=c.id_url_pattern?a?.href.match(new RegExp(c.id_url_pattern)):null;
 return {id:c.id_url_pattern?(u?.[1]||''):(c.id_pattern?(m?.[1]||''):rawId),title:e.querySelector(c.title_selector)?.innerText.trim()||'',
 url:a?.href||'',location:c.location_selector?(c.location_all?[...e.querySelectorAll(c.location_selector)].map(n=>n.innerText.trim()).filter(Boolean).join('; '):(e.querySelector(c.location_selector)?.innerText.trim()||'')):'',
 fields:Object.fromEntries(Object.entries(c.list_fields||{}).map(([k,s])=>[k,e.querySelector(s)?.innerText.trim()||''])),description:''};})};'''

LINK_DETAIL = r'''const c=arguments[0];return {url:location.href,title:c.detail_title_source==='document_title'?document.title.replace(new RegExp(c.detail_title_suffix||'$'),'').trim():(document.querySelector(c.detail_title_selector)?.innerText.trim()||''),
 description:c.detail_description_all?[...document.querySelectorAll(c.detail_description_selector)].map(e=>e.innerText.trim()).join(String.fromCharCode(10,10)):(document.querySelector(c.detail_description_selector)?.innerText.trim()||''),
 fields:c.detail_fields_selector?Object.fromEntries([...document.querySelectorAll(c.detail_fields_selector)].map(e=>[(e.querySelector(c.detail_field_label_selector)?.innerText.trim()||'').replace(/[：:]$/,''),e.querySelector(c.detail_field_value_selector)?.innerText.trim()||'']).filter(x=>x[0]&&x[1])):{},
 company_evidence:document.querySelector(c.company_selector)?.innerText||''};'''


def normalize(value):
    return re.sub(r'\s+', '', value or '')


def validate_snapshot(snapshot, config):
    """Reject foreign pages, empty shells, duplicate identities and missing JD evidence."""
    if urlsplit(snapshot['url']).hostname not in config['hosts']:
        raise ValueError('Unexpected page host')
    if not any(normalize(x) in normalize(snapshot.get('company_evidence'))
               for x in config['company_names']):
        raise ValueError('Company identity not present in configured visible DOM')
    rows=snapshot.get('rows') or []
    if not rows:
        raise ValueError('No observed job sections; not proof of zero jobs')
    seen=set()
    for row in rows:
        if not row.get('id') or row['id'] in seen:
            raise ValueError('Missing or duplicate DOM job identity')
        seen.add(row['id'])
        if urlsplit(row['url']).hostname not in config['hosts']:
            raise ValueError('Unexpected detail host')
        if config.get('id_url_pattern'):
            match=re.search(config['id_url_pattern'],row['url'])
            if not match or match.group(1)!=row['id']:
                raise ValueError('Inline URL job identity mismatch')
        if config.get('detail_path_pattern') and not re.fullmatch(config['detail_path_pattern'],urlsplit(row['url']).path):
            raise ValueError('Inline detail path mismatch')
        if not row.get('title'):
            raise ValueError('Job title or full description not available')
        if config.get('allow_pending_inline_descriptions'):
            continue
        if config.get('require_description_visible') and not row.get('description_visible'):
            raise ValueError('Inline description is still collapsed')
        if len(row.get('description','').strip())<40:
            raise ValueError('Job title or full description not available')
        if not all(s in row['description'] for s in config.get('required_jd_labels',[])):
            raise ValueError('Required JD sections are missing')
    return rows


def inline_expand_selector(row,config):
    if not re.fullmatch(r'[A-Za-z0-9_-]+',row['id']):
        raise ValueError('Unsafe inline identity')
    return config['card_selector']+':has('+config['id_selector']+'['+config['id_attribute']+'="'+row['id']+'"]) '+config['inline_expand_selector']


def validate_inline_expansion(snapshot,row,config):
    matches=[r for r in snapshot.get('rows',[]) if r['id']==row['id']]
    if len(matches)!=1 or matches[0]['title']!=row['title']:
        raise ValueError('Expanded inline identity mismatch')
    validate_snapshot(dict(snapshot,rows=matches),dict(config,allow_pending_inline_descriptions=False))
    return matches[0]


def validate_links(snapshot,config):
    if urlsplit(snapshot['url']).hostname not in config['hosts']:
        raise ValueError('Unexpected list host')
    if not any(normalize(x) in normalize(snapshot.get('company_evidence')) for x in config['company_names']):
        raise ValueError('List company evidence missing')
    rows=snapshot.get('rows') or [];seen=set()
    if not rows:raise ValueError('Empty list is not confirmed zero jobs')
    for row in rows:
        if not row['id'] or row['id'] in seen or not row['title']:raise ValueError('Invalid list identity')
        if config.get('layout')=='click_details':
            if not re.fullmatch(r'[A-Za-z0-9_-]+',row['id']):raise ValueError('Unsafe card identity')
        else:
            if urlsplit(row['url']).hostname not in config['hosts']:raise ValueError('Unexpected detail host')
            if not re.fullmatch(config['detail_path_pattern'],urlsplit(row['url']).path):raise ValueError('Unexpected detail path')
        seen.add(row['id'])
    return rows


def validate_search_shell(snapshot,config):
    if urlsplit(snapshot['url']).hostname not in config['hosts'] or not snapshot.get('search_visible'):
        raise ValueError('Search control is not ready on the expected site')
    if not any(normalize(x) in normalize(snapshot.get('company_evidence')) for x in config['company_names']):
        raise ValueError('Search page company evidence missing')


def validate_detail_identity(detail,row,config):
    u=urlsplit(detail['url'])
    if u.hostname not in config['hosts']:raise ValueError('Detail host identity mismatch')
    if config.get('layout')=='click_details':
        if not re.fullmatch(config['detail_path_pattern'],u.path):raise ValueError('Detail path identity mismatch')
        if parse_qs(u.query).get(config['detail_id_query'])!=[row['id']]:raise ValueError('Detail job ID mismatch')
    elif detail['url'].split('#')[0]!=row['url'].split('#')[0]:raise ValueError('Detail URL identity mismatch')
    if normalize(detail['title'])!=normalize(row['title']):raise ValueError('Detail title identity mismatch')
    if len(detail['description'])<40:raise ValueError('Detail body not ready')
    if not all(label in detail['description'] for label in config.get('required_jd_labels',[])):raise ValueError('Detail JD sections missing')
    if not any(normalize(x) in normalize(detail.get('company_evidence')) for x in config['company_names']):raise ValueError('Detail company evidence missing')


def validate_transition(previous,current,keyword=None,pagination=False):
    if [r['id'] for r in previous['rows']]==[r['id'] for r in current['rows']]:
        raise ValueError('Result identities unchanged')
    if keyword is not None and current.get('keyword')!=keyword:
        raise ValueError('Search filter not retained')
    if pagination and previous.get('page'):
        if not current.get('page') or int(current['page'])!=int(previous['page'])+1:
            raise ValueError('Active page did not advance by one')


def verified_search_can_resume(current,evidence,keyword):
    return (bool(current.get('rows')) and current.get('keyword')==evidence.get('keyword')==keyword
            and current.get('url')==evidence.get('url')
            and [r['id'] for r in current['rows']]==[r['id'] for r in evidence.get('rows',[])])


def wait_snapshot(proxy, config, timeout=60,reader=SNAPSHOT,validator=validate_snapshot):
    deadline=time.monotonic()+timeout
    previous=None;since=None;last_error=None
    while time.monotonic()<deadline:
        d=proxy.read_js(reader,config)
        try:
            validator(d,config)
        except ValueError as e:
            last_error=e;previous=None;since=None
        else:
            signature=json.dumps(d.get('rows',d),ensure_ascii=False,sort_keys=True)
            if signature!=previous:previous=signature;since=time.monotonic()
            elif time.monotonic()-since>=6:return d
        time.sleep(1)
    raise TimeoutError('DOM did not become stable: '+str(last_error or 'content changing'))


def collect(run_dir,config_path,output,limit=None,keyword=None,max_pages=1):
    with writer_lock(run_dir),writer_lock(output):
        return _collect(run_dir,config_path,output,limit,keyword,max_pages)


def _collect(run_dir,config_path,output,limit=None,keyword=None,max_pages=1):
    if limit is not None and limit<1:raise ValueError('Sample limit must be positive')
    if max_pages<1:raise ValueError('Page limit must be positive')
    root=Path(run_dir).resolve();config=read(config_path);auth=read(root/'public-access.json')
    if auth.get('authorized') is not True or not auth.get('evidence'):
        raise ValueError('Explicit public-page permission evidence required')
    if config.get('layout') not in ('inline_sections','link_details','click_details'):
        raise ValueError('Unverified layout')
    if config.get('search_required') and keyword is None:raise ValueError('This verified site requires an explicit search keyword')
    if config['layout']=='click_details' and (max_pages>1 or config.get('detail_navigation')!='new_tab'):
        raise ValueError('Card-click profile currently supports one list page and verified new-tab details only')
    out=Path(output);out.mkdir(parents=True,exist_ok=True)
    contract={k:config[k] for k in ('site','company','hosts','list_url','layout')}
    contract.update(keyword=keyword,max_pages=max_pages)
    if (out/'site-contract.json').exists() and read(out/'site-contract.json')!=contract:
        raise ValueError('Output belongs to another site/configuration; use a separate batch')
    write(out/'site-contract.json',contract)
    write(out/'config-evidence.json',config)
    manifest=read(root/'browser.json')
    p=connect_owned(root,int(manifest['address'].split(':')[-1]))
    # Eager mode stops loading at DOMContentLoaded and can abort SPA bundles.
    # Apply to this verified task tab, never to unrelated user tabs.
    p.set.load_mode.normal()
    pacing=(read(root/'runtime-config.json') if (root/'runtime-config.json').exists() else {}).get('pacing',{})
    guard=Guard(root,'independent',pacing.get('delay_min',30),pacing.get('delay_max',45),
                batch_size=pacing.get('batch_size',5),batch_rest=pacing.get('batch_rest',90))
    guard.ensure_can_resume();guard.check(p,force=True);guard.runtime.start('search');proxy=Proxy(p,guard)
    try:
        if (str(p.url) if urlsplit(config['list_url']).fragment else urlsplit(p.url)._replace(fragment='').geturl())!=config['list_url']:
            proxy.get(config['list_url'],timeout=25,retry=0)
        linked=config['layout'] in ('link_details','click_details')
        reader=LINK_LIST if linked else SNAPSHOT
        validator=validate_links if linked else validate_snapshot
        d=wait_snapshot(proxy,config,reader=reader,
                        validator=validate_search_shell if config.get('search_required') else validator)
        if keyword is not None:
            if not config.get('search_selector') or config.get('search_action') not in ('enter','button'):
                raise ValueError('Keyword search not verified for this profile')
            proof=read(out/'search-evidence.json') if (out/'search-evidence.json').exists() else {}
            if not verified_search_can_resume(d,proof,keyword):
                prior=d
                guard.delay();proxy.ele('css:'+config['search_selector']).input(keyword+('\n' if config['search_action']=='enter' else ''),clear=True)
                if config['search_action']=='button':proxy.ele('css:'+config['search_submit_selector']).click(by_js=False)
                def searched(value,cfg):
                    validator(value,cfg)
                    validate_transition(prior,value,keyword)
                d=wait_snapshot(proxy,config,reader=reader,validator=searched)
                write(out/'search-evidence.json',d)
        write(out/'dom-evidence.json',d)
        rows=validator(d,config)
        pages_read=1;write(out/'pages/page-001.json',d)
        while pages_read<max_pages:
            if not config.get('next_selector'):raise ValueError('Pagination not verified for this profile')
            next_button=proxy.ele('css:'+config['next_selector'],timeout=1)
            if not next_button or not next_button.states.is_displayed:break
            prior=d
            next_button.click()
            def paged(value,cfg):
                validator(value,cfg)
                validate_transition(prior,value,keyword,pagination=True)
            d=wait_snapshot(proxy,config,reader=reader,validator=paged)
            if {r['id'] for r in rows}&{r['id'] for r in d['rows']}:raise ValueError('Repeated job IDs across pages')
            rows+=d['rows'];pages_read+=1;write(out/f'pages/page-{pages_read:03d}.json',d)
        discovered=len(rows)
        write(out/'list-candidates.json',rows)
        if limit is not None:rows=rows[:limit]
        archived=read(out/'previous-unseen.json') if (out/'previous-unseen.json').exists() else []
        current=read(out/'jobs.json') if (out/'jobs.json').exists() else []
        old={j['job_key']:j for j in archived+current}
        # Save the complete previous set before incrementally replacing current jobs.
        # After an interruption, new current rows override these archived copies.
        write(out/'previous-unseen.json',list(old.values()))
        jobs=[]
        for row in rows:
            company_evidence=d['company_evidence']
            detail={}
            body_error=''
            if not linked and config.get('inline_expand_selector') and not row.get('description_visible'):
                guard.runtime.commit(phase='detail',checkpoint={'job_id':row['id'],'phase':'expand_inline'})
                proxy.ele('css:'+inline_expand_selector(row,config)).click(by_js=False)
                def expanded(value,cfg):validate_inline_expansion(value,row,cfg)
                expanded_page=wait_snapshot(proxy,config,reader=SNAPSHOT,validator=expanded)
                row=validate_inline_expansion(expanded_page,row,config)
            if not linked:
                try:
                    validate_snapshot(dict(d,rows=[row]),dict(config,allow_pending_inline_descriptions=False))
                except ValueError as exc:
                    body_error=str(exc)
            if linked:
                guard.runtime.commit(phase='detail',checkpoint={'job_id':row['id'],'phase':'detail'})
                detail_proxy=proxy;detail_tab=None
                if config['layout']=='click_details':
                    before=set(p.tab_ids)
                    proxy.ele('css:'+config['card_selector']+'['+config['id_attribute']+'="'+row['id']+'"]').click()
                    deadline=time.monotonic()+20;new=set()
                    while time.monotonic()<deadline:
                        new=set(p.tab_ids)-before
                        if new:break
                        time.sleep(.5)
                    if len(new)!=1:raise ValueError('Expected exactly one newly opened detail tab')
                    detail_tab=p.get_tab(next(iter(new)))
                    detail_tab.set.load_mode.normal()
                    write(out/'pending-detail.json',{'job_id':row['id'],'tab_id':detail_tab.tab_id,'url':str(detail_tab.url)})
                    guard.check(detail_tab,force=True);detail_proxy=Proxy(detail_tab,guard)
                else:proxy.get(row['url'],timeout=25,retry=0)
                def validate_detail(detail,unused):
                    validate_detail_identity(detail,row,config)
                detail=wait_snapshot(detail_proxy,config,reader=LINK_DETAIL,validator=validate_detail)
                row['description']=detail['description'];company_evidence=detail['company_evidence']
                row['url']=detail['url']
                write(out/(row['id']+'.json'),detail)
                if detail_tab:
                    guard.check(detail_tab,force=True);detail_tab.close()
            key=config['site']+':'+row['id'];prior=old.get(key,{})
            job={k:v for k,v in prior.items() if k.startswith('user_')}
            job.update(source=config['site'],job_key=key,source_id=row['id'],
                       company=config['company'],company_evidence=company_evidence,
                       job_name=row['title'],job_url='' if config.get('job_url_scope')=='list_page' else row['url'],
                       source_page_url=row['url'],job_url_scope=config.get('job_url_scope','job_page'),location=row.get('location',''),
                       salary='',experience='',education='',published_at='',
                       jd_description=row['description'],jd_read_at=now(),
                       jd_hash=hashlib.sha256(row['description'].encode()).hexdigest(),
                       detail_state='PENDING' if body_error else 'COLLECTED_DETAIL_DOM',detail_read_error=body_error)
            job['list_fields']=row.get('fields',{})
            job['detail_fields']=detail.get('fields',{})
            for field in config.get('list_field_map',[]):
                if field not in ('location','salary','published_at','employment_type','function','hiring_company','business_unit'):
                    raise ValueError('Unsupported list field mapping')
                if row.get('fields',{}).get(field):job[field]=row['fields'][field]
            employer=re.search(config.get('employer_pattern',r'(?!)'),row['description'])
            job['hiring_company']=employer.group(1) if employer else job.get('hiring_company','')
            job['company_scope']='招聘品牌/站点身份；雇佣主体仅在正文或经核实的公司DOM字段明确写出时提取'
            job['field_evidence']={k:'配置指定的卡片DOM字段：'+v for k,v in row.get('fields',{}).items() if k in config.get('list_field_map',[]) and v}
            for label,field in config.get('detail_field_map',{}).items():
                if field not in ('function','recruitment_type','employment_type','salary','team','location','published_at','experience','education'):
                    raise ValueError('Unsupported detail field mapping')
                value=detail.get('fields',{}).get(label)
                if value:
                    job[field]=value;job['field_evidence'][field]=label+'：'+value
            for field,pattern in config.get('field_patterns',{}).items():
                if field not in ('education','experience','salary','location','published_at'):
                    raise ValueError('Unsupported configured field')
                match=re.search(pattern,row['description'])
                if match:
                    job[field]=match.group(1) if match.lastindex else match.group(0)
                    job['field_evidence'][field]=match.group(0)
            if not job['location'] and config.get('sitewide_location_pattern'):
                match=re.search(config['sitewide_location_pattern'],d['text'])
                if match:
                    job['location']=match.group(1)
                    job['field_evidence']['location']='站点默认地点声明：'+match.group(0)
            if prior.get('jd_description') and prior['jd_description']!=job['jd_description']:
                job['description_history']=prior.get('description_history',[])+[
                    {'description':prior['jd_description'],'read_at':prior.get('jd_read_at')}]
            elif prior.get('description_history'):job['description_history']=prior['description_history']
            jobs.append(job)
            write(out/'jobs.json',jobs)
        # Old unseen jobs are retained separately, not silently closed or deleted.
        seen={j['job_key'] for j in jobs}
        write(out/'previous-unseen.json',[v for k,v in old.items() if k not in seen])
        write(out/'jobs.json',jobs)
        coverage=dict(site=config['site'],checked_at=now(),visible_jobs=discovered,sampled_jobs=len(jobs),with_jd=sum(j['detail_state']=='COLLECTED_DETAIL_DOM' for j in jobs),
                      layout=config['layout'],pagination=config['pagination'],
                      scope='Observed current list and validated JD pages; not whole-company coverage',
                      keyword=keyword,pages_read=pages_read,search_tested=keyword is not None,
                      pagination_tested=pages_read>1,application_tested=False)
        write(out/'coverage.json',coverage)
        guard.runtime.commit(task_state='BUDGET_COMPLETED',checkpoint=coverage)
        return coverage
    except Exception as e:
        # Login/challenge exceptions inherit BaseException and retain Guard's pause.
        # Ordinary DOM/network failures also need a durable checkpoint.
        write(out/'read-error.json',{'error_type':type(e).__name__,'error':str(e),'url':str(p.url),'at':now()})
        guard.runtime.pause('READ_ERROR',url=str(p.url),message=str(e))
        raise


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--run-dir',required=True);ap.add_argument('--config',required=True);ap.add_argument('--output',required=True);ap.add_argument('--limit',type=int);ap.add_argument('--keyword');ap.add_argument('--max-pages',type=int,default=1)
    a=ap.parse_args();print(json.dumps(collect(a.run_dir,a.config,a.output,a.limit,a.keyword,a.max_pages),ensure_ascii=False))
