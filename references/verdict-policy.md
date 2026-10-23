# Verdict Policy v1

判定结果使用确定性代数，不允许项目适配器自由写入互相矛盾的最终状态。

## 状态

- `PASS`：所有 required 层通过。
- `FAIL`：至少一个 required 层失败。
- `BLOCKED`：required 层被阻塞，或显式提供 `blocked_by`。
- `AMBIGUOUS`：required 层证据不完整或无法判定。
- `PARTIAL`：required 层通过，但 advisory 层未通过。
- `SKIPPED`：case 未执行。
- `ABORTED` / `STOPPED`：用户停止或任务中断；保留原始语义，不伪装成 PASS。

旧状态 `EXPECTED` 在生成 verdict 时归一为 `PASS`，但原始 `results.csv.final_status` 仍保留原值。

## 判定层

v1 固定支持两层：

| 层 | 用途 | 默认 |
| --- | --- | --- |
| `functional` | 功能正确性、证据完整性 | required |
| `timing` | 时序、延迟、窗口 | 可选 |

每层包含 `id`、`status`、`required`、`owner`、`reason`。`owner` 只能是：

`firmware`、`device`、`audio`、`cloud`、`config`、`test-design`、`infrastructure`、`unknown`。

## 聚合顺序

1. 任意 required `FAIL` → `FAIL`。
2. `blocked_by` 存在 → `BLOCKED`。
3. 任意 required `BLOCKED` → `BLOCKED`。
4. 任意 required `AMBIGUOUS` → `AMBIGUOUS`。
5. 任意 advisory 非 `PASS` → `PARTIAL`。
6. 所有 required `PASS` → `PASS`。
7. 其余 → `SKIPPED`。

`anomaly_codes` 会去重并保留在 verdict 中；阻塞原因写入 `blocked_by`。

## 持久化

每个 `results.csv` 行的 `facts_json` 内增加：

```json
{
  "_lstest": {
    "schema_version": 1,
    "verdict": {
      "status": "PASS",
      "layers": [
        {"id": "functional", "status": "PASS", "required": true, "owner": "firmware", "reason": ""}
      ],
      "anomaly_codes": [],
      "primary_reason": ""
    }
  }
}
```

`final_status` 与 `_lstest.verdict.status` 必须一致；否则 `result validate` 报 `RESULT_VERDICT_CONFLICT`。

## 结果校验退出码

```powershell
python -u lstest/scripts/lstest.py result validate <run-dir> --json
```

- `PASS`：退出码 0。
- `FAIL`：退出码 2。
- `BLOCKED`：退出码 3。
- 必需能力缺失时，`preflight --capabilities` 返回 `BLOCKED` 和退出码 3。
- 显式传入 `--allow-blocked` 时，`result validate` 对 `BLOCKED` 返回 0，便于人工审核后继续流水线；默认仍返回 3。

## 单轮验收模式映射

`interaction_acceptance.round_verdict()` 将 8 个验收模式统一映射到 `functional` 层：

| 模式 | 单轮通过条件 |
| --- | --- |
| `wake_only` | 必须唤醒 |
| `wake_playback` | 唤醒 + 唤醒播报完整 |
| `strict` | 唤醒 + 严格命中词表 |
| `strict_playback` | 严格命中 + 唤醒/识别播报完整 |
| `any_asr` | 唤醒 + 至少有一条识别结果 |
| `any_asr_playback` | 任意识别 + 播报完整 |
| `asr_optional` | 单轮允许 `empty`，任务级至少一次 `hit`/`misheard` |
| `asr_optional_playback` | 识别容错 + 播报完整 |

单轮结果使用 `hit`、`misheard`、`empty`、`asleep`；`asleep`、严格模式误识别、多结果、缺少播报都会映射为 `functional` FAIL。任务级 `run_verdict()` 负责 `asr_optional` 的“全程至少一次非空”约束。
