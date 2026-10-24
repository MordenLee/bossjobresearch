"""Zhilian split search-page adapter. Only DOM reads and guarded card selection."""
import time
from urllib.parse import urlsplit, urlunsplit, parse_qs, urlencode
HOME_URL = 'https://www.zhaopin.com/'


def create_browser(headless=False, profile_dir=None):
    from adapters.extract_liepin import create_browser as create
    return create(headless, profile_dir)


def read_dom(page, script):
    return (getattr(page, 'read_js', None) or page.run_js)(script)


def search_url(base, keyword):
    p = urlsplit(base)
    if p.hostname not in ('www.zhaopin.com', 'zhaopin.com') or p.path != '/jobs/':
        raise ValueError('请先通过智联首页搜索，配置已核实的 /jobs/ 搜索入口')
    q = parse_qs(p.query, keep_blank_values=True)
    q['kw'] = [keyword]
    q.pop('p', None)
    q.pop('page', None)
    return urlunsplit(('https', p.netloc, p.path, urlencode(q, doseq=True), ''))


def get_card_records(page):
    return read_dom(page, r'''return [...document.querySelectorAll('.job-list-panel .job-card')].map((e,index)=>{
      const text=s=>e.querySelector(s)?.innerText.trim()||'';
      const tags=[...e.querySelectorAll('.job-card__skill-tag')].map(x=>x.innerText.trim());
      return {card_index:index,job_name:text('.job-card__title-clamp'),company:text('.job-card__company-name'),
        salary:text('.job-card__salary'),location:text('.job-card__location'),tags,
        experience:tags.find(x=>/^(经验不限|\d.*年)$/.test(x))||'',
        education:tags.find(x=>/^(本科|大专|硕士|博士|学历不限|高中|中专)/.test(x))||''};
    });''')


def search_jobs(page, keyword):
    page.get(search_url(page.search_home, keyword), timeout=25, retry=0)
    end = time.monotonic() + 25
    while time.monotonic() < end:
        if get_card_records(page):
            return page
        time.sleep(.5)
    raise TimeoutError('LIST_NOT_READY: 未读取到岗位，不能据此认定零结果')


def read_panel(page):
    return read_dom(page, r'''const p=document.querySelector('.job-detail-panel');
      if(!p)return {};
      const text=s=>p.querySelector(s)?.innerText.trim()||'';
      const el=p.querySelector('.job-description__content');
      const cards=[...document.querySelectorAll('.job-list-panel .job-card')];
      return {url:p.querySelector('a.job-company-info__view-all')?.href||'',
        title:text('.job-detail-summary__title-text'),company:text('.job-detail-summary__company-name'),
        description:el?.innerText.trim()||'',selected_index:cards.findIndex(e=>e.classList.contains('job-card--active')),
        truncated:!el||[...p.querySelectorAll('.job-description button,.job-description a')].some(e=>e.getClientRects().length&&/展开|查看更多/.test(e.innerText))||!!(el&&getComputedStyle(el).webkitLineClamp!=='none'&&el.scrollHeight>el.clientHeight+2),
        fields:{salary:text('.job-detail-summary__salary'),company_info:text('.job-detail-summary__company-meta'),requirements:text('.job-detail-summary__tags')}};
    ''')


def panel_matches(row, panel):
    norm = lambda s: ''.join(str(s or '').split()).casefold()
    u = urlsplit(panel.get('url', ''))
    return (panel.get('selected_index') == row['card_index'] and bool(row.get('company'))
            and norm(panel.get('title')) == norm(row['job_name'])
            and norm(panel.get('company')) == norm(row['company'])
            and u.hostname in ('www.zhaopin.com', 'zhaopin.com') and u.path.startswith('/jobdetail/')
            and bool(panel.get('description')))


def extract_search_detail(page, row, timeout=12):
    old = read_panel(page)
    switched = not panel_matches(row, old)
    if switched:
        cards = page.eles('css:.job-list-panel .job-card', timeout=2)
        if len(cards) <= row['card_index']:
            return {'jd_description':'','detail_read_error':'CARD_NOT_FOUND'}
        # Recheck the card text before a position-based click; no implicit links or apply buttons.
        current = get_card_records(page)
        if len(current) <= row['card_index'] or any(current[row['card_index']].get(k) != row.get(k) for k in ('job_name','company','salary','location')):
            return {'jd_description':'','detail_read_error':'CARD_CHANGED'}
        cards[row['card_index']].click()
    end = time.monotonic() + timeout
    previous = None
    while time.monotonic() < end:
        panel = read_panel(page)
        signature = tuple(panel.get(k) for k in ('url','title','company','description'))
        if panel_matches(row, panel) and not (switched and (panel.get('description') == old.get('description') or panel.get('url') == old.get('url'))):
            if signature == previous:
                return {'job_url':panel['url'],'jd_source':'search_panel','jd_description':panel['description'],
                        'company_info':panel.get('fields',{}).get('company_info',''), 'collected_detail_evidence':panel}
            previous = signature
        else:
            previous = None
        time.sleep(.3)
    return {'jd_description':'','jd_source':'search_panel','detail_read_error':'PANEL_IDENTITY_OR_BODY_UNSTABLE'}


def collect_jobs_by_keyword(page, keyword, max_jobs=80, max_pages=2):
    # One loaded window only. Additional scrolling must be verified for the current site layout.
    result_page = search_jobs(page, keyword)
    rows = get_card_records(result_page)
    results = globals().get('_streaming_list', list)()
    seen = set()
    for row in rows:
        item = dict(row,source='zhilian',keyword=keyword)
        if globals().get('_skip_record',lambda r:False)(item):
            continue
        try:
            item.update(extract_search_detail(result_page,row))
        except Exception as exc:
            item.update(jd_description='',detail_read_error=type(exc).__name__+': '+str(exc))
        from workflow import canonical_url
        key = canonical_url(item.get('job_url'))
        if key and key in seen:
            continue
        if key:seen.add(key)
        item['coverage_scope']='loaded_list_window'
        results.append(item)
        if len(results)>=max_jobs:break
    return results
