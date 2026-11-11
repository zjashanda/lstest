# midea-fa2 · 暂不迁移 device profile

## 结论

`midea-fa2` **没有** `profile.json`，且当前不应创建。

原因：Polaris 仓库里 FA2 相关材料只有**命令/蜂鸣期望数据**和**需求/用例 xlsx**，
没有任何采集到的串口日志或 `tool.log` 样本，也就没有一条可用于推导正则的 marker 字符串。
在缺少真实 marker 的前提下凭命令词表编造正则，会直接违反 lstest 的
[`references/project-adaptation-standard.md`](../../references/project-adaptation-standard.md)
与 [`references/profile-schema.md`](../../references/profile-schema.md)：
profile 必须来自真实日志取证，不得猜测日志含义。

## Polaris 侧现有材料（只到此为止）

| 材料 | 内容 | 对 profile 的价值 |
| --- | --- | --- |
| `docs/fa2命令词.txt` | 343 条命令词 | 只有语料，无日志格式 |
| `docs/wiki/voice-validation/fa2-command-beep-expectation.json` | 每条命令的 `beep_expectation`（263 条 `expected_if_state_changes`、80 条 `not_required`） | 只有“是否期望蜂鸣”的规则期望，不是日志证据 |
| `docs/wiki/voice-validation/fa2-command-beep-expectation.md` | 期望表口径说明，明确“规则期望表，不是物理蜂鸣器实测事实表” | 说明缺证据时只能归 `UNKNOWN`/`evidence_gap` |
| `docs/requirements/美的FA2柜机需求文档_20250818.xlsx` | 柜机需求 | 需求条目，无 marker |
| `docs/requirements/美的FA2挂机需求文档_20250818.xlsx` | 挂机需求 | 需求条目，无 marker |
| `docs/cases/FA2新增命令词测试用例.xlsx` | 新增命令词用例 | 用例，无 marker |
| `references/project_marker_overlays.json` | 只有 `cskwb01` 与 `venusws63` 两个项目 | FA2 **没有** marker overlay |
| 仓库内 `*.log` | 0 个文件 | 无任何串口/工具日志样本 |

## 需要补齐的证据（缺一不可）

按“每条会改变设备状态的命令”采集，即期望表里 `beep_expectation = expected_if_state_changes`
的 263 条命令，每条至少一份原始记录：

1. **一份采集到的串口/tool 日志**，覆盖该命令的完整链路，且保留原始行（不做预格式化）：
   - 识别证据：ASR 文本行（离线或在线回调 / 云端 ASR）；
   - 命令证据：本地命令关键词 或 云端控制回复（`DeviceControl` 类回复）；
   - 播报证据：player/TTS 的 play 与 stop/complete 原始行；
   - 执行/蜂鸣证据：能证明状态变化后动作发生的原始 marker（actuator/beep/ack 等）。
2. **no-op 对照样本**：重复打开/重复关闭、已是当前模式等不产生状态变化的命令，证明期望表
   `not_expected_if_noop` 可被判据区分，避免把 no-op 误判为失败。
3. **端与口清单**：该项目实际串口拓扑（角色名、端口、波特率、只读/可写），以及 PA/声卡前置
   是否有可验证的成功 pattern。当前 Polaris 未提供 FA2 的 env 模板或项目 profile 文档。
4. **采集来源说明**：日志来自哪台设备、什么固件版本、哪次运行；用于填写 `provenance.source_paths`
   与证据等级。

满足以上条件后，按 [`profiles/README.md`](../README.md) 的布局新建
`profiles/midea-fa2/profile.json`，并把 `provenance.evidence_class` 从
`marker-strings-only` 提升为包含真实采集日志的等级（此时 `captured_device_logs` 才可为 `true`）。
在此之前，任何 FA2 结论只能来自人工/声学回采，不能引用本仓库的 profile 准入。

## 案例与语料入口

FA2 的命令词、期望表与案例合同统一放 `cases/fa2/`（由 cases 维护方创建，本目录不创建该路径）。
profile 尚未建立前，`cases/fa2/` 只能用于人工执行与证据采集，不得声明 `BLOCKED_PROFILE_CONTRACT`
之外的形式化通过结论。
