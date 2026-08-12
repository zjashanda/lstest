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
playback = runtime.play(
    audio_path,
    case_id=case_id,
    expected_recognition={"keyword": "ni3 hao3 kong1 tiao2", "intent": "ni3 hao3 kong1 tiao2"},
)
runtime.wait_observation_window(
    timeout_s,
    fetch=read_events,
    parse=parse_project_markers,
    stop_reason=supervisor.reason,
    predicate=is_complete,
)
evidence_path, evidence_sha256 = runtime.write_evidence(
    events, unit=unit, case_id=case_id, phase=phase, attempt=attempt,
)
timing_path = runtime.append_jsonl("online_timing.jsonl", timing_payload)
runtime.record_case(row)
```

适配层对每一条最终识别结果必须调用 `record_recognition`，先落原始值再关联播报。离线算法输出如 `keyword: ni3 hao3 kong1 tiao2`、`intent: ni3 hao3 kong1 tiao2` 必须原样传入 `raw_values`；转换后的中文或业务值仅放在 `normalized`。播放时使用 `expected_recognition` 声明当前播报语料期望的原始识别字段，数量正确但字段值不一致记 `RECOGNITION_RESULT_MISMATCH`。未关联播报的结果记 `UNEXPECTED_RECOGNITION`，单次播报的第二条及之后结果记 `MULTIPLE_RECOGNITIONS_FOR_PLAYBACK`，三者均使该轮原本的 `PASS`/`EXPECTED` 自动升级为 `FAIL`。

有多个唤醒词时，适配层从 profile 的有序 `wake_words` 需求表逐项选择当前项，在播报后调用 `record_wakeup(wake_word, raw_values, broadcast_id=playback["broadcast_id"])`。它会记录当前 `wake_word_id`、播报文本、预期原始字段和实际原始字段；错配记 `WAKE_WORD_MISMATCH`、错序记 `WAKE_WORD_ORDER_VIOLATION`。唤醒结果没有可关联播报时同时记 `WAKE_WORD_WITHOUT_PLAYBACK`，同一播报的第二条及之后唤醒结果同时记 `WAKE_WORD_MULTIPLE_RESULTS_FOR_PLAYBACK`；全部进入异常统计并使当前通过轮失败。

`fetch` 返回 `(events, complete)`，`parse` 只做项目 marker 解析；公共层不会翻译 `keyword`、`intent`、ASR 或在线原生 ID，也不会创建 `serial_merged.log`。项目 oracle 仍负责把事实转换成 `PASS/WARN/FAIL/BLOCKED`。
