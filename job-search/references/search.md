# 搜索与完整 JD 采集

主 Agent 从 CV 的可核实能力及用户目标形成搜索方向，并按任务中的搜索语言扩展同义词，记录方向依据、站点、地区、预算及实际覆盖。语言未指定时说明搜索计划，不把固定语言组合当作用户偏好。搜索顺序、批次与追加由主 Agent 决定；每次 Python 调用只处理一个站点。

```text
python scripts/collect.py --keywords <keywords.json> --run-dir <site-run> --sources boss --target 60 --per-keyword 6 --pages 2
```

keywords.json 为 {"keywords":["职位词","English Role"]}。BOSS默认在搜索页逐条切换卡片并读取右侧正文，不点击标题或“查看更多信息”打开新标签。自定义采集复用 `adapters.extract_boss.extract_search_detail(Proxy(browser, guard), card)`，不另写只保存列表卡片的路径。右侧链接ID须与卡片一致，等待非空正文稳定后立即持久化，纯读取使用 `Proxy.read_js`，点击仍受任务等待和批次休息控制。

保存 `jd_source=search_panel`、读取时间、岗位ID及 `panel_detail_evidence`。采集入口使用与详情补读相同的身份及完整性校验：URL、公司、标题严格核对，正文非空且未检测到截断才标READY并生成内容hash。旧适配器仅有正文或verified标志仍标PENDING，不能绕过统一校验。READY正文可直接进入审阅，不重复打开详情页；投递前仍实时核对必要条件。

切换后链接尚未更新、正文仍与上一条相同、面板缺失或空正文分别保存 `detail_read_error`，失败正文留空，不套用上一条JD。完全相同的正文也先保守留待核实。验证/拒绝访问向上抛出并按现有流程暂停，不吞掉异常重试。未READY的条目才进入detail.py补读，已有正文、真实沟通历史不因采集失败被覆盖。

先复制并按任务调整 assets/runtime-config.template.json 为运行目录内 runtime-config.json。登录预检读取可见标识并检查域名；BOSS 有默认 .nav-figure。其他站点需主 Agent 从当前页面核实 hosts 与 signed_in_selectors，未配置则 LOGIN_UNKNOWN 暂停，不能假定登录。选择器验证是标准适配工作；若需高成本网页读取程序则走升级确认。

首次新建浏览器有独立 profile 并保存 browser.json。已有端口仅在 --resume 且凭据匹配时连接。--reuse-login 是旧参数兼容项，不跳过主动预检。登录/验证码/拒绝访问和恢复见 states.md。没有浏览器连接或登录验证通过，不执行采集。

先读取并记录当前站点地区，不把浏览器上次选择的城市当作用户的永久限制。按当前任务确定搜索范围，并通过适配器或已核实站内控件设置、确认生效；地区未指定时说明实际覆盖。当前 CLI 没有通用城市筛选参数，不以本地过滤冒充网站筛选。未支持的平台或控件明确报告能力缺口。

导航/点击间隔用 --delay-min/--delay-max，批次休息用 --batch-size/--batch-rest，数值来自任务配置。间隔不保证避免风控。单次预算完成不等于全量覆盖，零结果必须有页面证据，空列表不能当零结果。

初筛后补读完整正文：

```text
python scripts/detail.py --jobs <jobs.json> --screening <screening.json> --run-dir <site-run> --attach-port <owned-port> --selectors <selectors.json> --limit 10
```

selectors.json 含 source、title/company/description 三个 CSS 选择器和可选 truncation_selector。assets/boss-detail-selectors.json 由已有源码选择器整理，仍需实际页面核实。其他站点按当前页面提供，不伪造已验证默认值。身份按规范化 URL、公司和标题严格相等核对；标题/公司变化退回核实，不自动把同标题其他 HC 接受为本岗。

--refresh 显式刷新已 READY 的岗位；正文变化保留历史，旧审阅随输入 hash 失效。不自动点击“展开全文”；检测截断先暂停，由主 Agent 用标准已核实控件展开后重新读，需成本升级则确认。读取错误、身份错配、正文不完整分别保存，不用这些结果证明岗位关闭。

恢复保留既有 jobs、coverage、skipped、failures；未完成关键词从顶部重放并去重。预算不足以越过旧边界时报告实际窗口，不冒充没有新岗位；新预算放新批次，经主 Agent 合并稳定键与出现记录。不同城市/方向批次的覆盖应在 search-plan.json 中独立记录。

## 猎聘

新版列表使用 `a[data-nick="job-detail-job-info"]` 与相邻公司区读取字段，避免依赖构建哈希类名。`runtime-config.json.search_url` 可配置从页面实际核实的 `/zhaopin/` URL；替换关键词只重置页码，保留城市、薪资、规模等平台条件。未配置时为全国，不从候选人配置猜城市代码。

猎聘正文从详情页 `[data-selector="job-intro-content"]` 读取，主标题限定在 `.job-apply-content`，避免混入相关推荐。保存 `collected_detail_evidence`，复用 `detail.apply_detail` 的 URL、标题、公司、完整性检查；匿名雇主或名称变化留身份待核实，保留原文，不冒充 READY。跟踪参数不参与 `/job/<id>.shtml` 与 `/a/<id>.shtml` 的稳定身份；两种路径不合并。

列表无卡片报读取未就绪，不等于无岗位。详情失败保存 `detail_read_error`；登录/验证暂停保留窗口。每条完成后通过采集入口立即落盘。该适配仅验证采集，不能据此声称支持猎聘自动投递。

## 智联新版分栏页

首页搜索输入为 `.search-wrapper__input`，按钮为 `.search-wrapper__button`；真实搜索会进入 `/jobs/?jl=…&kw=…`。先在页面确认地区，再将实际 URL 写入 `runtime-config.json.search_url`。适配器替换 kw 时保留平台筛选，不能将旧 `/sou/` 和 `.joblist-box__item` 当成新版页面。

列表使用 `.job-list-panel .job-card`，详情在 `.job-detail-panel`。逐条点击卡片本体，读取右侧标题、公司、正文及“查看更多职位信息”的 href，不点击该链接或投递按钮。列表没有公开岗位链接，因此先核对卡片索引与可见字段，等待选中状态、标题、公司、详情URL和正文稳定后，再取得稳定岗位身份；切换时仍沿用旧链接或旧正文则留错误，不跨卡片复用。详情再进入统一校验。相同标题和公司不代表同一 HC。

当前验证范围为已加载的一屏列表，适配器不会声称 max_pages 页已经读取。追加滚动、更多页或新布局需要先核实控件；空列表记为读取异常，尚未配置智联明确零结果标识。登录标识可由任务核实 `.c-login__top__name` 后配置，避免取隐藏菜单作为登录证据。
