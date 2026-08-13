# lstest 场景接口规范

场景实现以下接口：

```python
preflight(runtime)
next_case(state)
run_case(case, runtime)
judge(observed)
cleanup(runtime)
```

`next_case` 必须按需生成，不能一次性生成百万条随机用例。场景声明预期语料、音频、业务域/意图/动作、前置状态和安全等级；公共 runtime 负责播放、串口关联、耗时、停止、证据和落盘。

## 公共运行时边界

适配层可以保留自己的串口事件读取器和 marker/oracle，但播放、观察窗口、用例证据和结果写入应统一委托给 `lstest.scripts.runtime.ScenarioRuntime`：

```python
runtime.probe()
runtime.freeze_cases(
    final_cases,
    random_seed=random_seed,
    profile_version=profile.schema_version,
    profile_sha256=profile.sha256,
)
playback = runtime.play(
    audio_path,
    case_id=case_id,
    expected_recognition={"keyword": "ni3 hao3 kong1 tiao2", "intent": "ni3 hao3 kong1 tiao2"},
    accepted_raw_variants={"keyword": ["你好空调"]},
)
runtime.record_online_request(
    raw_request,
    case_id=case_id,
    request_id=raw_request["requestId"],
    evidence_refs=("serial_logs/serial_COM12_upper.log#120",),
)
runtime.record_player_marker(
    marker,
    case_id=case_id,
    broadcast_id=playback["broadcast_id"],
    port=event.port,
    raw_line=event.line,
    evidence_refs=("serial_logs/serial_COM11_player.log#120",),
)
runtime.wait_observation_window(
    timeout_s,
    fetch=read_events,
    parse=parse_project_markers,
    stop_reason=supervisor.reason,
    predicate=is_complete,
)
runtime.record_case(row)
```

`freeze_cases` 必须在打开串口、播放音频或发送业务命令前调用一次；它写入冻结的 `cases.csv`，之后不允许改写。`runtime.play` 会创建唯一 `broadcast_id`，并将主机播放器请求、进程启动、结束、失败、超时或阻塞写入唯一 `tool.log`。主机进程正常返回不等于设备已经播放；适配层必须把项目设备侧播放器 marker 调用 `record_player_marker` 原样记录，才可佐证设备 `START`/`END`。设备 `ERROR` marker、主机失败、超时或阻塞均记录任务异常并使当前通过轮失败。

项目启动持续串口采集后必须调用 `device_runtime.recover_initialization(serial_manager)`，它会等待设备初始化完成、稳定窗口、恢复并验证 profile 的 `safe_init` 命令，再自动开始重启监控。恢复中若出现第二次重启，旧 epoch 会取消而新 epoch 重新等待初始化。每轮/每个用例开始前调用 `device_runtime.check_ready()`；仅前置条件不可执行、profile 明确安全停止或用户停止时才停止新动作，单次命令恢复失败默认记录异常后继续。

适配层对每一条最终识别结果必须调用 `record_recognition`，先落原始值再关联播报。离线算法输出如 `keyword: ni3 hao3 kong1 tiao2`、`intent: ni3 hao3 kong1 tiao2` 必须原样传入 `raw_values`；转换后的中文或业务值仅放在 `normalized`。播放时使用 `expected_recognition` 声明当前播报语料期望的原始识别字段；允许值必须显式放在 `accepted_raw_variants`。在线场景必须在发送/注入请求时调用 `record_online_request`，然后再记录最终响应；只有同 epoch、同 CaseWindow、唯一的原生请求 ID/响应 ID 才会写入在线耗时，否则保留原始响应、标记关联无效并让耗时为空。运行时会分别写入严格原始匹配和允许变体匹配状态，绝不覆盖原始值。数量正确但字段值不一致记 `RECOGNITION_RESULT_MISMATCH`。未关联播报的结果记 `UNEXPECTED_RECOGNITION`，单次播报的第二条及之后结果记 `MULTIPLE_RECOGNITIONS_FOR_PLAYBACK`，关闭窗口或跨 epoch 后的结果记 `LATE_RESULT_AFTER_CASE_CLOSE`，三者均使该轮原本的 `PASS`/`EXPECTED` 自动升级为 `FAIL`。

有多个唤醒词时，适配层从 profile 的有序 `wake_words` 需求表逐项选择当前项，在播报后调用 `record_wakeup(wake_word, raw_values, broadcast_id=playback["broadcast_id"])`。它会记录当前 `wake_word_id`、播报文本、预期原始字段和实际原始字段；错配记 `WAKE_WORD_MISMATCH`、错序记 `WAKE_WORD_ORDER_VIOLATION`。唤醒结果没有可关联播报时同时记 `WAKE_WORD_WITHOUT_PLAYBACK`，同一播报的第二条及之后唤醒结果同时记 `WAKE_WORD_MULTIPLE_RESULTS_FOR_PLAYBACK`；全部进入异常统计并使当前通过轮失败。

`fetch` 返回 `(events, complete)`，`parse` 只做项目 marker 解析；公共层不会翻译 `keyword`、`intent`、ASR 或在线原生 ID，也不会创建 `serial_merged.log`、专项 evidence 文件或 JSONL。适配器必须把原始串口证据作为 `serial_logs/...#cursor` 传入，逐事件详情写 `tool.log`，每轮台账写 `results.csv`。项目 oracle 仍负责把事实转换成 `PASS/WARN/FAIL/BLOCKED`。
