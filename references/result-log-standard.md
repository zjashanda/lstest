# lstest 结果与工具日志标准

从本版本开始，新任务的结果目录只保留四类产物。旧任务目录不迁移、不删除、不改写。

```text
result/YYYYMMDD_HHMMSS_<task>/
├── serial_logs/
│   └── serial_<port>_<role>.log
├── tool.log
├── results.csv
└── cases.csv
```

## 四类产物职责

- `serial_logs/`：按端口和角色持续保存设备原始串口输出。每行格式为 `[北京时间] COMx role #cursor 原始内容`；`#cursor` 与人读行号一一对应。它是设备事实源，工具日志只引用相对路径和 `#cursor`，不复制大段串口内容，也不生成 `.bin` 或合并日志。
- `tool.log`：唯一完整的文本工具执行账本。它记录脚本动作、配置、命令、回执、旁证、重试、初始化/重启恢复、播放器生命周期、播报-识别关联、异常、健康策略、每轮检查矩阵、累计统计和最终结论。
- `results.csv`：每个完成或中断的逻辑轮次一行，运行中立即刷新。重试仍属于同一轮，不能被误统计为额外样本。
- `cases.csv`：在打开串口、播放音频或发送设备命令之前冻结最终用例顺序。运行中不得重写。

所有文本使用 UTF-8，两个 CSV 使用 UTF-8 BOM。用户停止时保留已存在的四类产物；`tool.log` 记录停止与收尾，`results.csv` 保留完成轮及当前 `ABORTED` 轮。除用户创建的临时 `STOP` 控制文件外，目录不应出现其他默认产物。

## tool.log 固定人读格式

`tool.log` 面向测试人员阅读，主流程必须是一行一个节点，不能输出由模型或项目适配器临时设计的格式。调试阶段由 profile/规则集维护并验证关键节点正则；正式日志不输出正则表达式、规则 ID 或自由命令名，只输出正则提取出的固定 `KEY: VALUE` 事实，随后由框架单独输出下一行 `判定: ...`。每行使用北京时间毫秒时间戳，节点标签只能是 `[CASE]`、`[ACTION]`、`[DEVICE]`、`[ONLINE]`、`[PLAYER]`、`[RESULT]`、`[COMMAND]`、`[ERROR]`、`[SUMMARY]` 或 `[SYSTEM]`。未知内部事件统一使用 `[SYSTEM]`，不得新增标签。

标准流程模板如下：

```text
2026-08-13 14:02:11.210 [CASE 1/5] START 场景=online-interaction 文本=今天的天气 case_id=online-weather-01
2026-08-13 14:02:11.236 [ACTION] 播放: 小T小T
2026-08-13 14:02:11.237 [ACTION] OFFLINE_TONE: ./wake.tone
2026-08-13 14:02:11.238 [ACTION] BROADCAST_ID: 1003
2026-08-13 14:02:14.801 [DEVICE] WAKE: xiao ti xiao ti 播报ID=1003
2026-08-13 14:02:14.802 [RESULT] 判定: 唤醒=PASS 播报ID=1003
2026-08-13 14:02:16.310 [ACTION] 播放: 今天的天气
2026-08-13 14:02:16.311 [ACTION] PLAY_URL: http://example.test/weather.mp3
2026-08-13 14:02:16.312 [ACTION] BROADCAST_ID: 1004
2026-08-13 14:02:19.455 [ONLINE] ONLINE_ASR: 今天的天气 播报ID=1004
2026-08-13 14:02:19.456 [ONLINE] REQUEST_ID: e659... RESPONSE_ID: e659..._0 ASR耗时=653ms
2026-08-13 14:02:19.457 [RESULT] 判定: 在线识别=PASS 配对=PASS 播报ID=1004
2026-08-13 14:02:19.620 [PLAYER] PLAYER: playing 播报ID=1004
2026-08-13 14:02:19.621 [RESULT] 判定: 播放器=PASS 播报ID=1004
2026-08-13 14:02:22.015 [PLAYER] PLAYER: stop 播报ID=1004
2026-08-13 14:02:22.016 [RESULT] 判定: 播放器=PASS 播报ID=1004
2026-08-13 14:02:22.030 [RESULT] 判定: 本轮=PASS 唤醒=PASS 在线识别=PASS 播放器=PASS
```

固定节点含义：

- `[CASE]`：用例开始/结束、序号、总数、场景和测试文本。
- `[ACTION]`：实际播放文本、`OFFLINE_TONE`/`PLAY_URL`/`AUDIO_FILE` 和 `BROADCAST_ID`。
- `[DEVICE]`：正则提取的 `WAKE`、`OFFLINE_ASR`、`OFFLINE_INTENT`、`OFFLINE_TEXT` 等设备原始事实；原始值不改写。
- `[ONLINE]`：正则提取的 `REQUEST_ID`、`RESPONSE_ID`、`ONLINE_ASR` 和 `ASR_LATENCY_MS`。
- `[PLAYER]`：正则提取的 `PLAYER: playing/stop/error` 和播放证据。
- `[RESULT]`：本轮各子项状态和最终判定。
- `[COMMAND]`、`[ERROR]`、`[SUMMARY]`：初始化命令、回执/重试、异常详情和累计统计。

模型和项目适配器只能调用 `TaskArtifacts.emit_fact`、`emit_judgement`、`emit_observation`、`record_anomaly` 等统一接口，不能直接拼接或写入 `tool.log`。渲染器固定时间戳、节点标签、事实 key、字段顺序和判定行；不适用字段省略，正则只保存在 profile/规则集，原始值使用 `raw`/专用原始字段保留。每轮结束的 `[SUMMARY]` 与相邻节点之间保留一行空白。

框架内部仍保留结构化事件，供结果重算和适配器调试使用；这些字段不是测试人员主日志格式，不能由项目适配器直接写入 `tool.log`。示例：

```text
time: 2026-08-13 12:00:00.123
level: INFO
event: SERIAL_COMMAND_VALIDATED
task_id: 20260813_120000_project_stress
epoch: 2
round: 17
case_id: offline-017
phase: initialization_recovery
source: -
device: -
port_role: COM11/csk
action_id: -
broadcast_id: broadcast-000017
correlation_id: requestId-raw-value
status: PASS
reason: serial_ack
attempt: 2
max_attempts: 2
elapsed_ms: 184
rule_id: profile.commands[0]
expected: {"keyword":"ni3 hao3 kong1 tiao2"}
actual: {"keyword":"ni3 hao3 kong1 tiao2"}
raw: {"keyword":"ni3 hao3 kong1 tiao2","intent":"ni3 hao3 kong1 tiao2"}
normalized: {"keyword_text":"你好空调"}
handling: continue
evidence: ["serial_logs/serial_COM11_csk.log#208"]
message: 初始化命令已验证成功。
details: {...}
```

必须记录：任务配置和 profile hash；每次命令发送/回执/旁证/超时/重试；每次播放器探测、请求、进程启动、退出、超时、stdout/stderr 与设备 marker；每个唤醒、离线和在线原始识别结果；所有关联 ID；异常栈和继续/停止处理；每轮 required/optional 检查矩阵、累计异常；最终统计、首个失败、首个严重异常、停止原因和四类文件路径。

离线 `keyword`、`intent`，在线服务原文、请求/响应 ID 和设备 marker 必须原样位于 `raw`；项目转换结果只能写入 `normalized`。日志同时记录 `raw_exact_status` 与 `semantic_status`。例如期望“二十五度”、实际“25度”时，只有 profile 明确允许该原始变体才可使 `semantic_status: PASS`，原始严格状态仍是 `FAIL`。

## 初始化与重启恢复

恢复流程必须在 `tool.log` 逐步记录：初始化 marker、epoch、稳定窗口、安全命令、端口/角色、尝试次数、直接回执、串口回执或发送后旁证、规则、耗时和证据。沉默不得判为成功。失败后按 profile 重试；耗尽时记录 `INITIALIZATION_RECOVERY_FAILED`，默认捕获异常并继续后续可执行轮次。

运行中发现 restart marker 时建立新 epoch 并关闭旧用例窗口。恢复过程中再次发现 restart 时必须记录旧恢复 `CANCELLED`，且旧 epoch 不得继续发送命令；新 epoch 完成初始化 marker 和稳定窗口后才重新恢复。

## results.csv 稳定列

`results.csv` 至少包含：轮次、用例、起止时间、epoch、场景、语料、音频/hash、`broadcast_id`、唤醒与离线识别尝试次数及原始字段、在线原生请求/响应 ID 与原始响应、播放器状态、设备播放器状态、各项耗时、关联有效性、`raw_exact_status`、`semantic_status`、检查矩阵摘要、原始/复核/最终状态、异常码、原因、证据和完整事实 JSON。

在线耗时仅在请求、响应、CaseWindow、epoch 和关联规则均唯一有效时填写；任何一个条件缺失、重复或跨用例时保留原始字段并将耗时留空，不能填零。最终统计必须重新读取 `results.csv`，有效分母排除 `BLOCKED`、`ABORTED` 和 `SKIPPED`。

## cases.csv 稳定列

冻结用例至少包含：`case_order`、`case_id`、`scenario`、`input_text`、`audio_path`、`audio_sha256`、唤醒/离线/在线期望原始值、`accepted_raw_variants`、来源文件/hash、随机种子和 profile 版本/hash。项目适配器完成读取、排序或随机化后立刻调用 `ScenarioRuntime.freeze_cases(...)`，然后才允许设备动作。

## 迁移要求

这是 breaking change。新项目适配 Skill 不得读取或创建 `task.log`、`tool_logs/`、`task_events.jsonl`、`errors.log`、`progress.json` 或 `summary_final.*`。需要机器统计时读取 `results.csv`；需要动作复盘时解析固定字段 `tool.log`；需要设备原始事实时读取对应分端口串口日志。旧历史目录保持原格式，不要求转换。
