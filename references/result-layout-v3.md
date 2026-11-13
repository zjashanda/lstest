# 结果目录布局 v3（最终结构）

本文件定义 lstest 任务结果目录的 canonical 布局。旧布局（v1/v2）、早期过渡布局
（`machine/` + `execution_logs/`）和旧 `cloud_run/` **继续可读**，但新任务只写 v3。

```text
result/<run-id>/
├── README.md                    # 阅读顺序与目录口径
├── manifest.json                # 每个产物的相对路径、大小、SHA-256（+迁移映射）
├── summary.md                   # 人类最终摘要
├── receipt.json                 # 唯一标准回执（ark-archive 签发；未签发则不产生）
├── events/
│   ├── tool.log                 # 完整动作审计：一事件一行、毫秒、TOOL+DEVICE
│   ├── events.jsonl             # 机器真源
│   ├── rounds.jsonl             # 逐轮机器事件（按需）
│   └── tool.live.log            # 运行期实时镜像（收尾后以 tool.log 为准）
├── logs/
│   ├── serial/                  # 串口原始日志
│   ├── runner.log               # runner/脚本日志
│   ├── window.log               # 窗口/隐藏通道
│   ├── cloud.log                # 云端请求/返回
│   ├── STOP                     # 停止标记
│   └── legacy/cloud_run/        # 迁移后的旧 cloud_run（只读兼容）
├── reports/
│   ├── results.csv              # 每 case/round 一行
│   ├── cases.csv                # 用例冻结表
│   ├── summary.json             # 机器最终摘要
│   ├── validation.json          # 产物校验结果
│   ├── preflight.json
│   ├── status.json
│   ├── evidence-summary.json
│   ├── finalize.json
│   └── migration-manifest.json  # 旧布局迁移的 SHA/mtime/原路径
├── artifacts/
│   ├── audio/                   # 测试音频
│   ├── recorder/                # PCM/WAV/录音
│   └── screenshots/             # 截图证据
├── evidence/
│   ├── raw/                     # 外置 raw：e<seq>_<EVENT>.json
│   ├── indexes/                 # 证据索引
│   └── hashes/                  # 证据 SHA 清单
└── milestones/
    └── stage-index.json         # 阶段里程碑索引
```

## 1. 根目录规则

根目录只允许：

- 文件：`README.md`、`manifest.json`、`summary.md`、`receipt.json`
- 目录：`events/`、`logs/`、`reports/`、`artifacts/`、`evidence/`、`milestones/`

机器 JSON 进 `reports/`（`summary.json` 也在此，避免根目录散落），执行日志进 `logs/`，
外置 raw 进 `evidence/raw/`，录音/音频/截图进 `artifacts/`。
`preflight.json`、`status.json`、`validator.json`、`evidence_summary.json`、`finalize.json`、
`rounds.jsonl` **不得**再出现在根目录。

## 2. 阅读顺序

`README.md → summary.md → events/tool.log → receipt.json → events/events.jsonl →
reports/ → logs/ → evidence/ → artifacts/ → milestones/`

- 结论只认 `summary.md` / `reports/summary.json` / `receipt.json`；
- `events/tool.log` 是完整动作审计，不合并串口 raw，只保留语义事件与证据引用；
- 缺 `receipt.json` 表示**未签发回执**，不等于 PASS。

## 3. canonical 路径表

| key | v3 | v2（旧） | v1（旧） |
|---|---|---|---|
| `events` | `events/events.jsonl` | `events.jsonl` | - |
| `tool_log` | `events/tool.log` | `tool.log` | `tool.log` |
| `rounds` | `events/rounds.jsonl` | `rounds.jsonl` | - |
| `summary_json` | `reports/summary.json` | `summary.json` | - |
| `results` | `reports/results.csv` | `results.csv` | `results.csv` |
| `cases` | `reports/cases.csv` | `cases.csv` | `cases.csv` |
| `validation` | `reports/validation.json` | `validator.json` | - |
| `preflight` | `reports/preflight.json` | `preflight.json` | - |
| `status` | `reports/status.json` | `status.json` | - |
| `evidence_summary` | `reports/evidence-summary.json` | `evidence_summary.json` | - |
| `finalize` | `reports/finalize.json` | `finalize.json` | - |
| `serial_dir` | `logs/serial/` | `serial_logs/` | `serial_logs/` |
| `raw_dir` | `evidence/raw/` | `raw/` | - |
| `recorder_dir` | `artifacts/recorder/` | `recorder/` | - |
| `stop` | `logs/STOP` | `STOP` | `STOP` |
| `cloud_run` | `logs/legacy/cloud_run/` | `cloud_run/` | `cloud_run/` |

早期过渡布局（NOAH-71 草案）同样可读：`machine/events.jsonl`、`machine/summary.json`、
`execution_logs/serial_logs/`、`execution_logs/*.log`；读取方无需先迁移。

## 4. Resolver（统一读取入口）

`scripts/result_layout.py` 是唯一的路径契约：

```python
from lstest.scripts import result_layout

result_layout.detect_layout(run_dir)          # v3 / v2 / v1 / v2.5
result_layout.resolve(run_dir, "tool_log")    # canonical → 历史别名，找不到返回 None
result_layout.resolve_required(run_dir, "events")
result_layout.resolve_relative(run_dir, "serial_logs/serial_COM5_ap.log#000123")
result_layout.normalize_reference("serial_logs/a.log")   # → logs/serial/a.log
```

引用映射规则：`serial_logs/ → logs/serial/`、`raw/ → evidence/raw/`、
`machine/ → reports/`、`execution_logs/serial_logs/ → logs/serial/`、
`execution_logs/ → logs/`、`cloud_run/ → logs/legacy/cloud_run/`。

`ToolLogValidator`、`eventlog verify`、receipt、ark-archive、ark-kb evidence 都必须使用
上述函数，禁止自行拼接根目录文件名。跨仓库无法直接 import 时，读取机器可读契约：

```bash
python -u lstest/scripts/lstest.py result schema --output result-layout.json
```

输出的 `lstest.result-manifest.v1` 文档包含 `canonical`、`root_allowed`、`reading_order`、
`legacy_readable` 与解析规则，可直接用于 receipt/ark-archive/ark-kb 的证据路径解析。

## 5. 迁移（只移动，不删除、不覆盖）

```bash
# 只读审计：列出根目录越界项与建议
python -u lstest/scripts/lstest.py result audit --run-dir <结果目录>

# dry-run：输出 moves/conflicts/skipped，文件不动
python -u lstest/scripts/lstest.py result migrate --run-dir <结果目录>

# 执行：逐个校验 SHA → 重命名 → 复核 SHA → 写 migration-manifest.json
python -u lstest/scripts/lstest.py result migrate --run-dir <结果目录> --apply
```

规则：

1. 默认 dry-run；`--apply` 才移动。
2. 目标已存在时记 `conflicts`，绝不覆盖，`ok=false`。
3. 未识别的根内容记 `skipped`（`未归类`），保留原位，人工确认后再处理。
4. 每个移动记录 `source`、`target`、`bytes`、`sha256`（目录为文件清单 + `tree_sha256`）、
   `mtime`、`mtime_after`，写入 `reports/migration-manifest.json`。
5. 迁移后保留原相对路径映射；事件里的旧引用（`serial_logs/…`）仍由 resolver 解析，
   不断证据链、不改写已落盘事件。
6. **不删除任何历史结果**；不自动清理其他任务目录。

## 6. manifest.json

```json
{
  "schema": "lstest.result-manifest.v1",
  "layout": "v3",
  "generated_at": "YYYY-MM-DD HH:MM:SS",
  "task_id": "<run-id>",
  "reading_order": ["README.md", "summary.md", "..."],
  "root_allowed": {"files": ["README.md", "..."], "dirs": ["events", "..."]},
  "entries": [
    {"key": "events", "role": "machine", "kind": "file",
     "path": "events/events.jsonl", "canonical": "events/events.jsonl",
     "bytes": 12345, "sha256": "..."}
  ],
  "migration": {"schema": "lstest.result-migration.v1", "moves": []}
}
```

`eventlog verify` 会核对 manifest 的路径、大小与 SHA-256；被手改会报
`MANIFEST_PATH` / `MANIFEST_SIZE` / `MANIFEST_SHA`。

## 7. 校验矩阵

| 场景 | 期望 |
|---|---|
| v3 新结果 | 根目录只在白名单内；`verify_bundle` ERROR=0 |
| v3 根目录仍有旧 JSON/日志 | `LAYOUT_VIOLATION` ERROR，提示迁移 |
| v2 旧结果（含根 JSON、`cloud_run/`） | `LAYOUT_LEGACY` WARN；可读、可校验 |
| v1 四产物 | 按 v1 边界校验；可读 |
| 无 `receipt.json` | 合法（未签发），不判错 |
| 无 `TASK_END`（中断） | 可读；最终状态 `NEEDS_REVIEW`/`INCOMPLETE` |
| 迁移后旧证据引用 | 由 resolver 解析到新路径，SHA 不变 |

## 8. 生成与兼容

- `EventLogWriter(layout="v3")`（默认）写 `events/events.jsonl` + `evidence/raw/`；
  `layout="v2"` 保留旧根目录布局，供旧读取方/回归使用。
- `eventlog build --layout v3|v2`，`eventlog render --layout …`。
- `TaskArtifacts(structured_events=True)` 默认 v3；`structured_events=False` 保持 v1 四产物，
  避免历史适配器语义漂移。
- 布局只改变路径，不改变 `tool.log` 的行格式、时序和判定；同一份事件在 v2/v3 渲染出的
  `tool.log` 除 raw 路径前缀外逐字节一致。
