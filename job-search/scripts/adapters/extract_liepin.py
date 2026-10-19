# Adapted from JobMiner; city selection and login handled by collect.py.
import random
import time
from typing import Dict, List
from urllib.parse import quote
from DrissionPage import ChromiumOptions, ChromiumPage
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

def search_jobs(page, keyword: str):
    page.get(LIEPIN_HOME)
    input_box = must_ele(page, 'css:input[placeholder="搜索职位、公司"]', timeout=20)
    input_box.clear()
    input_box.input(keyword)
    search_btn = must_ele(page, 'xpath://span[contains(@class,"search-btn") and normalize-space(.)="搜索"] | //button[normalize-space(.)="搜索"]', timeout=10)
    old_tab_ids = []
    try:
        old_tab_ids = list(page.tab_ids)
    except Exception:
        pass
    search_btn.click(by_js=True)
    time.sleep(2)
    result_page = switch_to_new_tab_if_any(page, old_tab_ids)
    try:
        result_page.ele('css:div.job-list-box', timeout=8)
    except Exception:
        url = f'https://www.liepin.com/zhaopin/?key={quote(keyword)}'
        result_page.get(url)
    must_ele(result_page, 'css:div.job-list-box', timeout=25)
    return result_page

def extract_job_cards(result_page) -> List[Dict[str, str]]:
    cards = result_page.eles('css:div.job-list-box div.job-card-pc-container', timeout=5) or []
    rows: List[Dict[str, str]] = []
    for card in cards:
        anchor = card.ele("css:a[data-nick='job-detail-job-info']", timeout=1)
        if not anchor:
            continue
        href = (anchor.attr('href') or '').strip()
        if not href:
            continue
        title = ''
        title_ele = anchor.ele('css:div.ellipsis-1[title]', timeout=1)
        if title_ele:
            title = (title_ele.attr('title') or title_ele.text or '').strip()
        salary = safe_text(anchor, 'css:span._40108E8PWS')
        location = safe_text(anchor, 'css:div._40108__9nJ span.ellipsis-1')
        company = safe_text(card, "css:div[data-nick='job-detail-company-info'] span.ellipsis-1")
        rows.append({'job_url': href, 'job_name': title, 'salary': salary, 'location': location, 'company': company})
    return rows

def open_detail_tab(browser_page, url: str):
    try:
        return browser_page.new_tab(url=url)
    except TypeError:
        return browser_page.new_tab(url)

def parse_job_detail(detail_tab) -> Dict[str, object]:
    must_ele(detail_tab, 'css:div.job-properties', timeout=20)
    props = safe_texts(detail_tab, 'css:div.job-properties > span:not(.split)')
    jd_intro = safe_text(detail_tab, "css:dd[data-selector='job-intro-content']")
    job_title = safe_text(detail_tab, 'css:div.job-title-box h1')
    company = safe_text(detail_tab, 'css:div.company-name a')
    return {'detail_job_name': job_title, 'detail_company': company, 'job_properties': props, 'jd_description': jd_intro}

def has_next_page(result_page) -> bool:
    next_li = result_page.ele('css:li.ant-pagination-next', timeout=2)
    if not next_li:
        return False
    cls = (next_li.attr('class') or '').lower()
    return 'ant-pagination-disabled' not in cls

def go_next_page(result_page) -> bool:
    if not has_next_page(result_page):
        return False
    next_btn = result_page.ele('css:li.ant-pagination-next', timeout=3)
    if not next_btn:
        return False
    next_btn.click(by_js=True)
    time.sleep(random.uniform(1.0, 3.0))
    must_ele(result_page, 'css:div.job-list-box', timeout=20)
    return True

def _fetch_detail(page, url: str) -> Dict[str, object]:
    empty = {'detail_job_name': '', 'detail_company': '', 'job_properties': [], 'jd_description': ''}
    detail_tab = None
    try:
        detail_tab = open_detail_tab(page, url)
        time.sleep(random.uniform(1.0, 5.0))
        return parse_job_detail(detail_tab)
    except Exception:
        return empty
    finally:
        try:
            if detail_tab:
                detail_tab.close()
        except Exception:
            pass

def collect_jobs_by_keyword(page, keyword: str, max_jobs: int=80, max_pages: int=2) -> List[Dict[str, object]]:
    result_page = search_jobs(page, keyword)
    results: List[Dict[str, object]] = _streaming_list()
    seen_urls = set()
    total_fetched = 0
    page_no = 1
    while page_no <= max_pages and len(results) < max_jobs:
        rows = extract_job_cards(result_page)
        for row in rows:
            url = row.get('job_url', '')
            if not url or url in seen_urls:
                continue
            if total_fetched > 0 and total_fetched % BATCH_SIZE == 0:
                time.sleep(random.uniform(BATCH_PAUSE_MIN, BATCH_PAUSE_MAX))
            detail = _fetch_detail(page, url)
            total_fetched += 1
            item = {'source': 'liepin', 'keyword': keyword, 'job_url': url, 'job_name': row.get('job_name', '') or detail.get('detail_job_name', ''), 'company': row.get('company', '') or detail.get('detail_company', ''), 'salary': row.get('salary', ''), 'location': row.get('location', ''), 'experience': '', 'education': '', 'job_properties': detail.get('job_properties', []), 'tags': detail.get('job_properties', []), 'jd_labels': detail.get('job_properties', []), 'jd_description': str(detail.get('jd_description', ''))}
            if not globals().get('_skip_record', lambda row: False)(item):
                results.append(item)
            seen_urls.add(url)
            if len(results) >= max_jobs:
                break
        if len(results) >= max_jobs:
            break
        if not go_next_page(result_page):
            break
        page_no += 1
    return results
