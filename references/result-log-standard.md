# 结果和工具日志标准

## 版本

| 版本 | 产物 | 状态 | 规范 |
|---|---|---|---|
| v1 | `serial_logs/*.log`、`tool.log`、`results.csv`、`cases.csv` | 兼容读取，旧任务仍有效 | 本文 |
| v2 | 在 v1 证据基础上增加 `events.jsonl`、`raw/`、`tool.log`（完整动作审计）、`summary.md/json` | 旧任务可读 | [structured-event-log-v2.md](structured-event-log-v2.md) |
| v3 | 在 v2 事件基础上按最终结果目录布局落盘：`events/`、`logs/`、`reports/`、`artifacts/`、`evidence/`、`milestones/`，根目录只留 `README.md`/`manifest.json`/`summary.md`/`receipt.json` | **新任务默认**；旧 v1/v2/`cloud_run` 可读 | [result-layout-v3.md](result-layout-v3.md) |

`ToolLogValidator.validate_run` 与 `eventlog verify` 通过 `scripts/result_layout.py`
自动识别布局：v3（`events/` 等目录）、v2（根 `events.jsonl`）、v1（四产物）、
早期过渡布局（`machine/`/`execution_logs/`）均可读。旧根 JSON、`serial_logs/`、
`cloud_run/` 不需要先迁移；新任务只写 v3。布局只改路径，不改 `tool.log` 行格式与判定。

v2 补充的硬规则（v1 未覆盖）：

- `tool.log` 是**完整动作审计**（TOOL 动作 + DEVICE 状态），每个事件恰好一行、毫秒时间戳；
  不允许续行、空行、段落式汇总。`run.log` 这个名称与"紧凑摘要"口径作废。
- 所有日志（tool、串口包装事件、audio/PDU/runner、判断日志）一律毫秒；设备 raw 另有 host 接收时间。
- `gate_pass=false` 时最终状态禁止 `PASS`；`test_outcome` 与 `delivery_status` 分开表达，
  并在 `TASK_END` 那一行同时给出 `recorded`/`test_outcome`/`delivery`/`final`/`gate`。
- 必须同时给出 `round_success_rate` 与 `first_attempt_success_rate`，禁止只报前者。
- 设备状态码必须登记在 [state-catalog.md](state-catalog.md) 或项目扩展；未登记码落 `DEVICE_UNPARSED` 并保留 `raw_code`，不得静默渲染。
- `tool.log`/`summary.md`/`summary.json` 只能由 `events.jsonl` 确定性生成，手改会被 `verify` 判漂移。

## v1：四产物（兼容层）

每次 v1 任务只生成：

```text
result/<timestamp>_<task>/
  serial_logs/*.log
  tool.log
  results.csv
  cases.csv
```

`serial_logs` 是连续原始证据；`tool.log` 是唯一人读执行账本；`results.csv` 每 case 一行；`cases.csv` 在设备动作前冻结。v2 用 `events.jsonl` 作为机器真源、`tool.log` 作为**完整动作审计**（TOOL + DEVICE 单行毫秒时序），`summary.md/json` 只放结论；`serial_logs`、`results.csv`、`cases.csv` 三者职责不变。v3 把这些产物移到 `events/`、`logs/serial/`、`reports/` 等目录，职责不变、读取走 resolver。

所有 `tool.log` 行使用北京时间 `YYYY-MM-DD HH:mm:ss.SSS`，标签只能是 `[SYSTEM]`、`[CASE]`、`[ACTION]`、`[COMMAND]`、`[DEVICE]`、`[ONLINE]`、`[PLAYER]`、`[ERROR]`、`[RESULT]`、`[SUMMARY]`。冒号右侧必须是 profile 正则捕获的原始值。

```text
2026-08-17 10:00:00.000 [CASE 1/2] START 场景=<scenario> 文本=<text> case_id=<case>
2026-08-17 10:00:00.010 [ACTION] 播放: <text>，文件=<basename>
2026-08-17 10:00:00.120 [DEVICE] WAKE: <profile-capture>
2026-08-17 10:00:00.300 [PLAYER] PLAYER: <profile-capture>
2026-08-17 10:00:01.000 [ONLINE] ONLINE_ASR:
2026-08-17 10:00:01.010 [ERROR] 异常: <profile-reason>，处理=本轮收尾后继续，证据=<port>#<cursor>
2026-08-17 10:00:01.100 [RESULT] 本轮=FAIL，原因=<primary-reason>，逻辑事实=<key>=<count>
2026-08-17 10:00:01.101 [SUMMARY] 本轮异常=<count>，累计异常=<count>
```

不得输出正则、rule id、捕获字段名、内部播报/关联 ID、绝对路径或项目自行格式化文本。播放器 raw marker 不得转换成固定单词。异常后仍持续输出随后到达的事实；仅轮末输出一次本轮 `[RESULT]`。

`results.csv` 使用通用字段：原始/逻辑事实计数、关联结论、异常码、主要原因、profile 版本和完整事实 JSON；不得固定某项目的识别字段列。`ToolLogValidator` 必须验证格式、唯一 RESULT、事实顺序、空值、原样展示、持续观察、计数一致性和四类产物边界。
