# 模块输入输出 v3

JSON 为主数据，ledger.jsonl 为实际外部操作事件，task-events.jsonl 为状态事件，CSV 为人工视图。岗位按 source+稳定 job_key 关联，不用序号。主 Agent 合并共享数据。

## Collector

输入：候选资料与搜索计划、单站关键词批次、配置预算、运行目录和任务浏览器凭据。输出 jobs、coverage、failures、skipped、login-check 与状态断点。

Job 必含 source/job_key/job_name/company/job_url，可含 salary/location/keyword/appearances。detail_state 为 PENDING/READY/INCOMPLETE/IDENTITY_MISMATCH/READ_ERROR。READY 有非空 jd_description、detail_identity_verified=true、detail_evidence、jd_read_at；正文变化保留 jd_history。旧适配器采到正文但未统一核验仍为 PENDING。

完整性核验依赖已核实的正文容器、非空正文、无已配置截断标记和身份一致；选择器不正确会破坏保证。语义明显缺段仍由 Reviewer 退回信息缺口。没有通用“展开全文”点击实现。

## Reviewer

审阅包含 schema_version=3、candidate、review_policy、jobs=[{job,input_binding}]。绑定含 job_hash/candidate_hash/policy_hash/policy_id/policy_version，Reviewer 原样回传，不为旧判断改 hash。

每条 Review 示例：

```json
{
  "source":"site", "job_key":"stable-key", "input_binding":{},
  "decision":"verify", "priority":2, "reason":"具体结论",
  "role_fit":{"status":"unknown","primary_duties":"主要交付物","jd_evidence":["JD原文"],"resume_evidence":["/cv/projects/0"]},
  "hard_constraints":[{"name":"hard-rule-id","status":"unknown","evidence":"现有事实与缺口"}],
  "rule_results":[{"rule_id":"hard-rule-id","status":"unknown","reason":"缺少信息","evidence":{"jd_quotes":[],"candidate_refs":[]}}],
  "risks":[{"severity":"blocking","finding":"信息缺口","evidence":"依据"}],
  "questions":["具体核实问题？"]
}
```

input_binding 使用实际包内值，空对象仅示意位置。rule_results 恰好覆盖策略全部规则；状态 pass/fail/unknown/not_applicable。JD 引用必须在当前正文中，履历引用指向已确认且有来源的 /cv/ 事实。unknown 可无证据，但需解释缺口。hard_constraints 恰好投影硬规则，name 用规则 ID。

risks 每项必须含 severity=blocking/caution、finding 和 evidence；不能使用 warning 等其他枚举。priority 必须为整数1/2/3；verify 的 questions 至少有一条非空字符串。policy.validate_review 同时检查完整Review信封与注入策略结果，主 Agent 合并后仍运行 workflow.build 做覆盖和待办校验。

evidence 可额外含 job_refs 数组，使用 Job 内 JSON Pointer 引用实际采集的公司、规模、薪资等结构字段，例如 /company、/company_size、/salary。字段必须存在且非空；不能把正文外公司事实伪造成 JD 原文引用。

## Executor

输入当前 candidate/policy/source-jobs/screening/reviews/v3 todos/shared ledger/浏览器凭据；执行前重新校验，不接受旧 v2 待办直接执行。candidate.authorization 是真实用户会话的授权镜像，含 sources/actions/user_instruction，可用 job_keys 限定范围；不能由配置自行赋权。

动作 contact/message/upload/fill/next/submit 分开。升级许可另行检查，不替代动作许可。上传事件 uploaded 在当前表单工具中仅指原生文件字段选择且读回一致，不证明雇主收到文件。submit 默认 uncertain，主 Agent 核对成功提示或申请记录后才能记录 submitted。

返回码通常 0 成功、1 错误/站点停止、2 等待用户或详情缺口。额度 gate 经旧入口可能返回 1，但统一状态仍 PAUSED，应按 pause_reason 判断。损坏事件不跳过，未知结果不重试。

## 迁移

原数据保留只读副本，新批次重新验证 JD 并生成新审阅。稳定 job_key 保留；换键保存原键/链接映射，通过 same_job 合并真实历史，不能按序号迁移。旧 candidate.preferences 与 policy 冲突时先核实并统一。CSV 不自动反写主数据。
