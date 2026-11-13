# 结构化事件日志 v2（完整动作审计 + 人机双读 + 判定一致性）

> 布局提示：新任务按 [result-layout-v3.md](result-layout-v3.md) 落盘——`events/events.jsonl`、
> `events/tool.log`、`reports/summary.json`、`evidence/raw/`。下文的 `events.jsonl`、`tool.log`、
> `summary.json`、`raw/` 指 v2 旧根目录布局，v1/v2 结果仍可读；两侧统一用
> `scripts/result_layout.py` 解析路径。

本文是 v2 的规范正文。v1（`tool.log` 四产物）仍被读取和校验，见
[result-log-standard.md](result-log-standard.md)。

v2 的核心是四个文件各司其职，**任何一个都不能替代另一个**：

| 文件 | 角色 | 读者 |
|---|---|---|
| `events.jsonl` | 机器真源：一行一个事件，固定 schema，可重算一切 | 程序 |
| `tool.log` | **完整动作审计**：TOOL 动作 + DEVICE 状态按 host 毫秒排序，每个事件恰好一行 | 人 |
| `summary.json` | 机器可读结论 | 程序 |
| `summary.md` | 人读结论（只读 summary.json） | 人 |

> 历史说明：v2 早期草案把人读文件叫 `run.log`，并按"紧凑摘要"渲染。该口径已作废。

## 1. 为什么需要 v2

旧版 29 字段平铺块把每个事件写成 29 行（`time:` / `level:` / … / `details:`），空字段用 `-`
占位。实证：55 个事件 = 1595 行，且：

- 结论被埋在字段里，人读不到，也无法按时间顺序复盘脚本动作。
- `status: PASS` 与同一事件的 `gate_pass=false` 可以同时存在，最终状态被写成 PASS。
- 只报最终成功率，首轮失败的趋势被 100% 掩盖。

v2 的硬规则：

1. **结论只能由事件计算**，不能手写；`gate_pass=false` 时最终状态禁止 PASS。
2. **`tool.log` 是完整审计，不是摘要**：动作、参数、前后状态、判断依据、证据一个不少。
3. **每个事件恰好一行**，毫秒级时间戳，禁止续行、空行、段落式汇总。

## 2. 目录结构

```text
result/<timestamp>_<task>/
  events.jsonl          # 机器真源：TOOL + DEVICE，一行一事件
  raw/                  # 外置 raw：e<seq>_<EVENT>.json，含 sha256 引用
  tool.log              # 完整动作审计（人可顺序复盘）
  tool.live.log         # 可选：运行中实时账本；收尾被 tool.log 取代
  summary.json          # 机器可读结论
  summary.md            # 人读结论
  serial_logs/*.log     # 设备原始串口（host 接收时间 + 设备原文）
  results.csv           # 每轮一行
  cases.csv             # 设备动作前冻结
```

- 新任务必须产出事件真源、`tool.log`、`summary.md`、机器 summary：
  v3 为 `events/events.jsonl`、`events/tool.log`、`summary.md`、`reports/summary.json`；
  v2 旧布局为根目录 `events.jsonl`、`tool.log`、`summary.md`、`summary.json`；`raw/` 按需（v3 为 `evidence/raw/`）。
- `tool.live.log` 只在结构化运行期间用于 tail，收尾后以 `tool.log` 为准，不参与验收。
- `serial_logs/`、`results.csv`、`cases.csv` 的职责不变。

## 3. events.jsonl

一行一个 JSON 对象，UTF-8，字段顺序固定如下（缺失值为 `null`，不得写 `-`）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema` | string | 固定 `lstest.event.v2` |
| `event_seq` | int | 从 1 开始连续递增；物理顺序即 `tool.log` 顺序的依据之一 |
| `time` | string | **host 接收时间**，北京时间 `YYYY-MM-DD HH:MM:SS.SSS`，无 `T`/`Z`/偏移 |
| `level` | string | `INFO` / `WARN` / `ERROR` |
| `event` | string | 大写事件名；无法识别时为 `UNKNOWN` |
| `task_id` | string\|null | 任务标识 |
| `round` | int\|null | 轮次 |
| `case_id` | string\|null | 用例/场景 |
| `phase` | string\|null | 阶段（`power_cycle`、`wake_wait`、`record_open`、`round_end` …） |
| `source` | string\|null | `tool` 动作来源脚本/工具；设备事件为 `device` |
| `device` | string\|null | 设备标识 |
| `port_role` | string\|null | 端口/角色，如 `COM4/csk` |
| `action_id` | string\|null | 动作标识 |
| `broadcast_id` | string\|null | 播报标识 |
| `correlation_id` | string\|null | 关联键；缺省按 `R<round>:<case_id>:<action>` 推导 |
| `status` | string\|null | 动作/判断结果（`PASS`/`FAIL`/`NO_WAKE`/`BLOCKED` …） |
| `reason` | string\|null | 机器原因码 |
| `attempt` / `max_attempts` | int\|null | 尝试次数 |
| `elapsed_ms` | number\|null | 耗时 |
| `rule_id` | string\|null | 判定规则标识 |
| `expected` | object\|null | 期望值/阈值/标志集合 |
| `actual` | object\|null | 实际值；gate 放在这里 |
| `normalized` | object\|null | 归一化派生值（窗口、间隔、偏移） |
| `handling` | string\|null | `continue` / `stop_round` / `abort` … |
| `evidence` | array | `[{path, line?, sha256?, external?}]`，path 为相对路径 |
| `message` | string\|null | 人类可读短句；物理一行（换行在渲染时转义） |
| `details` | object\|null | 其余结构化上下文；路径字段必须相对化 |
| `raw_ref` | object\|null | `{path, sha256, bytes, lines}` 指向 `raw/` |
| `legacy_warnings` | array | 仅迁移旧日志时出现 |

规则：

1. **禁止内嵌 raw。** 设备原始行、工具 stdout/stderr、`wake_raw` 等全部写入
   `raw/e<seq>_<EVENT>.json`，事件只保留 `raw_ref`（含 sha256，可复核）。
2. **禁止绝对路径。** 路径字段写入前收敛为 `result/` 之后的相对路径或文件名；`raw/` 例外。
3. **evidence 只做指针**：`path + line + sha256`，不复制内容。
4. **`-` 不是值**：缺失一律 `null`。
5. `time` 必须是毫秒；只到秒的输入在写入时补 `.000`，`T`/`Z`/偏移会被归一化。

### 最小事件集（生成完整摘要所需）

| 事件 | 必需字段 | 作用 |
|---|---|---|
| `TASK_START` | `task_id`、`expected.mode/target_rounds`、`details.purpose`（建议） | 说明测了什么、为什么、范围 |
| `DEVICE_BINDING` | `device`、`port_role`、`details.playback/record` | 环境与设备绑定 |
| `ROUND_RESULT` | `round`、`status`、`actual.attempts_total`、`actual.first_success_attempt` | 轮次结论与尝试分布 |
| `BROADCAST`（每次尝试） | `round`、`attempt`、`max_attempts`、`status`（`NO_WAKE`/`PASS`） | 首轮成功率与异常 |
| `RECORDING` / `PDU_CYCLE` | `elapsed_ms`、`status`、`actual.*` | 台架与录音质量 |
| `MILESTONE` | `actual.completed_rounds/aggregate` | 趋势与阶段统计 |
| `TASK_END` | `status`、`actual.gate`、`actual.aggregate` | 签发依据 |

没有 `TASK_END` 时按"中断"处理，最终状态只能是 `NEEDS_REVIEW`。

### DEVICE 事件

设备状态码由 [state-catalog.md](state-catalog.md) 登记：通用目录 + 项目
`lstest.state-extension.v1` 扩展。统一约定：

- `source="device"`，`details.device=true`，`details.audit_category` 取
  `PDU/WAKE/PLAYER/REC/TTS/ASR/NET/DEVICE`，
  `details.audit_kind=STATE`（或 START/END）。
- `details.state` 写状态转换，如 `ON→OFF`、`IDLE→PLAYING`。
- 设备自身时钟放进 `details.device_ts`，**不得**用它替代 host 时间；host 时间来自实际接收/动作窗口。
- **状态码必须登记**：未登记的 marker/适配器码落 `DEVICE_UNPARSED`（`level=WARN`），
  原始码保存在 `details.raw_code`，原始行摘要与 `details.raw_sha256` 一并保留。
- 项目只能新增 `P_` 前缀码或显式别名（`raw_code → maps_to`）；不得覆盖通用码，
  不得改写原始 marker 文本。
- 未识别但可能重要的设备行不得静默丢弃：写 `DEVICE_UNPARSED`，带 `details.raw_sha256` 与
  `details.raw_excerpt`，并保留证据定位。
- 连续重复的同一状态合并为一条事件，`details.repeat_count` 记次数、`details.last_hit` 记最后一次；
  ERROR、恢复和状态变化必须单独成条。

## 4. tool.log（完整动作审计）

**定义**：按真实执行先后，把 TOOL 动作与 DEVICE 状态合并成一条毫秒时序；每个事件物理上恰好一行。
人只读这个文件就能复盘：脚本做了什么、设备怎么响应、为什么作出该判断。

### 行格式

```text
<YYYY-MM-DD HH:MM:SS.mmm> [<范围>][<动作>][<阶段>]([<结果>]) <人类可读描述> | key=value | ... | evidence=<路径#行号>
```

- `范围`：`TASK` 或 `R01`、`R01/A02`。
- `动作`：TOOL 事件用 `RUN`/`ENV`/`SERIAL`/`PDU`/`WAKE`/`REC`/`PLAYER`/`TTS`/`ASR`/`NET`/`RESULT`；
  **DEVICE 事件固定用 `DEVICE`，后面直接写统一显示类别**（唤醒/asr/online_asr/tts/播放/电源/启动…），
  不写状态码、raw marker、rule id 或内部解析 ID。
- `阶段/结果`：TOOL 事件用 `START`/`END`/`STATE`/`JUDGE`/`RETRY`/`TIMEOUT`/`PASS`/`FAIL`/`NO_WAKE`/`BLOCKED`。
- 第四段 `<结果>` 只在 `JUDGE`/`END` 等需要区分判断时出现，例如 `[R01/A01][WAKE][NO_WAKE]`、
  `[TASK][RUN][END][NEEDS_REVIEW]`。
- 统一设备行示例（详见 [state-catalog.md](state-catalog.md) §2.2）：
  `[R01/A02][DEVICE] 唤醒：keyword=hai1 xin4 xiao3 ju4，zh:海信小聚`、
  `[R01/A02][DEVICE] tts：tts_id=1，addr=//1.pcm，play`、`[R01][DEVICE] 电源：断电`。
- 旧目录（摘要 `meta.display_format` 缺失）按旧的 `[DEVICE][状态码]` 渲染，`verify` 仍可通过；
  新目录默认统一显示。

真实样例（脱敏后）：

```text
2026-11-12 17:02:02.527 [R01][DEVICE][POWER_OFF] PDU 断电成功 | phase=power_cycle | case=wake-0001 | corr=R1:wake-0001:pdu-reboot-0001 | state=ON→OFF | evidence=tool.log
2026-11-12 17:02:22.527 [R01][PDU][PASS] PDU 断电 20s 后上电… | tool=pduControl.exe | action=pdu-reboot-0001 | expected=ip=…, socket=1, off_sec=20 | actual=rc=0, elapsed_ms=24326, power_on=… | reason=pdu_returncode_0 | elapsed=24326ms | since_prev=20.000s | evidence=tool.log
2026-11-12 17:02:25.183 [R01/A01][WAKE][NO_WAKE] 第 1 次播报未唤醒 | action=play-R0001-A01 | max_attempts=10 | play_s=0 | play_end_s=1.656 | gap_s=1 | window_s=1 | expected=wake_raw_any=… | actual=wake_seen=false, start_seen=false | hit=none | next=RETRY | evidence=raw/e0007_BROADCAST.json
2026-11-12 17:02:26.484 [R01/A02][DEVICE][PRE_WAKEUP] 设备上报预唤醒 | ncm=871 | threshold=800 | keyword=… | bMain=1 | device_ts=2026-01-01 00:00:02.513 | state=IDLE→WAKE_DETECTED | evidence=…
2026-11-12 17:07:09.419 [TASK][RUN][END][NEEDS_REVIEW] 任务结束 | recorded=PASS | test_outcome=PASS | delivery=DELIVERY_INCOMPLETE | final=NEEDS_REVIEW | gate=FAIL(structured_artifacts_present) | rounds=10/10 | reason=completed
```

### 硬规则

1. **一行一个事件**：message 中的换行/控制字符必须转义（`\n` 可见文本），不得产生续行；
   一条事件的信息不得拆成多行，信息过多就拆成多个真实时序事件。
2. **毫秒**：每条日志（tool、serial 包装事件、audio/PDU/runner 事件、判断日志）均为
   `YYYY-MM-DD HH:MM:SS.mmm`，禁止只到秒。
3. **自足**：每行包含范围、动作、阶段/结果、做法/关键参数、前后状态、判断依据、下一步、证据；
   不存在的字段不输出，禁止 `-` 占位。
4. **排序**：全部行按 host 接收时间升序；同一毫秒按 `event_seq` 稳定排序。
5. **不内嵌 raw**：stdout、串口原文、大 JSON 不进入 `tool.log`，只写提炼后的标志与
   `evidence=路径#行号`；raw 全文在 `raw/` 或 `serial_logs/`。
6. **不写汇总段落**：`tool.log` 不允许 header/footer/空行；结论看 `summary.md`，
   但 `TASK_END` 行必须同时给出 `recorded`、`test_outcome`、`delivery`、`final`、`gate`。
7. **不丢状态**：设备状态、错误、恢复必须出现；未识别行以 `DEVICE_UNPARSED` 出现。
8. **可去重**：连续重复的 DEVICE 状态合并一行，统一显示格式带 `（repeat=N）`；TOOL 动作不去重。
9. **不泄露内部解析信息**：设备行不得出现 `raw_marker=`、`catalog_status=`、`raw_code=`、
   `rule=`、`correlation_id=`、`device_ts=`、`event_seq=`；这些只存在于 `events.jsonl`/`raw/`。

### 应覆盖的设备状态类别

启动/电源（POWER_OFF/POWER_ON/BOOT_*/READY/REBOOT/RESET/CRASH/WATCHDOG）、连接链路
（SERIAL_UP/DOWN、WIFI_*、CLOUD_*、协议握手/心跳/超时）、唤醒（PRE_WAKEUP/WAKEUP/
WAKE_REJECT/WAKE_TIMEOUT，含关键词、分数、阈值、主辅唤醒）、识别（ASR_START/END、
OFFLINE_ASR_RESULT、ONLINE_ASR_START/RESULT/ERROR、文本摘要或 `text_sha`、耗时、路由）、
TTS（REQUEST/START/FIRST_PACKET/END/ERROR、voice/model、在线或本地）、播放（PREPARE/START/
PAUSE/RESUME/STOP/COMPLETE/ERROR、source、音量）、录音/VAD（RECORD_START/STOP、VAD_START/END、
参数、丢帧/溢出）、业务（命令、intent、执行回报、失败码）、资源异常（越阈值、ERROR/WARN/恢复）。

以上是类别要求，**具体状态码以 [state-catalog.md](state-catalog.md) 的登记清单为准**；
本表没有、目录也没有的码，按未登记处理（`DEVICE_UNPARSED` + `raw_code`）。

## 5. summary.json / summary.md

`summary.json` 的 `schema` 为 `lstest.summary.v2`，字段分四组：

| 组 | 字段 | 说明 |
|---|---|---|
| 元信息 | `meta`、`task_id`、`bundle_version` | 生成时间、来源日志 sha256、事件数 |
| 判定 | `test_outcome`、`delivery_status`、`final_status`、`gate`、`conflicts` 等 | 唯一结论来源 |
| 统计 | `counts`、`rates`、`latency`、`milestone`、`anomalies`、`first_failure`、`worst_round` | 全部由事件计算 |
| 上下文 | `environment`、`tooling`、`inputs`、`parameters`、`responsibility`、`missing`、`next_steps`、`evidence_index` | 缺失即 `null`/`[]` |

`rates` 必须同时包含：

- `round_success_rate`：PASS 轮次 / 完成轮次。
- `first_attempt_success_rate`：`first_success_attempt == 1` 的轮次 / 完成轮次。
- `first_attempt_gap_pct`：最终成功率与首轮成功率之差（百分点），暴露"最终 100% 掩盖首轮全失败"。
- `attempt_distribution`：第 1..N 次成功各多少轮、占比，`none` 表示 N 次均未成功。

`summary.md` 固定回答 8 个问题：测了什么/为什么/范围、对象与环境、最终结论与 gate、
通过/失败/阻塞与双成功率/分布/趋势、异常与是否退化、首败/最差轮/责任层/证据、
缺什么/下一步、证据索引（路径+行号+sha256）。

## 6. 判定矩阵

`test_outcome`（只看轮次）与 `delivery_status`（只看 gate 的交付完整性）分开计算：

| test_outcome | delivery_status | final_status |
|---|---|---|
| PASS | COMPLETE | **PASS** |
| PASS | DELIVERY_INCOMPLETE | **NEEDS_REVIEW** |
| PASS | BLOCKED | **BLOCKED** |
| FAIL | 任意 | **FAIL** |
| BLOCKED | 任意 | **BLOCKED** |
| INCOMPLETE / UNKNOWN | 任意 | **NEEDS_REVIEW** |

- `gate_pass=false` 时 `final_status` 禁止为 `PASS`；`TASK_END.status=PASS` 视为状态冲突，
  写入 `conflicts[]`，最终以 gate 为准，并体现在 `TASK_END` 那一行（`recorded` vs `final`）。
- 交付件类 gate 检查（`structured_artifacts_present`、`evidence_present`、`receipt_present`、
  `summary_present`、`raw_evidence_present`）为 false → `DELIVERY_INCOMPLETE`。
- 前置条件类（`prerequisites_met`、`infrastructure_ready`、`device_available`、
  `input_present`、`profile_contract_met`）为 false → `BLOCKED`。
- 没有 gate：`delivery_status=UNKNOWN`，`final_status=NEEDS_REVIEW`。**没有 gate 不得签发 PASS。**
- 状态 gate（profile 声明 `state_catalog.gate` 时追加）：
  - `required_states_present=false` → 前置/证据不满足 → `BLOCKED`；
  - `no_unrecovered_blocking=false` → 阻断状态未恢复 → `BLOCKED`；
  - 只有 `pass_role=positive_evidence` 的状态可作为 `required_states` 的通过证据。

## 7. 责任层与趋势

- `responsibility.layer` 只按规则判定：轮次原因含 PDU/台架 → `bench`；含串口/录音/采集 →
  `device`；仅交付件 gate 未通过 → `tool`；其余 → `unknown` 并列出 `suspects`（待隔离变量）。
- 不允许把"首轮未唤醒"直接归因为设备或语料；先做变量隔离，否则保持 `unknown`。
- 趋势按前一半 vs 后一半比较，变化超过 ±10% 记为 `degrading`/`improving`，不足 4 个样本为
  `insufficient`。`degrading` 必须出现在 `summary.md` 的"异常与趋势"里。

## 8. 校验

```bash
python -m lstest.scripts.lstest eventlog verify --run-dir <dir> [--check-files] [--json]
```

退出码：`0` 无 ERROR；`2` 存在 ERROR。

| code | 级别 | 含义 |
|---|---|---|
| `MISSING_EVENTS` / `EMPTY_EVENTS` / `BAD_EVENTS` | ERROR | 真源缺失或不可解析 |
| `EVENT_SEQ` | ERROR | `event_seq` 不连续或重复 |
| `TIME_FORMAT` / `MISSING_TIME` | ERROR | 时间不是北京时间毫秒格式 |
| `SUMMARY_DRIFT` | ERROR | `summary.json` 与事件重算不一致 |
| `TOOL_LOG_DRIFT` | ERROR | `tool.log` 与事件重算不一致（手工改日志） |
| `TOOL_LOG_FORMAT` | ERROR | 存在续行/空行/非毫秒/未注册标签 |
| `GATE_CONFLICT` / `OUTCOME_CONFLICT` | ERROR | `gate_pass=false` 却 PASS；有 FAIL 却 PASS |
| `ABS_PATH` | ERROR | 产物含绝对路径 |
| `SECRET` | ERROR | 疑似凭据值 |
| `MISSING_RAW` / `RAW_SHA` | ERROR | raw 引用丢失或哈希不符 |
| `SOURCE_STATUS_CONFLICT` | WARN | 源日志自身状态冲突（已按 gate 处理） |
| `STATE_UNREGISTERED` | WARN/ERROR | 设备状态码未登记（`--strict-states` 时为 ERROR） |
| `STATE_UNRECOVERED` | WARN/ERROR | blocking 状态在窗口内未恢复（`--strict-states` 时为 ERROR） |
| `STATE_LATE` | WARN | `device_ts` 早于上一条设备事件 |
| `STATE_PREREQUISITE` | WARN | 缺前置状态（仅 `gate.strict_prerequisites=true` 时报告） |
| `STATE_GATE` | WARN | 状态 gate 检查未通过 |
| `MISSING_EVIDENCE` / `EVIDENCE_SHA` | WARN/ERROR | 证据文件缺失/哈希不符（`--check-files`） |
| `UNKNOWN_EVENT` / `LEGACY_WARNING` | WARN | 旧日志缺字段或未知字段 |

`ToolLogValidator.validate_run` 检测到 `events.jsonl` 时按 v2 校验（含 `tool.log` 单行语法），
否则按 v1 四产物校验；两种目录不能混用。

## 9. 迁移与接入

1. 旧日志原地保留，迁移输出到新目录：

   ```bash
   python -m lstest.scripts.lstest eventlog build \
     --legacy <旧平铺日志> --out-dir <新目录> \
     --meta <任务上下文.json> --markers <设备marker表.json>
   ```

2. `--meta` 只放任务上下文（`title`、`purpose`、`scope`），结论仍全部由事件计算。
3. `--markers` 是项目提供的设备 marker 表（框架不内置项目 marker）：
   `source` 指向事件 raw 字段，`pattern` 用命名捕获组（自动成为 tool.log 的 k=v），
   `host_from` 选择 host 时间基准（`play_start`/`play_end`/`power_on`/`power_on_minus_off`/`record`），
   `state` 写状态转换。示例见 `tests/fixtures/legacy_flat_v1_smoke10.markers.json`。
   marker 的 `code` 必须已在 [state-catalog.md](state-catalog.md) 或 `--state-extension`
   中登记；未登记码降级为 `DEVICE_UNPARSED`，原始码保留在 `details.raw_code`。
   项目私有状态用 `P_` 前缀在扩展中声明；把通用码改义属于违约（加载即拒绝）。
4. 项目状态扩展：`--state-extension <扩展.json>`（`lstest.state-extension.v1`），
   或在 profile 的 `state_catalog` 字段内联声明。`eventlog build/render/verify` 必须使用
   同一份扩展，否则 `summary`/`tool.log` 重算会判漂移。
4. 旧日志的 `raw.node/user/python/platform/cwd` 会提升到 `details.environment`（raw 原文仍外置保留）。
5. 项目新代码：通过 `EventLogWriter` 或 `TaskArtifacts(structured_events=True)` 写事件；
   设备行用 `write_device_event()` 提交（未识别时自动记 `DEVICE_UNPARSED`）；
   禁止自行拼 JSONL、禁止写 `tool.log`/`summary.*`。
6. 结构化运行期间实时账本写 `tool.live.log`，收尾由框架渲染 `tool.log` 并覆盖；`tool.log` 是验收对象。
7. 长压任务：`tool.log`/`summary.*` 只在收尾渲染一次；中途看 `tool.live.log` 或 `MILESTONE`。

## 10. 时间契约（毫秒）与全局影响

**lstest 口径**：所有日志行——`tool.log`、`events.jsonl` 的 `time`、串口包装事件、
audio/PDU/runner 事件、判断日志——必须是北京时间 `YYYY-MM-DD HH:MM:SS.mmm`
（实现：`core.now_log()`，`FMT_LOG`）。

记录字段（manifest/receipt/results.csv/文件名时间戳）仍按 ark-kb 时间契约使用秒级
`YYYY-MM-DD HH:MM:SS`（`core.now_text()`），本次不改，避免连带破坏既有工具。
v1 旧任务的 `tool.log` 保持秒级仅为兼容读取；新任务（v2）不产生秒级日志。

- 设备原始行保留设备自身时钟原文；`serial_logs/*.log` 每行前缀是 **host 接收时间（毫秒）**，
  形如 `[2026-11-12 17:02:22.527] COM4 csk #12 <设备原文>`。
- 设备行内的设备时钟继续留在原文，`tool.log` 的对应行附 `device_ts=`。

**NoahsArk 全局时间契约影响清单**（本轮只列影响，不改其他仓库）：

| 对象 | 现状 | 是否属于"日志" | 建议 |
|---|---|---|---|
| `ark-archive` 回执/manifest | `YYYY-MM-DD HH:MM:SS`，明文禁止毫秒 | 记录字段，不是日志流 | 保持；如需毫秒另加 `*_ms` 机器字段 |
| `audio-runtime` / `tts-runtime` time lint | 禁止毫秒 | 是 | 升为允许/要求毫秒（与本次契约同步） |
| `venusahisiburner` / `device-io` | `T` + 毫秒、无时区 | 是 | 把 `T` 改为空格即可满足；毫秒保留 |
| `ark-node` / `asset-tools` | 展示 `YYYY-MM-DD HH:MM:SS`，机器 `+0800` | 展示是日志、机器是字段 | 日志侧补毫秒；机器字段不改 |
| `skillhub-sync` | `%Y-%m-%dT%H:%M:%SZ`（UTC+Z） | 是 | 已知违约；改北京时间毫秒 |
| `ark-dispatch` / `ark-kb` 文档时间 | 秒级 | 文档 | 不强制，引用日志时用毫秒 |
| manifest / receipt / 文件名时间戳 | `YYYYMMDD_HHMMSS` | 不是日志 | 不改，避免破坏既有工具 |

结论：**日志一律毫秒；记录字段与文件名时间戳先不动**。跨仓库统一前先按上表逐项确认，
避免把 manifest/receipt 的既定格式连带改坏。

## 11. 发布边界

`raw/`、`serial_logs/` 与旧日志可能含本机绝对路径、内网地址、账号名。公开仓库只能提交
**脱敏后的回归样本**，并在 `tests/fixtures/PROVENANCE.md` 记录原始文件 SHA-256 与替换映射。
`verify` 只扫描 `events.jsonl`、`tool.log`、`summary.md`、`summary.json`，不扫描 `raw/`。
