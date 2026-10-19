# 状态、暂停与恢复 v3

task_state 为 READY/RUNNING/PAUSED/BUDGET_COMPLETED/COMPLETED/FAILED；phase 为 planning/login/search/detail/review/export/execute。BUDGET_COMPLETED 只指本批预算完成，COMPLETED 由主 Agent 核对承诺范围与未完成项后记录。pause_reason 独立保存。

task-events.jsonl 是恢复依据，每条含 sequence、event_id、task_id 及完整状态；task-state.json 是可重建快照。先追加事件并落盘，再原子替换快照。事件损坏、序号断裂或 task_id 不符则保留文件停止，不跳过坏行。JSON/JSONL 跨文件没有数据库事务；恢复时按稳定键核对 jobs、coverage 与 checkpoint 后重建。

旧 status.json/events.jsonl 仅为兼容输出；统一状态以任务事件为准。ledger.jsonl 记录真实外部操作，不能由任务状态推断提交成功。

| pause_reason | 范围与解除条件 |
| --- | --- |
| LOGIN_REQUIRED / LOGIN_UNKNOWN | 当前站点；用户登录并明确回复，重新读取可见登录证据 |
| CHALLENGE | 站点；用户完成验证并回复，再检查页面与登录 |
| ACCESS_DENIED | 停止站点，不绕过、不换身份；用户明确要求重试并确认恢复后才重新核查 |
| QUOTA_CHECK | 当前批次；主 Agent 查询真实额度并保存新快照，通过 gate |
| QUOTA_LOW | 同上；新快照达到当前任务阈值，不自行消费重置权益 |
| USER_PAUSE | 当前运行目录；明确继续后核实环境 |
| NEEDS_INPUT | 岗位或批次；补事实/策略并校验，必要时重审 |
| UPGRADE_CONFIRMATION | 当前任务和具体方案；专门批准/拒绝，普通继续无效 |
| RESULT_UNCERTAIN | 当前动作；核查页面及 ledger，不直接重发 |
| READ_ERROR | 岗位或站点；主 Agent 可在当前页面只读诊断，核实加载、身份及标准选择器修复后记录依据恢复，不当零结果；超出标准能力走升级确认 |

暂停保存 checkpoint、token、原因与范围说明，进程退出，保留窗口，不轮询用户。每次新暂停更新 token。ack 只记录用户回应，仍为 PAUSED；实际预检通过后才能 start。访问拒绝和未知操作结果不能由自动重试消除。

READ_ERROR 的 ack 可记录主 Agent 在已授权范围内完成标准适配修复的实际证据；这不等于用户完成登录或验证，不可用于解除 LOGIN_REQUIRED/CHALLENGE/ACCESS_DENIED。

```text
python scripts/runtime.py --run-dir <run> inspect
python scripts/runtime.py --run-dir <run> ack --token <当前token> --evidence <用户明确回复的引用>
```

兼容 `collect.py --continue-run <run> --token <token>`，仅在用户明确回应后调用。原参数恢复加 --resume，接回已核实端口。确认标记不证明已登录；所有浏览器入口重新预检，无验证弹窗不证明登录。

## 断点与写入

同一运行目录用 .writer.lock 互斥。异常退出残留锁须先核实进程已结束再人工移除，不按年龄删除。主 Agent 串行运行共享数据/浏览器的工具，Reviewer 返回独立文件。

采集保存 run.json、jobs 和 coverage。恢复不能改原参数；已完成关键词跳过，未完成关键词从列表顶部重放，保存记录去重后经过旧边界。预算有限，未到新边界不声称搜索穷尽；空读取须有页面明确零结果证据。预算不足由主 Agent 新建追加批次，不静默升级。BOSS 不再因前几轮只有旧卡片提前判空。

详情断点保存当前和剩余 job_key；READY 默认跳过，--refresh 显式刷新。身份不符、正文空或截断分别留状态并暂停，不通过移出队列假装完成。

browser.json 含 address、tab_id、run_dir、ownership_verified 和 profile。新建任务浏览器自动留凭据；接回用 existing_only 且验证匹配。换阶段目录前，主 Agent 先核实同一任务、端口和标签页，再建立新目录凭据；不能只抄端口。凭据是核查记录，不是系统级身份认证。

## 额度与升级

runtime-config.json 可配置 quota；未配置时不伪造实时计量。额度快照需 source=mcp__codex_app__get_usage_limits、checked_at_epoch、remaining_percent、checked_processed_total，使用当前工作的同一额度桶。来源、时间或处理量不合即暂停。离线 Reviewer 调度前后由主 Agent 检查。

```text
python scripts/runtime.py --run-dir <run> refresh-quota --snapshot <真实额度快照.json> --processed <当前累计处理数>
python scripts/runtime.py --run-dir <run> request-upgrade --proposal <具体方案与替代办法> --cost-notice <可能大量消耗Token的说明>
python scripts/runtime.py --run-dir <run> decide-upgrade --token <token> --decision approve --evidence <用户明确批准的引用>
```

升级绑定 task_id 与方案 hash。拒绝也保存并解除等待，以便选择标准方案或交付受限结果；执行升级程序前调用 Runtime.check_upgrade(同一方案)。换任务或方案须重新确认，外部动作另核授权。脚本不能证明用户回应和快照真实性，主 Agent 从真实会话和工具取证。

旧状态数据保留只读，在新目录初始化 v3 并重验；旧审阅按新 policy 重新生成，真实 ledger 保持共享。不能把旧 SOURCE_STOPPED 或额度标签直接伪造成已解除的新状态。
