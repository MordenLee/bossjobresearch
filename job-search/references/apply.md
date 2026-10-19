# 授权执行与网页表单

只执行当前 action=apply、state=pending 且重新校验通过的 v3 待办。verify 先解决问题、重审并 build。apply 是资格建议，candidate.authorization 必须来自真实用户会话，sources/actions/user_instruction 明确，可用 job_keys 限范围。已有许可无需重复问。升级许可不扩大动作权限。

所有执行命令的 `<current-inputs>` 表示以下实际参数，不是可原样使用的标志：

```text
--candidate <candidate.json> --policy <policy.json> --source-jobs <batch-jobs.json> --screening <screening.json> --reviews <reviews.json>
```

执行器重验输入与待办内容，拒绝旧输入、旧审阅或擅自改过的 apply。授权与资格事实分开，修改 authorization 不要求重做语义审阅，但每次动作重新核验授权。

## BOSS 沟通入口

approved.json 为待办中 apply 岗位的数组，含相同 job_key/source/company/job_name/job_url/salary、role_fit、reason、approved_for_contact=true。布尔标记不能替代当前会话授权。

```text
python scripts/contact.py <current-inputs> --jobs <approved.json> --todos <todos.json> --ledger <campaign/ledger.jsonl> --profile-dir <owned-profile> --run-dir <site-run> --detail-selectors <boss-selectors.json>
```

已有任务浏览器可加 --attach-port。执行前登录预检，逐岗读取当前完整 JD、核对身份、薪资与审阅正文；变化则暂停重审。点击“立即沟通”前写意图，后读取实际结果。已有“继续沟通”表示历史联系，不重复点击。自定义消息、同 HR 切换和自动回复尚无通用执行器，不能把入口沟通成功说成已发送自定义话术或简历已送达。

## 通用表单

```text
python scripts/form.py inspect --site <source-site> --url <application-url> --profile-dir <owned-profile> --run-dir <form-run>
```

保存私有页面和 frame 源码、form.json、browser.json。仅支持可读取的原生 input/textarea/select/checkbox/radio/file；自定义控件、shadow DOM、不可读 frame 形成问题，不声称任意网站均可投递。官网申请仍保留原岗位 source；在该目录 runtime-config.json 中设置官网 hosts 和可见会话标识。无账号的公开表单需要单独定义可核查访问模式，当前登录门禁不会自动豁免。

主 Agent 核实当前页面对应公司与岗位，再写 plan.json：

```json
{"schema_version":2,"site":"source-site","job_key":"stable-key","url":"当前form.json的url","actions":[{"field_id":"main:/实际字段路径","answer_ref":"/cv/personal/email"}],"next_action":{"button_id":"main:/实际按钮路径","kind":"submit"}}
```

```text
python scripts/form.py prepare <current-inputs> --snapshot <form.json> --plan <plan.json> --todos <todos.json> --out-dir <form-run>
python scripts/form.py fill <current-inputs> --prepared <prepared.json> --todos <todos.json> --attach-port <owned-port> --run-dir <form-run> --authorized --ledger <campaign/ledger.jsonl>
python scripts/form.py advance <current-inputs> --prepared <prepared.json> --todos <todos.json> --attach-port <owned-port> --run-dir <form-run> --authorized --ledger <campaign/ledger.jsonl>
```

prepare 校验来源、范围、选项、必填与文件，缺失写 questions.json；用户回答后更新 candidate 并重新 prepare。fill 需 fill 授权，文件另需 upload；只填写并读回，不提交。advance 按 next/submit 分别授权，查 ledger、写意图、单次点击再保存结果。字段或结构改变即重新 inspect/prepare；不能只用旧计划强行点击。

submit 默认 uncertain，明确成功提示或申请记录经主 Agent 核实后：

```text
python scripts/workflow.py record --todos <todos.json> --todo-id <source:job_key> --ledger <campaign/ledger.jsonl> --state submitted --evidence <真实成功证据位置>
```

未知点击、持久化提交意图和不确定上传不自动重复。实际 ledger 损坏停止保留，重建不清空历史。上传的读回只证明原生文件字段选中，不证明雇主接收。浏览器保持可见；登录、验证码、额度和升级恢复均按 states.md。
