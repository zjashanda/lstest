# Profile 驱动迁移清单

旧接口和旧格式属于 breaking change。未迁移的项目只能调试，不得进入正式压测。

| 旧方式 | 替换方式 |
| --- | --- |
| 在框架或适配器中写项目 marker/正则 | 项目 formal profile 的 `event_rules` |
| 根据字段名自动选识别展示值 | `presentation_capture` 唯一声明 |
| 将播放器 marker 转成固定状态文本 | profile `state_class` 仅内部使用，原始 marker 原样显示 |
| `record_recognition`、`record_wakeup`、`record_player_marker` | `ScenarioRuntime.submit_raw_record(RawLogRecord(...))` |
| 直接写 `tool.log`、JSONL 或专项工具日志 | 框架渲染的唯一 `tool.log` 与 `results.csv` 事实列 |
| 固定某项目的结果 CSV 列 | 通用原始/逻辑事实计数、关联、异常、原因和完整事实 JSON |
| 普通异常直接停止任务 | `StopPolicy` 白名单和 `TaskSupervisor` 逐轮隔离 |

迁移完成条件：profile fixture 通过、项目适配器静态守卫通过、工具日志回放通过、无硬件 preflight 通过。没有 formal profile 的项目必须以 `BLOCKED_PROFILE_CONTRACT` 收尾。
