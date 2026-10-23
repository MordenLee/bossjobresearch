# Adapted from JobMiner; city selection and login handled by collect.py.
import random
import time
from typing import Dict, List
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
LIEPIN_HOME = 'https://www.liepin.com/'
BATCH_SIZE = 10
BATCH_PAUSE_MIN = 8.0
BATCH_PAUSE_MAX = 15.0

def must_ele(page_or_ele, selector: str, timeout: int=20, step: float=0.25):
    end = time.time() + timeout
    while time.time() < end:
        try:
            ele = page_or_ele.ele(selector, timeout=1)
            if ele:
                return ele
        except Exception:
            pass
        time.sleep(step)
    raise TimeoutError(f'Wait element timeout: {selector}')

def safe_text(parent, selector: str, default: str='') -> str:
    try:
        ele = parent.ele(selector, timeout=1)
        if ele and ele.text:
            return ele.text.strip()
    except Exception:
        pass
    return default

def safe_texts(parent, selector: str) -> List[str]:
    try:
        eles = parent.eles(selector, timeout=1)
        return [e.text.strip() for e in eles if e and e.text and e.text.strip()]
    except Exception:
        return []

def safe_attr(parent, selector: str, attr_name: str, default: str='') -> str:
    try:
        ele = parent.ele(selector, timeout=1)
        if ele:
            return (ele.attr(attr_name) or '').strip()
    except Exception:
        pass
    return default

def create_browser(headless=False, profile_dir=None):
    import socket
    from DrissionPage import ChromiumOptions, ChromiumPage
    co = ChromiumOptions()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    co.set_local_port(port)
    if profile_dir:
        co.set_user_data_path(str(profile_dir))
    if headless:
        co.headless(True)
    return ChromiumPage(co)

def switch_to_new_tab_if_any(page, old_tab_ids: List) -> object:
    try:
        new_tab_ids = [t for t in page.tab_ids if t not in old_tab_ids]
        if not new_tab_ids:
            return page
        tab = page.get_tab(new_tab_ids[-1])
        return tab or page
    except Exception:
        return page

def read_dom(page, script):
    reader = getattr(page, 'read_js', None) or page.run_js
    return reader(script)


def search_url(base, keyword):
    parsed = urlsplit(base)
    if parsed.hostname not in ('www.liepin.com', 'liepin.com') or parsed.path != '/zhaopin/':
        raise ValueError('配置已核实的猎聘 /zhaopin/ 搜索页；保留城市和其他站内筛选')
    params = parse_qs(parsed.query, keep_blank_values=True)
    params.update(key=[keyword], currentPage=['0'])
    return urlunsplit(('https', parsed.netloc, parsed.path, urlencode(params, doseq=True), ''))


def search_jobs(page, keyword: str):
    base = getattr(page, 'search_home', '')
    if '/zhaopin/' not in base:
        base = 'https://www.liepin.com/zhaopin/'
    page.get(search_url(base, keyword), timeout=25, retry=0)
    end = time.monotonic() + 30
    while time.monotonic() < end:
        state = read_dom(page, "return {count:document.querySelectorAll('a[data-nick=job-detail-job-info]').length,empty:document.body.innerText.includes('暂时没有合适的职位')};")
        if state['count'] or state['empty']:
            return page
        time.sleep(.5)
    raise TimeoutError('LIST_NOT_READY: 搜索页没有卡片或明确无结果提示')


def extract_job_cards(result_page):
    return read_dom(result_page, r"""return [...document.querySelectorAll('a[data-nick="job-detail-job-info"]')].map(a=>{
      const box=a.closest('.jobDetailBox')||a.parentElement;
      const co=box.querySelector('[data-nick="job-detail-company-info"]');
      const lines=a.innerText.split('\n').map(s=>s.trim()).filter(Boolean);
      return {job_url:a.href,job_name:a.querySelector('[title]')?.innerText.trim()||lines[0],
        company:co?.querySelector('.companyName')?.innerText.trim()||'',company_info:co?.innerText||'',
        salary:[...a.querySelectorAll('span')].map(e=>e.innerText.trim()).find(s=>/^(?:\d+(?:\.\d+)?[-~]\d+(?:\.\d+)?[kK万]|面议)(?:·\d+薪)?$/.test(s))||'',
        location:a.querySelector('.jobTitleBox span.ellipsis-1')?.innerText||'',
        experience:lines.find(s=>/经验|\d.*年/.test(s))||'',
        education:lines.find(s=>/本科|硕士|博士|大专|学历|高中/.test(s))||'',list_text:box.innerText};
    });""")


def open_detail_tab(browser_page, url: str):
    # New-tab navigation otherwise uses the browser's long default load timeout.
    tab = browser_page.new_tab()
    try:
        tab.get(url, timeout=25, retry=0)
        return tab
    except Exception:
        tab.close()
        raise


def parse_job_detail(detail_tab):
    must_ele(detail_tab, 'css:[data-selector="job-intro-content"]', timeout=20)
    return read_dom(detail_tab, r"""const text=s=>document.querySelector(s)?.innerText.trim()||'';
    const el=document.querySelector('[data-selector="job-intro-content"]');
    return {url:location.href,title:text('.job-apply-content .job-title'),
      company:text('.title-box a[href*="/company/"]').replace(/^[ ·]+/,''),
      description:el?.innerText.trim()||'',
      truncated:!el||([...el.querySelectorAll('a,button')].some(e=>e.getClientRects().length&&/展开|查看更多/.test(e.innerText))||
        (getComputedStyle(el).webkitLineClamp!=='none'&&el.scrollHeight>el.clientHeight+2)),
      fields:{properties:text('.job-properties'),salary:text('.job-apply-content .salary')}};""")


def has_next_page(result_page) -> bool:
    next_li = result_page.ele('css:li.ant-pagination-next', timeout=2)
    if not next_li:
        return False
    cls = (next_li.attr('class') or '').lower()
    return 'ant-pagination-disabled' not in cls

def go_next_page(result_page):
    if not has_next_page(result_page):
        return False
    before = [row['job_url'] for row in extract_job_cards(result_page)]
    result_page.ele('css:li.ant-pagination-next', timeout=3).click()
    end = time.monotonic() + 25
    while time.monotonic() < end:
        after = [row['job_url'] for row in extract_job_cards(result_page)]
        if after and after != before:
            return True
        time.sleep(.5)
    raise TimeoutError('NEXT_PAGE_NOT_READY: 翻页后卡片未更新')


def _fetch_detail(page, url):
    detail_tab = None
    try:
        detail_tab = open_detail_tab(page, url)
        return {'jd_source': 'detail_page', 'collected_detail_evidence': parse_job_detail(detail_tab)}
    except Exception as exc:
        return {'detail_read_error': type(exc).__name__ + ': ' + str(exc), 'jd_source': 'detail_page'}
    finally:
        # Proxy retains a tab when login/verification has paused the run.
        if detail_tab:
            detail_tab.close()


def collect_jobs_by_keyword(page, keyword, max_jobs=80, max_pages=2):
    from workflow import canonical_url
    result_page = search_jobs(page, keyword)
    results = globals().get('_streaming_list', list)()
    seen_urls = set()
    for page_no in range(max_pages):
        rows = extract_job_cards(result_page)
        if not rows and read_dom(result_page, "return document.body.innerText.includes('暂时没有合适的职位');"):
            return results
        if not rows:
            raise RuntimeError('LIST_NOT_READY: 未读到卡片，不代表零结果')
        for row in rows:
            url = canonical_url(row['job_url'])
            if not url or url in seen_urls:
                continue
            item = dict(row, source='liepin', keyword=keyword, job_url=url)
            if globals().get('_skip_record', lambda row: False)(item):
                continue
            detail = _fetch_detail(page, row['job_url'])
            item.update(detail)
            evidence = detail.get('collected_detail_evidence', {})
            item['jd_description'] = evidence.get('description', '')
            results.append(item)
            seen_urls.add(url)
            if len(results) >= max_jobs:
                return results
        if page_no + 1 >= max_pages or not go_next_page(result_page):
            break
    return results
