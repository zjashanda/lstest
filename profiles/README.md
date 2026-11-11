# profiles/ · 项目 profile 唯一目录

本目录保存 lstest 的**项目 profile 数据**。框架代码不含任何项目 marker、正则、端口或业务值；
项目差异只能落在这里的 JSON 中，并遵守 [`references/profile-schema.md`](../references/profile-schema.md)。

## 目录布局

```text
profiles/
  README.md                  # 本文件：布局、来源策略、旧路径映射
  cskwb01/
    profile.json             # CP + AP + WB01/ASR 三端拓扑
  venusws63/
    profile.json             # AP + 上位/WiFi，无 CP
  midea-fa2/
    README.md                # 明确不迁移的原因与所需证据（无 profile.json）
```

一个目录 = 一个项目 = 一个 `profile.json`。不允许一个目录放多份互斥 profile，
也不允许跨项目复用同一份 JSON。

## 旧路径映射

项目 Skill 模板历史上的 profile 路径是 `config/<project>-profile.json`（见
[`references/project-adapter-template.md`](../references/project-adapter-template.md)）。
新规范把项目数据统一放回 lstest 仓库，模板路径按下列规则映射：

| 旧路径（项目 Skill 内） | 新路径（本仓库） |
| --- | --- |
| `config/cskwb01-profile.json` | `profiles/cskwb01/profile.json` |
| `config/venusws63-profile.json` | `profiles/venusws63/profile.json` |
| `config/midea-fa2-profile.json` | 不存在，见 `profiles/midea-fa2/README.md` |
| `config/<project>-profile.json`（通用） | `profiles/<project>/profile.json` |

迁移后项目 Skill 不再自带 profile 副本：加载、fixture 与准入都由 lstest 统一执行，
避免同一项目出现两份会漂移的正则。

## 当前迁移状态

| 项目 | profile | contract_mode | 证据等级 | 说明 |
| --- | --- | --- | --- | --- |
| `cskwb01` | `profiles/cskwb01/profile.json` | `draft` | `marker-strings-only` | 10 条事件规则，来自 Polaris 文档与解析器 marker |
| `venusws63` | `profiles/venusws63/profile.json` | `draft` | `marker-strings-only` | 14 条事件规则，无 CP 拓扑 |
| `midea-fa2` | 无 | — | — | 只有命令/蜂鸣期望数据，没有串口 marker 样本 |

两份 profile 都是 `draft`：**未做真机验证**，不得用于正式压测准入。
正式压测要求 `contract_mode: "formal"` 且 `ProfileValidator.validate()` 通过。

## 来源（provenance）策略

每份 profile 必须有顶层 `provenance` 对象，字段固定为：

```json
{
  "source_repo": "zjashanda/Polaris",
  "source_commit": "<40 位 commit>",
  "source_paths": ["<相对仓库根路径>", "..."],
  "evidence_class": "marker-strings-only",
  "captured_device_logs": false,
  "verified": false,
  "note": "<证据边界说明>"
}
```

硬规则：

- `evidence_class: "marker-strings-only"` 表示正则只由文档/解析器里**已列出的 marker 字符串**推导。
- `captured_device_logs` 只有在仓库内确实提交了采集到的原始串口日志时才可为 `true`；
  当前所有 profile 都是 `false`。
- `verified` 只在真机单轮 smoke 通过并留下证据路径后才可为 `true`；当前都是 `false`。
- `fixtures` 是**按 marker 合成的字符串**，不是采集日志。任何 profile 的 `note` 必须写明这一点，
  不得用 fixture 通过暗示真机已验证。
- 新增项目或新增 marker 时，必须同时更新 `source_paths`，否则证据链不可追溯。

## 角色与 coverage

- `ports` 每项至少包含 `role`、`port`、`baudrate`、`capabilities`，只读口标 `read_only: true`。
- `notes.roles` 保存 Polaris 文档里的角色中文说明；它是人读元数据，不参与匹配。
- `coverage_policy` 用 `roles.full` / `roles.minimum` 声明完整与最小角色集，
  缺失时分别给出 `missing_non_minimum`（`COVERAGE_DEGRADED`）与 `missing_minimum`（`BLOCKED`）。
- `venusws63` 的 `asr` 与 `upper` 指向同一物理口 `COM13`，因此没有 `cp` 端口。

## 事件映射约定

`event_type` 只能取 `scripts/profile.py` 中 `EVENT_REGISTRY` 已登记的测试流程类别。
项目 marker 一律留在 profile JSON 内，不得写进框架代码。

| Polaris 侧 marker 语义 | lstest `event_type` |
| --- | --- |
| CP `WAKE(1)` / `wakeup_callback` / `Pre Wakeup` / `online_wakeup` / `mark has wakeup` | `wake_result` |
| `offline_asr_callbak` | `offline_result` |
| `online_asr_callbak` / `MSpeech Cloud 3 evt` | `online_result` |
| `mideaSkillId: DeviceControl` / `cloud.instructions.audioBroadcast` / `cloud.speech.reply`（强云端回复） | `command_ack` |
| 本地命令关键词 `WAKE(0) KEY=<kw>`、弱 `device ack` / `control ack` | `command_evidence` |
| `soundplayer status: 2/6`、`ttsplayer report state: play/stop` | `player_state`（`state_class` = `active`/`terminal`） |
| `TTS url is null` / `no valid tts url` | `device_exception` |
| `Boot Reason` / `VENUSA BOOT` | `restart` |

强证据（云端明确回复）用 `command_ack`；弱证据（本地关键词、actuator 旁证）用 `command_evidence`，
两者不可互相替代，具体选择已写在各 profile 的 `notes.event_mapping` 中。

`commands` 保持 `[]`：Polaris 文档给出的 `uut-pa.on` / `pa-enable.set` 属于控制口 PA 前置，
没有可验证的成功/旁证 pattern，因此不登记为可执行命令规则。

## 校验

```bash
# 仓库根（repos/）下执行
PYTHONPATH=$REPOS python -m unittest discover -s lstest/tests -t .
```

`tests/test_profiles_migrated.py` 校验：profile 可 `DeviceProfile.load()`、`schema_version == 2`、
`contract_mode == "draft"`、每条规则有命名捕获与展示捕获、正反 fixture 行为符合预期、
`provenance.captured_device_logs is False`。
