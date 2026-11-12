# 设备状态目录（lstest.state-catalog.v1）

本文是 `scripts/states.py` 的规范正文。目录是 lstest 里**合法状态码的唯一来源**：
设备状态码不再由适配器、marker 表或日志文本约定，而是先登记、再产出。

- 通用目录：201 条（124 device / 27 tool / 16 bench / 19 framework / 13 judgement / 2 delivery）。
- 项目扩展：`lstest.state-extension.v1`，只能新增 `P_` 前缀码或显式别名。
- 未登记码：落 `DEVICE_UNPARSED`，保留 `raw_code` 与原始行 hash，不静默渲染。

## 1. 为什么需要目录

v2 以前，"设备状态"是任意字符串：marker 表写 `code: "POWER_OFFF"`、项目自造
`WAKE_WORD_HIT`，框架都会渲染成正式 `[DEVICE][CODE]`，`verify` 也不报错。
结果是同一状态多个名字（`READY`/`INIT_READY`/`BOOT_COMPLETE`）、结论无法横向汇总、
`PLAYER_COMPLETE` 这种"链路完成"容易被当成"语义正确"。

目录解决三件事：

1. **可校验**：码、类别、PASS 角色都必须登记；未知码不能混进正式状态。
2. **可扩展**：项目差异用 `P_` 码和别名表达，不复制通用语义。
3. **可判定**：只有 `pass_role=positive_evidence` 的状态可以作为通过证据；
   `blocking` 状态未恢复时，gate 不得通过。

## 2. 分类

| 类别 | 条数 | 说明 | 代表码 |
|---|---|---|---|
| power | 9 | 电源/PDU | `POWER_OFF`、`POWER_ON`、`POWER_CYCLE`、`POWER_UNVERIFIED` |
| boot | 11 | 启动/复位/重启 | `BOOT_START`、`BOOT_COMPLETE`、`BOOT_FAIL`、`REBOOT_REQUEST`、`REBOOT_REASON` |
| ready | 2 | 就绪 | `READY`、`INIT_READY` |
| mode | 3 | 运行/工厂模式 | `FTM_MODE_ENTER`、`FTM_MODE_EXIT`、`DEVICE_MODE` |
| flash | 9 | 烧录 | `FLASH_PREFLIGHT`、`BURN_SUCCESS`、`VERIFY_VERSION`、`BOOT_STRAP_ON` |
| serial | 6 | 串口链路 | `SERIAL_OPEN`、`SERIAL_UP`、`SERIAL_DOWN`、`SERIAL_BUSY` |
| usb | 4 | USB/声卡 | `USB_ENUM`、`USB_MISSING`、`USB_UAC_READY` |
| net | 24 | 网络/云/在线请求 | `NET_UP`、`WIFI_CONNECTED`、`CLOUD_ONLINE`、`ONLINE_REQUEST_START`、`ONLINE_TIMEOUT` |
| wake | 10 | 唤醒/尝试 | `WAKE_KEYWORD`、`PRE_WAKEUP`、`WAKEUP`、`NO_WAKE`、`WAKE_LATE` |
| vad | 4 | VAD | `VAD_START`、`VAD_END`、`VAD_TIMEOUT` |
| asr | 10 | 识别（本地/在线） | `ASR_KEYWORD`、`ASR_RESULT`、`ONLINE_ASR_TEXT`、`ASR_ERROR` |
| tts | 14 | 合成/播放阶段 | `TTS_REQUEST`、`TTS_PLAY_ID`、`TTS_PLAY`、`TTS_COMPLETE`、`TTS_INTERRUPTED` |
| player | 15 | 播放/主机音频 | `PLAYER_PREPARE`、`PLAYER_START`、`PLAYER_COMPLETE`、`PLAYER_INTERRUPTED` |
| record | 7 | 录音 | `RECORD_START`、`RECORD_END`、`RECORD_EMPTY`、`RECORD_PASS` |
| command | 16 | 业务执行/技能 | `INTENT`、`LOCAL_SKILL`、`ONLINE_SKILL`、`DEVICE_ACTION`、`DEVICE_ACK`、`DEVICE_NACK` |
| ota | 3 | 升级 | `OTA_START`、`OTA_SUCCESS`、`OTA_FAIL` |
| resource | 2 | 资源 | `RESOURCE_WARN`、`RESOURCE_CRITICAL` |
| error | 2 | 异常 | `ERROR`、`WARN` |
| recovery | 4 | 恢复 | `RECOVERED`、`RECOVERY_FAILED`、`EVIDENCE_GAP`、`EVIDENCE_HASH_MISMATCH` |
| unknown | 7 | 未知/解析 | `DEVICE_UNPARSED`、`MARKER_UNMATCHED`、`MARKER_SCOPE_REJECTED`、`EVENT_LATE` |
| tool | 7 | 工具/宿主 | `TOOL_ERROR`、`DEVICE_NOT_FOUND`、`DEVICE_AMBIGUOUS`、`BACKEND_UNAVAILABLE` |
| task | 4 | 任务事件 | `TASK_START`、`TASK_END`、`ROUND_RESULT`、`MILESTONE` |
| version | 5 | 版本 | `FIRMWARE_VERSION`、`APP_VERSION`、`ALGO_VERSION`、`PROTOCOL_VERSION`、`PROFILE_VERSION` |
| bluetooth | 8 | 蓝牙 | `BT_ENABLED`、`BT_SCANNING`、`BT_CONNECTING`、`BT_CONNECTED`、`BT_ERROR` |
| audio | 6 | 音频链路 | `MIC_ERROR`、`SOUNDCARD_MISSING`、`AUDIO_UNDERRUN`、`DEVICE_LOST`、`DEVICE_RECOVERED` |
| judgement | 7 | 判定 | `PASS`、`FAIL`、`BLOCKED`、`NEEDS_REVIEW` |
| delivery | 2 | 交付 | `DELIVERY_INCOMPLETE`、`INCOMPLETE` |

完整清单：`python -m lstest.scripts.lstest state list`（`--json` 给机器读）。

## 2.1 项目基础 marker（用户给定的 7 个）

这些 raw marker 原样匹配，项目 profile 只声明"启用哪些、映射到哪个语义状态、显示类别与字段"：

| marker 名 | 原始正则 | 语义状态 | 显示类别 |
|---|---|---|---|
| `wakeupkw` | `.*wakeup_callback, keyword: (.*)` | `WAKE_KEYWORD` | 唤醒 |
| `wakeupttskw` | `.*wakeup_tts_callback, tts: (.*)` | `WAKE_TTS_CALLBACK` | tts |
| `asrkw` | `.*keyword:(.*)` | `ASR_KEYWORD` | asr |
| `ttskw` | `.*play id : (.*)` | `TTS_PLAY_ID` | tts |
| `onlineasrkw` | `.*online_asr_callbak, text: (.*)` | `ONLINE_ASR_TEXT` | online_asr |
| `onlinettskw` | `.*online_asr_tts_callbak, tts: (.*)` | `ONLINE_TTS_TEXT` | online_tts |
| `onlineskillkw` | `.*online_skill_callbak, text: (.*)` | `ONLINE_SKILL_TEXT` | online_skill |

匹配用 marker 表的 `pattern`（可加命名捕获组）；`display` 可声明 `category/stage/primary/fields`，
框架只按声明取值。项目没有的 marker 不启用、不补状态。

## 2.2 显示层（tool.log）

设备行只显示**统一类别 + 业务值 + 阶段**，不显示状态码、raw marker、正则名、rule id、
捕获字段名、内部关联 ID 或 `device_ts`。示例：

```text
2026-11-12 17:02:49.102 [R01/A02][DEVICE] 唤醒：keyword=hai1 xin4 xiao3 ju4，zh:海信小聚
2026-11-12 17:02:49.301 [R01/A02][DEVICE] asr：da3 kai1 kong1 tiao2，zh:打开空调
2026-11-12 17:02:49.310 [R01/A02][DEVICE] online_asr：da3 kai1 kong1 tiao2，zh:打开空调
2026-11-12 17:02:49.410 [R01/A02][DEVICE] tts：tts_id=1，addr=//1.pcm，play
2026-11-12 17:02:50.125 [R01/A02][DEVICE] tts：tts_id=1，play_end
2026-11-12 17:02:45.000 [R01][ACTION] 播放唤醒音频：file=wakeup.mp3
2026-11-12 17:02:49.000 [R01/A02][RESULT] 当前唤醒结果：PASS | attempts=2
```

- 一个显示类别可以由多个 raw marker 组成（如"唤醒"可由 `wakeup_callback`、`Pre Wakeup`、`WAKEUP` 映射）。
- `tts` 不是一个事件：request/start/id/play/pause/interrupt/play_end/error 是同一类的不同阶段。
- 绝对路径收敛为文件名，内网地址显示为 `<ip>`；敏感值不进入 tool.log。
- 原始 marker、host/device 时间、hash、证据只在 `events.jsonl`/`raw/`/`serial_logs/`；
  `tool.log` 不出现 `raw_marker=`、`catalog_status=`、`raw_code=`、`rule=`、`correlation_id=`。
- 旧目录（`meta.display_format` 不是 `unified`）仍按旧的 `[DEVICE][CODE]` 渲染，
  `verify` 不会因新格式把旧日志判成漂移；重新 `render` 才迁移到统一显示。

## 3. 条目字段

```json
{
  "code": "PLAYER_COMPLETE",
  "category": "player",
  "kind": "device",
  "label_zh": "设备播报结束",
  "raw_marker": ["Refresh PA to OFF"],
  "prerequisites": ["PLAYER_START"],
  "successors": ["WAKEUP", "NO_WAKE"],
  "pass_role": "positive_evidence",
  "recoverable": false,
  "duplicate_policy": "merge",
  "scope": "common",
  "supersedes": null,
  "version": 1
}
```

| 字段 | 取值 | 含义 |
|---|---|---|
| `kind` | `device`/`bench`/`tool`/`framework` | 可进入设备时间线；`judgement`/`delivery` 是结论，不得当设备状态 |
| `pass_role` | `positive_evidence` / `blocking` / `diagnostic` / `none` | 见 §6 |
| `duplicate_policy` | `merge` / `state_change` / `keep` | `merge` 连续重复合并；`state_change` 同码不同 state 才保留；`keep` 每次单独成条 |
| `scope` | `common` / `bench` / `framework` | 项目专属只出现在扩展中 |
| `supersedes` | 旧码或 `null` | 改名不删除历史；旧码继续可读 |

`raw_marker` 只作说明与检索，**不是匹配规则**：真正的匹配在项目 marker 表的 `pattern` 里。

## 4. 未登记码：`DEVICE_UNPARSED`

适配器提交 `code` 或 marker 表声明 `code` 时，框架先解析目录：

| 情形 | 结果 |
|---|---|
| 通用码 | 正常事件，`details.catalog_status=common` |
| 项目 `P_` 码（扩展已登记） | 正常事件，`details.catalog_status=project` |
| 别名 `raw_code → maps_to` | 事件用 `maps_to`，`details.raw_code` 保留原码 |
| 未登记 / 把 PASS/FAIL 当设备状态 | `DEVICE_UNPARSED`（`level=WARN`），保留 `details.raw_code`、`details.raw_sha256`、`details.raw_excerpt`、证据定位 |

硬规则：**框架不猜字段、不改写原始 marker**。marker 文本、捕获值、设备时钟原样保留；
只有 `event`（语义码）来自目录/扩展。`verify` 对未登记码给 `STATE_UNREGISTERED`。

## 5. 项目扩展（`lstest.state-extension.v1`）

```json
{
  "schema": "lstest.state-extension.v1",
  "profile_id": "tcl-csk7",
  "states": [
    {
      "code": "P_TCL_SETLOGLEV_OK",
      "category": "mode",
      "kind": "device",
      "label_zh": "CSK 日志级别设置成功",
      "raw_marker": ["flash.setloglev 4 success"],
      "prerequisites": ["READY"],
      "successors": ["WAKEUP"],
      "pass_role": "diagnostic",
      "duplicate_policy": "merge"
    }
  ],
  "aliases": [{"raw_code": "WAKE_WORD_HIT", "maps_to": "WAKEUP"}],
  "enabled_markers": ["wakeupkw", "asrkw", "ttskw"],
  "not_applicable": ["online_asr", "online_tts", "online_skill"],
  "gate": {
    "required_states": ["READY", "WAKEUP", "PLAYER_COMPLETE"],
    "max_unrecovered_blocking": 0,
    "recovery_window_s": 30,
    "strict_prerequisites": false
  }
}
```

校验（`state check` 或 profile 正式准入）：

1. `code` 必须 `^P_[A-Z0-9_]+$`，不得与通用码同名（`STATE_NAMESPACE`）。
2. `profile_id` 必须匹配 profile；`P_` 码只在同一 profile 产物中有效。
3. `aliases[].maps_to` 必须已登记；`raw_code` 不能是通用码（禁止用别名改写通用语义）。
4. 扩展引用未登记码直接失败（防拼写错误）。
5. `enabled_markers` 只列本项目真实启用的 marker；`not_applicable` 列项目没有的类别
   （如离线项目声明 `online_asr`/`online_tts`/`online_skill`），coverage 会标
   `NOT_APPLICABLE` 而不是 `NOT_EMITTED`。
5. profile 内声明位置：

```json
{
  "profile_id": "tcl-csk7",
  "schema_version": 2,
  "state_catalog": {"states": [], "aliases": [], "gate": {}}
}
```

`state_catalog.schema` 与 `profile_id` 可省略，由 profile 自动补；写了就必须一致。
不合法即 `BLOCKED_PROFILE_CONTRACT`（沿用 profile 合同退出码 2）。

## 6. PASS gate 与状态判定

| `pass_role` | 能否作为 PASS 证据 | 说明 |
|---|---|---|
| `positive_evidence` | 能 | 如 `WAKEUP`、`PLAYER_COMPLETE`、`VERIFY_VERSION`、`WAKEUP` |
| `blocking` | 不能；出现即阻断 | 如 `ERROR`、`BOOT_FAIL`、`EVIDENCE_GAP`、`DEVICE_UNPARSED` |
| `diagnostic` | 不能 | 只解释时序（`PRE_WAKEUP`、`VAD_START`） |
| `none` | 不参与 | 工具/任务事件 |

profile 声明 `gate.required_states` 后，框架追加两项 gate 检查：

- `required_states_present`：声明的状态必须都出现；
- `no_unrecovered_blocking`：blocking 状态必须在 `recovery_window_s` 内被恢复
  （`RECOVERED`/`READY`/`SERIAL_UP`/`USB_ENUM`/`WAKEUP` 或同类别非 blocking 状态）。

任一为 false → `delivery_status=BLOCKED`，`final_status` 不得为 `PASS`。
未声明 `gate` 时行为不变；旧 v1/v2 目录不受影响。

`strict_prerequisites` 打开后，`verify` 才会对"缺前置状态"给 `STATE_PREREQUISITE`；
默认只记录在 `summary.json` 的 `state_report.prerequisite_missing`，不刷屏。

## 7. 重复 / 迟到 / 恢复

| 情形 | 处理 |
|---|---|
| 连续同码同 state、间隔 ≤ 2 s | 合并一条，`repeat=N`、`last_hit`；`state_report.duplicates.collapsed_events` 计数 |
| 同码但 state 变化 | 不合并（即使 `duplicate_policy=merge`） |
| `device_ts` 比上一条设备事件早 > 0.5 s | `state_report.late` 记录；`verify` 给 `STATE_LATE`（WARN） |
| blocking 后窗口内恢复 | `state_report.blocking.recovered += 1` |
| blocking 后窗口内未恢复 | 进 `blocking.unrecovered`，gate `no_unrecovered_blocking=false` |

host 时间永远来自实际接收/动作窗口；`device_ts` 只原样保存，不参与排序。
这保证跨仓库（venusahisiburner/device-io 用 `T`+毫秒）复用设备时间时不会污染 lstest 时间线。

**关联**：设备事件沿用 TOOL 事件的 `correlation_id`。`state_report.correlation` 给出
`with_id`/`without_id` 计数与按关联键分组的码序列（最多 20 组、每组 12 个码）；
缺关联不会被丢弃，但会在报告里可见，供判定是否能把状态串成一条链路。

## 8. 外部契约映射（不复制对方状态名）

| 来源 | 原始状态 | lstest 处理 |
|---|---|---|
| device-io role | `resolved`/`pinned` | 前置条件，不落设备状态；失败映射 `DEVICE_NOT_FOUND`/`DEVICE_AMBIGUOUS` |
| device-io role | `missing`/`ambiguous`/`conflict` | `DEVICE_NOT_FOUND` / `DEVICE_AMBIGUOUS`（`kind=tool`，blocking） |
| device-io PDU | `on`/`off`/`unknown`、`verified=false` | `POWER_ON`/`POWER_OFF`（只表示读回后的物理状态）/`POWER_UNVERIFIED` |
| device-io PDU | backend unavailable | `PDU_BACKEND_UNAVAILABLE` |
| audio-runtime | `device-not-found`/`device-ambiguous`/`backend-unavailable`/`probe-failed` | `DEVICE_NOT_FOUND`/`DEVICE_AMBIGUOUS`/`BACKEND_UNAVAILABLE`/`TOOL_ERROR` |
| audio-runtime | play/record receipt | `HOST_AUDIO_*`、`RECORD_START/END` |
| tts-runtime | `USAGE`/`FAIL`/`UNSUPPORTED`/`BACKEND` | `TOOL_ERROR` / `TTS_ERROR` / `BACKEND_UNAVAILABLE` |
| tts-runtime | `start/progress/item_result/complete` | 归入 `TTS_START/TTS_END`；不直接复用其小写事件名 |
| venusahisiburner | `preflight/boot/burn/verify/recover/receipt`、`PASS/FAIL/BLOCKED/SKIPPED` | `FLASH_PREFLIGHT`、`BOOT_STRAP_*`、`BURN_*`、`VERIFY_VERSION`、`RESET` |
| venusahisiburner | `Flash Init Fail 0x80001341`、`AT+FTM=0`、`setloglev` | `BOOT_FAIL`、`FTM_MODE_ENTER/EXIT`、`DEVICE_MODE` |
| ark-node probe | `dns/https` 布尔、工具 `present/absent` | `NET_UP`/`NET_DOWN` 只作参考；能力画像不是事件流 |
| NOAH-57 台架 | `POWER_PASS/FAIL/BLOCKED`、`RECORD_PASS/FAIL/BLOCKED` | 保留 `kind=judgement`、`scope=bench`；不进入通用 gate |

## 9. CLI

```bash
python -m lstest.scripts.lstest state list [--category wake] [--json]
python -m lstest.scripts.lstest state check --extension <扩展.json>
python -m lstest.scripts.lstest state coverage --inventory <盘点.json> [--extension <扩展.json>] [--output <报告.json>]
python -m lstest.scripts.lstest eventlog build  ... --state-extension <扩展.json>
python -m lstest.scripts.lstest eventlog verify --run-dir <目录> [--state-extension <扩展.json>] [--strict-states]
```

退出码：`0` 通过；`2` 扩展/目录不合法或存在 ERROR。

## 9.1 项目覆盖率（5 类）

`state profile-coverage --extension <扩展.json> --events <events.jsonl>` 输出：

| 桶 | 含义 |
|---|---|
| `SUPPORTED` | 总目录 + 项目扩展登记的能力集合 |
| `EMITTED` | 本次运行真实出现的状态（含项目 `P_` 码） |
| `NOT_EMITTED` | 相关类别里支持但本次未出现（不能补假状态） |
| `NOT_APPLICABLE` | profile 明确声明没有的能力（离线项目的在线状态等） |
| `UNPARSED` | 未登记 marker；原文进 raw/evidence，tool.log 只显示 `未识别设备状态` |

`NOT_EMITTED` 不等于失败，只表示"本次没发生"；`UNPARSED` 才有解析缺口。

## 10. 证据等级（不得夸大）

| 等级 | 含义 | 当前状态 |
|---|---|---|
| 真机回放 | 真实日志派生并校验 | 5 个码：`POWER_OFF`、`POWER_ON`、`PRE_WAKEUP`、`PLAYER_START`、`PLAYER_COMPLETE`（NOAH-68 105 事件） |
| 代码/测试覆盖 | 单测用合成事件验证渲染与规则 | 20+ 个码（`DeviceCoverageTests` + `test_state_catalog.py`） |
| 仅登记 | 有来源文档/回执，但无 lstest 产出 | `BOOT_COMPLETE`、`FTM_MODE_*`、`USB_*`、`OTA_*`、`RECORD_PASS` 等 |
| 无来源 | 不得进入目录 | — |

`tests/fixtures/state_catalog_smoke.*` 是**合成样本**（非真机、无客户信息），
用于验证通用码、项目 `P_` 码、别名、未登记码、gate 与校验的完整链路。
真机状态覆盖仍以节点侧实际回放为准，不得用合成样本冒充真机结论。
