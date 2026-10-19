# Adapted from JobMiner; city selection and login handled by collect.py.
import json
import re
import time
from typing import Any, Dict, List, Tuple
from DrissionPage import ChromiumOptions, ChromiumPage
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

def search_keyword(page, keyword: str) -> None:
    input_box = must_ele(page, 'css:input#search-input', timeout=20)
    input_box.clear()
    input_box.input(keyword)
    search_btn = must_ele(page, 'css:.search-container .search-btn', timeout=10)
    search_btn.click(by_js=True)
    must_ele(page, 'css:.joblist .joblist-item', timeout=30)
    time.sleep(1.0)

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
    wrapper = card.parent() or card
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

def switch_to_new_tab_if_any(page, old_tab_ids: List) -> object:
    try:
        new_tab_ids = [t for t in page.tab_ids if t not in old_tab_ids]
        if not new_tab_ids:
            return page
        tab = page.get_tab(new_tab_ids[-1])
        return tab or page
    except Exception:
        return page

def click_card_open_detail(page, index: int) -> Tuple[object, bool]:
    cards = get_cards(page)
    if index >= len(cards):
        raise IndexError('卡片索引超出范围')
    target = cards[index]
    try:
        target.scroll.to_see()
    except Exception:
        pass
    old_tab_ids = []
    try:
        old_tab_ids = list(page.tab_ids)
    except Exception:
        pass
    clicked = False
    try:
        name_ele = target.ele('css:.jname', timeout=1)
        if name_ele:
            name_ele.click(by_js=True)
            clicked = True
    except Exception:
        clicked = False
    if not clicked:
        target.click(by_js=True)
    time.sleep(1.0)
    detail_tab = switch_to_new_tab_if_any(page, old_tab_ids)
    opened_new_tab = detail_tab is not page
    return (detail_tab, opened_new_tab)

def parse_detail_page(detail_page) -> Dict[str, Any]:
    must_ele(detail_page, 'css:.tCompany_center', timeout=30)
    title = safe_text(detail_page, 'css:.tHeader.tHjob h1')
    salary = safe_text(detail_page, 'css:.tHeader.tHjob strong')
    base_info = safe_text(detail_page, 'css:.tHeader.tHjob p.msg.ltype')
    head_tags = safe_texts(detail_page, 'css:.tHeader.tHjob .jtag .sp4')
    job_desc_box = detail_page.ele('css:.tBorderTop_box .job_msg.inbox', timeout=3)
    job_desc = clean_text(job_desc_box.text if job_desc_box else '')
    job_func = safe_text(detail_page, 'css:.job_msg.inbox p.fp a')
    work_addr = safe_text(detail_page, 'css:.tBorderTop_box .bmsg.inbox p.fp')
    company_intro = ''
    boxes = detail_page.eles('css:.tCompany_main .tBorderTop_box', timeout=2) or []
    for box in boxes:
        h2 = safe_text(box, 'css:h2 .bname')
        if '公司信息' in h2:
            txt = safe_text(box, 'css:.tmsg.inbox')
            if txt:
                company_intro = clean_text(txt)
                break
    company_name = safe_text(detail_page, 'css:.com_msg .com_name p')
    company_meta = safe_texts(detail_page, 'css:.com_tag .at')
    job_id = ''
    hid = detail_page.ele('css:input#hidJobID', timeout=1)
    if hid:
        job_id = (hid.attr('value') or '').strip()
    return {'detail_url': getattr(detail_page, 'url', ''), 'detail_job_id': job_id, 'detail_title': title, 'detail_salary': salary, 'detail_base_info': base_info, 'detail_tags': head_tags, 'jd_description': job_desc, 'job_function': job_func, 'work_address': work_addr, 'detail_company': company_name, 'detail_company_meta': company_meta, 'company_intro': company_intro}

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
    prev_first_title = ''
    first_card = page.ele('css:.joblist .joblist-item-job .jname', timeout=2)
    if first_card:
        prev_first_title = (first_card.text or '').strip()
    btn = must_ele(page, 'css:.el-pagination .btn-next', timeout=5)
    btn.click(by_js=True)
    end = time.time() + 25
    while time.time() < end:
        cur_first = page.ele('css:.joblist .joblist-item-job .jname', timeout=1)
        cur_title = (cur_first.text or '').strip() if cur_first else ''
        if cur_title and cur_title != prev_first_title:
            return True
        time.sleep(0.4)
    return bool(get_cards(page))

def collect_jobs_by_keyword(page, keyword: str, max_jobs: int=60, max_pages: int=2) -> List[Dict[str, Any]]:
    search_keyword(page, keyword)
    results: List[Dict[str, Any]] = _streaming_list()
    seen = set()
    page_idx = 1
    while page_idx <= max_pages and len(results) < max_jobs:
        cards = get_cards(page)
        if not cards:
            break
        for i in range(len(cards)):
            if len(results) >= max_jobs:
                break
            latest_cards = get_cards(page)
            if i >= len(latest_cards):
                break
            brief = parse_card(latest_cards[i])
            dedupe_key = f"{brief['job_id']}|{brief['job_name']}|{brief['company']}"
            if dedupe_key in seen:
                continue
            detail_page = None
            opened_new_tab = False
            detail = {}
            try:
                detail_page, opened_new_tab = click_card_open_detail(page, i)
                detail = parse_detail_page(detail_page)
            except Exception:
                detail = {'detail_url': '', 'detail_job_id': '', 'detail_title': '', 'detail_salary': '', 'detail_base_info': '', 'detail_tags': [], 'jd_description': '', 'job_function': '', 'work_address': '', 'detail_company': '', 'detail_company_meta': [], 'company_intro': ''}
            finally:
                if opened_new_tab and detail_page is not None:
                    try:
                        detail_page.close()
                    except Exception:
                        pass
            item = {'source': '51job', 'keyword': keyword, 'city': '', 'job_id': detail.get('detail_job_id') or brief.get('job_id', ''), 'job_name': detail.get('detail_title') or brief.get('job_name', ''), 'salary': detail.get('detail_salary') or brief.get('salary', ''), 'base_info': detail.get('detail_base_info', ''), 'location': brief.get('area', ''), 'tip': brief.get('tip', ''), 'tags': brief.get('tags', []), 'experience': brief.get('job_year', ''), 'education': brief.get('job_degree', ''), 'company': detail.get('detail_company') or brief.get('company', ''), 'company_url': brief.get('company_url', ''), 'company_logo': brief.get('company_logo', ''), 'company_props': brief.get('company_props', []), 'work_address': detail.get('work_address', ''), 'job_function': detail.get('job_function', ''), 'jd_description': detail.get('jd_description', ''), 'company_intro': detail.get('company_intro', ''), 'jd_labels': detail.get('detail_tags', []), 'detail_company_meta': detail.get('detail_company_meta', []), 'job_url': detail.get('detail_url', '')}
            if not globals().get('_skip_record', lambda row: False)(item):
                results.append(item)
            seen.add(dedupe_key)
        if len(results) >= max_jobs:
            break
        if not go_next_page(page):
            break
        page_idx += 1
    return results
