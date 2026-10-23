# Case Contract v1

本文件定义 `cases.csv` 中 `case_contract` 列的可选扩展。目标是把“每个 case 需要什么能力、证据、判定层、依赖和时序”冻结在设备动作之前，同时不增加新的结果列。

## 兼容原则

- 结果目录仍然只有 `serial_logs/*.log`、`tool.log`、`results.csv`、`cases.csv`。
- 旧项目只写 `metadata["timeline"]` 时，`case_contract` 仍保存原始 timeline 对象，不自动包一层命名空间。
- 新项目写 `metadata["case_contract"]` 时，保存完整对象：
  - `timeline`：原有 timeline 字段；
  - `lstest`：本规范新增字段。
- 两种写法都可以被 `DeviceProfile.normalized_case_contract()` 读取。
- `lstest` 是保留键；旧 timeline 字段不需要迁移。

## 新结构

```json
{
  "timeline": {
    "required_facts": ["WAKE_WORD_VERIFIED"]
  },
  "lstest": {
    "contract_version": 1,
    "requirement_ids": ["R-001"],
    "depends_on": ["case-000"],
    "requires_capabilities": ["device-serial"],
    "verdict_layers": [
      {"id": "functional", "required": true, "owner": "firmware"},
      {"id": "timing", "required": false, "owner": "device"}
    ],
    "evidence": {
      "advisory": ["PLAYER_STARTED"],
      "forbidden": []
    },
    "timing": {
      "id": "wake_latency",
      "start_fact": "WAKE_WORD_VERIFIED",
      "end_fact": "PLAYER_STARTED",
      "tolerance_ms": 300,
      "missing_policy": "ambiguous"
    },
    "corpus_id": "wake-corpus-v1"
  }
}
```

## 字段约束

- `contract_version`：当前只支持 `1`。
- `requirement_ids`：非空字符串列表；用于和项目需求表关联。
- `depends_on`：case id 列表；不能依赖自身，依赖必须存在。
- `requires_capabilities`：能力 id 列表；由 `capabilities.json` 提供定义。
- `verdict_layers`：至少一个 required 层；v1 只接受 `functional` 和 `timing`。
- `evidence.advisory` / `evidence.forbidden`：不能冲突，也不能同时作为 required fact。
- `timing`：必须声明 `id`、`start_fact`、`end_fact`；`tolerance_ms` 为非负整数。
- `timeline.required_facts`：必须来自当前 profile 的事件键；`tool.log` 中缺失时按现有逻辑补空值并影响判定。
- `corpus_id`：可选；用于关联独立语料文件。

## 校验入口

```powershell
python -u lstest/scripts/lstest.py preflight --profile <profile.json> --capabilities <capabilities.json>
python -u lstest/scripts/lstest.py result validate <run-dir>
```

`preflight` 只做静态检查和必需能力探测，不打开正式设备动作。`result validate` 只读校验结果目录，不修改任何文件。

## 错误示例

- `CASE_CONTRACT_INVALID`：结构不是对象或字段类型错误。
- `CASE_CONTRACT_UNKNOWN_FIELD`：`lstest` 中出现未声明字段。
- `CASE_CONTRACT_VERSION_UNSUPPORTED`：版本不是 1。
- `CASE_REQUIRED_FACT_UNKNOWN`：required fact 不在 profile 事件键中。
- `CASE_DEPENDENCY_INVALID` / `CASE_DEPENDENCY_UNKNOWN`：依赖自身或依赖不存在。
- `CASE_CAPABILITY_UNKNOWN`：引用了未定义能力。
- `CASE_VERDICT_LAYER_INVALID`：层 id、重复层、owner 或 required 非法。
- `CASE_TIMING_INVALID` / `CASE_TIMING_FACT_UNKNOWN`：时序定义或事实键非法。
- `CASE_CORPUS_UNKNOWN`：引用了未提供 corpus id。
