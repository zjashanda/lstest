---
name: lstest
description: 面向新设备项目的通用测试验证与压测框架，提供日志发现、profile 正则准入、串口证据、音频合成和播放、时序工具日志、异常隔离、恢复、统计与结果校验。适用于需要创建独立项目测试 Skill 的场景。
---

# lstest

`lstest` 是跨项目的测试验证和压测标准框架，不是某个设备的协议解析器。项目 Skill 不得修改、复制或分叉本目录；必须只提供独立的 profile、原始记录适配器、fixture、语料与场景合同。

## 固定接入顺序

1. 采集该项目真实正常、缺失、重复、异常、初始化、命令回执/旁证、重启、多端口和播放器日志。
2. 在项目 profile 中为每个关键事实填写来源范围、正则、命名捕获、主展示捕获、可选多字段展示、阶段、关联、镜像、空值、fixture 和恢复规则。
3. 先运行 profile fixture 与无硬件 `preflight`；未满足正式合同必须 `BLOCKED_PROFILE_CONTRACT`，不得猜测日志含义。
4. 项目适配器持续提交 `RawLogRecord` 给 `ScenarioRuntime.submit_raw_record(...)`；不得自行解析后写 `tool.log`。
5. 在真机授权后进行短回放/冒烟，再启动正式压测。

## 框架边界

- 框架只定义测试流程类别、时间戳、标签、结果产物、关联/计数和轮末判定；不内置项目 marker、正则、捕获字段、端口、命令、业务值或默认展示字段。
- 未配置 `display_captures` 时，`presentation_capture` 保持既有的唯一展示行为。需要展示实际字段名时，profile 可按顺序声明 `display_captures`；例如 `keyword`、`intent`、`queryId`。`tool.log` 只显示声明的 `tag_name: 原始捕获值`，不显示正则、规则 ID、内部关联 ID 或绝对路径。
- 播放器原始 marker 必须原样显示。`state_class` 仅供去重、窗口和耗时计算，绝不能改写显示文本。
- 每个活动 case 中到达的事实都立即按顺序显示，包括播报前与异常后；每轮只有一次 `[RESULT]`。必需而缺失的事实在轮末以空值行补齐。
- 结果目录仅保留 `serial_logs/*.log`、`tool.log`、`results.csv`、`cases.csv`。禁止 `.bin`、`task.log`、`tool_logs`、JSONL 和临时结果文件。
- 固定语料在首个设备动作前调用 `freeze_cases()`；随机长压先调用 `begin_lazy_cases()` 冻结场景、种子和 profile 身份，再于每轮动作前调用 `declare_case()`。未声明的 case 不得播放或发送设备命令。

## 异常与停止

每个动作、回调和 case 都必须捕获异常，记录证据、完成当前轮收尾并继续下一可执行轮。初始化/日志等级重试耗尽、识别失败、超时、重复结果、播放器失败、设备业务错误和单条解析异常都不是任务自动退出理由。

只有以下白名单可结束任务：用户停止、标准证据无法可靠写入、正式 profile 预检阻塞、必需基础设施恢复耗尽后不可执行、或 profile 声明且 fixture 验证的设备/数据/人员安全停止条件。

## 音频与设备

- `tts` 与 `tts-manifest` 使用 `edge-tts` 合成并校验 MP3、24 kHz、单声道、时长、响度和 SHA-256。
- 未指定声卡时使用系统默认 Render；指定稳定设备 key 时严格使用该设备，失败不回退。
- FFmpeg 只用于音频归一化，Windows 实际播放使用 `pygame`/DirectSound，Linux 使用 `aplay`。

## 常用命令

```powershell
python -u lstest/scripts/lstest.py --help
python -u lstest/scripts/lstest.py preflight --profile <project-profile.json>
python -u lstest/scripts/lstest.py tts --text-file utterance.txt --output audio/sample.mp3
python -u lstest/scripts/lstest.py audio scan --direction Render --json
```

详细合同和可复制的项目适配模板见：

- [references/profile-driven-log-discovery.md](references/profile-driven-log-discovery.md)
- [references/profile-driven-migration.md](references/profile-driven-migration.md)
- [references/profile-schema.md](references/profile-schema.md)
- [references/scenario-api.md](references/scenario-api.md)
- [references/result-log-standard.md](references/result-log-standard.md)
- [references/project-adaptation-standard.md](references/project-adaptation-standard.md)
- [references/acceptance.md](references/acceptance.md)

## 压测前环境清理与串口恢复

- `scripts/environment_cleanup.py`：清空音频锁目录、清理残留播放进程、删除过期临时音频、统计未使用/缺失的声卡端点。
- `scripts/serial_recovery.py`：通用串口断开重连工具，多次关闭/重开串口并用 probe 检查提示符恢复。
- 项目适配层应在压测启动前调用清理，并在控制口/CSK 口无响应时调用重连；只有全部重连失败才标记基础设施异常。

## 长时间压测统计与子进程清理

- 不要用 `list(csv.DictReader(...))` 全量加载 `results.csv`；长时间压测时单行可能很大，必须使用 `scripts/result_stats.py` 的 `ResultSummary` 增量读取，只解析上次位置之后的新增行。
- 多设备实时汇总应复用 `ResultSummary` 缓存；最终统计也复用缓存，避免反复全量扫描 `results.csv`。
- 启动子进程后调用 `environment_cleanup.register_process_cleanup(...)`，异常退出时自动终止子进程树；单个 PID 可使用 `environment_cleanup.kill_process_tree(pid)`，避免残留压测和播放进程。

## 单轮交互结果与验收模式

单轮交互结果统一为：

- `hit`：唤醒成功 + 识别正确
- `misheard`：唤醒成功 + 误识别
- `empty`：唤醒成功 + 无识别结果
- `asleep`：未唤醒

`interaction_acceptance` 提供可选验收模式（单轮交互判定）：

- `wake_only`：只看唤醒成功
- `wake_playback`：唤醒 + 至少 1 次完整唤醒播报
- `strict`：只接受 `hit`
- `strict_playback`：只接受 `hit`，并要求唤醒播报、识别响应和识别播报
- `any_asr`：接受 `hit`、`misheard`
- `any_asr_playback`：接受 `hit`、`misheard`，并要求唤醒播报、识别响应和识别播报
- `asr_optional`：单轮接受 `hit`、`misheard`、`empty`，整轮任务至少要有一次 `hit`/`misheard`
- `asr_optional_playback`：同 `asr_optional`，有识别结果的轮次还要求识别响应和识别播报

播报维度：

- 唤醒播报默认至少 1 次，并要求完整状态切换
- 识别响应至少 1 次（`offline_tts_callback` / `soundplayer`）
- 识别播报允许 1 次或多次，每次要求 PLAYING → COMPLETE 或 start → stop
- `asr_optional` 的“整轮至少一次非空”是任务级约束，不属于单轮结果

多结果异常：

- 单轮内所有唤醒结果记录到 `wake_results`，所有识别结果记录到 `asr_results`，并统计 `wake_result_count` / `asr_result_count`
- 一次播放出现多个唤醒结果或多个识别结果时，记录 `MULTI_WAKE` / `MULTI_ASR` / `MULTI_RESULT`
- `strict` / `strict_playback` 遇到多结果直接 FAIL；其他模式只记录异常，不影响本轮通过
