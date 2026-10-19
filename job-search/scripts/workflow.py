"""Validate one-site AI screening/reviews, build todos, and record outcomes."""
import argparse
import json
import re
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def pointer(data, ref):
    if not isinstance(ref, str) or not ref.startswith('/'):
        raise ValueError('需要 JSON Pointer')
    value = data
    for part in ref[1:].split('/'):
        part = part.replace('~1', '/').replace('~0', '~')
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def now():
    return datetime.now(timezone.utc).isoformat()


def append_ledger(path, event):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'at':now(), **event}, ensure_ascii=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def read_ledger(path):
    path = Path(path)
    if not path.exists():
        return []
    events = []
    for number, line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError('事件不是对象')
        except (ValueError, TypeError) as exc:
            raise ValueError(f'历史记录第{number}行损坏，停止操作；保留原文件待核实') from exc
        events.append(event)
    return events


def canonical_url(value):
    p = urlsplit(str(value or ''))
    if not p.netloc:
        return ''
    # BOSS HC identity is in the path; tracking/security query values rotate.
    query = '' if p.hostname in ('www.zhipin.com', 'zhipin.com') and p.path.startswith('/job_detail/') else p.query
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip('/'), query, ''))


def same_job(left, right):
    lk, rk = str(left.get('job_key') or ''), str(right.get('job_key') or '')
    if lk and lk == rk and not re.fullmatch(r'job-\d+', lk) and (left.get('source') or left.get('site')) == (right.get('source') or right.get('site')):
        return True
    lu = canonical_url(left.get('job_url') or left.get('url'))
    ru = canonical_url(right.get('job_url') or right.get('url'))
    if lu and ru:
        if lu == ru:
            return True
        # Distinct links on one site may be separate HCs even with equal titles.
        if urlsplit(lu).netloc == urlsplit(ru).netloc:
            return False
    li, ri = left.get('identity') or identity(left), right.get('identity') or identity(right)
    if li and ri and li == ri and li.rsplit('|', 1)[-1]:
        return True
    return False


def merge_history(bundle, existing=None, ledger=None):
    existing = existing or {}
    previous = existing.get('todos', []) + existing.get('archived_todos', [])
    states = {'contacted': 5, 'submitted': 6, 'uncertain': 4, 'needs_input': 3, 'failed': 2, 'skipped': 1}
    for todo in bundle['todos']:
        matches = [old for old in previous if same_job(todo, old)]
        events = []
        for old in matches:
            events.extend(old.get('execution_history', []))
            if old.get('execution'):
                events.append(old['execution'])
            elif old.get('state') in states:
                events.append({'state': old['state'], 'evidence': '原待办状态', 'at': existing.get('created_at', '')})
        events.extend(event for event in (ledger or []) if same_job(todo, event))
        unique = {json.dumps(event, ensure_ascii=False, sort_keys=True): event for event in events}
        events = sorted(unique.values(), key=lambda e: str(e.get('at', '')))
        if events:
            todo['execution_history'] = events
            candidates = []
            for event in events:
                state = event.get('state')
                if state in ('contact_intent', 'submit_intent'):
                    state = 'uncertain'
                if state in states:
                    candidates.append((states[state], str(event.get('at', '')), state, event))
            if candidates:
                _, _, state, execution = max(candidates, key=lambda e: (e[0], e[1]))
                todo.update(state=state, execution=execution)
            pending_intents = {}
            for event in events:
                state = event.get('state')
                if state in ('contact_intent', 'submit_intent', 'upload_intent', 'message_intent'):
                    pending_intents[state.removesuffix('_intent')] = event
                elif state == 'contacted':
                    pending_intents.pop('contact', None)
                elif state == 'submitted':
                    pending_intents.pop('submit', None)
                elif state == 'uploaded':
                    pending_intents.pop('upload', None)
                elif state == 'message_sent':
                    pending_intents.pop('message', None)
            if pending_intents:
                todo.update(state='uncertain', unresolved_intents=list(pending_intents.values()))
    archived = [old for old in previous if (old.get('execution') or old.get('execution_history') or old.get('state') not in (None, 'pending'))
                and not any(same_job(old, current) for current in bundle['todos'])]
    bundle['archived_todos'] = list({json.dumps(t, ensure_ascii=False, sort_keys=True): t for t in archived}.values())
    return bundle


def identity(job):
    clean = lambda x: re.sub(r'\s+', '', str(x or '')).casefold()
    parts = [clean(job.get(k)) for k in ('company', 'job_name', 'location')]
    if not parts[2]:
        parts[2] = clean(job.get('work_address') or job.get('address'))
    # Empty employer/title never produces a cross-site identity.
    return '|'.join(parts) if all(parts[:2]) else None


def has_value(value):
    return value is not None and value != '' and value != []


def indexed(rows, site, label):
    if not isinstance(rows, list):
        raise ValueError(f'{label} 必须为数组')
    out = {}
    for row in rows:
        key = row.get('job_key')
        if not key or key in out or row.get('source') != site:
            raise ValueError(f'{label}: 重复/缺失 key 或包含其他网站')
        out[key] = row
    return out


def build(site, jobs, screens, reviews, candidate, review_policy=None):
    if review_policy is not None:
        from policy import validate_policy, validate_review
        validate_policy(review_policy)
    originals = indexed(jobs, site, 'jobs')
    screened = indexed(screens, site, 'screening')
    reviewed = indexed(reviews, site, 'reviews')
    if set(originals) != set(screened):
        raise ValueError('初筛必须恰好覆盖本站所有岗位')
    keep = set()
    for key, row in screened.items():
        if row.get('decision') not in ('keep', 'skip') or not row.get('reason'):
            raise ValueError(f'{key}: 初筛缺少决定/原因')
        if row['decision'] == 'skip' and not row.get('evidence'):
            raise ValueError(f'{key}: 初筛排除缺少依据')
        if row['decision'] == 'keep':
            keep.add(key)
    if set(reviewed) != keep:
        raise ValueError('每个初筛保留岗位必须且只能有一条逐岗审阅')
    todos = []
    for key, job in originals.items():
        screen = screened[key]
        review = reviewed.get(key)
        if review:
            if review_policy is not None:
                validate_review(review, job, candidate, review_policy)
            decision = review.get('decision')
            if decision not in ('apply', 'verify', 'skip'):
                raise ValueError(f'{key}: 审阅决定无效')
            fit = review.get('role_fit', {})
            if fit.get('status') not in ('pass', 'fail', 'unknown') or not fit.get('primary_duties'):
                raise ValueError(f'{key}: 缺少主要职责判断')
            # All text-bearing original fields are available as source evidence.
            text = str(job.get('jd_description') or job.get('description') or job.get('jd') or '')
            normalize = lambda s: re.sub(r'\s+', '', s)
            for quote in fit.get('jd_evidence', []):
                if not isinstance(quote, str) or not quote.strip() or normalize(quote) not in normalize(text):
                    raise ValueError(f'{key}: JD 引用不在原始记录中')
            for ref in fit.get('resume_evidence', []):
                if not ref.startswith('/cv/') or not has_value(pointer(candidate, ref)):
                    raise ValueError(f'{key}: 简历证据引用无效')
                parts=ref.split('/')
                evidence=candidate.get('evidence',{})
                record=next((evidence['/'.join(parts[:n])] for n in range(len(parts),1,-1) if '/'.join(parts[:n]) in evidence),{})
                if record.get('confirmed') is not True or not record.get('source'):
                    raise ValueError(f'{key}: 简历事实未确认/缺少来源')
            constraints = review.get('hard_constraints')
            risks = review.get('risks')
            questions = review.get('questions')
            if not isinstance(constraints, list) or not isinstance(risks, list) or not isinstance(questions, list):
                raise ValueError(f'{key}: 缺少条件/风险/问题数组')
            if any(c.get('status') not in ('pass', 'fail', 'unknown', 'not_applicable') or not c.get('name') or not c.get('evidence') for c in constraints):
                raise ValueError(f'{key}: 硬条件记录无效')
            if any(r.get('severity') not in ('blocking', 'caution') or not r.get('finding') or not r.get('evidence') for r in risks):
                raise ValueError(f'{key}: 风险记录无效')
            if decision == 'apply':
                if (fit['status'] != 'pass' or not fit.get('jd_evidence') or not fit.get('resume_evidence')
                    or (review_policy is None and not constraints) or any(c['status'] != 'pass' for c in constraints)
                    or any(r['severity'] == 'blocking' for r in risks) or questions):
                    raise ValueError(f'{key}: 未解决职责/门槛/风险/问题，不得申请')
                from salary import salary_gate
                policy = candidate.get('preferences',{}).get('salary',{})
                if review_policy is None and policy.get('advertised_upper_must_exceed') is not None:
                    gate = salary_gate(job.get('salary'),policy['advertised_upper_must_exceed'],'upper')
                    if gate.get('upper') is None or gate['upper'] <= policy['advertised_upper_must_exceed']:
                        raise ValueError(f'{key}: 广告薪资上限未满足用户设置')
            if decision == 'verify' and (not questions or any(not isinstance(q, str) or not q.strip() for q in questions)):
                raise ValueError(f'{key}: 待核实必须有具体问题')
            priority = review.get('priority')
            if type(priority) is not int or priority not in (1, 2, 3) or not review.get('reason'):
                raise ValueError(f'{key}: 缺少优先级/原因')
            reason = review['reason']
        else:
            decision, reason, priority, questions = 'skip', screen['reason'], 3, []
        todo = {k: job.get(k, '') for k in ('job_key', 'source', 'company', 'job_name', 'salary', 'job_url')}
        todo['location'] = job.get('location') or job.get('work_address') or job.get('address') or ''
        todo.update(todo_id=f'{site}:{key}', identity=identity(job), action=decision, reason=reason,
                    priority=priority, questions=questions, state='pending', review=review,
                    screening=screen)
        todos.append(todo)
    bundle = dict(schema_version=3 if review_policy is not None else 2, site=site, created_at=now(),
                  todos=sorted(todos, key=lambda t: (t['priority'], t['todo_id'])))
    if review_policy is not None:
        from runtime import digest, candidate_digest
        bundle['input_binding'] = dict(candidate_hash=candidate_digest(candidate), policy_hash=digest(review_policy),
                                       jobs_hash=digest(jobs), screening_hash=digest(screens))
    return bundle


def markdown(bundle):
    lines = [f'# {bundle["site"]} 求职待办', '', '每条保留初筛与审阅依据；结果按实际执行回写。', '']
    for todo in bundle['todos']:
        lines += [f'- [{"x" if todo["state"] in ("contacted", "submitted", "skipped") else " "}] P{todo["priority"]} {todo["action"]}：{todo["company"]} / {todo["job_name"]}',
                  f'  - 状态：{todo["state"]}；薪资：{todo["salary"]}',
                  f'  - 依据：{todo["reason"]}', f'  - 链接：{todo["job_url"]}']
        lines += [f'  - 问题：{q}' for q in todo['questions']]
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('build')
    for arg in ('site', 'jobs', 'screening', 'reviews', 'candidate', 'out-dir'):
        p.add_argument('--' + arg, required=True)
    p.add_argument('--ledger', help='可选的跨campaign共享沟通/申请记录，重建时恢复真实执行历史')
    p.add_argument('--policy', required=True, help='当前任务 review_policy.json')
    p = commands.add_parser('record')
    for arg in ('todos', 'todo-id', 'ledger', 'evidence'):
        p.add_argument('--' + arg, required=True)
    p.add_argument('--state', required=True, choices=['contacted', 'submitted', 'failed', 'uncertain', 'skipped', 'needs_input'])
    args = parser.parse_args()
    from runtime import writer_lock
    with writer_lock(args.out_dir if args.command == 'build' else Path(args.todos).parent):
        return execute(args)


def execute(args):
    if args.command == 'build':
        out = Path(args.out_dir)
        bundle = build(args.site, read(args.jobs), read(args.screening), read(args.reviews), read(args.candidate), read(args.policy))
        existing_path = out / 'todos.json'
        if existing_path.exists():
            existing = read(existing_path)
            if existing.get('site') != args.site:
                raise ValueError('待办目录属于其他网站')
        else:
            existing = None
        bundle = merge_history(bundle, existing, read_ledger(args.ledger) if args.ledger else [])
        write(out / 'todos.json', bundle)
        (out / 'todos.md').write_text(markdown(bundle), encoding='utf-8')
        print(json.dumps({'site':args.site, 'counts':{a:sum(t['action'] == a for t in bundle['todos']) for a in ('apply', 'verify', 'skip')}}, ensure_ascii=False))
    else:
        bundle = read(args.todos)
        todo = next((t for t in bundle['todos'] if t['todo_id'] == args.todo_id), None)
        if todo is None:
            raise ValueError('未知 todo-id')
        if not args.evidence.strip():
            raise ValueError('真实操作记录必须有证据；记录事实不授予执行权限')
        execution = dict(state=args.state, evidence=args.evidence, at=now())
        append_ledger(args.ledger, dict(site=bundle['site'], job_key=todo['job_key'], identity=todo['identity'], url=todo['job_url'], **execution))
        todo.update(state=args.state, execution=execution)
        todo.setdefault('execution_history', []).append(execution)
        write(args.todos, bundle)
        Path(args.todos).with_suffix('.md').write_text(markdown(bundle), encoding='utf-8')


if __name__ == '__main__':
    import sys
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    try:
        main()
    except Exception as exc:
        print(json.dumps({'state':'ERROR','error_type':type(exc).__name__,'reason':str(exc)},ensure_ascii=False),flush=True)
        raise SystemExit(1)
