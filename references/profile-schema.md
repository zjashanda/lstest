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
