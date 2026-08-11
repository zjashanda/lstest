# lstest

通用设备初始化、串口/声卡/播放器探测、语音识别验证、日志证据和安全压测运行时。适用于需要复用设备测试基础设施的项目，包括单串口、双串口或多串口连接、可配置波特率、稳定声卡设备标识、项目配置档案、基础唤醒/ASR 冒烟测试和场景插件。

## Skill layout

- `agents/openai.yaml`
- `config/profile.example.json`
- `config/requirements.txt`
- `references/acceptance.md`
- `references/profile-schema.md`
- `references/result-log-standard.md`
- `references/scenario-api.md`
- `scripts/__init__.py`
- `scripts/core.py`
- `scripts/lstest.py`
- `scripts/observations.py`
- `scripts/playback.py`
- `scripts/profile.py`
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

`lstest` 是与业务无关的设备测试基础框架。它负责连接、前置检查、连续串口证据、播放器、日志、耗时、停止恢复和结果判定；项目适配器负责设备标记、设备 Shell 命令、在线关联字段和业务判定依据；场景插件只负责用例和业务动作。

## 使用边界

- 端口、波特率、声卡和 profile 必须由调用者或项目配置提供；框架不猜测 COM 号，不固定 `queryId` 或 Shell 命令。
- 支持零、一个或多个串口。缺少必要证据时输出 `BLOCKED_*`，不把串口打开成功当作唤醒/识别通过。
- 默认只执行安全的初始化和基础冒烟测试，不执行烧录、配网、注册/删除、网络切换或主动重启。
- 结果写入调用者工作区的 `result/YYYYMMDD_HHMMSS_<task>/`，不写入已安装 Skill；默认保存分端口日志，不生成合并串口日志。

## 快速入口

```powershell
python -u lstest/scripts/lstest.py --help
python -u lstest/scripts/lstest.py preflight --profile lstest/config/profile.example.json
python -u lstest/scripts/lstest.py init --profile <profile.json> --port COM11 --baudrate 921600 --playback-device-key <stable-key>
python -u lstest/scripts/lstest.py smoke --profile <profile.json> --port COM11 --baudrate 921600 --playback-device-key <stable-key> --hardware
```

多个串口重复 `--port`，可写成 `COM11:csk`、`COM12:upper`；未写角色时由 profile 在有限 marker 窗口内推断。声卡必须使用稳定 key，禁止静默回退默认设备。

## 标准流程

1. 读取项目 profile 和 `plan.md`，确认任务范围、端口角色、波特率、声卡 key 和安全授权。
2. 运行无硬件 `preflight`，检查 Python、pyserial、profile、音频和结果根目录。
3. 用户明确授权真机后运行 `init` 或 `smoke --hardware`；先打开串口并持续采集，再执行日志恢复和播放器探测。
4. 基础冒烟测试按唤醒、离线命令、非控制在线语料顺序执行；在线/播放器缺少证据时分层记录 `BLOCKED` 或 `WARN`。
5. 场景通过 `Scenario` 接口逐轮生成用例；随机任务禁止提前生成百万条列表。
6. 运行中查看 `task.log`、`progress.json`、`errors.log` 和分端口原始日志；可在结果目录创建 `STOP` 安全停止。
7. 结束后复核结构化事件、CSV、原始证据和最终汇总；短冒烟测试不覆盖长压结论。

## 结果与状态

人读日志必须显示动作、原始标签和值、唤醒/命令词/在线独立状态、耗时、识别/关联 ID、原因和证据位置；完整字段进入 `task_events.jsonl`。设备原始 `keyword`、`intent`、ASR 文本、在线 ID 和播放器 marker 不翻译。状态使用 `PASS`、`WARN`、`FAIL`、`BLOCKED`、`ABORTED`、`STOPPED`。出现 panic、crash、异常重启、watchdog、assert、数据损坏或工具未处理异常时，必须设置不可被后续成功覆盖的致命故障标记。

详细 profile、日志字段、场景契约和验收要求见：

- [references/profile-schema.md](references/profile-schema.md)
- [references/result-log-standard.md](references/result-log-standard.md)
- [references/scenario-api.md](references/scenario-api.md)
- [references/acceptance.md](references/acceptance.md)
