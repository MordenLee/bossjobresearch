# 初筛与逐岗审阅

screening.json 数组覆盖该批全部岗位，项含 job_key/source/decision=keep或skip/reason/evidence。skip 必须有明确事实；未知不当不合。所有 keep 保持待审，不只审排行靠前者。

Collector 补齐并核对完整 JD 后，主 Agent 生成审阅包并委派 Reviewer Subagent。读 agents/reviewer.md 和 protocols.md。Reviewer 只读该批输入并返回独立结果，主 Agent 合并和检查覆盖；正文、候选资料或策略变化须重审。

分批构建时 jobs/screening/reviews 属同一站点同一批，站点主数据保留其余队列。全部 keep 最终需审阅或明确未完成说明。详情缺失或身份错配留待补，不能捏造正式 Review。

apply 要职责 pass、有 JD 原文和已确认履历证据、强制规则全部 pass、无 blocking 风险和未决问题。verify 至少一个具体问题，skip 用明确不合证据。priority 1–3 不覆盖资格，软偏好未满足不自动否决。

```text
python scripts/workflow.py build --site <site> --jobs <batch-jobs.json> --screening <screening.json> --reviews <reviews.json> --candidate <candidate.json> --policy <policy.json> --out-dir <review-dir> --ledger <campaign/ledger.jsonl>
```

输出 v3 todos.json/todos.md，含输入绑定与审阅。代码校验不证明语义正确，主 Agent 仍检查未知、反例与来源。

建议与实际操作独立。重建按稳定键、规范链接和保守跨站身份合并历史；不同 HC 不因同标题合并，模糊重复核实。已操作但移出清单的岗位进 archived_todos。未完成外部意图保留 uncertain，旧联系成功不能掩盖后来的提交意图。
