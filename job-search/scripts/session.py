"""Read-only login preflight and task-owned existing-browser connection."""
from urllib.parse import urlsplit
from workflow import read, now

HOSTS = {'boss': ['zhipin.com'], 'zhilian': ['zhaopin.com'], 'liepin': ['liepin.com'],
         '51job': ['51job.com'], 'waiqi': ['waiqi.com']}
DEFAULT_SIGNALS = {'boss': ['css:.nav-figure']}


def inspect_login(page, source, config=None):
    config = config or {}
    hosts = config.get('hosts') or HOSTS.get(source, [])
    host = (urlsplit(str(page.url)).hostname or '').lower()
    result = dict(status='unknown', source=source, url=str(page.url), checked_at=now(), evidence=[])
    if not hosts or not any(host == h or host.endswith('.' + h) for h in hosts):
        result['evidence'] = ['当前域名不在任务站点配置内']; return result
    try:
        text = page.run_js("return document.body ? document.body.innerText : '';") or ''
        if any(x in text for x in ('请先登录', '扫码登录', '扫码登陆', '请登录后查看')):
            result.update(status='logged_out', evidence=['可见登录提示']); return result
        for selector in config.get('signed_in_selectors', DEFAULT_SIGNALS.get(source, [])):
            element = page.ele(selector, timeout=0.5)
            if element and element.states.is_displayed:
                result.update(status='authenticated', evidence=['可见登录标识：' + selector]); return result
    except Exception as exc:
        result['evidence'] = ['无法核实登录标识：' + type(exc).__name__]; return result
    result['evidence'] = ['未找到明确的可见登录标识']
    return result


def connect_owned(run_dir, port, manifest_path=None):
    """Never launch/restart a browser while attaching. A task receipt is required first."""
    from pathlib import Path
    path = Path(manifest_path) if manifest_path else Path(run_dir) / 'browser.json'
    manifest = read(path)
    address = f'127.0.0.1:{port}'
    if (manifest.get('address') != address or not manifest.get('tab_id')
            or manifest.get('ownership_verified') is not True
            or manifest.get('run_dir') != str(Path(run_dir).resolve())):
        raise ValueError('端口/任务目录/标签页归属未核实；需主 Agent 核查并保存 browser.json')
    from DrissionPage import ChromiumOptions, ChromiumPage
    options = ChromiumOptions().set_address(address).existing_only()
    return ChromiumPage(addr_or_opts=options, tab_id=manifest['tab_id'])
