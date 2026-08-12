# lstest

通用设备初始化、Edge TTS 音频合成、ListenAI 声卡枚举和稳定设备 key 播放、串口语音识别验证、日志证据和安全压测运行时。适用于需要复用设备测试基础设施的项目，包括单串口、双串口或多串口连接、可配置波特率、项目配置档案、基础唤醒/ASR 冒烟测试和场景插件。

## Skill layout

- `.pytest_cache/.gitignore`
- `.pytest_cache/CACHEDIR.TAG`
- `.pytest_cache/v/cache/lastfailed`
- `.pytest_cache/v/cache/nodeids`
- `agents/openai.yaml`
- `config/profile.example.json`
- `config/requirements.txt`
- `references/acceptance.md`
- `references/profile-schema.md`
- `references/project-adaptation-standard.md`
- `references/result-log-standard.md`
- `references/scenario-api.md`
- `scripts/__init__.py`
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
- `scripts/timing.py`
- `SKILL.md`
- `tests/__init__.py`
- `tests/test_foundation.py`

## Install the skill

Copy this folder into:

```text
~/.codex/skills/lstest
```

Then restart Codex.

## Usage and workflow

`lstest` 是从既有设备项目提炼的跨项目测试验证和压测标准执行框架。它沉淀连接、Edge TTS 音频合成、声卡策略、连续串口证据、播放-识别关联、初始化/重启恢复、异常统计、日志、耗时、停止恢复和结果判定；项目适配测试 Skill 只补充项目协议、设备标记、Shell 命令、在线关联字段、语料与业务判定。

## 使用边界

- 端口、波特率和 profile 必须由调用者或项目配置提供；框架不猜测 COM 号，不固定 `queryId` 或 Shell 命令。播放未指定声卡 key 时使用操作系统当前默认 Render 声卡；只有显式提供 key 时才严格绑定指定声卡。
- 支持零、一个或多个串口。缺少必要证据时输出 `BLOCKED_*`，不把串口打开成功当作唤醒/识别通过。
- 默认只执行安全的初始化和基础冒烟测试，不执行烧录、配网、注册/删除、网络切换或主动重启。
- 结果写入调用者工作区的 `result/YYYYMMDD_HHMMSS_<task>/`，不写入已安装 Skill；串口只保存分端口 `.log` 证据，不生成 `.bin` 原始字节文件或合并串口日志。
- `safe_init` 初始化命令只会在设备完成 profile 初始化 marker 后发送。命令按 profile 重试，优先校验直接/串口成功回执；没有回执时必须由发送后的 profile 旁证确认，沉默不能判定为成功。检测到 profile 重启 marker 后会自动重复该恢复流程。

## 新项目适配

后续项目必须新建独立的项目适配测试 Skill，并依照 `lstest` 的公开运行时、profile、场景接口、日志和验收规范实现；不得直接删改、复制后分叉或写入 `lstest` 底座。项目 Skill 默认继承本框架的前置检查、声卡与播放策略、播报-识别契约、异常计数、连续证据、停止恢复和状态口径，仅配置项目差异。完整边界见 [references/project-adaptation-standard.md](references/project-adaptation-standard.md)。

## 快速入口

```powershell
python -u lstest/scripts/lstest.py --help
python -u lstest/scripts/lstest.py tts --text-file utterance.txt --output audio/hello-ac.mp3 --case-id offline-001
python -u lstest/scripts/lstest.py tts-manifest --input corpus.csv --output-dir audio/generated --manifest-output audio/generated_manifest.csv
python -u lstest/scripts/lstest.py audio scan --direction Render --json
python -u lstest/scripts/lstest.py audio probe
python -u lstest/scripts/lstest.py audio play --audio-file audio/hello-ac.mp3
python -u lstest/scripts/lstest.py audio probe --device-key "VID_XXXX&PID_XXXX:STABLE_TOKEN"
python -u lstest/scripts/lstest.py audio play --audio-file audio/hello-ac.mp3 --device-key "VID_XXXX&PID_XXXX:STABLE_TOKEN"
python -u lstest/scripts/lstest.py preflight --profile lstest/config/profile.example.json
python -u lstest/scripts/lstest.py init --profile <profile.json> --port COM11 --baudrate 921600
python -u lstest/scripts/lstest.py smoke --profile <profile.json> --port COM11 --baudrate 921600 --hardware
```

多个串口重复 `--port`，可写成 `COM11:csk`、`COM12:upper`；未写角色时由 profile 在有限 marker 窗口内推断。播放默认走系统默认 Render 声卡；显式 key 不存在、重复或不可用时立即失败，绝不静默改用默认设备。

`tts` 与 `tts-manifest` 使用 Microsoft `edge-tts`，默认音色/参数与 TCL 语料生成一致：`zh-CN-XiaoxiaoNeural`、`-10%`、`+0Hz`、`+0%`。每个输出必须通过 MP3、24 kHz、单声道、时长、响度和 SHA-256 校验；批量合成写 UTF-8 BOM CSV manifest。在 Windows 终端无法可靠传递中文参数时，单条合成优先使用 `--text-file` 读取 UTF-8 文本，批量合成使用 UTF-8 CSV/JSONL manifest。播放前使用 FFmpeg 将音频归一化为 44.1 kHz、双声道 PCM WAV；Windows 实际播放使用 `pygame`/DirectSound，Linux 使用 `aplay`，FFmpeg 不直接承担播放。`audio ensure-laid` 是唯一会安装/刷新用户 shell profile 中 `laid` 与 Windows `audio-list` 的命令，执行前需用户明确要求。

## 标准流程

1. 读取项目 profile 和 `plan.md`，确认任务范围、端口角色、波特率、声卡 key 和安全授权。
2. 运行无硬件 `preflight`，检查 Python、pyserial、profile、音频和结果根目录。
3. 用户明确授权真机后运行 `init` 或 `smoke --hardware`；先打开串口并持续采集，再执行日志恢复和播放器探测。未指定播放 key 探测系统默认 Render 声卡；指定 key 时探测该设备。
4. 观察到 `initialization_patterns` 后才发送 profile 的 `safe_init` 命令，例如项目定义的日志等级设置。每条命令的直接/串口回执必须命中 `success_patterns`；无回执时仅允许使用发送后出现的 `evidence_patterns` 旁证。失败按 `retries` 重试，耗尽后记录 `INITIALIZATION_RECOVERY_FAILED`。
5. 运行中持续监听 `restart_patterns`；发现设备重启后停止判定旧会话结果，等待新的初始化 marker，再重新发送和验证所有 `safe_init` 命令。恢复过程写入 `task.log`、`task_events.jsonl`、`errors.log` 和 `tool_logs/initialization_recovery.jsonl`，累计异常不清零。
6. 基础冒烟测试按唤醒、离线命令、非控制在线语料顺序执行；在线/播放器缺少证据时分层记录 `BLOCKED` 或 `WARN`。
7. 场景通过 `Scenario` 接口逐轮生成用例；随机任务禁止提前生成百万条列表。每个主机播报建立唯一播报窗口，并声明该语料期望的原始识别字段；每条最终识别结果必须关联一个窗口且与该字段一致。播放器请求、启动、结束、失败、超时和设备侧原始 marker 都写入 `tool_logs/player_lifecycle.jsonl` 并绑定播报窗口。主机进程返回成功不能代替设备播放证据；无播报结果、同一播报多结果、识别内容错配、播放器失败/超时或设备侧播放器异常均为异常。
8. 离线识别的 `keyword`、`intent` 等算法原始输出必须先落入 `task.log`、`task_events.jsonl` 和 `tool_logs/recognition_raw.log`，例如 `ni3 hao3 kong1 tiao2`；转换后的中文或业务字段只能追加为 `normalized`。在线识别同样保存云端返回的原始文本。
9. 多唤醒词项目必须在 profile 的有序 `wake_words` 需求表中配置每一项的 `wake_word_id`、播报文本和设备侧 `expected_raw`；按清单逐项播报、逐项确认当前唤醒词，不能由任意唤醒成功替代当前项。无播报唤醒或同一播报多条唤醒结果也必须记录异常。
10. 压测每轮结束后在终端和 `task.log` 打印从任务开始至当前轮的累计异常统计，并写入 `tool_logs/exception_summary.log`；该计数不因轮次变化、串口重连或设备重启清零，相邻轮次的工具日志块之间保留一行空白。运行中查看 `task.log`、`progress.json`、`errors.log` 和分端口原始日志；可在结果目录创建 `STOP` 安全停止。
11. 结束后复核结构化事件、CSV、原始证据和最终汇总；短冒烟测试不覆盖长压结论。

## 结果与状态

人读日志必须显示动作、原始标签和值、唤醒/命令词/在线独立状态、耗时、识别/关联 ID、播放器状态、原因和证据位置；完整字段进入 `task_events.jsonl`。每轮的累计异常统计以非 `PASS`、非 `EXPECTED` 的最终用例状态为口径，`summary_final.json`、`progress.json` 同时记录 `exception_counts`、`exception_total`、`anomaly_counts`、`anomaly_total` 和 `sticky_counts`。设备原始 `keyword`、`intent`、ASR 文本、在线 ID 和播放器 marker 不翻译。`UNEXPECTED_RECOGNITION`、`MULTIPLE_RECOGNITIONS_FOR_PLAYBACK`、`RECOGNITION_RESULT_MISMATCH`、`WAKE_WORD_WITHOUT_PLAYBACK`、`WAKE_WORD_MULTIPLE_RESULTS_FOR_PLAYBACK`、`WAKE_WORD_MISMATCH`、`PLAYER_PLAYBACK_FAILED`、`PLAYER_PLAYBACK_TIMEOUT`、`PLAYER_PLAYBACK_BLOCKED`、`PLAYER_DEVICE_MARKER_ERROR` 是可恢复但不可忽略的异常，并使原本通过的当前轮变为 `FAIL`。状态使用 `PASS`、`WARN`、`FAIL`、`BLOCKED`、`ABORTED`、`STOPPED`。出现 panic、crash、异常重启、watchdog、assert、数据损坏或工具未处理异常时，必须设置不可被后续成功覆盖的致命故障标记。

详细 profile、日志字段、场景契约和验收要求见：

- [references/profile-schema.md](references/profile-schema.md)
- [references/result-log-standard.md](references/result-log-standard.md)
- [references/scenario-api.md](references/scenario-api.md)
- [references/project-adaptation-standard.md](references/project-adaptation-standard.md)
- [references/acceptance.md](references/acceptance.md)
