"""Read complete JD batches from a verified task browser; deterministic identity checks."""
import argparse
from urllib.parse import urlsplit
from pathlib import Path
from workflow import read, write, canonical_url, now
from runtime import Runtime, writer_lock, digest
from collect import Guard, UserActionRequired, StopSite
from session import connect_owned


def extract(page, config):
    selectors = {k: config[k] for k in ('title', 'company', 'description')}
    return page.run_js('''const selectors=arguments[0]; const out={url:location.href};
    for (const [key, selector] of Object.entries(selectors)) {
      const e=document.querySelector(selector);
      out[key]=e && e.getClientRects().length ? e.innerText : '';
    }
    out.truncated=!!Array.from(document.querySelectorAll(arguments[1]||'[data-job-search-no-truncation-marker]'))
      .find(e=>e.getClientRects().length);
    out.fields={};
    for (const [key, selector] of Object.entries(arguments[2]||{})) {
      const e=document.querySelector(selector);
      out.fields[key]=e && e.getClientRects().length ? e.innerText : '';
    }
    return out;''', selectors, config.get('truncation_selector'), config.get('extra_fields', {}))


def apply_detail(job, data):
    if data.get('fields'):
        job = dict(job, detail_page_facts=data['fields'])
    norm = lambda text: ''.join(str(text or '').split()).casefold()
    if (canonical_url(job.get('job_url')) != canonical_url(data.get('url'))
            or not norm(job.get('company')) or norm(job['company']) != norm(data.get('company'))
            or not norm(job.get('job_name')) or norm(job['job_name']) != norm(data.get('title'))):
        return dict(job, detail_state='IDENTITY_MISMATCH', detail_identity_verified=False, detail_evidence=data)
    if not str(data.get('description') or '').strip() or data.get('truncated'):
        return dict(job, detail_state='INCOMPLETE', detail_identity_verified=True, detail_evidence=data)
    old = {k: job.get(k) for k in ('jd_description', 'jd_read_at', 'jd_hash', 'detail_evidence')}
    history = list(job.get('jd_history', []))
    if old.get('jd_description') and old['jd_description'] != data['description']:
        history.append(old)
    return dict(job, jd_description=data['description'], jd_read_at=now(), jd_hash=digest(data['description']),
                detail_state='READY', detail_identity_verified=True, detail_evidence=data, jd_history=history)


def run(args):
    jobs = read(args.jobs); screens = read(args.screening)
    if not isinstance(jobs, list) or not isinstance(screens, list): raise ValueError('jobs/screening 必须为数组')
    if len({j['job_key'] for j in jobs}) != len(jobs): raise ValueError('重复岗位键')
    site = {j['source'] for j in jobs}
    if len(site) != 1: raise ValueError('仅处理一个站点')
    source = next(iter(site)); guard = Guard(args.run_dir, source)
    config = read(args.selectors)
    if config.get('source') != source: raise ValueError('详情选择器站点不匹配')
    if not all(isinstance(config.get(k), str) and config[k].strip() for k in ('title','company','description')):
        raise ValueError('需要已核实的详情标题、公司和正文 CSS 选择器')
    keep = {s['job_key'] for s in screens if s.get('decision') == 'keep'}
    queue = [j['job_key'] for j in jobs if j['job_key'] in keep and (args.refresh or j.get('detail_state') != 'READY')]
    if args.limit < 1: raise ValueError('limit 必须大于零')
    try:
        guard.ensure_can_resume(); guard.runtime.quota_gate(sum(bool(j.get('jd_description')) for j in jobs) + len(jobs))
        page = connect_owned(args.run_dir, args.attach_port)
        guard.preflight(page); guard.runtime.start('detail')
        for key in queue[:args.limit]:
            index = next(i for i,j in enumerate(jobs) if j['job_key'] == key); job = jobs[index]
            if job.get('source') != source: raise ValueError('站点不符')
            # Check before navigation, including after quota or user resume.
            guard.runtime.quota_gate(len(jobs) + sum(j.get('detail_state') == 'READY' for j in jobs))
            guard.runtime.commit(checkpoint={'job_key':key,'remaining_job_keys':queue[queue.index(key):]})
            guard.check(page, force=True); guard.delay(); page.get(job['job_url']); guard.check(page, force=True)
            try:
                page.ele('css:' + config['description'], timeout=config.get('load_timeout_seconds', 12))
                guard.check(page, force=True)
                data = extract(page, config)
                jobs[index] = apply_detail(job, data)
            except Exception as exc:
                jobs[index] = dict(job, detail_state='READ_ERROR', detail_identity_verified=False, detail_error=str(exc))
            write(args.jobs, jobs)
            if jobs[index]['detail_state'] != 'READY':
                guard.runtime.pause('READ_ERROR', phase='detail', checkpoint={'job_key':key},
                                    detail_state=jobs[index]['detail_state'])
                return 2
        guard.runtime.commit(task_state='BUDGET_COMPLETED', checkpoint={'remaining_job_keys':queue[args.limit:]})
        return 0
    except UserActionRequired:
        return 2
    except (StopSite, Exception) as exc:
        guard.event('SOURCE_STOPPED', reason=str(exc)); return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ('jobs', 'screening', 'run-dir', 'selectors'): parser.add_argument('--'+field, required=True)
    parser.add_argument('--attach-port', type=int, required=True)
    parser.add_argument('--limit', type=int, default=10); parser.add_argument('--refresh', action='store_true')
    args = parser.parse_args()
    with writer_lock(args.run_dir): raise SystemExit(run(args))


if __name__ == '__main__': main()
