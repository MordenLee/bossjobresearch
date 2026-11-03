# Workday 托管招聘门户：DrissionPage 采集

career / careers 是常见网址命名，不是统一招聘系统。本适配仅覆盖
`*.myworkdayjobs.com/<locale可选>/<site>`，不自动套用 Oracle、SAP SuccessFactors、
SmartRecruiters、品牌独立门户或 myworkdaysite.com。

复制 `assets/workday-dom.template.json` 到任务目录的 `workday-dom.json`。
租户、数据中心、站点取自已核实的真实入口，不能猜测。
默认 authenticated 模式使用 runtime-config.json 的 session 精确域名及现场核实登录标识。
用户授权公开采集后，可设 access_mode=public 并在 public_access.evidence 记录依据。
正常导航栏 Sign In 不等于登录墙；出现登录表单、验证或访问拒绝暂停。
沿用已核实归属的 browser.json 和 connect_owned，不接管日常 Chrome。

使用 `normal` 加载模式。DrissionPage 的 `eager` 会在 DOMContentLoaded 时调用
Page.stopLoading，可能取消 Workday 的延迟启动脚本，造成只有空 root 的页面。
2026-11-03 对 NVIDIA、Philips 的现场对照均确认：eager 空白，normal 恢复列表。
这不表示其他网络或站点异常已自动修复；旧空页需重新实测。

```text
python scripts/workday_dom.py --run-dir <task> --output <batch> --keyword Agent --max-pages 2 --limit 2
```

max-pages 为列表页预算，limit 为前 N 条详情；已有正文默认跳过，--refresh 保留变化历史。
续读时 limit 计入真正待补读的条目，不让已完成的前 N 行耗尽新批次预算。
仅读取，不点击申请。默认等待30–45秒，每5次操作休息90秒。
next_selector 必须在当前语言核实；英语实测 button[aria-label="next"]。

搜索核对输入、URL q、稳定列表及独立总数。仅明确0 JOBS FOUND或找到0个职务视为零结果；
未知语言总数保持null。翻页必须改变链接序列，按域名+站点+岗位ID去重。
重复页或总数变化保存并停止；唯一ID数量等于稳定总数才标list_complete。

详情验证租户、站点、ID和标题；正文仅取jobPostingDescription，不回退body。
结构化字段限定jobDetails或jobPostingPage内同时包含当前标题与正文的最小祖先容器，取首组同名标签，避免后置推荐岗位污染。
保存完整正文、多个地点、工作性质、发布日期原文、招聘编号和原始字段。
相对日期不伪造为精确日期；缺失薪资/学历/年限留空；公司标注来自任务配置。
COLLECTED_DETAIL_DOM不自动等于READY。JSON/CSV逐页逐岗保存，保留人工备注与旧记录。
coverage分开记录总数、列表数、正文数和结束原因；预算完成不代表全公司覆盖。
批次配置改变需新目录，未出现的旧记录不自动判下架。

## 可选的原生地区筛选

配置来自当前页面实际看到的控件与标签，不填写猜测的国家/城市ID：

```json
"location_filter": {
  "label": "China",
  "opener_selector": "css:button[data-automation-id=\"distanceLocation\"]",
  "apply_text": "View Jobs"
}
```

本项标签和确认文字须与当前租户/语言一致。打开菜单后程序从可见复选框的关联
label 精确匹配，取得实际ID；`China` 不会匹配 `China, Beijing`。缺失、重复、禁用
或无ID时停止。原生勾选后核实 checked，再点击已观察到的查看职位按钮。
结果URL须包含该实际ID，且结果序列或独立总数发生变化，才保存筛选生效证据。
搜索、地区选择及详情各阶段分别保存进度。菜单可打开不等于地区已生效。

`location-options.json`、`location-filter-evidence.json` 保存实际选项及前后结果；
`location-filter-progress.json` 记录阶段。确认按钮只支持已识别的查看职位文案，
不支持投递按钮。中文分页仍须核实当前按钮后配置，不能套用英语 aria-label。

验证/登录/不可检查页面会结束本站，coverage 使用 `PAUSED_SITE` 并保存实际
pause_reason，不再统一写成普通批次中断。人工处理要求仍由 Runtime 原因决定。

## GitHub调研（2026-10-26）

- [ats-jobs-scraper/main.py](https://github.com/GetAnything-1/ats-jobs-scraper/blob/main/src/main.py)：Workday分支使用HTTP CXS列表，want_desc未实现详情采集。参考租户识别和分页思路，不将README声称当验证结果。
- [job-crawler/job_crawler.py](https://github.com/felenko/job-crawler/blob/master/job_crawler.py)：含多ATS策略和Playwright回退；body正文回退可能混入导航及推荐内容。

本适配独立实现，未复制或运行第三方仓库代码。选择器来自实际DP DOM；未引入Apify、外部LLM、HTTP采集、自动登录或验证绕过。跨租户/语言仍需小批量验证。

## 2026-11-03 补充实测

- NVIDIA：normal 模式恢复列表；Analyst 搜索显示597条，原生选择实际 China 选项并点击 View Jobs 后显示49条，URL含现场选项ID。本批预算1页，保存20条列表、1份完整JD（Sales Operation Specialist，3409字符）。中国筛选后的多页采集本轮未实测。
- Philips：自动跳转 zh-CN，Data 搜索保存20条列表、1份完整JD（Lead Data Scientist，8623字符）。本批没有执行中国筛选或中文翻页。
- 上述样本仅用于适配验证，不代表符合任何候选人的求职条件。批次正文状态仍为 COLLECTED_DETAIL_DOM；城市、薪资等匹配规则由当前任务输入决定。
- 离线回归215项通过；这不代表目录内所有公司、语言、地区筛选和分页均已实时验证。

## 2026-10-26 实测边界

- NVIDIA 英语列表：Agent搜索显示555条；预算2页读取40个去重岗位，已验证翻页，未穷尽。
- 修复侧栏/独立详情容器差异后，NVIDIA搜索JR2020478：1条列表、1条完整JD，上海；正文2650字符。
- Philips自动切换zh-CN：搜索590631，1条列表、1条完整JD，上海；正文11376字符。中文总数字段已据现场证据补充解析；中文翻页按钮尚未实测。
- 薪资未提供时留空，发布时间保留相对日期原文。样本用于适配验证，不代表岗位推荐或投递。
