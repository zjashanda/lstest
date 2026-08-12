# lstest 结果日志标准

每个任务目录至少包含：

- `task_config.json`：输入、profile 路径/hash、声卡、端口和版本；播放目标明确标记为 `system_default_render` 或 `specified_device_key`。
- `task.log`：北京时间毫秒、人读动作、结果、耗时、ID/ASR/意图、原因和证据位置。
- `task_events.jsonl`：完整结构化事件，供重建和机器分析。
- `errors.log`、`progress.json`、`results.csv`、`summary_final.json`、`summary_final.md`。
- `serial_logs/serial_<port>_<role>.log`：分端口连续原始证据；串口采集只保留此 `.log`，不创建 `.bin` 原始字节文件，也不默认创建 `serial_merged.log`。
- `tool_logs/`：播放器、在线计时和其他工具原始输出；其中 `exception_summary.log` 在每轮结束后追加任务级累计异常快照，轮次块之间必须空一行，`recognition_raw.log` 保存每条离线/在线识别的原始字段，`broadcast_recognition_anomalies.jsonl` 保存播报-识别一对一异常，`wake_word_verification.log` 保存多唤醒词逐项确认及无播报/多结果异常，`initialization_recovery.jsonl` 保存每次启动/重启后的初始化 marker、每条命令的发送次数、回执/旁证、证据引用、重试和最终结果，`player_lifecycle.jsonl` 保存每个播放器状态事件。

设备命令恢复事件必须包含初始化等待、重启 marker（如有）、命令、端口/角色、尝试次数、最大次数、直接回执、验证来源（`direct_ack`、`serial_ack` 或 `serial_evidence`）、回执/旁证规则、耗时和串口证据引用。重试耗尽或未找到旁证必须写入 `errors.log`、结构化事件和任务级异常统计，不能把“无输出”写成成功。

关联和耗时必须同时保存规范化字段与项目原生字段。无最终识别、未配对 ID 或无播放器生命周期时，不得伪造耗时或物理播放 PASS。

`player_lifecycle.jsonl` 的每条记录必须包含时间、事件、`lifecycle_status`、来源（主机播放器或设备 marker）、用例和 `broadcast_id`、音频、播放目标/模式、播放器脚本、工具状态和证据引用。主机播放至少记录 `REQUESTED`、`PROCESS_STARTED`、`COMPLETED`、`FAILED`、`TIMEOUT` 或 `BLOCKED`，并记录超时、退出码、耗时和原始工具输出文件；设备侧 marker 记录原始 marker、规范化 `REQUEST`/`PREPARED`/`START`/`PAUSE`/`STOP`/`END`/`ERROR`、端口、原始行和串口证据引用。主机进程成功只能说明播放命令已完成，`device_playback_status` 必须保持 `UNVERIFIED`；只有项目设备侧 `START`/`END` 等 marker 才能作为设备播放旁证。主机播放失败、超时、阻塞或设备 `ERROR` marker 必须写入异常统计并使当前通过轮变为 `FAIL`。

关键人读日志必须能直接看出三类判定：

```text
[WAKEUP] wake_keyword: xiao ti xiao ti | wakeup_status: PASS
[COMMAND] keyword: da kai kong tiao | intent: da kai kong tiao | command_status: PASS
[ONLINE] queryId: abc_0 | asr.text: 今天的天气 | online_status: PASS | latency_ms: 812
```

设备原始 `keyword`、`intent`、ASR 文本、在线 ID 和播放器 marker 不得翻译。离线算法拼音结果必须原样写出，例如 `keyword: ni3 hao3 kong1 tiao2 | intent: ni3 hao3 kong1 tiao2`；规范化中文只能追加在 `normalized`，不得覆盖 `raw_tags` 或工具日志中的原始字段。工具侧只追加 `tool_status`、`tool_reason`、预期值、实际值、耗时和证据引用。终端与 `task.log` 的关键人读行必须一致，原始完整串口内容以分端口 `.log` 文件为准。`progress.json` 和 `summary_final.json` 必须保留从任务开始到结束不清零的 `exception_counts`、`exception_total`、`anomaly_counts`、`anomaly_total` 和 `sticky_counts`；异常状态以非 `PASS`、非 `EXPECTED` 的最终用例状态累计，串口重连或设备重启不得清零。
