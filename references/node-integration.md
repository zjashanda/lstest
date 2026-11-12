# 节点侧接入说明（v2 工具日志）

目标：让节点上的测试工具（如 win010 的 `device-x_protocol_v2.py`）不再自行拼 29 字段平铺日志，
改为通过 `lstest` 写 v2 事件，从而自动得到单行 `tool.log`、`events.jsonl`、`summary.*`。

> 当前状态：**框架侧已实现并通过日志级回归；节点侧尚未接入，真机未验证。**
> 在节点侧完成接入、跑完一轮真实任务并回传 `eventlog verify` 结果之前，
> 本项只能标记 `NEEDS_REVIEW`，不得写成"真机已验证"。

## 1. 五步接入

1. 结果目录由框架创建：
   `TaskArtifacts(result_root, task_slug, structured_events=True, state_extension=<扩展>)`。
   - profile 声明了 `state_catalog` 时，用 `DeviceProfile.state_extension()` 取到扩展再传入；
   - 运行中 `tool.live.log` 可 tail；收尾由框架渲染 `tool.log`（验收对象）。
2. 每个动作/判断调 `EventLogWriter.emit(...)`（或 `artifacts.emit(...)`，框架会镜像）。
   最少字段：`round`、`attempt`、`max_attempts`、`status`、`expected`、`actual`、
   `evidence`；有耗时给 `elapsed_ms`；有关联给 `action_id`/`correlation_id`。
3. 设备状态用 `write_device_event(line, code=..., state=..., host_time=..., evidence=[...])` 提交：
   - `code` 必须是 [state-catalog.md](state-catalog.md) 登记的通用码，或项目
     `lstest.state-extension.v1` 扩展里的 `P_` 码；
   - 未登记 → 自动记 `DEVICE_UNPARSED`（含 `raw_code`/`raw_sha256`），不丢行；
   - 项目私有码、别名写进 profile 的 `state_catalog` 或单独扩展文件；
   - marker 可带 `display`（category/stage/primary/fields）；tool.log 只显示统一类别与业务值，
     不显示 raw marker、rule id 或捕获字段名；
   - 也可用 `eventlog build --markers <marker表.json> --state-extension <扩展.json>` 从历史 raw 派生。
4. 收尾必须写 `TASK_END`，并把 gate 放进 `actual.gate`：

   ```python
   artifacts.emit(
       "TASK_END",
       status="PASS" if gate_pass else "FAIL",
       reason="completed",
       actual={"gate": {"structured_artifacts_present": True, "gate_pass": gate_pass,
                        "evaluated_at": now_human()}},
       evidence=["results.csv", "cases.csv", "serial_logs/serial_COM4_csk.log"],
   )
   ```

5. 收尾后执行：

   ```bash
   python -m lstest.scripts.lstest eventlog verify --run-dir <结果目录> [--check-files] \
     [--state-extension <扩展.json>] [--strict-states]
   ```

   `build` 用了扩展时，`verify` 必须给同一份扩展，否则 `summary`/`tool.log` 重算会判漂移。
   `STATE_UNREGISTERED`/`STATE_UNRECOVERED` 默认 WARN，`--strict-states` 提升为 ERROR。

   注意：日志行必须用 `now_log()`（毫秒）；`now_text()` 只用于 manifest/receipt/
   results.csv 等记录字段。混用会让 `verify` 的 `TIME_FORMAT` 报错。

   `ERROR=0` 才算产物合格；`WARN` 需要人工确认（例如源日志状态冲突）。

## 2. 最小可运行示例

```python
from lstest.scripts.core import TaskArtifacts, now_human
from lstest.scripts.eventlog import load_markers

artifacts = TaskArtifacts(result_root, "protocol-v3-smoke10", structured_events=True)
markers, ts_pattern = load_markers("my_project_markers.json")

artifacts.emit("TASK_STARTED", message="smoke10 开始",
               expected={"mode": "smoke", "target_rounds": 10},
               details={"tool": "device-x_protocol_v3.py", "profile_id": "device-x-...",
                        "purpose": "长压前 10 轮门禁"})

for round_no in range(1, 11):
    power_on = do_power_cycle(round_no)           # 你的设备动作
    artifacts.emit("PDU_CYCLE", round=round_no, phase="power_cycle", status="PASS",
                   action_id=f"pdu-reboot-{round_no:04d}",
                   expected={"ip": pdu_ip, "socket": 1, "off_sec": 20},
                   actual={"rc": 0, "elapsed_ms": elapsed, "power_on_at": power_on},
                   evidence=["tool.log"])

    for attempt in range(1, 11):
        result = broadcast_and_wait(attempt)       # 播放 + 等待窗口
        artifacts.emit("BROADCAST", round=round_no, attempt=attempt, max_attempts=10,
                       phase="wake_wait", status="PASS" if result.woke else "NO_WAKE",
                       expected={"wake": True, "response_complete": True},
                       actual={"wake": result.woke, "wake_seen": result.wake_seen},
                       normalized={"play_s": result.play_s, "gap_s": result.gap_s,
                                   "window_s": result.window_s},
                       handling="stop_round" if result.woke else "continue",
                       evidence=[{"path": result.serial_log, "line": result.serial_line}])
        if result.woke:
            break

    artifacts.emit("ROUND_RESULT", round=round_no, case_id=f"wake-{round_no:04d}",
                   phase="round_end", status="PASS",
                   actual={"attempts_total": attempt, "first_success_attempt": attempt if attempt == 1 else None},
                   reason="all_checks_pass")

artifacts.emit("TASK_END", status="PASS", reason="completed",
               actual={"completed_rounds": 10, "target_rounds": 10,
                       "gate": {"structured_artifacts_present": True, "gate_pass": True,
                                "evaluated_at": now_human()}},
               evidence=["results.csv", "cases.csv"])
summary = artifacts.finalize("PASS", "smoke10 完成")
```

## 3. 旧 29 字段 → v2 映射

| 旧字段 | v2 位置 | 说明 |
|---|---|---|
| `time` | `time` | 北京时间毫秒（`now_log()`）；只到秒的输入自动补 `.000`。记录字段仍用 `now_text()` 秒级 |
| `event` | `event` | 大写事件名 |
| `task_id` / `round` / `case_id` / `phase` | 同名字段 | 不变 |
| `status` / `reason` | 同名字段 | 不变 |
| `attempt` / `max_attempts` / `elapsed_ms` | 同名字段 | 必须写进 `ROUND_RESULT.actual.attempts_total` |
| `expected` / `actual` | 同名字段 | 不要塞 raw |
| `raw` | `raw_ref` + `raw/e<seq>_<EVENT>.json` | 框架外置，带 sha256 |
| `normalized` | 同名字段 | 窗口/间隔/偏移 |
| `handling` | `handling` | `continue`/`stop_round`/`abort` |
| `evidence` | `evidence[]` | 相对路径 + 行号，可加 sha256 |
| `message` | `message` | 渲染时换行转义为一行 |
| `details` | `details` | 路径字段自动相对化；设备事件放 `device/state/parameters` |

## 4. 设备 marker 表（可选）

```json
{
  "markers": [
    {
      "source": "wake_raw",
      "code": "PRE_WAKEUP",
      "state": "IDLE→WAKE_DETECTED",
      "host_from": "play_end",
      "message": "设备上报预唤醒",
      "pattern": "Pre Wakeup:.*?\"ncm\":(?P<ncm>\\d+),\"ncmThreshold\":(?P<threshold>\\d+)"
    }
  ]
}
```

- `source`：事件 `raw` 里的字段名；`pattern` 命名捕获组自动成为 `k=v`。
- `host_from`：`record`/`play_start`/`play_end`/`power_on`/`power_on_minus_off`。
- `state`：渲染成 `state=旧→新`；设备自身时钟自动提取为 `device_ts`（若 raw 含）。
- 连续重复状态由渲染器合并为一行（`repeat`/`last_hit`）。

## 5. 真机接入验收（节点侧执行，云端不代跑）

1. 用 `structured_events=True` 跑 10 轮 smoke，结果目录必须同时出现
   `events.jsonl`、`tool.log`、`summary.md/json`、`raw/`（有 raw 时）。
2. `tool.log` 行数 == `events.jsonl` 事件数；每行毫秒开头、无续行。
3. `TASK_END` 行含 `recorded/test_outcome/delivery/final/gate`，且 gate 与 final 不矛盾。
4. `eventlog verify --run-dir <目录> --check-files` 输出 `ERROR=0`。
5. 回传：结果目录、`verify` 输出、`tool.log` 与 `summary.json`（脱敏后）。

未完成上述 5 项前，NOAH-68 的状态只能是 `NEEDS_REVIEW`（框架已验证、节点未接入），
不得写成"真机验证通过"。
