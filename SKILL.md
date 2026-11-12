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
- 结果目录：v1 仅保留 `serial_logs/*.log`、`tool.log`、`results.csv`、`cases.csv`；v2 增加 `events.jsonl`、`raw/`、`summary.md`、`summary.json`，`tool.log` 升级为完整动作审计（TOOL 动作 + DEVICE 状态，每事件一行、毫秒）。运行中实时账本为 `tool.live.log`。禁止 `.bin`、`task.log`、`tool_logs` 和临时结果文件。
- v2 的结论只能由 `events.jsonl` 计算：`tool.log`/`summary.md`/`summary.json` 均为确定性渲染物，禁止手改；`gate_pass=false` 时最终状态禁止 PASS。
- 项目适配器不得自行拼 JSONL 或写 `tool.log`/`summary.*`；必须通过 `EventLogWriter` 或 `TaskArtifacts(structured_events=True)`；设备行用 `write_device_event()` 提交（未识别行自动记 `DEVICE_UNPARSED`）。

## 新 canonical 分层（profile / case / adapter）

lstest 是测试与语音的**唯一 canonical**。项目差异一律落在数据层，禁止沉淀进公共引擎：

| 层 | 目录 | 内容 | 禁止 |
|---|---|---|---|
| 项目差异 | `profiles/<project>/profile.json` | 端口、marker 正则、命名捕获、fixture、覆盖策略、能力声明、溯源 | 不得写进 `scripts/` |
| 用例期望 | `cases/`（`packs/`、`examples/`、`fa2/`） | 声明式断言、case 元数据、溯源 | 不得是 Python 代码 |
| 原始记录适配 | `adapters/<project>/` | 只采集原始行并提交 `RawLogRecord`、声明 case 计划 | 不得解析、不得写 `tool.log`、不得复制引擎 |
| 测试方法知识 | `docs/voice-validation/` | 方法、验证包、失败模式、项目知识、intake 模板 | 不得替代 profile 合同 |

断言、归因、覆盖是框架能力，但**期望值必须来自 `cases/`**：

- `scripts/assertions.py` 提供通用规则（`event_exists`、`event_within_ms`、`event_order`、`event_absent`、`event_not_during`、`sequence`、`duration`、`response`、`count_range`、`state_required`、`no_reboot`）。缺时间戳的时序规则只能是 `SKIP`，不得判 `PASS`。
- `scripts/attribution.py` 从事实序列构图并归因失败；项目私有因果规则通过参数传入，不写死在引擎。
- `scripts/coverage.py` 按 profile 的端口能力与 `coverage_policy` 判定 `BLOCKED` / `COVERAGE_DEGRADED`，禁止按项目名推断能力。

profile 的 `contract_mode` 只有 `formal` 才能用于正式压测。没有真实日志支撑的项目必须保持 `draft` 且 `provenance.verified: false`；fixture 只能是文档 marker 派生的合成样本，不得声称是真机抓取。

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

## 运行环境与依赖

- Python 3.10+。
- 安装依赖：

```powershell
python -m pip install -r requirements.txt
```

- 根目录 `requirements.txt` 指向 `config/requirements.txt`。
- TTS 依赖 `lstts-core`：

```text
lstts-core @ git+https://github.com/zjashanda/lstts.git@main
```

- `lstts` 是 TTS 的唯一来源。lstest 不复制 TTS 实现。

## 更新 lstts

更新 `lstts` 仓库后，在 lstest 使用的 venv 中执行：

```powershell
pip install -U --force-reinstall --no-cache-dir "lstts-core @ git+https://github.com/zjashanda/lstts.git@main"
```

- 不需要修改 lstest 代码。
- 多个 Skill 共用 venv 时执行一次即可。
- 固定版本时把 `@main` 换成对应 tag，例如 `@v1.0.0`。
- 检查版本：

```powershell
python -c "import lstts_core; print(lstts_core.__version__)"
```

## 环境变量

只使用核心密钥环境变量：

| 变量 | 用途 | 来源 |
|---|---|---|
| `LSTTS_TTS_TOKEN` | IndexTTS token | lstts |
| `ACCESS_KEY_ID` | OSS AccessKey ID | skill-git，仅云端音色需要 |
| `ACCESS_KEY_SECRET` | OSS AccessKey Secret | skill-git，仅云端音色需要 |

- 只使用本地音色时，只需要 `LSTTS_TTS_TOKEN`。
- 旧变量 `LSTEST_TTS_TOKEN` 仍可读取，优先级低于 `LSTTS_TTS_TOKEN`。
- URL、超时、endpoint、bucket、prefix 和路径不写环境变量，放在 `~/.lstest/tts.json`、`~/.lstest/oss.json` 或 lstts 的 `references/*.json`。

## 音频与设备

- `tts` 与 `tts-manifest` 使用 lstts（IndexTTS）服务合成并校验 WAV 的时长、响度、静音/削波和 SHA-256；lstts 是本 skill 唯一的合成通道，不依赖任何本地或在线旧 TTS 引擎。
- 音色优先取本地目录 `~/.lstest/voices/<姓名>.wav`，其次使用 `lstts-core` 内置音色（`付小春`、`小宁`），也可直接传参考音频路径。
- 服务地址按 `LSTEST_TTS_CONFIG`、`~/.lstest/tts.json`、lstts `references/index_tts.json` 顺序读取；token 由 `LSTTS_TTS_TOKEN` 覆盖。
- 未指定声卡时使用系统默认 Render；指定稳定设备 key 时严格使用该设备，失败不回退。
- FFmpeg 只用于音频归一化，Windows 实际播放使用 `pygame`/DirectSound，Linux 使用 `aplay`。

## 设备文档接入（`--device-snapshot`）

设备事实的唯一来源是 `device-io`。lstest 不枚举 USB、不扫描串口、不猜 COM 号，只读取 `device-io` 产出的 JSON。`preflight`、`init`、`smoke` 都支持可选参数：

```powershell
python -u lstest/scripts/lstest.py preflight --profile <project-profile.json> --device-snapshot device-role-map.json
```

接受两种文档，按顶层 `schema` 分派：

| schema | 产出命令 | 解析方式 |
|---|---|---|
| `ark.device-role-map.v1` | `device-io roles --spec spec.json --format json --save FILE` | 取 `roles[role].port`；`state` 为 `resolved`/`pinned` 才接受 |
| `ark.device-io.v1` | `device-io ports --format json --out FILE` | 用 profile 已声明的端口名/接口标签与 `ports[].device`/`ports[].interface` 做大小写不敏感**精确相等**匹配 |

优先级与覆盖语义：

- 给了 `--device-snapshot`：profile `ports[]` 里声明的每个 role 都必须由该文档解析出端口，并覆盖 profile 中该 role 的端口；`--port` 显式给出的同 role 端口也会被替换，其余参数（波特率、能力）保持原样。
- 没给：行为与既有版本完全一致，仍然使用 `--port` / profile 里的端口，不读取任何设备文档。
- 解析失败一律硬失败，**绝不静默回落到 profile 或 `--port`**：role 缺失、`ambiguous`（多条命中）、`conflict`、`missing`、顶层 `ok: false`、schema 不支持都按 `BLOCKED` 处理，往 stderr 输出一行 JSON 错误（含 role、state、reason），退出码 `2`（本仓既有 `BLOCKED` 约定）。成功路径退出码 `0`。

`preflight` 成功时在原有字段之外增加结构化块，不打印整份 snapshot：

```json
"device_snapshot": {
  "source": "<文档绝对路径>",
  "schema": "ark.device-role-map.v1",
  "generated_at": "2026-11-11 17:50:00",
  "mode": "role-map",
  "roles": {"ap": {"state": "resolved", "port": "COM21", "reason": "only serial port on the selected board"}}
}
```

`mode` 取 `role-map` 或 `snapshot`。`init`/`smoke` 同样在给出该参数时打印 `{"status": ..., "device_snapshot": ...}`。

## 常用命令

```powershell
python -u lstest/scripts/lstest.py --help
python -u lstest/scripts/lstest.py preflight --profile <project-profile.json>
python -u lstest/scripts/lstest.py preflight --profile <project-profile.json> --device-snapshot <device-role-map.json>
python -u lstest/scripts/lstest.py tts --text-file utterance.txt --voice 付小春 --output audio/sample.wav
python -u lstest/scripts/lstest.py audio scan --direction Render --json
python -u lstest/scripts/lstest.py eventlog build --legacy <旧平铺日志> --out-dir <新结果目录> --meta <任务上下文.json> --markers <设备marker表.json>
python -u lstest/scripts/lstest.py eventlog verify --run-dir <结果目录> [--check-files]
```

详细合同和可复制的项目适配模板见：

- [references/profile-driven-log-discovery.md](references/profile-driven-log-discovery.md)
- [references/profile-driven-migration.md](references/profile-driven-migration.md)
- [references/profile-schema.md](references/profile-schema.md)
- [references/scenario-api.md](references/scenario-api.md)
- [references/result-log-standard.md](references/result-log-standard.md)
- [references/structured-event-log-v2.md](references/structured-event-log-v2.md)
- [references/node-integration.md](references/node-integration.md)
- [references/project-adaptation-standard.md](references/project-adaptation-standard.md)
- [references/acceptance.md](references/acceptance.md)
