# 项目适配治理

新项目使用 `skill-creator` 建立独立项目测试 Skill，并引用安装版 `lstest`。该项目只维护：真实日志取证清单、formal profile、fixture、原始记录采集适配器、语料、case 合同和项目 oracle。

禁止行为：复制或修改 `lstest`、向公共代码加入项目正则/marker、直接打开或追加 `tool.log`、注册自由标签/事实 key、提交预格式化日志行、生成额外结果日志、在正式运行使用未验证 profile。

适配器先采集原始行，再以 `RawLogRecord` 提交。框架执行 profile、渲染原始事实、完成关联、统计与轮末判断。项目在 profile 中表达日志业务语义，不得由 Python 字段名或播放器 marker 文本隐式表达。

项目交付前必须完成：fixture、无硬件 preflight、正常回放、异常后持续观察回放、停止白名单路径、结果目录校验和已授权的真机单轮 smoke。项目方没有提供正式 profile、语料、设备和授权时，不得用空 profile 代替真机结论。
