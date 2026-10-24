"""ATS collection core: injected verified transport, no implicit network/login.

Moka and Beisen share orchestration, not response schemas. See references/ats.md.
"""
from dataclasses import dataclass, asdict
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit
from workflow import read, write, now


class ATSError(ValueError):
    pass


@dataclass(frozen=True)
class Site:
    platform: str
    tenant: str
    company: str
    host: str
    org_id: str = ''
    site_id: int = 0
    category: int = 1
    page_size: int = 10

    def __post_init__(self):
        u = urlsplit(self.host)
        if (self.platform not in ('moka', 'beisen') or not self.tenant.strip()
                or not self.company.strip() or u.scheme != 'https' or not u.hostname
                or u.username or u.password or u.query or u.fragment or u.path not in ('', '/')
                or not 1 <= self.page_size <= 100):
            raise ValueError('Invalid ATS tenant configuration')
        if self.platform == 'moka' and (not self.org_id or self.site_id <= 0):
            raise ValueError('Moka requires verified org_id and site_id')
        if self.platform == 'beisen' and self.category not in (1, 2):
            raise ValueError('Beisen category must be 1 (social) or 2 (campus)')

    @property
    def source_id(self):
        return f'{self.platform}:{self.tenant}'


@dataclass
class Page:
    rows: list
    total: int | None


def total_count(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ATSError('INVALID_TOTAL')
    return value


def records(value):
    if not isinstance(value, list) or any(not isinstance(x, dict) for x in value):
        raise ATSError('INVALID_LIST_SCHEMA')
    return value


class Moka:
    def request(self, site, page, keyword):
        return site.host.rstrip('/') + '/api/outer/ats-apply/website/jobs/module', {
            'orgId': site.org_id, 'siteId': site.site_id,
            'pagination': {'pageIndex': page, 'pageSize': site.page_size}, 'locale': 'zh-cn'}

    def parse(self, response):
        # Transport must deliver verified decoded JSON; no hardcoded keys or cookies.
        if isinstance(response.get('data'), str):
            raise ATSError('ENCODED_RESPONSE_REQUIRES_VERIFIED_DECODER')
        data = response.get('data')
        if not isinstance(data, dict) or 'jobs' not in data:
            raise ATSError('MOKA_LIST_SCHEMA_CHANGED')
        return Page(records(data['jobs']), total_count((data.get('jobStats') or {}).get('total')))

    def identity(self, row):
        return row.get('id'), row.get('title')


class Beisen:
    def request(self, site, page, keyword):
        return site.host.rstrip('/') + '/api/Jobad/GetJobAdPageList', {
            'pageIndex': page, 'pageSize': site.page_size, 'keyWord': keyword,
            'category': site.category, 'langType': 'zh_CN'}

    def parse(self, response):
        if response.get('Code') != 200:
            raise ATSError('BEISEN_RESPONSE_ERROR')
        return Page(records(response.get('Data')), total_count(response.get('Count', response.get('Total'))))

    def identity(self, row):
        # Id is the public detail identifier; retain JobAdId separately in raw.
        return row.get('Id'), row.get('JobAdName')


ADAPTERS = {'moka': Moka, 'beisen': Beisen}


def normalize(site, adapter, raw):
    job_id, title = adapter.identity(raw)
    if not job_id or not str(title or '').strip():
        raise ATSError('MISSING_JOB_ID_OR_TITLE')
    identity = f'{site.source_id}:{job_id}'
    location = next((raw[k] for k in ('LocationName', 'LocName', 'WorkPlace', 'WorkCity', 'Location')
                     if isinstance(raw.get(k), str) and raw[k].strip()), '')
    return {'source': site.platform, 'source_id': site.source_id, 'company': site.company,
            'job_id': str(job_id), 'job_key': 'ats-' + sha256(identity.encode()).hexdigest()[:24],
            'job_name': str(title).strip(), 'location': location, 'job_url': '',
            'jd_description': '', 'detail_state': 'PENDING', 'detail_identity_verified': False,
            'list_raw': raw, 'last_seen_at': now()}


def collect(site, fetch_page, run_dir, **options):
    from runtime import writer_lock
    with writer_lock(run_dir):
        return _collect(site, fetch_page, run_dir, **options)


def _collect(site, fetch_page, run_dir, *, keyword='', max_pages=10, detail_reader=None, page_reader=None):
    """fetch_page(url, json_body)->dict; detail_reader(job)->independent detail evidence.

    Caller provides rate limits, verified endpoints, challenge/login handling, timeouts.
    Detail evidence needs job_id, source_id, actual URL/title/company/body, truncated bool.
    This core never makes HTTP calls or executes supplied website scripts.
    """
    if max_pages < 1:
        raise ValueError('max_pages must be positive')
    # Moka keyword parameter is not verified. Do not silently pretend to filter.
    if page_reader is None and site.platform == 'moka' and keyword:
        raise ValueError('Moka server keyword filtering not verified; collect then screen')
    root = Path(run_dir)
    root.mkdir(parents=True, exist_ok=True)
    config = asdict(site)
    if (root/'site.json').exists() and read(root/'site.json') != config:
        raise ValueError('Use a separate directory for a different tenant/configuration')
    write(root/'site.json', config)
    jobs = {j['job_key']: j for j in read(root/'jobs.json')} if (root/'jobs.json').exists() else {}
    adapter = ADAPTERS[site.platform]()
    seen, signatures = set(), set()
    result = dict(source_id=site.source_id, keyword=keyword, pages=0, raw_fetched=0,
                  unique_fetched=0, reported_total=None, complete=False, status='RUNNING', errors=[])
    try:
        for number in range(1, max_pages + 1):
            if page_reader is not None:
                page = page_reader(number, keyword)
                if not isinstance(page, Page):
                    raise ATSError('DOM_READER_MUST_RETURN_PAGE')
                records(page.rows)
                total_count(page.total)
            else:
                url, body = adapter.request(site, number, keyword)
                response = fetch_page(url, body)
                # Offline/legacy parsing path; DP entry never uses this transport.
                page = adapter.parse(response)
            previous_total = result['reported_total']
            if previous_total is not None and page.total != previous_total:
                raise ATSError('TOTAL_CHANGED_DURING_SCAN')
            result.update(pages=number, reported_total=page.total)
            batch = [normalize(site, adapter, raw) for raw in page.rows]
            signature = tuple(j['job_key'] for j in batch)
            if batch and (signature in signatures or all(k in seen for k in signature)):
                raise ATSError('REPEATED_PAGE_NO_PROGRESS')
            signatures.add(signature)
            write(root/'pages'/f'{number:05}.json', {'at':now(), 'rows':page.rows, 'total':page.total})
            result['raw_fetched'] += len(batch)
            for job in batch:
                key = job['job_key']
                seen.add(key)
                old = jobs.get(key)
                if old:
                    # Preserve full JD, review history and user fields on list refresh.
                    merged = dict(old, **{k:v for k,v in job.items() if k in
                                  ('job_name','location','list_raw','last_seen_at')})
                    if old['job_name'] != job['job_name']:
                        merged.update(detail_state='PENDING', detail_identity_verified=False)
                    job = merged
                jobs[key] = job
                write(root/'jobs.json', list(jobs.values()))
                if detail_reader and job['detail_state'] != 'READY':
                    try:
                        data = detail_reader(dict(job))
                        jobs[key] = accept_detail(site, job, data)
                    except Exception as exc:
                        jobs[key] = dict(job, detail_state='READ_ERROR', detail_read_error=str(exc))
                        result['errors'].append({'job_key':key, 'error':str(exc)})
                    write(root/'jobs.json', list(jobs.values()))
            result['unique_fetched'] = len(seen)
            write(root/'coverage.json', result)
            if page.total is not None and len(seen) == page.total:
                result.update(complete=True, status='COMPLETE')
                break
            if not batch:
                result['status'] = 'END_UNVERIFIED' if page.total is None else 'PARTIAL'
                break
            if page.total is not None and len(seen) > page.total:
                raise ATSError('COUNT_EXCEEDS_TOTAL')
        else:
            result['status'] = 'BUDGET_COMPLETED'
    except Exception as exc:
        result.update(status='PARTIAL' if seen else 'FAILED', complete=False)
        result['errors'].append({'error':str(exc)})
    except BaseException:
        result.update(status='INTERRUPTED', complete=False)
        raise
    finally:
        result['unique_fetched'] = len(seen)
        result['ready_count'] = sum(j['detail_state'] == 'READY' for k,j in jobs.items() if k in seen)
        result['finished_at'] = now()
        write(root/'jobs.json', list(jobs.values()))
        write(root/'coverage.json', result)
    return result


def accept_detail(site, job, data):
    from detail import apply_detail
    if (str(data.get('job_id', '')) != job['job_id'] or data.get('source_id') != site.source_id
            or not isinstance(data.get('truncated'), bool)):
        raise ATSError('DETAIL_ID_OR_COMPLETENESS_NOT_VERIFIED')
    u = urlsplit(data.get('url', ''))
    if u.scheme != 'https' or u.netloc != urlsplit(site.host).netloc or not u.path or u.username:
        raise ATSError('UNEXPECTED_DETAIL_ORIGIN')
    # List may have no URL. Bind only after independent tenant + job ID verification.
    expected = dict(job, job_url=job.get('job_url') or data['url'])
    return apply_detail(expected, data)
