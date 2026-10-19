# Adapted from JobMiner; city selection and login handled by collect.py.
import random
import time
from typing import Any, Dict, List
from DrissionPage import ChromiumOptions, ChromiumPage
HOME_URL = 'https://www.zhaopin.com/'

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
    return '\n'.join((line.rstrip() for line in str(text).replace('\r\n', '\n').split('\n'))).strip()

def human_delay(min_sec: float=1.0, max_sec: float=5.0) -> None:
    time.sleep(random.uniform(min_sec, max_sec))

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
    page.get(page.search_home)
    input_box = must_ele(page, 'css:input.zp-search__input', timeout=25)
    input_box.clear()
    input_box.input(keyword)
    search_btn = must_ele(page, 'css:a.zp-search__btn.zp-search__btn--blue', timeout=10)
    old_tab_ids = []
    try:
        old_tab_ids = list(page.tab_ids)
    except Exception:
        pass
    search_btn.click(by_js=True)
    time.sleep(1.0)
    result_page = switch_to_new_tab_if_any(page, old_tab_ids)
    must_ele(result_page, 'css:.positionlist .joblist-box__item', timeout=35)
    return result_page

def get_cards(result_page):
    return result_page.eles('css:.positionlist .positionlist__list .joblist-box__item', timeout=4) or []

def parse_card(card) -> Dict[str, Any]:
    job_url = safe_attr(card, 'css:.jobinfo__name-row a.jobinfo__name', 'href')
    job_name = safe_text(card, 'css:.jobinfo__name-row a.jobinfo__name')
    salary = safe_text(card, 'css:.jobinfo__salary')
    tags = safe_texts(card, 'css:.jobinfo__tag .joblist-box__item-tag')
    other_items = safe_texts(card, 'css:.jobinfo__other-info .jobinfo__other-info-item')
    location = other_items[0] if len(other_items) > 0 else ''
    experience = other_items[1] if len(other_items) > 1 else ''
    education = other_items[2] if len(other_items) > 2 else ''
    company = safe_text(card, 'css:.companyinfo__name')
    company_url = safe_attr(card, 'css:.companyinfo__name', 'href')
    company_logo = safe_attr(card, 'css:.companyinfo__logo-image', 'src')
    company_tags = safe_texts(card, 'css:.companyinfo__tag .joblist-box__item-tag')
    staff_name = safe_text(card, 'css:.companyinfo__staff-name')
    staff_state = safe_text(card, 'css:.companyinfo__staff-state')
    return {'job_url': job_url, 'job_name': job_name, 'salary': salary, 'tags': tags, 'location': location, 'experience': experience, 'education': education, 'company': company, 'company_url': company_url, 'company_logo': company_logo, 'company_tags': company_tags, 'staff_name': staff_name, 'staff_state': staff_state}

def open_detail_tab(browser_page, url: str):
    try:
        return browser_page.new_tab(url=url)
    except TypeError:
        return browser_page.new_tab(url)

def parse_detail(detail_tab) -> Dict[str, Any]:
    must_ele(detail_tab, 'css:.summary-planes', timeout=25)
    title = safe_text(detail_tab, 'css:.summary-planes__title span')
    update_time = safe_text(detail_tab, 'css:.summary-planes__time')
    salary = safe_text(detail_tab, 'css:.summary-planes__salary')
    info_items = safe_texts(detail_tab, 'css:.summary-planes__info li')
    detail_city_area = info_items[0] if len(info_items) > 0 else ''
    detail_exp = info_items[1] if len(info_items) > 1 else ''
    detail_degree = info_items[2] if len(info_items) > 2 else ''
    detail_job_type = info_items[3] if len(info_items) > 3 else ''
    detail_hiring_count = info_items[4] if len(info_items) > 4 else ''
    jd_skills = safe_texts(detail_tab, 'css:.describtion-card__skills-item')
    jd_text = safe_text(detail_tab, 'css:.describtion-card__detail-content')
    return {'detail_url': getattr(detail_tab, 'url', ''), 'detail_title': title, 'detail_update_time': update_time, 'detail_salary': salary, 'detail_city_area': detail_city_area, 'detail_experience': detail_exp, 'detail_education': detail_degree, 'detail_job_type': detail_job_type, 'detail_hiring_count': detail_hiring_count, 'jd_skills': jd_skills, 'jd_description': clean_text(jd_text)}

def has_next_page(result_page) -> bool:
    next_btn = result_page.ele("xpath://a[contains(@class,'soupager__btn') and contains(.,'下一页')]", timeout=2)
    if not next_btn:
        return False
    cls = (next_btn.attr('class') or '').lower()
    dis = (next_btn.attr('disabled') or '').lower()
    return 'disable' not in cls and dis not in ['disabled', 'true']

def go_next_page(result_page) -> bool:
    if not has_next_page(result_page):
        return False
    first_name = safe_text(result_page, 'css:.positionlist .joblist-box__item .jobinfo__name')
    next_btn = must_ele(result_page, "xpath://a[contains(@class,'soupager__btn') and contains(.,'下一页')]", timeout=8)
    next_btn.click(by_js=True)
    end = time.time() + 25
    while time.time() < end:
        cur_first = safe_text(result_page, 'css:.positionlist .joblist-box__item .jobinfo__name')
        if cur_first and cur_first != first_name:
            return True
        time.sleep(0.4)
    return bool(get_cards(result_page))

def collect_jobs_by_keyword(page, keyword: str, max_jobs: int=80, max_pages: int=2) -> List[Dict[str, Any]]:
    result_page = search_jobs(page, keyword)
    results: List[Dict[str, Any]] = _streaming_list()
    seen = set()
    page_idx = 1
    while page_idx <= max_pages and len(results) < max_jobs:
        cards = get_cards(result_page)
        if not cards:
            break
        for card in cards:
            if len(results) >= max_jobs:
                break
            brief = parse_card(card)
            key = f"{brief['job_name']}|{brief['company']}|{brief['salary']}|{brief['location']}"
            if key in seen:
                continue
            if not brief['job_url']:
                continue
            detail = {'detail_url': '', 'detail_title': '', 'detail_update_time': '', 'detail_salary': '', 'detail_city_area': '', 'detail_experience': '', 'detail_education': '', 'detail_job_type': '', 'detail_hiring_count': '', 'jd_skills': [], 'jd_description': ''}
            detail_tab = None
            try:
                human_delay(1.0, 5.0)
                detail_tab = open_detail_tab(page, brief['job_url'])
                detail = parse_detail(detail_tab)
            except Exception:
                pass
            finally:
                try:
                    if detail_tab:
                        detail_tab.close()
                except Exception:
                    pass
            item = {'source': 'zhilian', 'keyword': keyword, 'city': '', 'job_name': detail.get('detail_title') or brief.get('job_name', ''), 'salary': detail.get('detail_salary') or brief.get('salary', ''), 'location': brief.get('location', ''), 'experience': brief.get('experience', ''), 'education': brief.get('education', ''), 'tags': brief.get('tags', []), 'company': brief.get('company', ''), 'company_url': brief.get('company_url', ''), 'company_logo': brief.get('company_logo', ''), 'company_tags': brief.get('company_tags', []), 'hr_name': brief.get('staff_name', ''), 'hr_state': brief.get('staff_state', ''), 'job_url': brief.get('job_url', ''), 'update_time': detail.get('detail_update_time', ''), 'detail_city_area': detail.get('detail_city_area', ''), 'detail_experience': detail.get('detail_experience', ''), 'detail_education': detail.get('detail_education', ''), 'detail_job_type': detail.get('detail_job_type', ''), 'detail_hiring_count': detail.get('detail_hiring_count', ''), 'jd_skills': detail.get('jd_skills', []), 'jd_labels': detail.get('jd_skills', []), 'jd_description': detail.get('jd_description', ''), 'detail_url': detail.get('detail_url', '')}
            if not globals().get('_skip_record', lambda row: False)(item):
                results.append(item)
            seen.add(key)
        if len(results) >= max_jobs:
            break
        if not go_next_page(result_page):
            break
        page_idx += 1
    return results
