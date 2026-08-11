# lstest 结果日志标准

每个任务目录至少包含：

- `task_config.json`：输入、profile 路径/hash、声卡、端口和版本。
- `task.log`：北京时间毫秒、人读动作、结果、耗时、ID/ASR/意图、原因和证据位置。
- `task_events.jsonl`：完整结构化事件，供重建和机器分析。
- `errors.log`、`progress.json`、`results.csv`、`summary_final.json`、`summary_final.md`。
- `serial_logs/serial_<port>_<role>.log`：分端口连续原始证据；不默认创建 `serial_merged.log`。
- `tool_logs/`：播放器、在线计时和其他工具原始输出。

关联和耗时必须同时保存规范化字段与项目原生字段。无最终识别、未配对 ID 或无播放器生命周期时，不得伪造耗时或物理播放 PASS。

关键人读日志必须能直接看出三类判定：

```text
[WAKEUP] wake_keyword: xiao ti xiao ti | wakeup_status: PASS
[COMMAND] keyword: da kai kong tiao | intent: da kai kong tiao | command_status: PASS
[ONLINE] queryId: abc_0 | asr.text: 今天的天气 | online_status: PASS | latency_ms: 812
```

设备原始 `keyword`、`intent`、ASR 文本、在线 ID 和播放器 marker 不得翻译。工具侧只追加 `tool_status`、`tool_reason`、预期值、实际值、耗时和证据引用。终端与 `task.log` 的关键人读行必须一致，原始完整串口内容以分端口文件为准。
