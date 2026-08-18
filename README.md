# lstest

面向新设备项目的通用测试验证与压测框架，提供日志发现、profile 正则准入、串口证据、音频合成和播放、时序工具日志、异常隔离、恢复、统计与结果校验。适用于需要创建独立项目测试 Skill 的场景。

## Skill layout

- `agents/openai.yaml`
- `config/profile.example.json`
- `config/requirements.txt`
- `references/acceptance.md`
- `references/profile-driven-log-discovery.md`
- `references/profile-driven-migration.md`
- `references/profile-schema.md`
- `references/project-adaptation-standard.md`
- `references/project-adapter-template.md`
- `references/result-log-standard.md`
- `references/scenario-api.md`
- `scripts/__init__.py`
- `scripts/adapter_guard.py`
- `scripts/audio_synthesis.py`
- `scripts/core.py`
- `scripts/install_laid_linux.sh`
- `scripts/install_laid_windows.ps1`
- `scripts/listenai_play.py`
- `scripts/lstest.py`
- `scripts/observations.py`
- `scripts/playback.py`
- `scripts/profile.py`
- `scripts/runtime.py`
- `scripts/serial_capture.py`
- `scripts/shell.py`
- `scripts/smoke.py`
- `scripts/timeline.py`
- `scripts/timing.py`
- `SKILL.md`
- `tests/__init__.py`
- `tests/test_foundation.py`
- `tests/test_profile_driven.py`

## Install the skill

Copy this folder into:

```text
~/.codex/skills/lstest
```

Then restart Codex.

## Usage and workflow

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
