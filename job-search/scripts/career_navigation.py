"""Select one observed public job-list link without changing an existing search."""
import re
from urllib.parse import urlsplit,parse_qs

LABELS={'社会招聘':0,'社招岗位':0,'全部职位':1,'所有职位':1,'招聘职位':1,'查看更多职位':1,'其他职位 查看更多职位':1,'搜索职位':2,
        'view all jobs':1,'see all jobs':1,'open positions':1,'current openings':1,
        'view all opportunities':1,'search jobs':2,'find jobs':2,'explore jobs':2,
        'all jobs':1,'open roles':1,'view open roles':1,'see open roles':1,
        'search all jobs':2,'search our jobs':2,'browse jobs':2,'view job openings':2,
        'job search':2,'search for jobs':2,'career opportunities':3}


def detect_public_barrier(snapshot):
    """Recognize public error/verification pages without matching security JD prose."""
    url=snapshot.get('url','');text=snapshot.get('text','').strip()
    if not re.match(r'^https?://',url):return 'READ_ERROR'
    headings=[snapshot.get('title','').strip(),text.splitlines()[0] if text else '']
    for heading in headings:
        if re.match(r'^(?:access denied|403 forbidden|request blocked)(?:\s*$|\s*[|:])',heading,re.I):return 'ACCESS_DENIED'
        if re.match(r'^(?:just a moment|just a quick security check|verifying your browser|verify you are human|attention required|security check)[.!…]*(?:\s*$|\s*\|\s*cloudflare)',heading,re.I):return 'CHALLENGE'
    if '__cf_chl' in url and len(text)<180:return 'CHALLENGE'
    if re.match(r'^(?:request unavailable|service unavailable|bad gateway)$',snapshot.get('title','').strip(),re.I):return 'READ_ERROR'
    return None


def choose_job_link(current_url,links):
    current=urlsplit(current_url)
    if re.search(r'/(?:search|search-results|job-search|jobsearch|jobs|positions|open-opportunities|job-list)(?:/|$)',current.path,re.I):return None
    query=current.query+'&'+current.fragment.partition('?')[2]
    if any(k.lower() in {'country','countrycode','location','locations','q','query','keyword','keywords','loc_query','jobtypes'} for k in parse_qs(query)):
        return None
    candidates=[]
    for index,link in enumerate(links):
        label=re.sub(r'\s+',' ',link.get('text','')).strip().lower()
        label=re.sub(r'[→↗›»]+$','',label).strip()
        if re.fullmatch(r'社会招聘\s+(?:experienced recruitment|experienced hires|social recruitment)',label):label='社会招聘'
        if label not in LABELS or not link.get('visible',False):continue
        target=urlsplit(link.get('url',''))
        if target.scheme not in ('https','http') or target.username or target.password:continue
        same_document=target._replace(fragment='').geturl().rstrip('/')==current._replace(fragment='').geturl().rstrip('/')
        if same_document and (target.fragment==current.fragment or not re.match(r'^/(?:jobs(?:/|\?|$)|page/)',target.fragment,re.I)):continue
        if re.search(r'campus|university|graduate|internship|students',target.path,re.I):continue
        if re.search(r'\.(?:pdf|docx?|xlsx?|zip)$',target.path,re.I):continue
        if not link.get('raw_href'):continue
        candidates.append((LABELS[label],index,link))
    return min(candidates,key=lambda item:item[:2])[2] if candidates else None
