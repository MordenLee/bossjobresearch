# 招聘页读取与原生操作

`scripts/career_browser.py` 连接已核实归属的任务Chrome。网站访问全部走DrissionPage，不用requests、招聘接口或页面fetch。`assets/career-sites.json` 包含本轮可读取的11个入口，证据级别注明上次实际覆盖，不代表完整JD采集已验证。

在job-search目录运行，依赖 `pip install DrissionPage`：

```powershell
python scripts/career_browser.py sites
python scripts/career_browser.py read --site goldman --run-dir work/goldman-read --browser-receipt <任务browser.json>
python scripts/career_browser.py run --site rippling --run-dir work/rippling --browser-receipt <任务browser.json> --plan assets/career-browser-example.json
```

默认操作间隔30–45秒，每5次休息45秒。正文等待最长60秒，稳定至少6秒。可用`--delay-min/--delay-max/--wait-seconds`调整；等待不保证避免风控。读取失败不自动反复重试。

凭据包含`address=127.0.0.1:<port>`、`profile`、`tab_id`、`run_dir`、`ownership_verified=true`，且keeper标签实际存在。脚本仅连接已有浏览器；端口失效明确报错，由调用方重新打开同一任务profile并核实凭据，不重启日常Chrome。

## API

将scripts加入Python导入路径：

```python
from career_browser import CareerBrowser

with CareerBrowser('goldman', 'work/goldman', '<browser.json>') as page:
    snapshot = page.last
    print(snapshot['text'])
    print(snapshot['controls'])
    fields = [c for c in snapshot['controls'] if c['type'] == 'search']
    if len(fields) == 1:
        snapshot = page.operate('search', fields[0]['id'],
            snapshot_id=snapshot['snapshot_id'], value='Python')
```

`operate`支持input、search（输入后回车）、click、select（原生下拉框已观察的value）。也可先input再click实际搜索按钮。翻页点击实际Next控件，不拼页码URL。自定义下拉组件不冒充原生select，不支持的按钮明确报错。

任务标签使用 normal 加载模式。DrissionPage 的 eager 模式在 DOMContentLoaded 时会停止加载，可能取消招聘 SPA 的后续脚本；空白根容器不能据此判定网站故障或没有岗位。

search 输入后重新读取控件，允许本次输入触发的清空按钮变化；同一输入路径、字段身份、GET 表单目标仍须一致，提交前再次校验。回车可能只选中自动补全项，点击搜索按钮也可能不提交；必须另存真实输入值、结果 URL、岗位序列等证据，不能以 ACTION_OBSERVED 当作搜索成功。

`click_text('See open roles')`精确匹配可见文本；同目标重复链接可去重，不同目标要求具体控件ID。CLI计划也支持`{"op":"search","label":"Search","value":"Python"}`，label需当前快照唯一匹配，不盲猜控件。

操作前和等待间隔后均核对快照ID、URL、控件路径、文字、类型和目标地址。旧控件变化要求重新读取。ACTION_OBSERVED仅表示操作后页面已读，content_changed/url_changed不证明搜索命中、翻页正确或完整JD已读取；仍需适配器核实岗位ID及语义。

同域导航默认允许；跨域用`--allow-host <确切hostname>`或`allowed_hosts=(...)`明确指定已观察到的目标。投递、登录、注册、订阅、上传和下载由其他工作流处理。

## 数据、暂停和恢复

每次读取保存`pages/<快照ID>.json`和`latest.json`，含正文、普通DOM、开放Shadow DOM、链接和普通DOM控件。iframe正文、关闭Shadow DOM不进入；开放Shadow DOM内部控件目前只读。

`operations.jsonl`保存意图和结果证据，`task-events.jsonl`保存状态历史，`tab-cleanup.json`保存实际清理结果。上下文退出只关闭本次登记的新标签。登录、验证码、访问拒绝保留页面并返回退出码2。白屏或Loading不等于零岗位。

用户明确处理并确认后，用现有`runtime.py ack`记录当前token的回应，再给原命令加`--resume`。恢复接回原检查点标签，重新核实页面后才继续。`run --resume`计划仅放未完成步骤，不自动重放原计划。异常操作结果不盲目重试。

本模块不把任意正文自动拆成完整JD或生成READY。完整采集继续使用`independent_dom.py`及Moka/北森/Workday适配器；新增布局仍需逐站验证。
