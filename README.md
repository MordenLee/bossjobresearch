# job-search

一个 Agent-native 求职 Skill：从简历和求职需求出发，搜索岗位、读取完整 JD、逐岗审阅，输出可编辑 CSV，并在用户明确授权的范围内执行沟通或表单操作。

主 Agent 负责规划和调度，Python 负责网页读取、确定性校验与持久化，Reviewer Subagent 负责 JD 语义审阅。无需自行配置外部 LLM API。

当前版本：**v3.0.1**。技能入口：[job-search/SKILL.md](job-search/SKILL.md)。

## 安装与使用

从 Releases 下载压缩包，将解压后的 `job-search/` 放入所用 Agent 的技能目录。也可以直接复制本仓库中的同名文件夹。

网站读取及简历解析依赖：

```sh
python -m pip install -r job-search/scripts/requirements.txt
```

向主 Agent 提出需求，例如：

> 使用 job-search，根据我的简历，在 BOSS 上搜索一个适合的关键词，读取完整 JD 后给出推荐。这次只搜索和推荐。

主 Agent 应按 Skill 文档准备候选资料、任务策略、运行目录与登录检查。运行数据保存在独立工作目录；仓库提供的是通用模板，不含候选人、招聘账号、Cookie 或真实岗位会话。

## 核心流程

`简历及需求 → 单站采集 → 完整 JD → Reviewer 审阅 → 校验及待办 → CSV → 授权执行`

- 主 Agent 动态选择方向、顺序和批次，各网站数据独立保存。
- 搜索前主动核实登录；登录未知、验证码、访问拒绝或额度问题保存断点并暂停。
- Reviewer 注入任务专属 `review_policy`，硬条件、软偏好、排除项、tradeoff 和核实规则分别表达。
- 城市、语言、薪资口径、公司规模、雇主名单及审阅倾向均来自当前任务；通用模板不预填这些偏好。
- 审阅绑定 JD、候选事实与策略内容；输入变化后旧结果不能继续执行。
- JSON 为主数据，JSONL 记录状态和真实操作，CSV 重导出保留人工选择及备注。
- `apply` 是审阅建议；沟通、消息、上传、填写、下一步和提交各自核对授权。
- 高成本工具升级需当前任务及方案的明确许可，不扩大外部动作权限。

## 文档与工具

| 内容 | 入口 |
| --- | --- |
| 状态、暂停、断点与恢复 | [states.md](job-search/references/states.md) |
| 模块输入输出 | [protocols.md](job-search/references/protocols.md) |
| 通用 Reviewer | [reviewer.md](job-search/agents/reviewer.md) |
| 任务策略 | [review-policy.md](job-search/references/review-policy.md) |
| 搜索及完整 JD | [search.md](job-search/references/search.md) |
| 授权执行及表单 | [apply.md](job-search/references/apply.md) |
| CSV 协作 | [export.md](job-search/references/export.md) |

主要脚本位于 `job-search/scripts/`：`collect.py`、`detail.py`、`policy.py`、`workflow.py`、`runtime.py`、`export_csv.py`、`contact.py` 和 `form.py`。具体参数参见各脚本 `--help` 与相应文档。

## 验证

```sh
python -m pip install -r requirements-dev.txt
python -m unittest discover -s job-search/scripts/tests -v
python tools/check_package.py
```

**82 项离线测试通过**，覆盖右侧JD切换及身份校验、正文完整性、偏好模板中立性、输入版本、规则边界、暂停恢复、未知操作结果、CSV 人工字段保留及命令行衔接。另已完成 BOSS 登录、原生规模筛选、岗位采集、完整 JD 核对、Reviewer 审阅和 CSV 交付的真实只读试跑；右侧采集核心已用两条真实卡片验证不新增标签。

网站 DOM 和风控表现会变化。默认登录标识仅配置了 BOSS；其他平台需核实当前页面并配置。BOSS优先在搜索页逐条读取右侧JD，核对岗位ID并统一验证URL、公司、标题及正文完整性；READY的正文直接用于审阅，未通过的岗位才单独补读详情。其他旧适配器仍在正式审阅前统一核验。

当前 BOSS 执行器支持沟通入口，通用表单执行器支持可读取的原生控件。自定义消息、HR 自动回复、复杂自定义控件和无登录公开表单需具体适配。消息、上传与投递路径未做真实网站端到端验证。

## 开发与发布

测试使用合成数据和模拟浏览器，不需要真实简历或招聘账号。运行目录、浏览器资料、日志和授权记录不应提交到仓库。发布压缩包只包含 `job-search/` 技能文件；下载后可用同一 Release 的 SHA256 文件校验。
