"""Validate injected policy and version-bound Reviewer output; no semantic scoring."""
import argparse
from pathlib import Path
from runtime import digest, candidate_digest
from workflow import read, pointer, has_value

CATEGORIES = ('hard_constraints', 'soft_preferences', 'exclusions', 'tradeoffs',
              'priority_rules', 'verification_rules')
BLOCKING = ('hard_constraints', 'exclusions', 'verification_rules')


def validate_policy(policy):
    required = {'schema_version', 'policy_id', 'version', *CATEGORIES}
    if not isinstance(policy, dict) or set(policy) != required or type(policy['schema_version']) is not int or policy['schema_version'] != 1:
        raise ValueError('review_policy 字段或 schema_version 无效')
    if not all(isinstance(policy.get(k), str) and policy[k].strip() for k in ('policy_id', 'version')):
        raise ValueError('policy_id/version 必须非空')
    ids = set(); soft_ids = set()
    for category in CATEGORIES:
        if not isinstance(policy[category], list):
            raise ValueError(category + ' 必须是数组')
        for rule in policy[category]:
            fields = {'id', 'statement', 'source'} | ({'may_relax_rule_ids'} if category == 'tradeoffs' else set())
            if not isinstance(rule, dict) or set(rule) != fields:
                raise ValueError('策略规则字段不符：' + category)
            if not all(isinstance(rule[k], str) and rule[k].strip() for k in ('id', 'statement')):
                raise ValueError('规则 id/statement 必须非空')
            if rule['id'] in ids:
                raise ValueError('规则 ID 必须跨分类唯一')
            ids.add(rule['id'])
            if category == 'soft_preferences': soft_ids.add(rule['id'])
            source = rule['source']
            if (not isinstance(source, dict) or set(source) != {'kind', 'ref', 'quote'}
                    or source.get('kind') not in ('user_instruction', 'candidate_evidence', 'confirmed_preference')
                    or not all(isinstance(source[k], str) and source[k].strip() for k in ('ref', 'quote'))):
                raise ValueError('每条规则需明确来源类型、引用与原文')
    for rule in policy['tradeoffs']:
        targets = rule['may_relax_rule_ids']
        if (not isinstance(targets, list) or not targets or any(not isinstance(x, str) for x in targets)
                or len(targets) != len(set(targets)) or not set(targets) <= soft_ids):
            raise ValueError('tradeoff 只允许放宽明确引用的软偏好')
    return policy


def binding(job, candidate, policy):
    return {'job_hash': digest(job), 'candidate_hash': candidate_digest(candidate),
            'policy_hash': digest(policy), 'policy_id': policy['policy_id'], 'policy_version': policy['version']}


def confirmed_ref(candidate, ref):
    if not isinstance(ref, str) or not ref.startswith('/cv/') or not has_value(pointer(candidate, ref)):
        raise ValueError('履历引用必须指向 candidate.cv 中的非空事实')
    parts = ref.split('/')
    evidence = candidate.get('evidence', {})
    record = next((evidence['/'.join(parts[:n])] for n in range(len(parts), 1, -1)
                   if '/'.join(parts[:n]) in evidence), {})
    if record.get('confirmed') is not True or not record.get('source'):
        raise ValueError('履历事实缺少确认与来源')


def validate_review_shape(review, job, candidate):
    """Validate the full v3 review envelope, not only its injected-rule portion."""
    if (review.get('decision') not in ('apply','verify','skip') or type(review.get('priority')) is not int
            or review['priority'] not in (1,2,3) or not isinstance(review.get('reason'),str)
            or not review['reason'].strip()):
        raise ValueError('审阅缺少有效决定、优先级或理由')
    fit = review.get('role_fit')
    if (not isinstance(fit,dict) or fit.get('status') not in ('pass','fail','unknown')
            or not isinstance(fit.get('primary_duties'),str) or not fit['primary_duties'].strip()):
        raise ValueError('审阅缺少主要职责判断')
    for name in ('jd_evidence','resume_evidence'):
        if not isinstance(fit.get(name),list): raise ValueError('职责证据必须为数组')
    norm=lambda s: ''.join(s.split())
    for quote in fit['jd_evidence']:
        if not isinstance(quote,str) or not quote.strip() or norm(quote) not in norm(job['jd_description']):
            raise ValueError('职责引用不在当前JD中')
    for ref in fit['resume_evidence']: confirmed_ref(candidate,ref)
    risks,questions=review.get('risks'),review.get('questions')
    if not isinstance(risks,list) or not isinstance(questions,list): raise ValueError('风险与问题必须为数组')
    if any(not isinstance(q,str) or not q.strip() for q in questions): raise ValueError('核实问题必须是非空字符串')
    for risk in risks:
        if (not isinstance(risk,dict) or risk.get('severity') not in ('blocking','caution')
                or not isinstance(risk.get('finding'),str) or not risk['finding'].strip()
                or not isinstance(risk.get('evidence'),str) or not risk['evidence'].strip()):
            raise ValueError('风险需blocking/caution枚举、事实和证据')
    if review['decision']=='verify' and not questions: raise ValueError('verify必须包含具体问题')
    if review['decision']=='apply' and (fit['status']!='pass' or not fit['jd_evidence']
            or not fit['resume_evidence'] or questions or any(r['severity']=='blocking' for r in risks)):
        raise ValueError('职责、履历、风险或未决问题阻止apply')


def validate_review(review, job, candidate, policy):
    validate_policy(policy)
    if review.get('input_binding') != binding(job, candidate, policy):
        raise ValueError('审阅输入已变化或缺少版本绑定，需重新审阅')
    if review.get('job_key') != job.get('job_key') or review.get('source') != job.get('source'):
        raise ValueError('审阅岗位身份不符')
    jd = job.get('jd_description')
    if not isinstance(jd, str) or not jd.strip() or job.get('detail_state') != 'READY' or job.get('detail_identity_verified') is not True:
        raise ValueError('完整 JD 与岗位身份尚未核实，不能生成正式审阅')
    validate_review_shape(review, job, candidate)
    rules = {r['id']: (category, r) for category in CATEGORIES for r in policy[category]}
    results = review.get('rule_results')
    if not isinstance(results, list) or any(not isinstance(r, dict) for r in results):
        raise ValueError('rule_results 必须是对象数组')
    result_ids = [r.get('rule_id') for r in results]
    if any(not isinstance(x, str) for x in result_ids) or len(set(result_ids)) != len(results) or set(result_ids) != set(rules):
        raise ValueError('审阅必须恰好覆盖策略中的所有规则')
    norm = lambda s: ''.join(s.split())
    for result in results:
        if result.get('status') not in ('pass', 'fail', 'unknown', 'not_applicable') or not str(result.get('reason') or '').strip():
            raise ValueError('规则结果需状态及理由')
        evidence = result.get('evidence')
        if (not isinstance(evidence, dict) or not {'jd_quotes', 'candidate_refs'} <= set(evidence)
                or set(evidence) - {'jd_quotes', 'candidate_refs', 'job_refs'}):
            raise ValueError('规则证据需 JD 引用与履历引用数组')
        quotes, refs = evidence['jd_quotes'], evidence['candidate_refs']
        if not isinstance(quotes, list) or not isinstance(refs, list):
            raise ValueError('证据需数组')
        for quote in quotes:
            if not isinstance(quote, str) or not quote.strip() or norm(quote) not in norm(jd):
                raise ValueError('规则 JD 引用不在当前正文中')
        for ref in refs: confirmed_ref(candidate, ref)
        job_refs = evidence.get('job_refs', [])
        if not isinstance(job_refs, list): raise ValueError('job_refs 必须为数组')
        for ref in job_refs:
            if not isinstance(ref, str) or not ref.startswith('/') or not has_value(pointer(job, ref)):
                raise ValueError('岗位字段引用无效')
        if result['status'] in ('pass', 'fail') and not quotes and not refs and not job_refs:
            raise ValueError('确定规则结论必须有证据')
    decision = review.get('decision')
    mandatory = [r for r in results if rules[r['rule_id']][0] in BLOCKING]
    if any(r['status'] == 'fail' for r in mandatory) and decision != 'skip':
        raise ValueError('硬条件/排除/强制核实规则失败，不能由软偏好覆盖')
    if decision == 'apply' and any(r['status'] != 'pass' for r in mandatory):
        raise ValueError('存在未通过的强制规则，不得建议申请')
    # The legacy field remains a readable projection, not another independent policy.
    hard = {r['rule_id']: r for r in results if rules[r['rule_id']][0] == 'hard_constraints'}
    projection = review.get('hard_constraints', [])
    if not isinstance(projection, list) or len(projection) != len(hard):
        raise ValueError('hard_constraints 必须恰好覆盖策略硬条件')
    if {r.get('name') for r in projection} != set(hard):
        raise ValueError('hard_constraints.name 必须使用规则 ID')
    if any(r.get('status') != hard[r['name']]['status'] for r in projection):
        raise ValueError('硬条件摘要与逐规则结论不一致')
    if any(not r.get('evidence') for r in projection): raise ValueError('硬条件摘要需证据或信息缺口说明')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--policy', required=True)
    parser.add_argument('--jobs'); parser.add_argument('--candidate'); parser.add_argument('--out')
    args = parser.parse_args(); policy = validate_policy(read(args.policy))
    if args.jobs or args.candidate or args.out:
        if not all((args.jobs, args.candidate, args.out)): parser.error('生成审阅包需 jobs/candidate/out')
        from workflow import write
        candidate = read(args.candidate); jobs = read(args.jobs)
        if not isinstance(jobs, list): raise ValueError('jobs 必须是数组')
        if len({(j['source'], j['job_key']) for j in jobs}) != len(jobs): raise ValueError('重复岗位键')
        for job in jobs:
            if job.get('detail_state') != 'READY' or job.get('detail_identity_verified') is not True or not job.get('jd_description'):
                raise ValueError('仅将已核实完整 JD 放入审阅包')
        write(args.out, dict(schema_version=3, candidate=candidate, review_policy=policy,
                             jobs=[dict(job=job, input_binding=binding(job, candidate, policy)) for job in jobs]))
    else:
        print('policy valid')


if __name__ == '__main__':
    main()
