---
name: lstest
description: 面向新设备项目的通用测试验证与压测框架，提供日志发现、profile 正则准入、串口证据、音频合成和播放、时序工具日志、异常隔离、恢复、统计与结果校验。适用于需要创建独立项目测试 Skill 的场景。
---

# lstest

`lstest` 是跨项目的测试验证和压测标准框架，不是某个设备的协议解析器。项目 Skill 不得修改、复制或分叉本目录；必须只提供独立的 profile、原始记录适配器、fixture、语料与场景合同。

## 固定接入顺序

1. 采集该项目真实正常、缺失、重复、异常、初始化、命令回执/旁证、重启、多端口和播放器日志。
2. 在项目 profile 中为每个关键事实填写来源范围、正则、命名捕获、唯一展示捕获、阶段、关联、镜像、空值、fixture 和恢复规则。
3. 先运行 profile fixture 与无硬件 `preflight`；未满足正式合同必须 `BLOCKED_PROFILE_CONTRACT`，不得猜测日志含义。
4. 项目适配器持续提交 `RawLogRecord` 给 `ScenarioRuntime.submit_raw_record(...)`；不得自行解析后写 `tool.log`。
5. 在真机授权后进行短回放/冒烟，再启动正式压测。

## 框架边界

- 框架只定义测试流程类别、时间戳、标签、结果产物、关联/计数和轮末判定；不内置项目 marker、正则、捕获字段、端口、命令、业务值或默认展示字段。
- profile 的 `presentation_capture` 是唯一展示值。`tool.log` 只显示 `KEY: 原始捕获值`，不显示正则、规则 ID、捕获字段名、内部关联 ID 或绝对路径。
- 播放器原始 marker 必须原样显示。`state_class` 仅供去重、窗口和耗时计算，绝不能改写显示文本。
- 每个活动 case 中到达的事实都立即按顺序显示，包括播报前与异常后；每轮只有一次 `[RESULT]`。必需而缺失的事实在轮末以空值行补齐。
- 结果目录仅保留 `serial_logs/*.log`、`tool.log`、`results.csv`、`cases.csv`。禁止 `.bin`、`task.log`、`tool_logs`、JSONL 和临时结果文件。

## 异常与停止

每个动作、回调和 case 都必须捕获异常，记录证据、完成当前轮收尾并继续下一可执行轮。初始化/日志等级重试耗尽、识别失败、超时、重复结果、播放器失败、设备业务错误和单条解析异常都不是任务自动退出理由。

只有以下白名单可结束任务：用户停止、标准证据无法可靠写入、正式 profile 预检阻塞、必需基础设施恢复耗尽后不可执行、或 profile 声明且 fixture 验证的设备/数据/人员安全停止条件。

## 进程与窗口约束（强制）

- 本 skill 及其脚本、生成模板中，任何启动终端的调用（`cmd`、`powershell`、`pwsh`、`wt`、`conhost`、`Start-Process`、`subprocess`、`os.system`、`Popen`、脚本包装器等）都必须在后台执行，禁止在桌面出现任何控制台窗口。
- 统一使用 .NET `ProcessStartInfo`：`UseShellExecute = $false`、`CreateNoWindow = $true`、`WindowStyle = Hidden`；Python 子进程优先使用 `pythonw.exe`。
- 子进程 stdout/stderr 必须重定向到日志文件，不得依赖可见控制台输出。
- 禁止用 `Win32_Process.Create` 创建 `cmd.exe` 或 `powershell.exe`，该方式会分配可见控制台窗口。
- 不能只写“后台运行”：后台进程同样可能拥有可见控制台窗口，必须显式抑制窗口。
- 无法保证不弹窗时，必须停止执行并说明阻塞原因，不得先运行再观察。

## 音频与设备

- `tts` 与 `tts-manifest` 使用 IndexTTS 服务合成并校验 WAV 的时长、响度、静音/削波和 SHA-256；不再使用 edge-tts。
- 音色取本地音色目录（默认 `~/.lstest/voices/<姓名>.wav`，可用 `LSTEST_VOICE_DIR` 覆盖）：默认音色 `付小春`，女性音色 `小宁`；也可直接传参考音频路径。
- 服务地址与 token 依次取 `LSTEST_TTS_URL`/`LSTEST_TTS_TOKEN`、`LSTEST_TTS_CONFIG`、`~/.lstest/tts.json`，或已安装 `index-tts` / `lstts` skill 的 `references/index_tts.json`。
- 未指定声卡时使用系统默认 Render；指定稳定设备 key 时严格使用该设备，失败不回退。
- FFmpeg 只用于音频归一化，Windows 实际播放使用 `pygame`/DirectSound，Linux 使用 `aplay`。

## 常用命令

```powershell
python -u lstest/scripts/lstest.py --help
python -u lstest/scripts/lstest.py preflight --profile <project-profile.json>
python -u lstest/scripts/lstest.py tts --text-file utterance.txt --voice 付小春 --output audio/sample.wav
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
