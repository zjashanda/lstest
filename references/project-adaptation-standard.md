# 项目适配治理

新项目使用 `skill-creator` 建立独立项目测试 Skill，并引用安装版 `lstest`。该项目只维护：真实日志取证清单、formal profile、fixture、原始记录采集适配器、语料、case 合同和项目 oracle。

禁止行为：复制或修改 `lstest`、向公共代码加入项目正则/marker、直接打开或追加 `tool.log`、注册自由标签/事实 key、提交预格式化日志行、生成额外结果日志、在正式运行使用未验证 profile。

适配器先采集原始行，再以 `RawLogRecord` 提交。框架执行 profile、渲染原始事实、完成关联、统计与轮末判断。项目在 profile 中表达日志业务语义，不得由 Python 字段名或播放器 marker 文本隐式表达。

项目交付前必须完成：fixture、无硬件 preflight、正常回放、异常后持续观察回放、停止白名单路径、结果目录校验和已授权的真机单轮 smoke。项目方没有提供正式 profile、语料、设备和授权时，不得用空 profile 代替真机结论。

## 设备端口来源

项目 skill 不得在适配器里重新枚举串口或写死 COM 号。端口应来自 `device-io` 产出的文档，并在无硬件 preflight 阶段传入：

```powershell
python -u lstest/scripts/lstest.py preflight --profile <project-profile.json> --device-snapshot <device-role-map.json>
```

- 接受 `ark.device-role-map.v1`（`device-io roles --save`）与 `ark.device-io.v1`（`device-io ports --out`）。
- profile `ports[]` 里的每个 role 都必须被文档解析出来，否则以退出码 `2` 硬失败，stderr 含 role、state、reason；不会回落到 profile 里的端口。
- 不传该参数时保持既有行为，历史项目可继续使用 profile 端口。
