# 搜索与完整 JD 采集

主 Agent 从 CV 的可核实能力及用户目标形成搜索方向、同义词与中英文词组，记录方向依据、站点、地区、预算及实际覆盖。英文命中不等于外企。搜索顺序、批次与追加由主 Agent 决定；每次 Python 调用只处理一个站点。

```text
python scripts/collect.py --keywords <keywords.json> --run-dir <site-run> --sources boss --target 60 --per-keyword 6 --pages 2
```

keywords.json 为 {"keywords":["职位词","English Role"]}。原适配器保留边搜索边读详情的能力，但正文统一核验前标 PENDING；不能因有 jd_description 就跳过身份与完整性检查。

先复制并按任务调整 assets/runtime-config.template.json 为运行目录内 runtime-config.json。登录预检读取可见标识并检查域名；BOSS 有默认 .nav-figure。其他站点需主 Agent 从当前页面核实 hosts 与 signed_in_selectors，未配置则 LOGIN_UNKNOWN 暂停，不能假定登录。选择器验证是标准适配工作；若需高成本网页读取程序则走升级确认。

首次新建浏览器有独立 profile 并保存 browser.json。已有端口仅在 --resume 且凭据匹配时连接。--reuse-login 是旧参数兼容项，不跳过主动预检。登录/验证码/拒绝访问和恢复见 states.md。没有浏览器连接或登录验证通过，不执行采集。

默认不改站点城市。用户要求地区筛选时，主 Agent 应通过适配器或已核实站内控件设置并确认生效；当前 CLI 没有通用城市筛选参数，不以本地过滤冒充网站筛选。未支持的平台或控件明确报告能力缺口。

导航/点击间隔用 --delay-min/--delay-max，批次休息用 --batch-size/--batch-rest，数值来自任务配置。间隔不保证避免风控。单次预算完成不等于全量覆盖，零结果必须有页面证据，空列表不能当零结果。

初筛后补读完整正文：

```text
python scripts/detail.py --jobs <jobs.json> --screening <screening.json> --run-dir <site-run> --attach-port <owned-port> --selectors <selectors.json> --limit 10
```

selectors.json 含 source、title/company/description 三个 CSS 选择器和可选 truncation_selector。assets/boss-detail-selectors.json 由已有源码选择器整理，仍需实际页面核实。其他站点按当前页面提供，不伪造已验证默认值。身份按规范化 URL、公司和标题严格相等核对；标题/公司变化退回核实，不自动把同标题其他 HC 接受为本岗。

--refresh 显式刷新已 READY 的岗位；正文变化保留历史，旧审阅随输入 hash 失效。不自动点击“展开全文”；检测截断先暂停，由主 Agent 用标准已核实控件展开后重新读，需成本升级则确认。读取错误、身份错配、正文不完整分别保存，不用这些结果证明岗位关闭。

恢复保留既有 jobs、coverage、skipped、failures；未完成关键词从顶部重放并去重。预算不足以越过旧边界时报告实际窗口，不冒充没有新岗位；新预算放新批次，经主 Agent 合并稳定键与出现记录。不同城市/方向批次的覆盖应在 search-plan.json 中独立记录。
