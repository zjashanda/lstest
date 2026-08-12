# lstest 配置档案规范

配置档案（profile）是项目事实的唯一入口，不把项目命令、端口号或设备标记写进公共 Python。

最小字段：`schema_version`、`profile_id`、`ports`、`initialization_patterns`、`restart_patterns`、`commands`、`correlation`、`player_markers`、`observations`。项目有多个唤醒词时，必须额外提供有序 `wake_words` 需求表。

`ports` 只声明角色和能力，不声明固定 COM 号。运行时连接参数由命令行或调用者配置提供。

播放声卡为可选运行时参数 `playback_device_key`：未配置时使用电脑当前默认 Render 声卡；配置稳定 key 时严格绑定该声卡，key 不存在、重复或不可用时立即失败，绝不改用默认声卡。

`commands` 是项目命令白名单。初始化恢复命令设置 `safe_init: true`（默认值）；非初始化命令必须显式设置 `safe_init: false`。每条初始化恢复命令必须配置至少一种可审计验证规则：

- `success_patterns`：命令直接返回内容或发送后串口新出现的成功回执必须匹配。直接返回了非空内容但不匹配时，该次尝试失败，不能用旁证覆盖错误回执。
- `evidence_patterns`：仅当没有任何直接/串口回执时允许使用的初始化后旁证，例如设备日志等级查询结果、模块就绪状态或业务日志。旁证必须在该次命令发送后的新串口事件中匹配。
- `retries`：失败后的重试次数，默认 `1`，即最多两次发送。
- `timeout_s`、`evidence_timeout_s`、`retry_delay_s`：分别控制回执等待、旁证等待和两次尝试之间的间隔，均为正秒数。

恢复前必须先等 `initialization_patterns` 出现。`recovery.initialization_timeout_s` 控制这段等待，`recovery.restart_poll_interval_s` 控制运行中重启 marker 的检查频率。`recovery.stop_on_failure` 默认 `true`：重试耗尽后记录 `INITIALIZATION_RECOVERY_FAILED` 并请求安全停止，避免继续产生无法审计的无日志结果；仅明确确认可在初始化缺失时继续的项目才能设为 `false`。命令未列入白名单、换行、命令串联和错误端口必须拒绝。

`correlation` 通过 `fields` 和规则 ID 描述项目原生关联字段；可以是 `queryId`、`requestId`、`traceId`、`sessionId`、消息序号或复合键。

`observations` 按项目配置 `wakeup`、`offline_asr`、`online` 等标签提取规则。公共库只使用规则，不固定字段名称和正则内容。

`wake_words` 的每一项至少包含 `wake_word_id`、`spoken_text` 和 `expected_raw`。测试按清单顺序逐项选择当前唤醒词，播报 `spoken_text` 后将设备原始字段逐字与 `expected_raw` 比较；不能以任意唤醒 marker 命中代替当前项确认。例如：

```json
{
  "wake_word_id": "default-xiao-ti",
  "spoken_text": "小T小T",
  "expected_raw": {"keyword": "xiao ti xiao ti"}
}
```

关键事件必须保留两层数据：

- `raw_tags`：设备实际标签名和值，以及端口、角色、游标、时间戳和证据路径。离线 `keyword`、`intent` 等算法拼音结果必须按设备原文保存，例如 `ni3 hao3 kong1 tiao2`。
- `normalized` 与工具判断：统一字段、预期值、`wakeup_status`、`command_status`、`online_status`、原因和耗时。

统一字段不能替换原始值；例如项目使用 `requestId` 时，日志仍输出 `requestId: <raw>`，同时保存统一的 `correlation_id`。
