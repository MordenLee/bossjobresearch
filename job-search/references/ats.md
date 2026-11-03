# Moka / 北森适配设计（初版）

独立企业招聘页面的已验证布局和DP采集入口见 [independent-sites.md](independent-sites.md)。逐公司待办属于任务数据；只有现场核实且跑通的配置才算已支持。

新增外企系统入口：[Workday DP 采集](workday.md)，使用独立 `scripts/workday_dom.py` 与租户配置，不改变现有 Moka / 北森适配。

`scripts/ats.py` 是可离线测试的采集核心，不是已完成现场验证的官网爬虫。不注册到 collect.py 的浏览器站点列表；现有 BOSS/猎聘/智联/51job 路径不变。协议映射参考公开源码并独立实现，当前公司接口、权限和字段仍需现场确认。

## 默认入口：用户登录后的 DrissionPage

真实网站采集使用 `scripts/ats_browser.py::collect_browser`。它不调用 HTTP 传输或页面 fetch/XHR，而将受 Guard 保护的 DP Proxy 交给已核实的 DOM 适配器。下面的 fetch_page 协议仅保留作离线响应回放/旧解析兼容，不是默认联网路径。

1. 为任务创建独立 Chrome profile，打开企业招聘入口，保存归属核实的 browser.json。接回只使用 connect_owned，不自动启动其他浏览器或接管日常 profile。
2. 用户自己完成账号、短信、扫码等登录；主 Agent 从实际页面核实可见登录标识，写 runtime-config.json。session.hosts 必须是当前租户的精确域名，signed_in_selectors 必须为已验证的可见用户标识，不能用全站通用导航替代。不导出 Cookie、密码或验证码。
3. 运行前、每页读取前后、每条详情读取前后均核实登录。未知/失效就保存 Runtime PAUSED；采集中的暂停会保留 jobs.json 和 coverage.json，浏览器保持开启。
4. 用户明确回复完成后，沿用 states.md 的 token 绑定 continue.json 恢复流程；程序不会因登录标识出现就自动确认。恢复再次核实登录，通过后从第一页重放去重，保留已经读取的正文。
5. 同租户详情新标签通过 Proxy 获取，仍检查验证状态；详情回调返回前回到租户列表。跨域登录由用户完成并返回原租户页面，不把登录中心或另一租户当作本企业登录成功。

```python
from ats import Site, Page
from ats_browser import collect_browser

# 具体 list_reader/detail_reader 来自已现场核实的企业 DOM 适配器。
# list_reader(proxy, page_number, keyword) -> Page(rows, total)
# rows 用 Moka id/title 或北森 Id/JobAdName 字段；ID必须来自真实DOM证据。
# detail_reader(proxy, job) -> 独立详情证据（字段见下文）。
result = collect_browser(site, run_dir, port, list_reader, detail_reader,
                         keyword='数据分析', max_pages=5)
```

任务配置增加 pacing：delay_min、delay_max、batch_size、batch_rest；默认30–45秒、每5次操作休息90秒。纯DOM读取使用 proxy.read_js，点击/导航使用 Proxy 方法。每家公司页面选择器仍需现场核实，本版本没有声称已完成 Moka/北森真实站点的登录和JD采集验收。

## 职责

- `Site`：招聘系统、租户、公司、HTTPS origin、Moka org/site ID、北森社招/校招类别。任务配置保存，不放个人简历、Cookie、筛选偏好。
- `Moka` / `Beisen`：生成列表请求及分别解析响应。北森使用 Id 作为详情身份；JobAdId 等原始字段保留。
- `collect`：持有目录写锁，逐页保存公开岗位记录，逐岗保存标准结果；恢复时重放分页、按平台+租户+岗位ID合并。已读正文和用户字段保留；不把历史记录数量计作本轮覆盖。
- `fetch_page(url, body)`：由调用方提供已核实的传输层，包括超时、等待、批次休息、登录/验证暂停、HTTP状态及响应检查。默认没有网络请求，不提供绕过验证码或自动登录。
- `detail_reader(job)`：独立读取完整详情，返回 job_id、source_id、真实 url/title/company/description、明确的 truncated 布尔值。公司名称必须来自详情证据，不能简单复制输入。详情ID与租户一致后交由现有 detail.apply_detail 校验，生成正文hash及历史。

## 调用示例（离线回放）

```python
from pathlib import Path
from ats import Site, collect
from workflow import read

site = Site('beisen', 'example-social', '示例企业', 'https://example.invalid')
responses = iter(read('recorded-list-pages.json'))
result = collect(site, lambda url, body: next(responses), Path('work/ats-example'),
                 max_pages=5)
```

没有 detail_reader 时岗位保持 PENDING，绝不将列表摘要标为 READY。生成的 jobs.json 可交给现有 `export_csv.py --jobs` 导出；审阅仍需完整正文和任务策略。当前为 Python 调用接口，没有另加网络 CLI。

## 完整性和错误

`coverage.json` 分开记录 raw_fetched、unique_fetched、reported_total、ready_count、页数、错误与结束原因。complete 仅表示本轮列表唯一ID数量与稳定的平台总数严格一致，不表示所有详情已读，也不表示全公司所有招聘渠道已覆盖。没有总数时保持未知；平台明确 total=0 且列表为空才算确认零结果。不能用短页证明已结束，也不使用90%阈值。

重复页、总数变化、缺ID、响应字段变化均返回错误；已采集内容保留。普通详情错误不删除岗位；用户暂停/验证类 BaseException 写入 INTERRUPTED 后向上抛出。未出现的旧岗位不自动关闭。分页快照保存在 pages/，完整JD在 jobs.json。运行目录应专用于一个租户配置；不同招聘系统不因ID相同自动合并。

Moka 本轮只支持已核实的解码后列表 JSON；遇到密文明确报错。未实现租户解码、实时传输、详情URL发现及详情字段提取，不包含硬编码 Cookie、密钥或旧版详情URL猜测。Moka 服务端关键词筛选未核实，非空 keyword 会明确拒绝；可全量发现后交上层筛选。

后续现场接入顺序：从公司官网确认招聘入口 → 核实租户与列表响应 → 实现受控传输 → 获取独立详情证据 → 以小批量校验字段、分页、JD完整性 → 再开放批量采集。系统共用编排，不共用未经确认的接口字段。

## Moka DOM 搜索实测入口

`python scripts/moka_dom.py --run-dir <已登录任务目录> --keyword Agent --output <本轮输出目录>`

已在 Kimi 的 Moka 页面验证搜索框 `input[placeholder="搜索职位关键词"]`。输入后同时核对 URL 中 keyword、平台总数和稳定卡片内容；只采集带 card-content 的岗位链接，排除“最新职位”推荐。分页核对岗位 URL 序列变化与总数；已实测“工程师”关键词 38 条、两页采集，总数与去重结果一致。

运行配置 session 需包含精确 hosts、已核实的 signed_in_selectors、tenant_path，顶层 company 用于站点配置；采集公司名称取页面版权信息。共享 Moka 域名不能替代企业路径验证。

列表优先提取 JD 正文；空正文或“暂无”进入详情补读，最多等待 60 秒，且非占位内容连续稳定 6 秒才接受。超时只标 PENDING，不声称官网没有 JD。保存职位名称、公司、性质、职能类型、地点、发布日期、稳定 URL、关键词和完整正文；薪资/经验/学历无结构化证据时留空。结果为 COLLECTED_LIST_DOM/COLLECTED_DETAIL_DOM，尚未自动等同通用 READY 审阅校验。

保留原始搜索证据、JSON 与 UTF-8 BOM CSV；CSV 人工备注由既有导出器合并保留。建议每次采集使用独立输出目录。本入口仅采集，不点击申请职位。

2026-10-25 实测：Agent 13 条、产品 11 条、工程师 38 条，合并去重 51 条，其中 50 条取得正文。未提供正文或地点的条目保留待核实，不能由单个样本推断平台通用行为。

## DeepSeek 官网搜索与 Moka 详情

入口 `scripts/deepseek_dom.py --run-dir <task> --keyword Agent --output <batch>`。
官网 `https://talent.deepseek.com/` 使用自身搜索组件，详情链接到 Moka `https://app.mokahr.com/social-recruitment/high-flyer/140576#/job/<id>`。通过可见搜索框输入、稳定结果和实际链接采集；逐条验证目标域名、企业路径、岗位 ID 与标题，再读取完整描述和标签对应的职位信息。不能套用 Kimi 列表 metadata 的位置。

公开采集需任务目录 `public-access.json` 中保存用户真实授权与 authorized=true；默认仍遵循登录规则。即便公开采集获授权，Guard 仍检查登录提示、验证及访问拒绝并暂停。每个任务独立确认访问范围，代码不自动登录或提交申请。

字段包括公司（官网版权信息）、岗位、职能类别、地点、稳定链接、关键词、正文与详情字段；官网/详情未提供的薪资、发布日期等留空。原始搜索证据与逐条详情证据随 JSON、CSV 保存。当前搜索适配只处理有结果的可见列表；空列表不证明零岗位，会超时保留异常。无站内总数标识时只报告实际搜索结果行数，不推断全站穷尽。

## 北森经典表格模板：已实测 DP 入口

`python scripts/beisen_dom.py --run-dir <task> --output <batch> [--limit 3] [--refresh]`

复制 [北森配置模板](../assets/beisen-dom.template.json) 到任务目录的 `beisen-dom.json`，填入实际核实的 host、list_url、tenant、company。`public_access.authorized=true` 及 evidence 必须来自用户明确允许公开页面采集；这不自动解除已有的验证或访问拒绝暂停。默认操作间隔 30–45 秒，每 5 次操作休息 90 秒。

此适配器已验证 TalkingData 的经典表格布局：列表从 `tr a[href*="/zpdetail/"]` 及同一行的列提取，详情从 `.xiangqingtext`、`.xiangqinglist` 提取。保存完整正文、工作职责、任职资格、性质、薪资、地点、发布日期、截止时间、招聘类别及人数。字段取真实页面标签，缺失留空；公司来自已核实的任务站点配置，并在 JSON 标明来源，不能视作详情独立公司验证。输出状态为 COLLECTED_DETAIL_DOM，不自动冒充 READY。

详情域名、数字岗位 ID、标题需匹配。占位正文不会当成采集完成；读取失败逐岗保存，验证暂停传播并保存当前批次。支持已有正文跳过、显式刷新、变化正文保留历史、CSV 人工备注合并及目录写锁；输出目录配置发生变化须新建批次。逐条写出 jobs.json、原始搜索/详情证据与 jobs.csv。

当前覆盖范围仅为已加载的表格列表，不自动分页、不适配所有北森新版布局，不将空表格解释为零岗位。此前 TalkingData 实测列表 3 条，3 条均读取了完整职责与任职资格。
