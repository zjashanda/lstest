# Profile 合同

正式压测必须使用 `contract_mode: "formal"` 的项目 profile。profile 是项目日志解释的唯一来源，框架不从字段名、marker 文本或捕获顺序推断语义。

```json
{
  "schema_version": 2,
  "profile_id": "<project-profile-id>",
  "contract_mode": "formal",
  "event_rules": [{
    "rule_id": "<project-rule-id>",
    "event_type": "<registered-event-type>",
    "sources": {"sources": ["<source>"], "ports": ["<port>"], "roles": ["<role>"]},
    "regex": "<project-regex-with-named-captures>",
    "presentation_capture": "<one-named-capture>",
    "stage": "<case-stage>",
    "correlation": {"identity_capture": "<optional-named-capture>"},
    "mirror_policy": "distinct|mirror|reject_ambiguous",
    "required_for": ["<case-stage>"],
    "empty_placeholder": "",
    "fixtures": {"positive": [{"text": "<matching-log>", "presentation": "<raw-value>"}], "negative": [{"text": "<nonmatching-log>"}]}
  }]
}
```

允许的 `event_type` 由 `scripts/profile.py` 的 `EVENT_REGISTRY` 登记，只代表测试流程类别。项目不得自行制造 tool.log key。播放器类别额外声明 `state_class`（`preparation`、`active`、`terminal`、`error`、`unknown`）和 `render_policy`；它们不改变 `presentation_capture` 的原始值。

安全停止放在 `safety_stop`，必须引用 `safety_eligible: true` 的规则，带 `reason`、`risk_category: device|data|person` 和正反 fixture。识别失败、超时、重试耗尽、重复结果和普通业务错误不能声明为安全停止。

正式准入会验证命名捕获、唯一展示捕获、来源、阶段、关联、镜像、空值、正反 fixture 和安全停止 fixture；任一缺失即 `BLOCKED_PROFILE_CONTRACT`。

## 状态目录扩展（`state_catalog`）

profile 可声明项目私有的设备状态码与别名，格式是 `lstest.state-extension.v1`：

```json
{
  "profile_id": "<project-profile-id>",
  "state_catalog": {
    "states": [{
      "code": "P_TCL_SETLOGLEV_OK",
      "category": "mode",
      "kind": "device",
      "label_zh": "日志级别设置成功",
      "pass_role": "diagnostic",
      "duplicate_policy": "merge"
    }],
    "aliases": [{"raw_code": "WAKE_WORD_HIT", "maps_to": "WAKEUP"}],
    "enabled_markers": ["wakeupkw", "asrkw"],
    "not_applicable": ["online_asr", "online_tts"],
    "gate": {"required_states": ["READY", "WAKEUP"], "max_unrecovered_blocking": 0}
  }
}
```

规则：

- `code` 必须是 `P_` 前缀；不得覆盖通用状态；`profile_id` 由 profile 自动补齐且必须一致。
- `aliases` 只翻译语义码，原始 marker 文本仍保留在 `details.parameters`/`details.raw_code`。
- `enabled_markers` 只列本项目真实出现的 marker；项目没有的能力写入 `not_applicable`，
  coverage 标 `NOT_APPLICABLE`，不得补假状态。
- marker 可声明 `display`（category/stage/primary/fields），tool.log 只按声明显示业务值。
- 未在通用目录或本扩展登记的码 → `DEVICE_UNPARSED`；正式准入时扩展非法即
  `BLOCKED_PROFILE_CONTRACT`（退出码 2）。
- 状态语义、PASS gate 与时序规则见 [state-catalog.md](state-catalog.md)。

## ports 与 `--device-snapshot`

`ports` 是 `{port, role, baudrate, capabilities}` 数组，role 是 profile 内唯一的端口身份。端口号可以写死用于历史项目，但这不是推荐做法：设备事实的唯一来源是 `device-io`。

给 `preflight` / `init` / `smoke` 加 `--device-snapshot FILE` 后，`ports[]` 里声明的每个 role 都必须由该文档解析出端口，并覆盖 profile 中的 `port` 字段：

```json
{"ports": [{"role": "ap", "port": "COM14", "baudrate": 921600, "capabilities": ["serial_log"]}]}
```

- `ark.device-role-map.v1`：只接受 `state` 为 `resolved`/`pinned` 的 role，端口取 `roles[role].port`。
- `ark.device-io.v1`：用上面声明的 `port`（或可选 `interface`）与 `ports[].device`/`ports[].interface` 做大小写不敏感精确相等匹配，0 条命中即 `missing`，多条命中即 `ambiguous`。
- 任一 role 缺失、歧义、冲突或顶层 `ok: false` 时退出码为 `2`，stderr 给出含 role、state、reason 的一行 JSON；不会回落到 profile 端口。
- 不传该参数时行为不变，profile 里的 `port` 与 `--port` 参数照旧生效。
