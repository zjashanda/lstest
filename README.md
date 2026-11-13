# lstest

面向新设备项目的通用测试验证与压测框架，提供日志发现、profile 正则准入、串口证据、音频合成和播放、时序工具日志、异常隔离、恢复、统计与结果校验。适用于需要创建独立项目测试 Skill 的场景。

## Skill layout

- `SKILL.md`
- `agents/openai.yaml`
- `config/profile.example.json`
- `config/requirements.txt`
- `profiles/README.md`（新 canonical 项目差异层）
- `profiles/<project>/profile.json`
- `cases/README.md`（声明式用例合同）
- `cases/packs/`、`cases/examples/`、`cases/fa2/`
- `adapters/`（薄原始记录适配）
- `docs/voice-validation/`（测试方法与项目知识）
- `docs/migration/polaris-capability-mapping.md`（Polaris 吸收映射）
- `tools/parity/`（与 Polaris 的 dry-run 对照）
- `references/acceptance.md`
- `references/profile-driven-log-discovery.md`
- `references/profile-driven-migration.md`
- `references/profile-schema.md`
- `references/project-adaptation-standard.md`
- `references/project-adapter-template.md`
- `references/result-log-standard.md`
- `references/scenario-api.md`
- `references/structured-event-log-v2.md`
- `references/state-catalog.md`（设备状态目录/项目扩展/PASS gate）
- `references/node-integration.md`
- `requirements.txt`
- `scripts/__init__.py`
- `scripts/adapter_guard.py`
- `scripts/assertions.py`
- `scripts/attribution.py`
- `scripts/audio_synthesis.py`
- `scripts/core.py`
- `scripts/coverage.py`
- `scripts/device_snapshot.py`
- `scripts/eventlog.py`
- `scripts/environment_cleanup.py`
- `scripts/install_laid_linux.sh`
- `scripts/install_laid_windows.ps1`
- `scripts/interaction_acceptance.py`
- `scripts/listenai_play.py`
- `scripts/lstest.py`
- `scripts/observations.py`
- `scripts/playback.py`
- `scripts/profile.py`
- `scripts/result_stats.py`
- `scripts/runtime.py`
- `scripts/serial_capture.py`
- `scripts/serial_recovery.py`
- `scripts/shell.py`
- `scripts/smoke.py`
- `scripts/states.py`（状态目录、扩展校验、时序分析、覆盖率）
- `scripts/timeline.py`
- `scripts/timing.py`
- `tests/__init__.py`
- `tests/test_adapters.py`
- `tests/test_assertions.py`
- `tests/test_attribution.py`
- `tests/test_coverage.py`
- `tests/test_device_snapshot.py`
- `tests/test_environment_cleanup.py`
- `tests/test_foundation.py`
- `tests/test_eventlog.py`
- `tests/test_state_catalog.py`
- `tests/fixtures/state_catalog_smoke.txt`
- `tests/fixtures/state_catalog_smoke.markers.json`
- `tests/fixtures/state_catalog_smoke.extension.json`
- `tests/fixtures/state_catalog_smoke.meta.json`
- `tests/fixtures/legacy_flat_v1_smoke10.txt`
- `tests/fixtures/legacy_flat_v1_smoke10.meta.json`
- `tests/fixtures/legacy_flat_v1_smoke10.markers.json`
- `tests/fixtures/PROVENANCE.md`
- `tests/test_long_run_cleanup.py`
- `tests/test_migrated_cases.py`
- `tests/test_parity_polaris.py`
- `tests/test_profile_driven.py`
- `tests/test_profiles_migrated.py`
- `tests/test_result_stats.py`

## Install the skill

Copy this folder into:

```text
~/.codex/skills/lstest
~/.config/opencode/skills/lstest
```

Then restart Codex or OpenCode.

## Usage and workflow

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
- v1 结果目录仅保留 `serial_logs/*.log`、`tool.log`、`results.csv`、`cases.csv`；v2 增加 `events.jsonl`、`raw/`、`summary.md`、`summary.json`，`tool.log` 为完整动作审计（每事件一行、毫秒、TOOL+DEVICE）。禁止 `.bin`、`task.log`、`tool_logs` 和临时结果文件。
- v2 的结论只由 `events.jsonl` 计算；`gate_pass=false` 时禁止签发 PASS；手改 `tool.log`/`summary.*` 会被 `eventlog verify` 判为漂移。

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

## TTS 音频库（先查库、miss 才合成）

- lstest 只做**轻量消费适配器**：`python -u lstest/scripts/tts_library_cli.py spec|lookup|fetch ...`
- 顺序：生成完整 `ark.tts-spec.v1` → `asset-tools tts-library lookup` → 命中 `fetch` 到 `<task>/assets/tts/original`；未命中交给 `tts-runtime` 合成。
- 边界：lstest 不写 1006、不建本地缓存、不存共享凭据；**库不可达 ≠ 资产不存在**。
- 既有 `tts` / `tts-manifest` 行为不变，迁移建议见 [`docs/tts-library.md`](docs/tts-library.md)。

## 常用命令

```powershell
python -u lstest/scripts/lstest.py --help
python -u lstest/scripts/lstest.py preflight --profile <project-profile.json>
python -u lstest/scripts/lstest.py tts --text-file utterance.txt --voice 付小春 --output audio/sample.wav
python -u lstest/scripts/lstest.py audio scan --direction Render --json
python -u lstest/scripts/lstest.py eventlog build --legacy <旧平铺日志> --out-dir <新结果目录> --meta <任务上下文.json> --markers <设备marker表.json>
python -u lstest/scripts/lstest.py eventlog verify --run-dir <结果目录> [--check-files]
```

运行全部单元测试（需要 `lstts-core` 可导入）：

```bash
PYTHONPATH=. python -m unittest discover -s lstest/tests -t .
```

详细合同和可复制的项目适配模板见：

- [references/profile-driven-log-discovery.md](references/profile-driven-log-discovery.md)
- [references/profile-driven-migration.md](references/profile-driven-migration.md)
- [references/profile-schema.md](references/profile-schema.md)
- [references/scenario-api.md](references/scenario-api.md)
- [references/result-log-standard.md](references/result-log-standard.md)
- [references/project-adaptation-standard.md](references/project-adaptation-standard.md)
- [references/acceptance.md](references/acceptance.md)
- [references/structured-event-log-v2.md](references/structured-event-log-v2.md)
- [references/node-integration.md](references/node-integration.md)
