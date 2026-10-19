"""DrissionPage inspect -> AI mapping -> prepare -> fill -> single advance."""
import argparse
import hashlib
import json
from pathlib import Path
from workflow import read, write, pointer, has_value, append_ledger, now, read_ledger, same_job, merge_history
from execution import add_inputs, checked_bundle, authorize, check_todo
from runtime import writer_lock, digest as content_digest
from session import connect_owned

# Extract actual DOM labels and constraints without exposing hidden token fields.
INSPECT_JS = r'''
const shown = e => !!(e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden');
const xpath = e => {
  let parts=[];
  while(e && e.nodeType===1){
    let n=1, p=e.previousElementSibling;
    while(p){if(p.tagName===e.tagName)n++;p=p.previousElementSibling;}
    parts.unshift(e.tagName.toLowerCase()+'['+n+']'); e=e.parentElement;
  }
  return '/'+parts.join('/');
};
const label = e => {
 let texts = Array.from(e.labels||[]).map(x=>x.innerText);
 let ids = (e.getAttribute('aria-labelledby')||'').split(/\s+/).filter(Boolean);
 texts.push(...ids.map(id=>document.getElementById(id)?.innerText||''));
 return texts.join(' ').trim() || e.getAttribute('aria-label') || e.placeholder || e.name || e.id || '';
};
const fields=Array.from(document.querySelectorAll('input,textarea,select,[role="combobox"],[role="textbox"],[contenteditable="true"]'))
.filter(e=>e.type!=='hidden' && !['submit','button','reset','image'].includes(e.type) &&
  (shown(e)||e.type==='file'||Array.from(e.labels||[]).some(shown)))
.map(e=>{
 let type=e.tagName==='SELECT'?'select':e.tagName==='TEXTAREA'?'textarea':e.type||e.getAttribute('role')||'custom';
 let path=xpath(e), native=['INPUT','SELECT','TEXTAREA'].includes(e.tagName);
 let description=(e.getAttribute('aria-describedby')||'').split(/\s+/).map(id=>document.getElementById(id)?.innerText||'').join(' ');
 let fileNames=e.type==='file'?Array.from(e.files||[]).map(f=>f.name):[];
 return {path,selector:'xpath:'+path,type,label:label(e),name:e.name||'',
 required:!!e.required||e.getAttribute('aria-required')==='true'||label(e).includes('*'),
 disabled:!!e.disabled,readonly:!!e.readOnly,
 supported:native&&!['password','range','color','image','button','submit','reset'].includes(type),
 multiple:!!e.multiple,maxlength:e.maxLength>=0?e.maxLength:null,
 min:e.min||null,max:e.max||null,pattern:e.pattern||null,description,
 value:type==='checkbox'||type==='radio'?!!e.checked:type==='file'?fileNames:
 type==='select'&&e.multiple?Array.from(e.selectedOptions).map(o=>o.value):e.value||'',
 choice_value:type==='radio'||type==='checkbox'?e.value||'':'',valid:e.validity?e.validity.valid:null,validation_message:e.validationMessage||'',
 options:e.tagName==='SELECT'?Array.from(e.options).map(o=>({value:o.value,label:o.text,disabled:o.disabled})):[]};
});
const buttons=Array.from(document.querySelectorAll('button,input[type="submit"],input[type="button"],[role="button"],a'))
.filter(shown).map(e=>({path:xpath(e),selector:'xpath:'+xpath(e),label:(e.innerText||e.value||e.getAttribute('aria-label')||'').trim(),disabled:!!e.disabled,type:e.type||e.tagName.toLowerCase()}));
return {url:location.href,title:document.title,text:document.body?.innerText||'',fields,buttons,
shadow_hosts:Array.from(document.querySelectorAll('*')).filter(e=>e.shadowRoot).length};
'''


def digest(snapshot):
    fields = [{k:f.get(k) for k in ('field_id','selector','type','label','required','disabled','readonly','supported','multiple','maxlength','min','max','pattern','options','name','choice_value')} for f in snapshot['fields']]
    context_identity = {name:{k:ctx[k] for k in ('url','title')} for name,ctx in snapshot['contexts'].items()}
    buttons = [{k:b.get(k) for k in ('button_id','selector','type','label','context')} for b in snapshot['buttons']]
    value = {'url':snapshot['url'],'contexts':context_identity,'fields':fields,'buttons':buttons,'unsupported':snapshot['unsupported']}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def contexts(page):
    result = {'main':page}
    for index, frame in enumerate(page.get_frames() or []):
        result[f'frame{index}'] = frame
    return result


def snapshot(page, guard=None, out=None):
    result = dict(schema_version=2, url=str(page.url), captured_at=now(), contexts={}, fields=[], buttons=[], unsupported=[])
    for name, context in contexts(page).items():
        try:
            if guard:
                guard.check(context, force=True)
            data = context.run_js(INSPECT_JS)
            result['contexts'][name] = {'url':data['url'],'title':data['title'],'text':data['text']}
            if out:
                Path(out, 'page.html' if name == 'main' else name + '.html').write_text(context.html, encoding='utf-8')
            for kind, idkey in (('fields','field_id'),('buttons','button_id')):
                for row in data[kind]:
                    row.update(context=name)
                    row[idkey] = name + ':' + row['path']
                    result[kind].append(row)
            if data.get('shadow_hosts'):
                result['unsupported'].append({'context':name,'reason':'发现 shadow DOM，需单独检查内部必填项'})
        except Exception as exc:
            result['unsupported'].append({'context':name,'reason':str(exc)})
    result['fingerprint'] = digest(result)
    return result


def confirmed_answer(candidate, ref, site, job_key):
    value = pointer(candidate, ref)
    if not has_value(value):
        raise ValueError('资料缺失')
    parts = ref.split('/')
    if ref.startswith('/answers/'):
        if len(parts) != 4 or parts[3] != 'value':
            raise ValueError('答案必须引用 /answers/<index>/value')
        record = candidate['answers'][int(parts[2])]
        scope = record.get('scope', {})
        if 'site' not in scope or 'job_key' not in scope:
            raise ValueError('答案未声明复用范围')
        if scope['site'] not in (None,site) or scope['job_key'] not in (None,job_key):
            raise ValueError('答案不适用于本站/本岗位')
    else:
        evidence = candidate.get('evidence', {})
        parents = ['/'.join(parts[:n]) for n in range(len(parts),1,-1)]
        record = next((evidence[p] for p in parents if p in evidence), {})
    if record.get('confirmed') is not True or not record.get('source'):
        raise ValueError('资料未确认或缺少出处')
    return value


def utf16_length(value):
    return len(str(value).encode('utf-16-le')) // 2


def validate_value(field, value):
    kind = field['type']
    if field.get('disabled') or field.get('readonly') or not field.get('supported'):
        raise ValueError('控件不可填或需要单独适配')
    if kind in ('checkbox','radio'):
        if type(value) is not bool:
            raise ValueError('勾选值必须为明确的 true/false')
        if field['required'] and kind == 'checkbox' and not value:
            raise ValueError('必填勾选未确认同意')
    elif kind == 'file':
        files = value if isinstance(value,list) else [value]
        if not files or any(not isinstance(v,str) or not Path(v).is_file() for v in files):
            raise ValueError('上传文件缺失')
        if len(files)>1 and not field['multiple']:
            raise ValueError('控件不允许多文件')
        return [str(Path(v).resolve()) for v in files]
    elif kind == 'select':
        options={o['value'] for o in field['options'] if not o['disabled']}
        vals = value if isinstance(value,list) else [value]
        if any(v not in options for v in vals) or len(vals)>1 and not field['multiple']:
            raise ValueError('答案不在可选值中或不允许多选')
        if field['required'] and not all(has_value(v) for v in vals):
            raise ValueError('必选项仍为空')
    else:
        if not isinstance(value,(str,int,float)) or type(value) is bool:
            raise ValueError('文本字段需字符串或数值')
        value = str(value)
        if field.get('maxlength') is not None and utf16_length(value)>field['maxlength']:
            raise ValueError('答案超过字符限制')
    return value


def unresolved_required(fields, proposed):
    questions=[]
    radio={}
    for f in fields:
        if f['disabled']:
            continue
        value=proposed.get(f['field_id'],f['value'])
        if f['type']=='radio':
            key=(f['context'],f['name'] or f['field_id'])
            radio.setdefault(key,[]).append((f,value))
        elif f['required'] and (not has_value(value) or f['type']=='checkbox' and value is not True):
            questions.append({'field_id':f['field_id'],'label':f['label'],'required':True,'question':'请补充或确认此必填字段'})
    for group in radio.values():
        if any(f['required'] for f,v in group) and sum(v is True for f,v in group)!=1:
            f=group[0][0]
            questions.append({'field_id':f['field_id'],'label':f['label'],'required':True,'question':'请为此必选单选组明确选择一个选项'})
    return questions


def prepare(snap, plan, candidate, todos=None):
    if plan.get('url') != snap['url'] or not plan.get('site') or not plan.get('job_key'):
        raise ValueError('计划缺少岗位身份或 URL 与当前表单不一致')
    if candidate.get('schema_version') not in (2, 3):
        raise ValueError('候选人资料版本无效')
    if todos is not None:
        todo = next((t for t in todos['todos'] if t['job_key']==plan['job_key']),None)
        if todos.get('site')!=plan['site'] or not todo or todo['action']!='apply' or todo['state']!='pending':
            raise ValueError('岗位未通过本站审阅或待办已执行')
        if not todo.get('identity'):
            raise ValueError('缺少公司岗位身份，需要核实')
        verify_identity(snap, todo)
        plan = dict(plan, identity=todo['identity'])
    fields={f['field_id']:f for f in snap['fields']}
    actions=[]; questions=[]; seen=set()
    for action in plan.get('actions',[]):
        key=action.get('field_id')
        if key in seen or key not in fields:
            raise ValueError('未知/重复字段 ID')
        seen.add(key); field=fields[key]
        try:
            value=confirmed_answer(candidate,action.get('answer_ref'),plan['site'],plan['job_key'])
            value=validate_value(field,value)
            actions.append(dict(field_id=key,answer_ref=action['answer_ref'],value=value))
        except (ValueError,KeyError,IndexError,TypeError) as exc:
            questions.append({'field_id':key,'label':field['label'],'required':field['required'],'question':str(exc)})
    proposed={a['field_id']:a['value'] for a in actions}
    for f in snap['fields']:
        if f['type']=='radio' and f['value'] is True and proposed.get(f['field_id']) is False:
            if not any(g['type']=='radio' and g['name']==f['name'] and g['context']==f['context'] and proposed.get(g['field_id']) is True for g in snap['fields']):
                questions.append({'field_id':f['field_id'],'label':f['label'],'question':'取消已有单选必须明确选择同组另一个选项'})
    questions.extend(q for q in unresolved_required(snap['fields'],proposed) if q['field_id'] not in {q['field_id'] for q in questions})
    questions.extend({'field_id':f['field_id'],'label':f['label'],'required':f['required'],'question':'此自定义控件需要单独适配/人工检查'} for f in snap['fields'] if not f['supported'])
    questions.extend({'question':u['reason'],'context':u['context']} for u in snap['unsupported'])
    next_action=plan.get('next_action')
    if next_action:
        buttons={b['button_id']:b for b in snap['buttons']}
        if next_action.get('button_id') not in buttons or next_action.get('kind') not in ('next','submit'):
            raise ValueError('下一步按钮或动作类型无效')
    for question in questions:
        question.update(site=plan['site'],job_key=plan['job_key'],blocks=['fill','advance'])
    return dict(schema_version=2,site=plan['site'],job_key=plan['job_key'],identity=plan.get('identity'),url=snap['url'],
                state='NEEDS_INPUT' if questions else 'READY',snapshot=snap,actions=actions,next_action=next_action,questions=questions)


def same_structure(prepared, fresh):
    if prepared['snapshot']['fingerprint']!=fresh['fingerprint']:
        raise ValueError('页面/字段已变化，重新 inspect/prepare，不继续操作')


def verify_identity(snap, todo):
    text = snap.get('contexts', {}).get('main', {}).get('text', '')
    if not todo.get('company') or not todo.get('job_name') or not all(todo[k] in text for k in ('company','job_name')):
        raise ValueError('表单页面未显示对应公司和岗位，需人工核实并适配身份检查')


def verify_filled(prepared, fresh):
    same_structure(prepared,fresh)
    fields={f['field_id']:f for f in fresh['fields']}
    for a in prepared['actions']:
        f=fields[a['field_id']]; expected=a['value']; actual=f['value']
        if f['type']=='file':
            expected=[Path(v).name for v in expected]
        if f['type']=='select' and f['multiple']:
            expected=expected if isinstance(expected,list) else [expected]
            actual=sorted(actual); expected=sorted(expected)
        if actual!=expected:
            raise ValueError('字段读回不一致：'+f['label'])
    if unresolved_required(fresh['fields'],{}):
        raise ValueError('仍有必填字段未填')
    invalid=[f['label'] for f in fresh['fields'] if not f['disabled'] and f.get('valid') is False]
    if invalid:
        raise ValueError('浏览器字段校验未通过：'+','.join(invalid))


def connect(args):
    from adapters.extract_boss import create_browser
    if args.attach_port:
        return connect_owned(args.run_dir, args.attach_port)
    if not args.profile_dir:
        raise ValueError('新浏览器需独立 profile-dir')
    return create_browser(headless=False,profile_dir=Path(args.profile_dir).resolve())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('prepare')
    add_inputs(p)
    for k in ('snapshot','plan','todos','out-dir'):
        p.add_argument('--'+k,required=True)
    for command in ('inspect','fill','advance'):
        p=sub.add_parser(command)
        p.add_argument('--run-dir',required=True)
        p.add_argument('--attach-port',type=int)
        p.add_argument('--profile-dir')
        if command=='inspect':
            p.add_argument('--url')
            p.add_argument('--site', required=True)
        else:
            add_inputs(p)
            p.add_argument('--todos', required=True)
            p.add_argument('--prepared',required=True)
            p.add_argument('--authorized',action='store_true')
        if command in ('fill', 'advance'):
            p.add_argument('--ledger',required=True)
    args=parser.parse_args()
    root = args.out_dir if args.command == 'prepare' else args.run_dir
    with writer_lock(root):
        return execute(args)


def execute(args):
    if args.command=='prepare':
        bundle = read(args.todos)
        checked_bundle(args, bundle)
        prepared=prepare(read(args.snapshot),read(args.plan),read(args.candidate),read(args.todos))
        prepared['input_binding'] = bundle['input_binding']
        prepared['review_hash'] = content_digest(check_todo(bundle, prepared['job_key'])['review'])
        write(Path(args.out_dir)/'prepared.json',prepared)
        write(Path(args.out_dir)/'questions.json',prepared['questions'])
        print(json.dumps({'state':prepared['state'],'questions':len(prepared['questions'])},ensure_ascii=False))
        return
    # Avoid starting/navigating any browser before checking the execution plan.
    prepared=None
    if args.command!='inspect':
        prepared=read(args.prepared)
        bundle = read(args.todos); candidate = checked_bundle(args, bundle)
        bundle = merge_history(bundle, bundle, read_ledger(args.ledger))
        todo = check_todo(bundle, prepared['job_key'])
        if (prepared.get('input_binding') != bundle['input_binding']
                or prepared.get('review_hash') != content_digest(todo['review'])):
            raise ValueError('表单计划的审阅或输入已过期')
        actions = ['fill'] if args.command == 'fill' else [prepared.get('next_action', {}).get('kind')]
        snapshot_fields = {f['field_id']: f for f in prepared['snapshot']['fields']}
        for action in prepared['actions']:
            expected = validate_value(snapshot_fields[action['field_id']],
                       confirmed_answer(candidate, action.get('answer_ref'), prepared['site'], prepared['job_key']))
            if expected != action['value']:
                raise ValueError('表单计划值与当前确认资料不一致')
        if args.command == 'fill' and any(snapshot_fields[a['field_id']]['type'] == 'file' for a in prepared.get('actions', [])):
            actions.append('upload')
        authorize(candidate, prepared['site'], actions, prepared['job_key'])
        if prepared['state']!='READY':
            raise ValueError('仍需用户信息，停止执行')
        if not args.attach_port:
            raise ValueError('执行只允许接回已核实任务浏览器')
        if not args.authorized:
            raise ValueError('填写/上传/点击需核对当前用户授权后传 --authorized')
    if args.command=='advance' and (not args.authorized or not prepared.get('next_action')):
        raise ValueError('需要当前用户授权及明确下一步计划')
    out=Path(args.run_dir); out.mkdir(parents=True,exist_ok=True)
    from collect import Guard, StopSite, UserActionRequired
    guard=Guard(out,prepared['site'] if prepared else args.site)
    try:
        guard.ensure_can_resume()
        page=connect(args)
        write(out/'browser.json',{'address':page.browser.address,'tab_id':page.tab_id,
              'run_dir':str(out.resolve()),'ownership_verified':True,
              'owned_profile':args.profile_dir,'headless':page.browser.states.is_headless})
        if args.command=='inspect':
            if args.url:
                guard.delay(); page.get(args.url)
            guard.preflight(page)
            snap=snapshot(page,guard,out)
            write(out/'form.json',snap)
            guard.event('FORM_INSPECTED',fields=len(snap['fields']),unsupported=snap['unsupported'])
            return
        guard.preflight(page)
        guard.runtime.start('execute')
        fresh=snapshot(page,guard)
        verify_identity(fresh, todo)
        same_structure(prepared,fresh)
        if args.command=='fill':
            fieldmap={f['field_id']:f for f in fresh['fields']}
            log=[]
            for action in prepared['actions']:
                guard.check(page,force=True)
                field=fieldmap[action['field_id']]
                context=contexts(page)[field['context']]
                guard.check(context,force=True)
                element=context.ele(field['selector'],timeout=2)
                if not element:
                    raise ValueError('字段消失，停止填写')
                value=validate_value(field,action['value'])
                log.append({'field_id':field['field_id'],'state':'INTENT'})
                write(out/'fill-log.json',log)
                if field['type'] in ('checkbox','radio'):
                    if field['type']=='radio' and value is False:
                        pass  # Selecting the confirmed alternative clears this radio.
                    elif bool(element.run_js('return this.checked;'))!=value:
                        guard.delay(); element.click()
                elif field['type']=='select':
                    if field['multiple']:
                        element.select.clear()
                    element.select.by_value(value)
                elif field['type']=='file':
                    existing = read_ledger(args.ledger)
                    upload_key = f"{prepared['site']}:{prepared['job_key']}:upload:{field['field_id']}:{fresh['fingerprint']}"
                    if any(same_job(prepared, e) and e.get('state') in ('upload_intent','uploaded','uncertain')
                           and e.get('attempt_key', upload_key) == upload_key for e in existing):
                        raise ValueError('已有上传意图或未知结果，先核实，不重复上传')
                    append_ledger(args.ledger, {'site':prepared['site'],'job_key':prepared['job_key'],
                                  'url':prepared['url'],'identity':prepared.get('identity'),
                                  'attempt_key':upload_key,
                                  'state':'upload_intent','evidence':field['label']})
                    guard.delay(); element.click.to_upload(value,by_js=True)
                else:
                    element.input(value,clear=True)
                log[-1]['state']='FILLED'
                write(out/'fill-log.json',log)
            guard.check(page,force=True)
            after=snapshot(page,guard,out)
            write(out/'after-fill.json',after)
            verify_filled(prepared,after)
            if 'upload' in actions:
                append_ledger(args.ledger, {'site':prepared['site'],'job_key':prepared['job_key'],
                              'url':prepared['url'],'identity':prepared.get('identity'),
                              'state':'uploaded','evidence':'原生文件字段读回一致；不证明招聘方已接收'})
            write(out/'filled.json',{'state':'FILLED_VERIFIED','fingerprint':after['fingerprint'],'at':now()})
            guard.event('FORM_FILLED_VERIFIED',fields=len(log))
            return
        verify_filled(prepared,fresh)
        action=prepared['next_action']
        key=f'{prepared["site"]}:{prepared["job_key"]}:{action["kind"]}:{fresh["fingerprint"]}'
        ledger=Path(args.ledger)
        history=read_ledger(ledger)
        for event in history:
            if event.get('attempt_key')==key or (same_job(prepared,event) and event.get('state') in ('submitted','uncertain','submit_intent')):
                raise ValueError('已有提交/未知结果/点击意图，先核实，不重复点击')
        button=next(b for b in fresh['buttons'] if b['button_id']==action['button_id'])
        if button['disabled']:
            raise ValueError('下一步按钮仍不可用')
        context=contexts(page)[button['context']]
        guard.check(context,force=True)
        element=context.ele(button['selector'],timeout=2)
        if not element:
            raise ValueError('按钮消失')
        entry={'site':prepared['site'],'job_key':prepared['job_key'],'identity':prepared.get('identity'),'url':prepared['url'],
               'attempt_key':key,'state':'submit_intent' if action['kind']=='submit' else 'next_intent','evidence':button['label']}
        append_ledger(ledger,entry)
        write(out/'advance.json',entry)
        guard.delay(); element.click()
        # Mark uncertainty immediately; a challenge or process interruption cannot erase intent.
        entry['state']='uncertain' if action['kind']=='submit' else 'step_advanced'
        append_ledger(ledger,entry)
        write(out/'advance.json',entry)
        guard.check(page,force=True)
        after=snapshot(page,guard,out)
        write(out/'after-advance.json',after)
        guard.event('FORM_ADVANCED',result=entry['state'],url=str(page.url))
        if entry['state'] == 'uncertain':
            guard.runtime.pause('RESULT_UNCERTAIN', phase='execute', checkpoint={'job_key':prepared['job_key']},
                                message='提交已点击，需主 Agent 核查明确成功证据')
            raise UserActionRequired('需核查实际提交结果')
    except UserActionRequired:
        raise SystemExit(2)
    except (StopSite,Exception) as exc:
        guard.event('FORM_STOPPED',reason=str(exc))
        raise SystemExit(1)


if __name__=='__main__':
    import sys
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    try:
        main()
    except Exception as exc:
        print(json.dumps({'state':'ERROR','error_type':type(exc).__name__,'reason':str(exc)},ensure_ascii=False),flush=True)
        raise SystemExit(1)
