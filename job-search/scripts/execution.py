"""Revalidate current inputs and scoped human authorization immediately before execution."""
from workflow import read, build
from runtime import digest


def add_inputs(parser):
    for name in ('candidate', 'policy', 'source-jobs', 'screening', 'reviews'):
        parser.add_argument('--' + name, required=True)


def checked_bundle(args, bundle):
    candidate, policy = read(args.candidate), read(args.policy)
    jobs, screens, reviews = read(args.source_jobs), read(args.screening), read(args.reviews)
    fresh = build(bundle['site'], jobs, screens, reviews, candidate, policy)
    if bundle.get('schema_version') != 3 or fresh['input_binding'] != bundle.get('input_binding'):
        raise ValueError('待办缺少当前输入绑定或已过期；先重新审阅/build')
    stored = {t['job_key']: t for t in bundle['todos']}
    if set(stored) != {t['job_key'] for t in fresh['todos']}:
        raise ValueError('待办岗位集合与当前输入不一致')
    for row in fresh['todos']:
        old = stored[row['job_key']]
        for key in ('action', 'job_url', 'company', 'job_name', 'salary', 'review'):
            if old.get(key) != row.get(key):
                raise ValueError('待办内容与确定性重建不一致')
    return candidate


def authorize(candidate, site, actions, job_key):
    auth = candidate.get('authorization', {})
    if (not isinstance(auth, dict) or not isinstance(auth.get('sources'), list)
            or not isinstance(auth.get('actions'), list)
            or (auth.get('job_keys') is not None and not isinstance(auth['job_keys'], list))
            or not isinstance(auth.get('user_instruction'), str) or not auth['user_instruction'].strip()
            or site not in auth.get('sources', []) or not set(actions) <= set(auth.get('actions', []))
            or (auth.get('job_keys') is not None and job_key not in auth['job_keys'])):
        raise ValueError('缺少当前用户对站点、动作或岗位范围的授权依据')


def check_todo(bundle, job_key):
    todo = next((t for t in bundle['todos'] if t['job_key'] == job_key), None)
    if not todo or todo.get('action') != 'apply' or todo.get('state') != 'pending':
        raise ValueError('岗位不在当前可执行待办中')
    return todo
