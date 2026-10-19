# 候选人资料与问询

candidate.json 是跨站共享的真实履历与申请信息，存工作区而非安装目录。结构见 assets/candidate.template.json。

- cv：personal、education、experience、projects、skills、languages。沿用 CV 日期与角色，实习不算正式工作，协作模型迭代不推定独立训练经验。
- evidence：JSON Pointer → `{source,quote,confirmed}`。转录用户 CV 的事实 confirmed=true，source 写文件路径，quote 写原文；推测不得作为申请事实。
- preferences：搜索方向、薪酬口径、平台、语言、城市及用户偏好的资料视图；目标与已有能力分开。主 Agent 将用于审阅的需求按来源编入独立 review_policy.json，v3 不在代码中另藏偏好门槛。两者冲突先核实并统一。
- answers：每条有 id、value、source、confirmed、scope。scope={site,job_key}，null 表示用户允许的通用范围，默认新答案限当前岗位。false/0 有效，null/空串缺失。
- documents：实际文件路径及出处，上传前核实正确版本。
- authorization：sources/actions/user_instruction 保存用户已允许的平台、动作与原话；可用 job_keys 限定岗位。contact/message/upload/fill/next/submit 分开。模板值和命令标志不能替代会话授权。授权变化不改变资格事实，但每次执行重新核验。

姓名、联系方式、入职日期、工作许可、搬迁、期望薪资等缺失时不猜测、不默认 Yes/No。筛选薪资门槛不等于表单期望薪资。CV 之间矛盾时问该事实。

questions.json 记录字段 id、标签、必填、问题、阻塞动作和 site/job_key；一次问当前必要信息，答复写入 candidate 后重新 prepare。不要提前索取所有潜在个人信息。

answer_ref 示例 `/cv/personal/email` 或 `/answers/0/value`。form.py prepare 仅使用 confirmed=true 的 evidence/answer，检查 scope。自由文本从真实 CV/JD 生成后也先保存有出处的 answer；不虚构经历。
