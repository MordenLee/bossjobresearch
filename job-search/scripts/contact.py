"""Click Boss's visible communication button for an AI-approved shortlist once."""
import argparse
import json
import time
from pathlib import Path
from urllib.parse import urlsplit
from adapters.extract_boss import create_browser
from collect import Guard, Proxy, atomic_json, StopSite, UserActionRequired
from salary import salary_gate
from DrissionPage.errors import ElementNotFoundError
from workflow import read, append_ledger, identity, read_ledger, same_job, merge_history, write, markdown
from execution import add_inputs, checked_bundle, authorize
from runtime import writer_lock
from session import connect_owned
from detail import extract, apply_detail

def optional_element(page, locator, timeout=1):
    try:
        return page.ele(locator, timeout=timeout)
    except ElementNotFoundError:
        return None

def validate_role_fit(row):
    fit = row.get('role_fit') or {}
    if row.get('status') == 'excluded' or fit.get('status') != 'pass':
        raise ValueError('岗位主要职责未确认匹配，不得仅凭待核实标签发起沟通')
    if not str(fit.get('primary_duties') or '').strip():
        raise ValueError('缺少主要职责判断')
    for key in ('jd_evidence', 'resume_evidence'):
        evidence = fit.get(key)
        if not isinstance(evidence, list) or not any(isinstance(x,str) and x.strip() for x in evidence):
            raise ValueError('缺少JD与简历对应证据')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_inputs(parser)
    parser.add_argument('--detail-selectors', required=True)
    parser.add_argument('--jobs', required=True)
    parser.add_argument('--profile-dir', required=True)
    parser.add_argument('--run-dir', required=True)
    parser.add_argument('--min-salary', type=float, help='用户设置的最低固定月薪；明确不限时省略')
    parser.add_argument('--todos', required=True, help='本站已验证的逐岗审阅待办')
    parser.add_argument('--ledger', required=True, help='本 campaign 各站共享的申请记录')
    parser.add_argument('--attach-port', type=int, help='已核实属于本次任务浏览器的本地调试端口')
    parser.add_argument('--delay-min', type=float, default=0.25)
    parser.add_argument('--delay-max', type=float, default=0.65)
    parser.add_argument('--batch-size', type=int, default=0)
    parser.add_argument('--batch-rest', type=float, default=0)
    args = parser.parse_args()
    if args.delay_min < 0 or args.delay_max < args.delay_min or args.batch_size < 0 or args.batch_rest < 0 or (args.batch_rest and not args.batch_size):
        parser.error('操作间隔/批次参数无效')
    with writer_lock(args.run_dir):
        return execute(args, parser)


def execute(args, parser):
    jobs = json.loads(Path(args.jobs).read_text(encoding='utf-8-sig'))
    if not isinstance(jobs, list):
        parser.error('候选清单必须为数组')
    bundle = read(args.todos)
    candidate = checked_bundle(args, bundle)
    source_jobs = {j['job_key']: j for j in read(args.source_jobs)}
    selectors = read(args.detail_selectors)
    if selectors.get('source') != 'boss': raise ValueError('详情选择器站点不匹配')
    if bundle.get('site') != 'boss':
        parser.error('仅接受 Boss 单站待办')
    todo_map = {t['job_key']:t for t in bundle['todos']}
    ledger_path = Path(args.ledger)
    history = read_ledger(ledger_path)
    for row in jobs:
        authorize(candidate, 'boss', ['contact'], row.get('job_key'))
        todo = todo_map.get(row.get('job_key'))
        if not todo or todo['action'] != 'apply' or todo['state'] != 'pending':
            parser.error('候选必须来自本站当前可申请待办')
        for key in ('job_name','company','job_url','salary'):
            if todo.get(key) != row.get(key):
                parser.error('候选与已审阅待办身份/薪资不一致')
        row['application_identity'] = todo.get('identity')
        try:
            validate_role_fit(row)
        except ValueError as exc:
            parser.error(f'{row.get("job_name", "未知岗位")}: {exc}')
        if urlsplit(row.get('job_url','')).hostname != 'www.zhipin.com':
            parser.error('仅接受已采集 Boss 岗位链接')
        if args.min_salary is not None and salary_gate(row.get('salary'), args.min_salary)['status'] != 'pass':
            parser.error('自动沟通仅接受已明确达到最低月薪的候选岗位')
        if not row.get('reason') or row.get('approved_for_contact') is not True:
            parser.error('候选缺少 AI 匹配依据或当前用户授权下的具体判断')
    run = Path(args.run_dir).resolve()
    run.mkdir(parents=True, exist_ok=True)
    log_path = run / 'communication.json'
    log = json.loads(log_path.read_text(encoding='utf-8')) if log_path.exists() else []
    guard = Guard(run, 'boss', args.delay_min, args.delay_max, batch_size=args.batch_size, batch_rest=args.batch_rest)
    browser = None
    try:
        guard.ensure_can_resume()
        if args.attach_port:
            browser = connect_owned(run, args.attach_port)
        else:
            browser = create_browser(headless=False, profile_dir=Path(args.profile_dir).resolve())
            browser.get('https://www.zhipin.com')
            atomic_json(run/'browser.json', {'source':'boss','address':browser.browser.address,
                        'tab_id':browser.tab_id,'run_dir':str(run),'ownership_verified':True,
                        'profile_dir':str(Path(args.profile_dir).resolve())})
        guard.preflight(browser)
        guard.runtime.start('execute')
        page = Proxy(browser, guard)
        for row in jobs:
            if any(same_job(row,entry) and entry.get('state') in ('CLICK_INTENT','CONTACT_STARTED','CHAT_OPENED','ALREADY_CONTACTED','CLICKED_RESULT_UNCERTAIN') for entry in log):
                continue
            if any(same_job(dict(row, identity=row.get('application_identity')),h) and
                   h.get('state') in ('contacted','submitted','uncertain','contact_intent','submit_intent') for h in history):
                guard.event('CONTACT_SKIPPED',job_key=row['job_key'],reason='共享记录已有申请/联系/未知点击结果')
                continue
            entry = {k:row.get(k) for k in ('job_key','job_name','company','job_url','salary','priority_score','reason')}
            page.get(row['job_url'])
            guard.check(browser, force=True)
            body = page.ele('css:body').text
            fresh_job = apply_detail(source_jobs[row['job_key']], extract(browser, selectors))
            original = source_jobs[row['job_key']]
            if (fresh_job.get('detail_state') != 'READY'
                    or fresh_job.get('jd_description') != original.get('jd_description')):
                guard.runtime.pause('NEEDS_INPUT', phase='execute', checkpoint={'job_key':row['job_key']},
                                    message='当前 JD 或身份已变化，需刷新采集并重新审阅')
                raise UserActionRequired('JD 新鲜度检查未通过')
            if row['job_name'] not in body or row['company'] not in body:
                entry.update(state='IDENTITY_NOT_VERIFIED', reason='页面未同时显示候选岗位和公司，未点击')
                log.append(entry); atomic_json(log_path,log)
                continue
            salary_text = str(row.get('salary') or '')
            encoded_salary = str(row.get('salary_from_page') or '')
            if salary_text not in body and (not encoded_salary or encoded_salary not in body):
                entry.update(state='SALARY_NOT_VERIFIED', reason='页面未显示已采集薪资，未点击')
                log.append(entry); atomic_json(log_path,log)
                continue
            already = optional_element(page, 'xpath://a[normalize-space(.)="继续沟通"] | //button[normalize-space(.)="继续沟通"]')
            if already:
                entry.update(state='ALREADY_CONTACTED')
                log.append(entry); atomic_json(log_path,log)
                outcome=dict(site='boss',job_key=row['job_key'],identity=row['application_identity'],url=row['job_url'],state='contacted',evidence='页面已显示继续沟通；本次未点击')
                append_ledger(ledger_path,outcome);history.append(outcome)
                continue
            button = optional_element(page, 'xpath://a[normalize-space(.)="立即沟通"] | //button[normalize-space(.)="立即沟通"]',timeout=3)
            if not button:
                entry.update(state='BUTTON_NOT_FOUND')
                log.append(entry); atomic_json(log_path,log)
                continue
            # Intent is durable before any external mutation; never retry an ambiguous click.
            entry.update(state='CLICK_INTENT')
            log.append(entry); atomic_json(log_path,log)
            intent = dict(site='boss',job_key=row['job_key'],identity=row['application_identity'],url=row['job_url'],state='contact_intent',evidence='立即沟通按钮')
            append_ledger(ledger_path,intent)
            history.append(intent)
            before_tabs = set(browser.tab_ids)
            button.click()
            time.sleep(1.5)
            guard.check(browser, force=True)
            tabs = [browser.get_tab(t) for t in browser.tab_ids if t not in before_tabs]
            targets = tabs or [browser]
            verified = False
            for target in targets:
                guard.check(target,force=True)
                text = target.run_js('return document.body ? document.body.innerText : ""') or ''
                if '/chat' in str(target.url) and (row['company'] in text or row['job_name'] in text):
                    entry.update(state='CHAT_OPENED',result_url=str(target.url),page_excerpt=text[:1500])
                    verified = True
                    break
            if not verified:
                text = browser.run_js('return document.body ? document.body.innerText : ""') or ''
                if optional_element(browser, 'xpath://a[normalize-space(.)="继续沟通"] | //button[normalize-space(.)="继续沟通"]'):
                    entry.update(state='CONTACT_STARTED',result_url=str(browser.url),page_excerpt=text[-1500:])
                else:
                    entry.update(state='CLICKED_RESULT_UNCERTAIN',result_url=str(browser.url),page_excerpt=text[-2000:])
                    atomic_json(log_path,log)
                    guard.runtime.pause('RESULT_UNCERTAIN', phase='execute', checkpoint={'job_key':row['job_key']},
                                        message='沟通按钮已点击但结果不明确，先核查真实会话')
                    guard.event('CONTACT_STOPPED',reason='沟通按钮已点击但结果不明确，不重复操作')
                    raise SystemExit(1)
            atomic_json(log_path,log)
            outcome = dict(intent,state='contacted',evidence=entry['state'])
            append_ledger(ledger_path,outcome)
            history.append(outcome)
            guard.event('CONTACT_RECORDED',job_name=row['job_name'],result=entry['state'])
    except UserActionRequired:
        if log and log[-1]['state']=='CLICK_INTENT':
            log[-1]['state']='CLICKED_RESULT_UNCERTAIN'
        raise SystemExit(2)
    except (StopSite,Exception) as exc:
        guard.event('CONTACT_STOPPED',reason=str(exc))
        if log and log[-1]['state']=='CLICK_INTENT':
            log[-1]['state']='CLICKED_RESULT_UNCERTAIN'
            if guard.runtime.load()['task_state'] != 'PAUSED':
                guard.runtime.pause('RESULT_UNCERTAIN', phase='execute', checkpoint={'job_key':log[-1]['job_key']})
        atomic_json(log_path,log)
        raise SystemExit(1)
    finally:
        # Keep the task-owned browser visible so the user can read conversations.
        atomic_json(log_path,log)
        # Rebuilding or changing review decisions cannot erase actual outcomes.
        bundle = merge_history(bundle, bundle, history)
        write(args.todos, bundle)
        Path(args.todos).with_suffix('.md').write_text(markdown(bundle), encoding='utf-8')
    guard.event('CONTACT_FINISHED',records=len(log))


if __name__=='__main__':
    import sys
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    try:
        main()
    except Exception as exc:
        print(json.dumps({'state':'ERROR','error_type':type(exc).__name__,'reason':str(exc)},ensure_ascii=False),flush=True)
        raise SystemExit(1)
