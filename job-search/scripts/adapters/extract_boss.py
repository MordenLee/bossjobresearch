# Adapted from JobMiner; city selection and login handled by collect.py.
import json
import re
import time
from typing import Any, Dict, List
from urllib.parse import urljoin
from DrissionPage import ChromiumOptions, ChromiumPage

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

def safe_attr(parent, selector: str, attr_name: str, default: str='') -> str:
    try:
        ele = parent.ele(selector, timeout=1)
        if ele:
            return (ele.attr(attr_name) or '').strip()
    except Exception:
        pass
    return default

def safe_texts(parent, selector: str) -> List[str]:
    try:
        eles = parent.eles(selector, timeout=1)
        return [e.text.strip() for e in eles if e and e.text and e.text.strip()]
    except Exception:
        return []

def clean_desc_text(text: str) -> str:
    if not text:
        return ''
    s = str(text)
    s = s.replace('kanzhun', '').replace('boss', '')
    s = re.sub('\\s+\\n', '\n', s)
    s = re.sub('\\n{3,}', '\n\n', s)
    return s.strip()

def extract_job_id_from_href(href: str) -> str:
    if not href:
        return ''
    m = re.search('/job_detail/([^/.?]+)\\.html', href)
    return m.group(1) if m else ''

def try_parse_json(body):
    if isinstance(body, (dict, list)):
        return body
    if isinstance(body, bytes):
        try:
            return json.loads(body.decode('utf-8', errors='ignore'))
        except Exception:
            return None
    if isinstance(body, str):
        try:
            return json.loads(body)
        except Exception:
            return None
    return None

def find_job_list(obj):
    if isinstance(obj, dict):
        for k in ['jobList', 'list', 'zpData', 'data']:
            if k in obj:
                v = obj[k]
                if isinstance(v, list):
                    if not v or isinstance(v[0], dict):
                        return v
                elif isinstance(v, dict):
                    ret = find_job_list(v)
                    if ret:
                        return ret
        for _, v in obj.items():
            ret = find_job_list(v)
            if ret:
                return ret
    elif isinstance(obj, list):
        if obj and isinstance(obj[0], dict):
            keys = set(obj[0].keys())
            if {'jobName', 'positionName', 'salary', 'salaryDesc'} & keys:
                return obj
        for it in obj:
            ret = find_job_list(it)
            if ret:
                return ret
    return []

def drain_packets(page, seconds: float=1.2):
    packets = []
    end = time.time() + seconds
    while time.time() < end:
        try:
            pkt = page.listen.wait(timeout=0.4)
        except Exception:
            break
        if pkt:
            packets.append(pkt)
    return packets

def update_api_maps_from_packets(packets, api_by_id, api_by_tc):
    for p in packets:
        try:
            url = getattr(p, 'url', '') or ''
            low = url.lower()
            if not any((k in low for k in ['job', 'search', 'recommend', 'rec'])):
                continue
            resp = getattr(p, 'response', None)
            if not resp:
                continue
            data = try_parse_json(getattr(resp, 'body', None))
            if data is None:
                continue
            jobs = find_job_list(data)
            if not jobs:
                continue
            for j in jobs:
                if not isinstance(j, dict):
                    continue
                title = (j.get('jobName') or j.get('positionName') or '').strip()
                company = (j.get('brandName') or j.get('companyName') or '').strip()
                jid = str(j.get('encryptJobId') or j.get('jobId') or j.get('securityId') or '').strip()
                salary = j.get('salaryDesc') or j.get('salary') or (f"{j.get('salaryMin', '')}-{j.get('salaryMax', '')}" + (j.get('salaryUnit', '') or '')).strip('-') or ''
                obj = {'api_job_id': jid, 'api_title': title, 'api_company': company, 'api_salary': str(salary).strip(), 'api_raw': j, 'api_url': url}
                if jid:
                    api_by_id[jid] = obj
                if title or company:
                    api_by_tc[f'{title}|{company}'] = obj
        except Exception:
            pass

def close_popups(page) -> None:
    selectors = ['css:.satisfaction-dialog .close', 'css:.satisfaction-dialog .ui-icon-close', 'css:.satisfaction-close', 'css:.dialog-wrap .close', 'css:.layer-close', 'css:.overseas-nav-box .close', 'css:.overseas-nav-box .ui-icon-close']
    for sel in selectors:
        try:
            btn = page.ele(sel, timeout=0.4)
            if btn:
                btn.click(by_js=True)
        except Exception:
            pass

def get_cards(page):
    return page.eles('css:.rec-job-list .card-area .job-card-wrap', timeout=3) or []

def get_card_records(page) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for card in get_cards(page):
        href = safe_attr(card, 'css:.job-title .job-name', 'href')
        if not href:
            continue
        records.append({'href': href, 'job_id': extract_job_id_from_href(href), 'title': safe_text(card, 'css:.job-name'), 'salary': safe_text(card, 'css:.job-salary'), 'tags': safe_texts(card, 'css:.tag-list li'), 'company': safe_text(card, 'css:.boss-name'), 'location': safe_text(card, 'css:.company-location')})
    return records

def find_card_by_href(page, href: str):
    for card in get_cards(page):
        card_href = safe_attr(card, 'css:.job-title .job-name', 'href')
        if card_href == href:
            return card
    return None

def scroll_left_list(page) -> None:
    js = "\n(() => {\n  const container = document.querySelector('.job-list-container');\n  const list = document.querySelector('.rec-job-list');\n  if (container) {\n    container.scrollTop += Math.max(700, Math.floor(container.clientHeight * 0.92));\n  }\n  if (list && list.lastElementChild) {\n    list.lastElementChild.scrollIntoView({block: 'end', inline: 'nearest'});\n  }\n  window.scrollBy(0, 550);\n  return true;\n})();\n"
    try:
        page.run_js(js)
    except Exception:
        try:
            page.scroll.down(800)
        except Exception:
            pass

def _search_keyword(page, keyword: str) -> None:
    page.get(getattr(page, 'search_home', 'https://www.zhipin.com/web/geek/job'))
    must_ele(page, 'css:.rec-job-list', timeout=25)
    close_popups(page)
    search_input = must_ele(page, 'css:input.input', timeout=10)
    search_btn = must_ele(page, 'css:a.search-btn', timeout=10)
    search_input.clear()
    search_input.input(keyword)
    search_btn.click()
    must_ele(page, 'css:.rec-job-list .card-area .job-card-wrap', timeout=25)

def _wait_detail_box(page, timeout: int=3):
    end = time.time() + timeout
    while time.time() < end:
        detail = page.ele('css:.job-detail-box', timeout=1)
        if detail:
            return detail
        time.sleep(0.2)
    return None

def _click_and_extract_detail(page, href: str) -> Dict[str, Any]:
    card = find_card_by_href(page, href)
    if not card:
        return {}
    try:
        close_popups(page)
        card.scroll.to_see()
    except Exception:
        pass
    clicked = False
    try:
        anchor = card.ele('css:.job-title .job-name', timeout=1)
        if anchor:
            anchor.click(by_js=True)
            clicked = True
    except Exception:
        pass
    if not clicked:
        try:
            card.click(by_js=True)
            clicked = True
        except Exception:
            return {}
    detail = _wait_detail_box(page)
    if not detail:
        return {}
    detail_title = safe_text(detail, 'css:.job-detail-info .job-name')
    detail_salary = safe_text(detail, 'css:.job-detail-info .job-salary')
    detail_tags = safe_texts(detail, 'css:.job-detail-header .tag-list li')
    jd_labels = safe_texts(detail, 'css:.job-label-list li')
    jd_desc = clean_desc_text(safe_text(detail, 'css:.desc'))
    hr_name = safe_text(detail, 'css:.job-boss-info .name')
    hr_title = safe_text(detail, 'css:.job-boss-info .boss-info-attr')
    address = safe_text(detail, 'css:.job-address-desc')
    more_info_url = safe_attr(detail, 'css:.more-job-btn', 'href')
    more_info_url = urljoin('https://www.zhipin.com', more_info_url) if more_info_url else ''
    try:
        detail_html = detail.html
    except Exception:
        detail_html = ''
    return {'detail_title': detail_title, 'detail_salary': detail_salary, 'detail_tags': detail_tags, 'jd_labels': jd_labels, 'jd_description': jd_desc, 'hr_name': hr_name, 'hr_title': hr_title, 'address': address, 'more_info_url': more_info_url, 'detail_html': detail_html}

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

def collect_jobs_by_keyword(page, keyword: str, max_jobs: int=80, max_scroll_rounds: int=18) -> List[Dict[str, Any]]:
    try:
        page.listen.start(['joblist', 'search', 'zpgeek', 'job_rec', 'recommend'])
    except Exception:
        pass
    _search_keyword(page, keyword)
    api_by_id = {}
    api_by_tc = {}
    update_api_maps_from_packets(drain_packets(page, seconds=2.0), api_by_id, api_by_tc)
    results: List[Dict[str, Any]] = _streaming_list()
    seen_hrefs = set()
    for round_idx in range(max_scroll_rounds):
        close_popups(page)
        cards = get_card_records(page)
        for record in cards:
            href = record.get('href', '')
            if not href or href in seen_hrefs:
                continue
            api_preview = api_by_id.get(record.get('job_id', '')) or api_by_tc.get(f"{record.get('title', '').strip()}|{record.get('company', '').strip()}") or {}
            preview = dict(record, source='boss', job_url=urljoin('https://www.zhipin.com', href), salary=api_preview.get('api_salary') or record.get('salary',''))
            if globals().get('_skip_record', lambda row: False)(preview):
                seen_hrefs.add(href)
                continue
            detail_data = _click_and_extract_detail(page, href)
            update_api_maps_from_packets(drain_packets(page, seconds=0.8), api_by_id, api_by_tc)
            job_id = record.get('job_id', '')
            left_title = record.get('title', '')
            left_company = record.get('company', '')
            detail_title = detail_data.get('detail_title')
            api_hit = None
            if job_id and job_id in api_by_id:
                api_hit = api_by_id[job_id]
            else:
                key = f'{(detail_title or left_title).strip()}|{left_company.strip()}'
                api_hit = api_by_tc.get(key)
            salary_from_page = detail_data.get('detail_salary') or record.get('salary', '')
            salary = api_hit['api_salary'] if api_hit and api_hit.get('api_salary') else salary_from_page
            item = {'job_id': job_id, 'job_name': detail_title or left_title, 'salary': salary, 'salary_from_page': salary_from_page, 'tags': detail_data.get('detail_tags') or record.get('tags', []), 'company': left_company, 'location': record.get('location', ''), 'job_url': urljoin('https://www.zhipin.com', href), 'more_info_url': detail_data.get('more_info_url', ''), 'jd_labels': detail_data.get('jd_labels', []), 'jd_description': detail_data.get('jd_description', ''), 'hr_name': detail_data.get('hr_name', ''), 'hr_title': detail_data.get('hr_title', ''), 'address': detail_data.get('address', ''), 'detail_html': detail_data.get('detail_html', ''), 'api_matched': bool(api_hit), 'api_url': (api_hit or {}).get('api_url', ''), 'keyword': keyword}
            results.append(item)
            seen_hrefs.add(href)
            if len(results) >= max_jobs:
                return results
        scroll_left_list(page)
        time.sleep(0.8)
        # Replaying saved cards is not evidence that the remaining list is empty.
    return results

def save_jobs_json(jobs: List[Dict[str, Any]], output_file: str) -> None:
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)
