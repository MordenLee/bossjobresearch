# 审阅策略

通用规则在 agents/reviewer.md，任务需求注入 review_policy.json。结构和模板见 assets/review_policy.schema.json、assets/review_policy.template.json。此版本依据交接决定重新实现，未声称恢复未找到的 v0 原文。

策略含 schema_version=1、policy_id、version 及六个数组：hard_constraints、soft_preferences、exclusions、tradeoffs、priority_rules、verification_rules。空数组表示没有指定该类规则，不擅自添加职业或薪资限制。

通用 Skill 只保留事实引用、规则优先关系及结果校验机制。具体城市、薪资金额或口径、公司规模、职业方向、雇主名单及推荐/排除理由属于任务数据，不随技能发布。空模板不是默认推荐策略，旧候选资料中的特殊规则也须先编入有来源的任务 policy，不能由兼容代码暗中执行。

每条规则含 id、statement、source。source 含 kind=user_instruction/candidate_evidence/confirmed_preference、ref=可定位资料或对话引用、quote=原文。ID 跨分类唯一。候选事实不自动成为偏好，推断需求先核实，不能以“系统默认”伪造来源。

tradeoffs 额外含非空 may_relax_rule_ids，仅可引用 soft_preferences。硬条件、排除项、强制核实项不能覆盖。priority_rules 影响顺序不改变资格。verification_rules 是申请前必须解决的信息条件，未知阻止 apply。

排除项 pass=确认未触发、fail=触发、unknown=未知。强制规则申请前全部 pass；不适用的强制规则由主 Agent 修订，Reviewer 不偷删。

```text
python scripts/policy.py --policy <policy.json>
python scripts/policy.py --policy <policy.json> --candidate <candidate.json> --jobs <ready-jobs.json> --out <review-packet.json>
```

校验器验证字段、来源结构、唯一 ID、tradeoff 目标、输入绑定、规则覆盖与证据位置；不代替语义判断，也不能证明原文确实表达需求。主 Agent 核对来源。修改策略更新 version；内容 hash 也会阻止同版本内容改变后旧审阅继续执行。
