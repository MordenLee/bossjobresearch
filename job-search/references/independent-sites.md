# 独立企业招聘页面：DP采集

公开页面通用读取与原生交互入口见[career-browser.md](career-browser.md)。它封装11个已读入口，提供控件快照、搜索输入、链接/翻页点击、原生下拉选择及验证暂停；不能据此把整站JD适配标为通过。

另有`scripts/huawei_detail_dom.py`单页详情读取器，已通过1条完整JD现场验证。它读取已由真实卡片点击打开的详情，校验来源标题、地点、族类、部门、经验及点击导航URL。列表DOM没有公开ID，source_id因此留空，detail_job_id从真实详情URL取得，并保留PENDING审阅就绪限制；不得把详情ID复制成来源卡片ID自证。搜索与分页尚未接入，华为不能计入整站适配完成数量。

独立域名不等于自研系统。先核实页面结构，再决定复用已知ATS、增加DOM配置，还是实现新的布局模块。逐公司状态放任务目录，不能把待办105条写成支持105站。

当前脚本 `scripts/independent_dom.py` 支持 `inline_sections`、`link_details` 和 `click_details` 布局。已现场验收：`assets/independent-sites/douban.json`（3条完整JD）、`fydeos.json`（5条完整JD）、`airbnb.json`（界面搜索、两页20个ID及2条完整JD）、`fanruan.json`（界面按钮搜索及2条新标签完整JD）。豆瓣和FydeOS当前页面未发现搜索或分页控件，不伪造对应能力。

`geely-moka.json`复用内联布局支持Moka品牌域名：已现场验证原生搜索“数据”、30个当页岗位ID和3条完整JD。ID来自真实锚点，核对hash路由ID和租户路径；公司、性质、职能、地点的顺序仅限该配置的实际DOM，原文与选择器一起留存，不套用其他Moka站。分页尚未验证。

内联配置可显式启用`allow_pending_inline_descriptions`：列表身份与公司校验保持生效，稳定读取后逐岗检查正文。缺失或不完整的正文仍保留为PENDING并记录原因，不阻断同页其他岗位，也不计入with_jd。列表总数、采样数、完整正文数分别统计；不把平台写的“暂无”当成采集成功。

内联折叠页可配置`inline_expand_selector`和`require_description_visible`：通过卡片真实ID定位原生展开控件，点击后再次核对ID、标题、公司与完整可见正文。字段未展开时拒绝计入完整JD。`jd-social.json`当前仍为实验配置：原生搜索已经生效，标题和实际下拉箭头点击后却未展开，已保留READ_ERROR及现场证据，不能计入已验证站点。未取得独立详情链接时，job_url留空，实际列表地址保存在source_page_url，岗位身份由source_id区分，不拼造详情URL。

同批恢复搜索时，只有已落盘的搜索证据与当前URL、输入关键词、非空岗位ID序列完全一致，才复用当前结果；否则重新执行并核验搜索。不能仅凭输入框已有文字就声称搜索通过。

```text
python scripts/independent_dom.py --run-dir <owned-task-directory> --config assets/independent-sites/douban.json --output <batch-output>
```

首次验证可用 `--limit 2` 读取小样本，list-candidates.json仍保存当前页全部发现记录；coverage.json分别记录visible_jobs、sampled_jobs和with_jd。小样本完成不能解释为该站采集穷尽。输出目录绑定site-contract.json；不同企业或列表范围需使用独立批次目录。

已核实配置可增加 `--keyword Engineer --max-pages 2 --limit 2`。搜索通过实际可见输入框回车执行，不拼接HTTP接口；要求输入值匹配且岗位ID序列变化。分页点击实际next_selector，校验活动页码递增、ID序列变化、关键词保留及跨页无重复。每页证据保存pages/，不依据“点过按钮”或页码单独判断成功。未配置对应控件的站点不支持该功能。

搜索支持已验证的enter或button动作。`search_required`配置允许首次无岗位的搜索页在公司标识与可见搜索框就绪后接受关键词，仍须看到真实岗位ID才能通过采集校验；这不把初始空壳解释为零岗位。

`click_details`当前仅支持单页卡片列表及实际新开标签的详情导航。来源ID必须来自DOM属性，校验后才用于CSS选择器；新标签出现后先检查登录/验证，再核对实际域名、路径、查询参数ID、标题和完整JD章节。无法确认唯一新标签时暂停，不能据ID拼猜详情URL。只关闭当前任务本次新开的且已成功读取的详情标签；暂停时保留窗口。详情字段按可见标签映射，不能把“社招”等招聘类别误当全职/兼职性质。

任务目录必须有归属核实的browser.json和有用户授权证据的public-access.json。runtime-config.json可配置操作间隔，缺省30–45秒，每5次操作休息90秒。默认只连接已运行的任务Chrome，不启动日常profile。

`browser_tabs.py`可清理调用方明确登记的新标签，并复查是否实际关闭；不遍历关闭用户或其他任务的标签。登录、安全验证、访问拒绝等人工检查点保留对应页面；普通读取异常可在保存证据后关闭。关闭失败明确记录，不能仅凭调用close就声称已清理。目录核查脚本可在finally中使用此工具。

`career_navigation.py`从实际可见链接选择职位入口，保留原始href与来源页面；已有查询或地点条件不重置，不将投递按钮、校招专用路径、下载文件或同页锚点作为列表导航。调用方通过DP原生点击并核对实际URL/新标签，保存入口前后证据。eBay官网到职位搜索页已现场验证此流程；其他站点仍需逐站核实，不自动代表岗位采集通过。

运行前及读取中检查可见登录、验证、拒绝访问提示；出现后Runtime保存PAUSED并退出，保留标签页供用户处理。恢复沿用states.md的用户明确确认、token和重新核实流程。页眉的普通“登录”链接不等于读取需要登录。

Guard对“安全验证”“人机验证”等普通名词要求独立验证标题或明确提示语境；不能因为安全类JD提到“安全验证项目实施”就要求用户处理验证码。验证URL、滑块提示、明确登录与验证码指令仍会暂停。真实验证不可由此自动解除；分类器误报修复必须保存原始文本、修复证据和状态更正记录，不能伪造用户完成验证的确认。

公司标识、稳定岗位ID、标题、职责和要求均需来自实际可见DOM。详情链接由实际列表取得，逐一核对URL、页面标题、公司标识、JD章节；列表下方推荐区不作正文。内容至少稳定6秒才接受；空页、公司不符、跨域详情、重复ID、缺少章节均拒绝，不等同零岗位。采集状态为COLLECTED_DETAIL_DOM，不自动冒充统一READY或匹配推荐。

输出jobs.json、原始DOM证据、coverage.json。可选配置field_patterns只提取正文中的原始匹配片段，同时保存field_evidence；缺失的薪资、学历、经验、地点、日期留空。可选站点默认地点规则必须有明确可见声明，并由岗位正文中的具体地点覆盖。招聘品牌和正文明确注明的雇佣主体分别保存。重跑保留user_字段、变化正文历史及未再次出现的旧岗位。运行目录和输出目录均加写锁。

新增布局需另行验证搜索输入生效、结果身份变化、详情标题/ID一致及分页变化。页面壳加载完成不代表客户端交互已就绪；导航控件缺失应保存READ_ERROR和当前DOM，不猜测接口、不用HTTP请求或页面fetch补采。

2026-11-03：任务标签明确使用 normal 模式，避免 eager 在 DOMContentLoaded 停止后续 SPA 脚本。`link_details` 可配置 `location_all: true`，从卡片中已观察的地点节点保留全部地点，不只取第一个。

新增 Amazon DOM 配置：列表标题链接与 Job ID 来自实际卡片，详情采集 `#job-detail-body .content > .section`，包含 Description、Basic Qualifications、Preferred Qualifications。现场存在空的 `.section.description`，不能以它单独判断 JD 为空。地区参数由任务配置覆盖 `list_url`；初次验证使用页面实际中国列表 URL。自动关键词搜索和分页尚未验证，配置不提供相应控件。只读公开职位，不进入申请流程。

Amazon 配置现场通过当前页10条列表、1份完整JD（5212字符）验证；样本保留北京、上海、深圳全部地点。公司标识采用实际 `.footer .footer-nav`，不是不存在的 footer 标签。首次错误配置因公司证据缺失被拒绝，修正后新批次通过，旧失败证据保留。任务自建标签已核实关闭。
