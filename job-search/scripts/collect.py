"""Sequential JobMiner collection with explicit human checkpoints; no LLM API."""
import argparse
import importlib
import json
import os
import random
import re
import time
import uuid
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from salary import salary_gate
from workflow import canonical_url
from runtime import Runtime, writer_lock
from session import inspect_login, connect_owned

SOURCES = {
    'boss': ('extract_boss', 'https://www.zhipin.com'),
    'zhilian': ('extract_zhilian', 'https://www.zhaopin.com'),
    'waiqi': ('extract_shenxianwaiqi', 'https://waiqi.com/position'),
    'liepin': ('extract_liepin', 'https://www.liepin.com'),
    '51job': ('extract_51job', 'https://www.51job.com'),
}


class StopSite(BaseException):
    """Bypass legacy adapters' broad except Exception so collection really stops."""


class UserWaitTimeout(StopSite):
    pass


class UserActionRequired(StopSite):
    """A durable checkpoint; the caller returns without polling or closing tabs."""
    pass


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


class Guard:
    def __init__(self, run_dir, source, delay_min=0.25, delay_max=0.65, wait_seconds=1800, batch_size=0, batch_rest=0):
        self.run_dir = Path(run_dir)
        self.source = source
        self.delay_min, self.delay_max = delay_min, delay_max
        self.wait_seconds = wait_seconds
        self.last_checks = {}
        self.batch_size, self.batch_rest, self.actions = batch_size, batch_rest, 0
        self.runtime = Runtime(run_dir)

    def event(self, state, **fields):
        current = self.runtime.load()
        phase = current['phase']
        if state == 'COLLECTING': phase = 'search'
        if state.startswith('CONTACT_') or state.startswith('FORM_'): phase = 'execute'
        changes = dict(phase=phase, legacy_event=state)
        if current['task_state'] != 'PAUSED':
            changes['task_state'] = ('BUDGET_COMPLETED' if state == 'COMPLETED' else
                                     'FAILED' if state.endswith('STOPPED') else 'RUNNING')
        self.runtime.commit(**changes)
        data = dict(state=state, source=self.source, pid=os.getpid(), at=datetime.now(timezone.utc).isoformat(), **fields)
        atomic_json(self.run_dir / 'status.json', data)
        with (self.run_dir / 'events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(data, ensure_ascii=False) + '\n')
        print(json.dumps(data, ensure_ascii=False), flush=True)

    def pause(self, reason, page, pause_reason=None):
        pause_reason = pause_reason or getattr(self, 'detected_pause_reason', 'CHALLENGE')
        state = self.runtime.pause(pause_reason, url=str(page.url), message=reason)
        self.event('WAITING_FOR_USER', reason=reason, url=str(page.url), token=state['token'])
        raise UserActionRequired(reason)

    def preflight(self, page):
        self.check(page, force=True)
        config = self.runtime.config.get('session', {})
        result = inspect_login(page, self.source, config)
        atomic_json(self.run_dir / 'login-check.json', result)
        if result['status'] != 'authenticated':
            self.pause('请登录后回复继续；恢复时会重新核实登录状态', page,
                       'LOGIN_REQUIRED' if result['status'] == 'logged_out' else 'LOGIN_UNKNOWN')
        self.runtime.start(self.runtime.load()['phase'])
        return result

    def blocking_reason(self, page):
        self.detected_pause_reason = 'CHALLENGE'
        url = str(page.url or '').lower()
        # Inspect visible text only. Do not mistake hidden login panels for a challenge.
        try:
            if hasattr(page, 'run_js'):
                text = page.run_js("return document.body ? document.body.innerText : '';") or ''
            else:
                body = page.ele('css:body', timeout=0.2)
                text = (body.text if body else '') or ''
        except Exception as exc:
            raise StopSite('无法检查页面验证状态，停止当前站点') from exc
        if any(phrase in text.lower() for phrase in ('访问过于频繁', '请求过于频繁', '访问被拒绝', '访问受限', 'ip被拦截', 'ip 被拦截')):
            self.runtime.pause('ACCESS_DENIED', url=str(page.url), evidence='可见访问限制')
            raise StopSite('网站限流或拒绝访问，停止当前站点')
        if '_security_check' in url or re.search(r'/(?:login|passport|verify|security-check)(?:[/?#]|$)|captcha', url) or urlsplit(url).hostname == 'safe.liepin.com':
            if re.search(r'/(?:login|passport)(?:[/?#]|$)', url): self.detected_pause_reason = 'LOGIN_REQUIRED'
            return '当前页面需要登录或安全验证'
        phrases = ('扫码登录', '扫码登陆', '扫描二维码登录', '请完成验证', '请先完成登录',
                   '请先登录', '拖动滑块', '滑动验证', '安全验证', '人机验证',
                   '点击完成验证', '点击验证', '短信验证码', '请使用手机扫码', '请登录后查看')
        if any(phrase in text for phrase in phrases):
            if any(x in text for x in ('扫码登录','扫码登陆','请先登录','请先完成登录','请登录后查看')):
                self.detected_pause_reason = 'LOGIN_REQUIRED'
            return '页面出现扫码、登录或人工验证提示'
        return ''

    def check(self, page, force=False):
        state_path, marker = self.run_dir / 'status.json', self.run_dir / 'continue.json'
        current = self.runtime.load()
        confirmation = json.loads(marker.read_text(encoding='utf-8')) if marker.exists() else {}
        if current['task_state'] == 'PAUSED' and not current.get('acknowledged'):
            if (current.get('pause_reason') not in ('LOGIN_REQUIRED','LOGIN_UNKNOWN','CHALLENGE','USER_PAUSE')
                    or confirmation.get('token') != current.get('token')):
                raise UserActionRequired('任务暂停：' + current['pause_reason'])
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding='utf-8'))
            if state.get('state') == 'WAITING_FOR_USER':
                confirmation = json.loads(marker.read_text(encoding='utf-8')) if marker.exists() else {}
                if confirmation.get('token') != state.get('token'):
                    raise UserActionRequired(state.get('reason', '等待用户处理并确认'))
                marker.unlink()
                reason = self.blocking_reason(page)
                if reason:
                    self.pause(reason, page)
                result = inspect_login(page, self.source, self.runtime.config.get('session', {}))
                atomic_json(self.run_dir / 'login-check.json', result)
                if result['status'] != 'authenticated':
                    self.pause('用户确认后仍未能核实登录', page,
                               'LOGIN_REQUIRED' if result['status'] == 'logged_out' else 'LOGIN_UNKNOWN')
                self.runtime.start(self.runtime.load()['phase'])
                self.event('RUNNING', reason='用户确认后重新检查页面通过')
                force = True
        request = self.run_dir / 'pause.request'
        if request.exists():
            request.unlink(missing_ok=True)
            self.pause('用户要求暂停或检查页面', page, 'USER_PAUSE')
            force = True
        page_id = id(page)
        if not force and time.monotonic() - self.last_checks.get(page_id, 0) < 1.0:
            return
        while True:
            reason = self.blocking_reason(page)
            if not reason:
                self.last_checks[page_id] = time.monotonic()
                return
            self.pause(reason, page)

    def ensure_can_resume(self):
        state_path = self.run_dir / 'status.json'
        state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
        if state.get('state') == 'WAITING_FOR_USER':
            marker = self.run_dir / 'continue.json'
            confirmation = json.loads(marker.read_text(encoding='utf-8')) if marker.exists() else {}
            if confirmation.get('token') != state.get('token'):
                raise UserActionRequired(state.get('reason', '等待用户处理并确认'))
            current = self.runtime.load()
            if current['task_state'] == 'PAUSED' and not current.get('acknowledged'):
                self.runtime.acknowledge(confirmation['token'], '主 Agent 已根据用户明确回复保存 continue.json')
        try:
            self.runtime.ensure_ready()
        except ValueError as exc:
            raise UserActionRequired(str(exc)) from exc

    def delay(self):
        if self.batch_size and self.actions and self.actions % self.batch_size == 0:
            self.event('BATCH_REST', actions=self.actions, seconds=self.batch_rest)
            # Short sleep chunks keep interruption responsive; no website requests during rest.
            remaining = self.batch_rest
            while remaining > 0:
                chunk = min(remaining, 1)
                time.sleep(chunk)
                remaining -= chunk
        time.sleep(random.uniform(self.delay_min, self.delay_max))
        self.actions += 1


class Proxy:
    """Guard page/element operations, including detail tabs and parent elements."""
    def __init__(self, raw, guard, page=None):
        self._raw, self._guard = raw, guard
        self._page = page if page is not None else raw
        self.search_home = str(self._page.url)

    def wrap(self, value):
        if isinstance(value, list):
            return [self.wrap(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.wrap(item) for item in value)
        if hasattr(value, 'ele') and hasattr(value, 'eles'):
            is_page = hasattr(value, 'get') and hasattr(value, 'url')
            return Proxy(value, self._guard, None if is_page else self._page)
        return value

    def read_js(self, script, *args):
        """For explicitly read-only DOM extraction; legacy run_js remains guarded."""
        self._guard.check(self._page)
        return self._raw.run_js(script, *args)

    def __getattr__(self, name):
        if name == 'scroll':
            return ScrollProxy(getattr(self._raw, name), self._guard, self._page)
        if name in {'text', 'html'}:
            self._guard.check(self._page)
        attr = getattr(self._raw, name)
        if not callable(attr):
            return attr
        def invoke(*args, **kwargs):
            if name in {'close', 'quit'}:
                if self._guard.runtime.load()['task_state'] == 'PAUSED':
                    return None
                return attr(*args, **kwargs)
            mutates = name in {'get', 'new_tab', 'click', 'input', 'run_js', 'clear'}
            self._guard.check(self._page, force=mutates)
            if name in {'get', 'new_tab', 'click', 'run_js'}:
                self._guard.delay()
            result = attr(*args, **kwargs)
            # A new tab's verification must be checked before its contents are parsed.
            if name in {'get_tab', 'new_tab'} and result:
                self._guard.check(result, force=True)
            else:
                self._guard.check(self._page, force=mutates)
            return self.wrap(result)
        return invoke


class ScrollProxy:
    def __init__(self, raw, guard, page):
        self.raw, self.guard, self.page = raw, guard, page

    def __getattr__(self, name):
        def invoke(*args, **kwargs):
            self.guard.check(self.page, force=True)
            self.guard.delay()
            result = getattr(self.raw, name)(*args, **kwargs)
            self.guard.check(self.page, force=True)
            return result
        return invoke


def dedupe_keys(job):
    keys = []
    source = str(job.get('source', ''))
    if job.get('job_id'):
        keys.append('id:' + source + ':' + str(job['job_id']))
    if job.get('job_url'):
        p = urlsplit(str(job['job_url']))
        # Some sites identify a job in its query, so retain query parameters.
        keys.append('url:' + canonical_url(job['job_url']))
    name, company = str(job.get('job_name', '')).strip(), str(job.get('company', '')).strip()
    location = str(job.get('work_address') or job.get('address') or job.get('location') or '').strip()
    if not keys and name and company and location:
        keys.append(f'ncl:{name.casefold()}|{company.casefold()}|{location.casefold()}')
    return keys


def collect(args):
    with writer_lock(args.run_dir):
        return _collect(args)


def _collect(args):
    run_dir = Path(args.run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    if not args.resume and ((run_dir / 'jobs.json').exists() or (run_dir / 'status.json').exists()):
        raise ValueError('运行目录已有记录，请为新采集使用新的 run-dir，以免覆盖进度')
    keywords_data = json.loads(Path(args.keywords).read_text(encoding='utf-8-sig'))
    keywords = keywords_data.get('keywords') if isinstance(keywords_data, dict) else keywords_data
    if not isinstance(keywords, list) or not keywords or any(not isinstance(k, str) or not k.strip() for k in keywords):
        raise ValueError('keywords 必须是非空字符串列表')
    keywords = list(dict.fromkeys(k.strip() for k in keywords))
    sources = list(dict.fromkeys(args.sources))
    if len(sources) != 1:
        raise ValueError('每次仅采集一个网站；各站分别初筛、审阅和输出待办')
    random.shuffle(sources)
    if args.resume:
        jobs = json.loads((run_dir / 'jobs.json').read_text(encoding='utf-8'))
        failures_path = run_dir / 'failures.json'
        failures = json.loads(failures_path.read_text(encoding='utf-8')) if failures_path.exists() else []
    else:
        jobs, failures = [], []
    seen = {key for job in jobs for key in dedupe_keys(job)}
    skipped_path = run_dir / 'skipped.json'
    skipped = json.loads(skipped_path.read_text(encoding='utf-8')) if args.resume and skipped_path.exists() else []
    atomic_json(run_dir / 'jobs.json', jobs)
    run_config = dict(sources=sources, keywords=keywords, target=args.target,
                                         pages=args.pages, city_mode='website_default', scoring='AI',
                                         min_salary=getattr(args,'min_salary',None), salary_range=getattr(args,'salary_range','lower'))
    if args.resume and (run_dir / 'run.json').exists():
        if json.loads((run_dir / 'run.json').read_text(encoding='utf-8')) != run_config:
            raise ValueError('恢复参数已变化；请保留原批次并新建采集批次')
    atomic_json(run_dir / 'run.json', run_config)
    def save():
        atomic_json(run_dir / 'jobs.json', jobs)
        atomic_json(run_dir / 'failures.json', failures)
        atomic_json(run_dir / 'skipped.json', skipped)
    def accept(job, source):
        item = {k: v for k, v in job.items() if k not in {'detail_html', 'api_url', 'hr_name'}}
        item['source'] = source
        item['salary_check'] = salary_gate(item.get('salary'), getattr(args,'min_salary',None), getattr(args,'salary_range','lower'))
        keys = dedupe_keys(item)
        if any(key in seen for key in keys):
            return
        if len(jobs) >= args.target:
            return
        Runtime(run_dir).quota_gate(len(jobs) + sum(bool(j.get('jd_description')) for j in jobs))
        seen.update(keys)
        # Existing keys remain unchanged on resume; new keys survive campaign reordering.
        stable = str(item.get('job_id') or next((key for key in keys if key.startswith('url:')), '') or '|'.join(keys) or uuid.uuid4().hex)
        item['job_key'] = source + '-' + hashlib.sha256(stable.encode('utf-8')).hexdigest()[:24]
        item['collected_at'] = datetime.now(timezone.utc).isoformat()
        # Legacy card/detail adapters do not uniformly prove identity or completeness.
        item['detail_state'] = 'PENDING'
        item['detail_identity_verified'] = False
        jobs.append(item)
        save()

    coverage_path = run_dir / 'coverage.json'
    coverage = json.loads(coverage_path.read_text(encoding='utf-8')) if args.resume and coverage_path.exists() else []
    for source_idx, source in enumerate(sources):
        if len(jobs) >= args.target:
            break
        guard = Guard(run_dir, source, args.delay_min, args.delay_max, batch_size=getattr(args,'batch_size',0), batch_rest=getattr(args,'batch_rest',0))
        browser = None
        try:
            guard.ensure_can_resume()
            guard.runtime.quota_gate(len(jobs) + sum(bool(j.get('jd_description')) for j in jobs))
            module_name, home = SOURCES[source]
            adapter = importlib.import_module('adapters.' + module_name)
            profile_dir = Path(args.profile_dir).resolve() if getattr(args,'profile_dir',None) else run_dir / 'browser' / source
            profile_dir.mkdir(parents=True, exist_ok=True)
            if args.attach_port:
                browser = connect_owned(run_dir, args.attach_port)
            else:
                browser = adapter.create_browser(headless=False, profile_dir=profile_dir)
            atomic_json(run_dir / 'browser.json', {'source':source,'address':getattr(getattr(browser,'browser',None),'address',None),
                        'tab_id':getattr(browser,'tab_id',None),'run_dir':str(run_dir),'ownership_verified':True,
                        'profile_dir':str(profile_dir),'attached':bool(args.attach_port)})
            # Defaults come from the source module, including the actual position URL.
            home = getattr(adapter, 'WAIQI_POSITION_URL', home)
            home = getattr(adapter, 'HOME_URL', home)
            home = getattr(adapter, 'LIEPIN_HOME', home)
            if not args.attach_port:
                browser.get(home)
            guard.preflight(browser)
            page = Proxy(browser, guard)
            search_url = guard.runtime.config.get('search_url')
            if search_url:
                if source != 'boss' or urlsplit(search_url).hostname not in ('www.zhipin.com','zhipin.com') or urlsplit(search_url).path not in ('/web/geek/job','/web/geek/jobs'):
                    raise ValueError('搜索入口必须是已核实的本站职位搜索页')
                page.search_home = search_url
            else:
                page.search_home = SOURCES[source][1] if source != 'boss' else 'https://www.zhipin.com/web/geek/job'
            quota = max(1, (args.target - len(jobs) + len(sources) - source_idx - 1) // (len(sources) - source_idx))
            before = len(jobs)
            site_keywords = keywords[:]
            done_keywords = {c['keyword'] for c in coverage if c.get('state') in ('ZERO_RESULTS','BUDGET_COMPLETED')}
            site_keywords = [kw for kw in site_keywords if kw not in done_keywords]
            original_collector = adapter.collect_jobs_by_keyword
            # Stream each appended record so a later verification/crash preserves earlier jobs.
            class StreamingList(list):
                def append(self, item):
                    super().append(item)
                    accept(item, source)
            original_collector.__globals__['_streaming_list'] = StreamingList
            def skip_record(record):
                if any(key in seen for key in dedupe_keys(record)):
                    duplicate_seen[0] += 1
                    return True
                gate = salary_gate(record.get('salary'),getattr(args,'min_salary',None),getattr(args,'salary_range','lower'))
                if gate['status'] == 'below':
                    skipped.append(dict(record,salary_check=gate))
                    save()
                    return True
                return False
            original_collector.__globals__['_skip_record'] = skip_record
            # The bundled adapters initialize their result list using this factory.
            for keyword_index, kw in enumerate(site_keywords):
                remaining = min(args.target - len(jobs), quota - (len(jobs) - before))
                if remaining <= 0:
                    break
                guard.event('COLLECTING', keyword=kw, kept=len(jobs))
                guard.runtime.commit(checkpoint={'keyword':kw,'saved_job_keys':[j['job_key'] for j in jobs],
                                                 'completed_keywords':sorted(done_keywords)})
                guard.check(browser, force=True)
                guard.delay()
                per_keyword = getattr(args,'per_keyword',None) or max(1, (remaining + len(site_keywords) - keyword_index - 1) // (len(site_keywords) - keyword_index))
                options = {'max_jobs': min(30, remaining, per_keyword)}
                if source == 'boss':
                    options['max_scroll_rounds'] = max(4, args.pages * 5)
                else:
                    options['max_pages'] = args.pages
                try:
                    start_count = len(jobs)
                    duplicate_seen = [0]
                    result = original_collector(page, kw, **options)
                    guard.check(browser, force=True)
                    for job in result:
                        accept(job, source)
                    if not result and len(jobs) == start_count:
                        if duplicate_seen[0]:
                            coverage.append(dict(source=source,keyword=kw,state='BUDGET_COMPLETED',
                                                 pages_budget=args.pages,new_jobs=0,replayed_records=duplicate_seen[0],
                                                 coverage_scope='configured_window',url=str(browser.url)))
                            atomic_json(coverage_path,coverage)
                            guard.event('KEYWORD_DONE',keyword=kw,kept=len(jobs),replayed_records=duplicate_seen[0])
                            continue
                        excerpt = browser.run_js("return document.body ? document.body.innerText : ''; ") or ''
                        confirmed_zero = any(phrase in excerpt for phrase in ('没有找到相关职位', '暂无相关职位', '暂无职位', '没有搜索到'))
                        outcome = 'ZERO_RESULTS' if confirmed_zero else 'EMPTY_UNVERIFIED'
                        coverage.append(dict(source=source,keyword=kw,state=outcome,pages_budget=args.pages,new_jobs=len(jobs)-start_count,url=str(browser.url)))
                        atomic_json(coverage_path,coverage)
                        if not confirmed_zero:
                            raise ValueError('未读取到岗位，页面也未明确显示零结果；需检查加载或适配器')
                        save()
                        guard.event(outcome, keyword=kw)
                        continue
                    coverage.append(dict(source=source,keyword=kw,state='BUDGET_COMPLETED',pages_budget=args.pages,new_jobs=len(jobs)-start_count,url=str(browser.url)))
                    atomic_json(coverage_path,coverage)
                    guard.event('KEYWORD_DONE', keyword=kw, kept=len(jobs))
                except StopSite:
                    raise
                except Exception as exc:
                    failures.append(dict(source=source, keyword=kw, error=str(exc)))
                    save()
                    guard.event('SOURCE_STOPPED', reason=str(exc))
                    return 1
        except UserActionRequired:
            save()
            return 2
        except (StopSite, Exception) as exc:
            failures.append(dict(source=source, error=str(exc)))
            save()
            guard.event('SOURCE_STOPPED', reason=str(exc))
            if isinstance(exc, UserWaitTimeout):
                guard.event('STOPPED', kept=len(jobs), reason='等待用户超时，未继续后续站点')
            return 1
        finally:
            state = json.loads((run_dir/'status.json').read_text(encoding='utf-8')) if (run_dir/'status.json').exists() else {}
            if browser and getattr(args,'close_browser',False) and not args.attach_port and state.get('state') != 'WAITING_FOR_USER':
                try:
                    browser.quit()
                except Exception:
                    pass
    save()
    Guard(run_dir, '').event('COMPLETED', kept=len(jobs), failures=len(failures), coverage_scope='configured_budget', unsearched_keywords=[kw for kw in keywords if not any(c['keyword']==kw for c in coverage)])
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--keywords')
    parser.add_argument('--run-dir')
    parser.add_argument('--sources', nargs='+', choices=list(SOURCES), default=['boss'])
    parser.add_argument('--target', type=int, default=60)
    parser.add_argument('--pages', type=int, default=2)
    parser.add_argument('--delay-min', type=float, default=0.25)
    parser.add_argument('--delay-max', type=float, default=0.65)
    parser.add_argument('--batch-size', type=int, default=0, help='每批受控浏览器操作数量；0关闭批次休息')
    parser.add_argument('--batch-rest', type=float, default=0, help='批次之间休息秒数；按当前任务配置')
    parser.add_argument('--close-browser', action='store_true', help='仅关闭本次新建且不在等待用户的浏览器；默认保留窗口')
    parser.add_argument('--resume', action='store_true', help='保留已有 jobs.json 并继续采集，需确认原进程已停止')
    parser.add_argument('--attach-port', type=int, help='恢复时连接已确认属于本次运行的浏览器端口')
    parser.add_argument('--profile-dir', help='显式复用已核实属于本任务的浏览器配置目录，仅限单个来源')
    parser.add_argument('--reuse-login', action='store_true', help='已有登录状态可确认时跳过重复登录确认；新验证仍暂停')
    parser.add_argument('--min-salary', type=float, help='税前月薪最低期望（元）')
    parser.add_argument('--salary-range', choices=['lower','upper'], default='lower')
    parser.add_argument('--per-keyword', type=int, help='每个关键词采集上限，保证多个搜索方向均有预算')
    parser.add_argument('--continue-run')
    parser.add_argument('--token')
    args = parser.parse_args()
    if args.continue_run:
        run_dir = Path(args.continue_run)
        state = json.loads((run_dir / 'status.json').read_text(encoding='utf-8'))
        if state.get('state') != 'WAITING_FOR_USER' or not args.token or args.token != state.get('token'):
            parser.error('状态或 token 不匹配；只能继续当前等待用户的采集')
        atomic_json(run_dir / 'continue.json', {'token': args.token})
        print('确认已保存；原进程已退出，请重新运行原命令并加 --resume --attach-port <已核实端口>，页面仍须重新检查', flush=True)
        return
    if not args.keywords or not args.run_dir:
        parser.error('需要 --keywords 和 --run-dir')
    if args.target < 1 or args.pages < 1 or args.delay_min < 0 or args.delay_max < args.delay_min:
        parser.error('target/pages 必须大于零；delay-min 不小于零，delay-max 不小于 delay-min')
    if args.attach_port and (not args.resume or len(args.sources) != 1):
        parser.error('attach-port 仅用于 resume 且仅指定一个已确认属于本次运行的站点')
    if args.profile_dir and len(args.sources) != 1:
        parser.error('profile-dir 仅限单个已核实来源')
    if args.per_keyword is not None and args.per_keyword < 1:
        parser.error('per-keyword 必须大于零')
    if args.batch_size < 0 or args.batch_rest < 0 or (args.batch_rest and not args.batch_size):
        parser.error('批次参数不能为负；休息需要设置batch-size')
    raise SystemExit(collect(args) or 0)


if __name__ == '__main__':
    import sys
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    try:
        main()
    except Exception as exc:
        print(json.dumps({'state':'ERROR','error_type':type(exc).__name__,'reason':str(exc)},ensure_ascii=False),flush=True)
        raise SystemExit(1)
