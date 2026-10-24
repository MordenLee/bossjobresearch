# Moka / 北森适配设计（初版）

`scripts/ats.py` 是可离线测试的采集核心，不是已完成现场验证的官网爬虫。不注册到 collect.py 的浏览器站点列表；现有 BOSS/猎聘/智联/51job 路径不变。协议映射参考公开源码并独立实现，当前公司接口、权限和字段仍需现场确认。

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
