# 结构化事件日志 v2（人机双读与判定一致性）

本文是 v2 的规范正文。v1（`tool.log` 四产物）仍被读取和校验，见
[result-log-standard.md](result-log-standard.md)。v2 不是"再写一份摘要"，而是把
**机器真源、人读时间线、结论**三层拆开，并强制两者可互相重算。

## 1. 为什么需要 v2

旧版 29 字段平铺块把每个事件写成 29 行（`time:` / `level:` / … / `details:`），
空字段用 `-` 占位。实证：55 个事件 = 1595 行，且：

- 结论被埋在文本里，人读不到；机器能解析但无法快速检索。
- `status: PASS` 与同一事件的 `gate_pass=false` 可以同时存在，最终状态被写成 PASS。
- 只报最终成功率，首轮失败的趋势被 100% 掩盖。

v2 的硬规则是：**结论只能由事件计算，不能手写；`gate_pass=false` 时最终状态禁止 PASS。**

## 2. 目录结构

```text
result/<timestamp>_<task>/
  events.jsonl          # 机器真源：一行一个事件，固定 schema
  raw/                  # 外置 raw：e<seq>_<EVENT>.json，含 sha256 引用
  run.log               # 人读紧凑时间线（由 events 确定性渲染）
  summary.json          # 机器可读结论（由 events 确定性生成）
  summary.md            # 人读结论（只读 summary.json）
  serial_logs/*.log     # 设备原始证据（连续、不可改写）
  results.csv           # 每轮一行
  cases.csv             # 设备动作前冻结
  tool.log              # 可选：运行时实时账本（v1 兼容层）
```

- 新任务必须产出 `events.jsonl`、`run.log`、`summary.md`、`summary.json`；`raw/` 按需。
- `serial_logs/`、`results.csv`、`cases.csv` 的职责不变，仍由框架写入。
- `tool.log` 仅在需要实时 tail 时保留；它不是结论来源，也不能替代 `run.log`。

## 3. events.jsonl

一行一个 JSON 对象，UTF-8，字段顺序固定如下（缺失值为 `null`，不得写 `-`）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema` | string | 固定 `lstest.event.v2` |
| `event_seq` | int | 从 1 开始连续递增；写入顺序即物理顺序 |
| `time` | string | 北京时间 `YYYY-MM-DD HH:MM:SS.SSS`，无 `T`/`Z`/偏移 |
| `level` | string | `INFO` / `WARN` / `ERROR` |
| `event` | string | 大写事件名；无法识别时为 `UNKNOWN` |
| `task_id` | string\|null | 任务标识 |
| `round` | int\|null | 轮次 |
| `case_id` | string\|null | 用例/场景 |
| `phase` | string\|null | 阶段（`power_cycle`、`wake_wait`、`record_open`、`round_end` …） |
| `source` | string\|null | 产生事件的工具/脚本 |
| `device` | string\|null | 设备标识 |
| `port_role` | string\|null | 端口/角色，如 `COM4/csk` |
| `action_id` | string\|null | 动作标识 |
| `broadcast_id` | string\|null | 播报标识 |
| `correlation_id` | string\|null | 关联键；缺省时按 `R<round>:<case_id>:<action>` 推导 |
| `status` | string\|null | 事件结果（`PASS`/`FAIL`/`NO_WAKE`/`BLOCKED` …） |
| `reason` | string\|null | 机器原因码 |
| `attempt` / `max_attempts` | int\|null | 尝试次数 |
| `elapsed_ms` | number\|null | 耗时 |
| `rule_id` | string\|null | 判定规则标识（不得出现在 run.log） |
| `expected` | object\|null | 期望值 |
| `actual` | object\|null | 实际值（gate 放在这里） |
| `normalized` | object\|null | 归一化派生值 |
| `handling` | string\|null | `continue` / `stop_round` / `abort` … |
| `evidence` | array | `[{path, line?, sha256?, external?}]`，path 必须是相对路径 |
| `message` | string\|null | 人类可读短句（原文保留，不翻译） |
| `details` | object\|null | 其余结构化上下文；路径字段必须相对化 |
| `raw_ref` | object\|null | `{path, sha256, bytes, lines}` 指向 `raw/` |
| `legacy_warnings` | array | 仅迁移旧日志时出现 |

规则：

1. **禁止内嵌 raw。** 设备原始行、工具 stdout/stderr、`wake_raw` 等全部写入
   `raw/e<seq>_<EVENT>.json`，事件只保留 `raw_ref`（含 sha256，可复核）。
2. **禁止绝对路径。** `details` 中的 `*_dir`、`*_path`、`wav`、`cwd` 等路径字段写入前
   收敛为"`result/` 之后的相对路径"或文件名；`raw/` 例外（证据原文，不进入公开仓库）。
3. **evidence 只做指针。** 大对象用 `path + line + sha256`，不复制内容。
4. **`-` 不是值。** 缺失一律 `null`，避免机器把占位符当字符串。
5. `raw` 达到任何大小都外置（默认 `--raw-min-bytes 0`）。

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

`actual.gate` 形状（布尔检查 + 汇总位）：

```json
{
  "smoke10_all_pass": true,
  "structured_artifacts_present": false,
  "gate_pass": false,
  "evaluated_at": "2026-11-12 17:07:09.372"
}
```

也接受 `{"gate_pass": false, "checks": {"...": false}}` 的嵌套写法。

## 4. run.log

由 `events.jsonl` 确定性渲染，**任何一行都不得手工修改**；`verify` 会重算比对。

```text
# lstest run.log v2 | task=<task_id> | events=<n> | rounds=<completed>/<target>
# 测了什么: <title 或 purpose>
# 结论: test_outcome=<..> delivery=<..> final=<..>（依据与证据见 summary.md）
R01 17:02:22.527 PDU_CYCLE      PASS elapsed_ms=24326 ...
    17:02:25.183 BROADCAST      NO_WAKE attempt=1/10 ... | 第 1 次播报未唤醒
                    └ wake_seen=false start_seen=false gap_s=1 window_s=1
...
SUMMARY rounds=10/10 pass=10 fail=0 blocked=0 round_success_rate=100% first_attempt_success_rate=0%
SUMMARY 异常: NO_WAKE ×10
SUMMARY 责任层=<layer>（<basis>）
SUMMARY 缺口: <缺失项>
SUMMARY 下一步: <一步一条>
```

规则：

- 每个事件 1 行；只有非成功状态（`NO_WAKE`/`FAIL`/`BLOCKED` …）追加 1 行 `└` 细节（最多 2 行）。
- 空字段、`raw` 路径、超长 JSON 不显示；同一 key 只显示一次，`actual` 覆盖 `expected`。
- 顶部 3 行回答"测了什么 + 结论"，底部 `SUMMARY` 回答"比例、异常、责任层、缺口、下一步"。
- 完整推理、gate 表、证据索引只在 `summary.md`，避免两个摘要各写一套结论。
- `rule_id`、正则、捕获字段名、绝对路径不得出现。

## 5. summary.json / summary.md

`summary.json` 的 `schema` 为 `lstest.summary.v2`，字段分四组：

| 组 | 字段 | 说明 |
|---|---|---|
| 元信息 | `meta`、`task_id`、`bundle_version` | 生成时间、来源日志 sha256、事件数 |
| 判定 | `test_outcome`、`test_outcome_basis`、`delivery_status`、`delivery_failed_checks`、`final_status`、`final_status_rule`、`gate`、`conflicts` | 唯一结论来源 |
| 统计 | `counts`、`rates`、`latency`、`milestone`、`anomalies`、`first_failure`、`worst_round` | 全部由事件计算 |
| 上下文 | `environment`、`tooling`、`inputs`、`parameters`、`responsibility`、`missing`、`next_steps`、`evidence_index` | 缺失即 `null`/`[]`，不猜 |

`rates` 必须同时包含：

- `round_success_rate`：PASS 轮次 / 完成轮次。
- `first_attempt_success_rate`：`first_success_attempt == 1` 的轮次 / 完成轮次。
- `first_attempt_gap_pct`：最终成功率与首轮成功率之差（百分点），用于暴露"最终 100% 掩盖首轮全失败"。
- `attempt_distribution`：第 1..N 次成功各多少轮、占比，`none` 表示 N 次均未成功。

只报 `round_success_rate` 视为摘要不合格（`verify` 会判 `SUMMARY_DRIFT`）。

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
  写入 `conflicts[]`（`TASK_END_STATUS_CONFLICT`），最终以 gate 为准。
- `delivery_status` 的 gate 检查项分类：
  - 交付件类（为 false 判 `DELIVERY_INCOMPLETE`）：`structured_artifacts_present`、
    `evidence_present`、`receipt_present`、`summary_present`、`raw_evidence_present`。
  - 前置条件类（为 false 判 `BLOCKED`）：`prerequisites_met`、`infrastructure_ready`、
    `device_available`、`input_present`、`profile_contract_met`。
  - 其余业务检查为 false 判 `DELIVERY_INCOMPLETE` 并从 `missing[]` 列出。
- 没有 gate：`delivery_status=UNKNOWN`，`final_status=NEEDS_REVIEW`。**没有 gate 不得签发 PASS。**

## 7. 责任层与趋势

- `responsibility.layer` 只按规则判定：轮次原因含 PDU/台架 → `bench`；含串口/录音/采集 →
  `device`；仅交付件 gate 未通过 → `tool`；其余 → `unknown` 并列出 `suspects`（待隔离变量）。
- 不允许把"首轮未唤醒"直接归因为设备或语料；必须先做变量隔离，否则保持 `unknown`。
- 趋势按前一半 vs 后一半比较，变化超过 ±10% 记为 `degrading`/`improving`，不足 4 个样本为
  `insufficient`。`degrading` 必须出现在 `summary.md` 的"异常与趋势"里。

## 8. 校验

```bash
python -m lstest.scripts.lstest eventlog verify --run-dir <dir> [--check-files] [--json]
```

退出码：`0` 无 ERROR；`2` 存在 ERROR。检查项：

| code | 级别 | 含义 |
|---|---|---|
| `MISSING_EVENTS` / `EMPTY_EVENTS` / `BAD_EVENTS` | ERROR | 真源缺失或不可解析 |
| `EVENT_SEQ` | ERROR | `event_seq` 不连续或重复 |
| `TIME_FORMAT` / `MISSING_TIME` | ERROR | 时间不是北京时间毫秒格式 |
| `SUMMARY_DRIFT` | ERROR | `summary.json` 与事件重算不一致（手工改结论） |
| `RUNLOG_DRIFT` | ERROR | `run.log` 与事件重算不一致（手工改日志） |
| `GATE_CONFLICT` / `OUTCOME_CONFLICT` | ERROR | `gate_pass=false` 却 PASS；有 FAIL 却 PASS |
| `ABS_PATH` | ERROR | 产物含绝对路径 |
| `SECRET` | ERROR | 疑似凭据值 |
| `MISSING_RAW` / `RAW_SHA` | ERROR | raw 引用丢失或哈希不符 |
| `SOURCE_STATUS_CONFLICT` | WARN | 源日志自身状态冲突（已按 gate 处理） |
| `MISSING_EVIDENCE` / `EVIDENCE_SHA` | WARN/ERROR | 证据文件缺失/哈希不符（`--check-files`） |
| `UNKNOWN_EVENT` / `LEGACY_WARNING` | WARN | 旧日志缺字段或未知字段 |

`ToolLogValidator.validate_run` 检测到 `events.jsonl` 时按 v2 校验，否则仍按 v1 四产物校验；
两种目录不能混用。

## 9. 迁移

1. 旧日志原地保留，不改写。迁移输出到新的结果目录：

   ```bash
   python -m lstest.scripts.lstest eventlog build \
     --legacy <旧平铺日志> --out-dir <新目录> --meta <任务上下文.json>
   ```

2. `--meta` 只放任务上下文（`title`、`purpose`、`scope`、`inputs[]`），结论仍全部由事件计算。
3. 旧日志的 `raw.node/user/python/platform/cwd` 会提升到 `details.environment`（raw 原文仍外置保留）。
4. 项目侧新代码：通过 `EventLogWriter`（或 `TaskArtifacts(structured_events=True)`）写事件，
   禁止自行拼 JSONL、禁止写 `run.log`/`summary.*`。
5. 长压任务：`run.log`/`summary.*` 只在收尾渲染一次；中途看进度用 `tool.log` 或
   `MILESTONE` 事件，避免每轮重写大文件。

## 10. 发布边界

`raw/` 与旧日志可能含本机绝对路径、内网地址、账号名。公开仓库只能提交**脱敏后的
回归样本**，并在 `tests/fixtures/PROVENANCE.md` 记录原始文件 SHA-256 与替换映射。
`verify` 只扫描 `events.jsonl`、`run.log`、`summary.md`、`summary.json`，不扫描 `raw/`。
