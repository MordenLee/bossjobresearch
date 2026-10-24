# Adapted from JobMiner; city selection and login handled by collect.py.
import json
import re
import time
from typing import Any, Dict, List, Tuple
from urllib.parse import urlsplit, urlunsplit, parse_qs, urlencode
HOME_URL = 'https://www.51job.com/'

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

def clean_text(text: str) -> str:
    if not text:
        return ''
    s = str(text).replace('\r\n', '\n')
    s = re.sub('\\n{3,}', '\n\n', s)
    return s.strip()

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

def find_first_by_text(parent, selector: str, text: str):
    try:
        eles = parent.eles(selector, timeout=2) or []
    except Exception:
        eles = []
    for ele in eles:
        try:
            if (ele.text or '').strip() == text:
                return ele
        except Exception:
            continue
    return None

def search_url(base, keyword):
    p = urlsplit(base)
    if p.hostname != 'we.51job.com' or p.path != '/pc/search':
        raise ValueError('请先在页面搜索并配置已核实的 51job search_url')
    q = parse_qs(p.query, keep_blank_values=True)
    q['keyword'] = [keyword]
    q.pop('pageNum', None)
    return urlunsplit(('https', p.netloc, p.path, urlencode(q, doseq=True), ''))


def search_keyword(page, keyword):
    page.get(search_url(page.search_home, keyword), timeout=25, retry=0)
    must_ele(page, 'css:.joblist .joblist-item-job', timeout=30)


def parse_sensorsdata(card) -> Dict[str, Any]:
    txt = (card.attr('sensorsdata') or '').strip()
    if not txt:
        return {}
    try:
        return json.loads(txt)
    except Exception:
        return {}

def get_cards(page):
    return page.eles('css:.joblist .joblist-item-job', timeout=4) or []

def parse_card(card) -> Dict[str, Any]:
    sensor = parse_sensorsdata(card)
    wrapper = card
    job_name = safe_text(card, 'css:.jname') or str(sensor.get('jobTitle') or '').strip()
    salary = safe_text(card, 'css:.sal') or str(sensor.get('jobSalary') or '').strip()
    area = safe_text(card, 'css:.area .shrink-0') or str(sensor.get('jobArea') or '').strip()
    tip = safe_text(card, 'css:.joblist-item-jobinfo .tip')
    tags = safe_texts(card, 'css:.joblist-item-tags .tag')
    company_name = safe_text(wrapper, 'css:.joblist-item-right .cname')
    company_url = safe_attr(wrapper, 'css:.joblist-item-right a.comp', 'href')
    company_logo = safe_attr(wrapper, 'css:.joblist-item-right .comlogo', 'src')
    company_props = safe_texts(wrapper, 'css:.joblist-item-right .bc .dc')
    return {'job_id': str(sensor.get('jobId') or '').strip(), 'job_name': job_name, 'salary': salary, 'area': area, 'tip': tip, 'tags': tags, 'job_year': str(sensor.get('jobYear') or '').strip(), 'job_degree': str(sensor.get('jobDegree') or '').strip(), 'job_time': str(sensor.get('jobTime') or '').strip(), 'company': company_name, 'company_url': company_url, 'company_logo': company_logo, 'company_props': company_props, 'sensors_raw': sensor}

def switch_to_new_tab_if_any(page, old_tab_ids):
    end = time.monotonic() + 10
    while time.monotonic() < end:
        new_ids = [t for t in page.tab_ids if t not in old_tab_ids]
        if len(new_ids) > 1:
            raise ValueError('AMBIGUOUS_NEW_TABS')
        if new_ids:
            return page.get_tab(new_ids[0])
        time.sleep(.25)
    raise TimeoutError('DETAIL_TAB_NOT_OPENED')


def click_card_open_detail(page, index: int) -> Tuple[object, bool]:
    cards = get_cards(page)
    if index >= len(cards):
        raise IndexError('卡片索引超出范围')
    target = cards[index]
    old_tab_ids = list(page.tab_ids)
    page.set.timeouts(page_load=25)
    name_ele = must_ele(target, 'css:.jname', timeout=3)
    name_ele.click(by_js=True)
    time.sleep(1.0)
    detail_tab = switch_to_new_tab_if_any(page, old_tab_ids)
    opened_new_tab = detail_tab is not page
    return (detail_tab, opened_new_tab)

def job_id_from_url(url):
    p = urlsplit(url)
    m = re.fullmatch(r'/[^/]+/(\d+)\.html', p.path)
    return m.group(1) if p.hostname == 'jobs.51job.com' and m else ''


def parse_detail_page(page):
    must_ele(page, 'css:.job_msg.inbox', timeout=30)
    read = getattr(page, 'read_js', None) or page.run_js
    data = read(r"""const text=s=>document.querySelector(s)?.innerText.trim()||'';
      const el=document.querySelector('.job_msg.inbox > div:first-child');
      return {url:location.href,title:text('.tHeader h1'),company:text('.com_name p'),
        description:el?.innerText.trim()||'',
        truncated:!el||!!(el.scrollHeight>el.clientHeight+2 && ['hidden','clip'].includes(getComputedStyle(el).overflowY)),
        fields:{salary:text('.tHeader strong'),requirements:text('.tHeader .msg'),
          work_address:text('.bmsg.inbox p.fp'),company_info:text('.com_tag')}};""")
    if not job_id_from_url(data['url']):
        raise ValueError('UNEXPECTED_DETAIL_URL')
    return data


def collect_detail(page, index, brief):
    current = parse_card(get_cards(page)[index])
    if any(current.get(k) != brief.get(k) for k in ('job_id','job_name','company')):
        raise ValueError('CARD_CHANGED')
    tab, opened = click_card_open_detail(page, index)
    try:
        data = parse_detail_page(tab)
        if job_id_from_url(data['url']) != brief['job_id']:
            raise ValueError('DETAIL_JOB_ID_MISMATCH')
        return {'job_url':data['url'], 'jd_source':'detail_page',
                'jd_description':data['description'], 'collected_detail_evidence':data}
    finally:
        if opened:
            tab.close()


def has_next_page(page) -> bool:
    btn = page.ele('css:.el-pagination .btn-next', timeout=2)
    if not btn:
        return False
    cls = (btn.attr('class') or '').lower()
    disabled_attr = (btn.attr('disabled') or '').lower()
    return 'disabled' not in cls and disabled_attr not in ['disabled', 'true']

def go_next_page(page) -> bool:
    if not has_next_page(page):
        return False
    before = [parse_sensorsdata(c).get('jobId') for c in get_cards(page)]
    btn = must_ele(page, 'css:.el-pagination .btn-next', timeout=5)
    btn.click(by_js=True)
    end = time.time() + 25
    while time.time() < end:
        after = [parse_sensorsdata(c).get('jobId') for c in get_cards(page)]
        if after and after != before:
            return True
        time.sleep(0.4)
    raise TimeoutError("PAGINATION_NOT_CHANGED")

def collect_jobs_by_keyword(page, keyword, max_jobs=60, max_pages=2):
    search_keyword(page, keyword)
    results = globals().get('_streaming_list', list)()
    seen = set()
    for page_idx in range(max_pages):
        cards = get_cards(page)
        if not cards:
            raise TimeoutError('LIST_NOT_READY')
        for index in range(len(cards)):
            brief = parse_card(get_cards(page)[index])
            key = brief['job_id']
            if not key:
                raise ValueError('MISSING_JOB_ID')
            if key in seen:
                continue
            seen.add(key)
            item = dict(brief, source='51job', keyword=keyword,
                        location=brief['area'], experience=brief['job_year'],
                        education=brief['job_degree'], jd_description='')
            if globals().get('_skip_record', lambda row:False)(item):
                continue
            try:
                item.update(collect_detail(page, index, brief))
            except Exception as exc:
                item['detail_read_error'] = type(exc).__name__ + ': ' + str(exc)
            results.append(item)
            if len(results) >= max_jobs:
                return results
        if page_idx + 1 >= max_pages or not go_next_page(page):
            break
    return results
