<!-- 由 tools/parity/harness.py 生成；数字来自本次真实运行，禁止手改 -->

# lstest ↔ Polaris 判定一致性报告

本报告由 parity harness 在离线环境直接加载 **真实 Polaris 运行时原语**后生成。
lstest 侧调用 `scripts/assertions.py` / `scripts/attribution.py` / `scripts/coverage.py`，
Polaris 侧调用其 `runtime/` 下的同名能力，两侧输入同一事件序列。

## 运行信息

| 项 | 值 |
| --- | --- |
| 生成时间（北京时间，UTC+8，YYYY-MM-DD HH:MM:SS） | 2026-11-11 09:06:02 |
| 命令 | `python -m tools.parity.harness --fixtures tools/parity/fixtures` |
| fixtures | `tools/parity/fixtures` |
| Polaris runtime | `/home/bszheng/multica_workspaces/noahsark-c582889dd600/noah-54-b91acc48004c/workdir/noah54/repos/Polaris/satellite/cucumber-agent-testing/runtime` |
| 成功导入的 Polaris 模块 | assertion_dsl, assertion_engine, event_graph, events, state_assertion_dsl, state_coverage_policy, timeline |
| 注入的 stub 包 | 无 |
| 导入失败 | 无 |
| 是否含注入对照用例 | 否 |

## 汇总

| 指标 | 数值 |
| --- | --- |
| 场景数 | 7 |
| 对照项总数 | 31 |
| 一致 | 31 |
| 不一致 | 0 |
| 覆盖的合同 kind | 11/11 |
| 缺失的合同 kind | 无 |
| lstest 结论分布 | FAIL=15，PASS=13，SET:0=1，SET:1=2 |

## 状态映射（显式）

| Polaris | lstest | 依据 |
| --- | --- | --- |
| `PASS` | `PASS` | 同名同义：证据满足期望。 |
| `FAIL` | `FAIL` | 同名同义：证据违反期望。 |
| `BLOCKED` | `BLOCKED` | 同名同义：缺少执行前提，不能判定。 |
| `SKIP` | `SKIP` | 同名同义：证据不足但不构成失败。 |
| `SKIPPED` | `SKIP` | Polaris 覆盖策略无任何检查项时返回 SKIPPED，等价于 lstest SKIP。 |
| `TIMING_AMBIGUOUS` | `AMBIGUOUS` | Polaris 表示时序无法确认；lstest 用 AMBIGUOUS 表达同一含义。 |
| `WARN` | `AMBIGUOUS` | Polaris 的非致命偏离信号；lstest 用 AMBIGUOUS 保留“不可判 PASS”的语义。 |
| `PASS_WITH_SKIPPED_TIMING` | `AMBIGUOUS` | 有时序项被跳过，不能算干净的 PASS，降级为 AMBIGUOUS。 |

未登记的新 Polaris 状态一律映射为 `AMBIGUOUS`，不会静默变成 `PASS`。

## 逐 kind 结果

| kind | 对照项 | 一致 |
| --- | --- | --- |
| `attribution_graph` | 2 | 2 |
| `count_range` | 3 | 3 |
| `coverage_states` | 1 | 1 |
| `duration` | 2 | 2 |
| `event_absent` | 2 | 2 |
| `event_exists` | 4 | 4 |
| `event_not_during` | 2 | 2 |
| `event_order` | 4 | 4 |
| `event_within_ms` | 2 | 2 |
| `no_reboot` | 2 | 2 |
| `response` | 2 | 2 |
| `sequence` | 3 | 3 |
| `state_required` | 2 | 2 |

## 逐项结果

| 场景 | 对照项 | kind | 模式 | lstest | polaris | 一致 |
| --- | --- | --- | --- | --- | --- | --- |
| happy_path | wake_present | `event_exists` | status | `PASS` | `PASS` | 是 |
| happy_path | wake_present_from_ap | `event_exists` | status | `PASS` | `PASS` | 是 |
| happy_path | exception_absent | `event_absent` | status | `PASS` | `PASS` | 是 |
| happy_path | wake_before_asr | `event_order` | status | `PASS` | `PASS` | 是 |
| happy_path | asr_within_3s | `event_within_ms` | status | `PASS` | `PASS` | 是 |
| happy_path | no_exception_during_wake | `event_not_during` | status | `PASS` | `PASS` | 是 |
| happy_path | command_chain | `sequence` | status | `PASS` | `PASS` | 是 |
| happy_path | ack_to_media_completed | `duration` | status | `PASS` | `PASS` | 是 |
| happy_path | request_response | `response` | status | `PASS` | `PASS` | 是 |
| happy_path | wake_count | `count_range` | status | `PASS` | `PASS` | 是 |
| happy_path | player_active_state | `state_required` | status | `PASS` | `PASS` | 是 |
| happy_path | no_runtime_reboot | `no_reboot` | status | `PASS` | `PASS` | 是 |
| happy_path | attribution_wake_triggers_asr | `attribution_graph` | set | `SET:1` | `SET:1` | 是 |
| happy_path | coverage_required_states | `coverage_states` | set | `SET:1` | `SET:1` | 是 |
| slow_chain_fail | asr_within_3s | `event_within_ms` | status | `FAIL` | `FAIL` | 是 |
| slow_chain_fail | wake_to_player_within_4s | `duration` | status | `FAIL` | `FAIL` | 是 |
| slow_chain_fail | ordered_chain_broken | `sequence` | status | `FAIL` | `FAIL` | 是 |
| missing_events_fail | wake_present | `event_exists` | status | `FAIL` | `FAIL` | 是 |
| missing_events_fail | wake_before_asr | `event_order` | status | `FAIL` | `FAIL` | 是 |
| missing_events_fail | wake_count | `count_range` | status | `FAIL` | `FAIL` | 是 |
| missing_events_fail | player_active_state | `state_required` | status | `FAIL` | `FAIL` | 是 |
| missing_events_fail | no_runtime_reboot | `no_reboot` | status | `FAIL` | `FAIL` | 是 |
| forbidden_event_fail | no_exception_during_wake | `event_not_during` | status | `FAIL` | `FAIL` | 是 |
| forbidden_event_fail | exception_absent | `event_absent` | status | `FAIL` | `FAIL` | 是 |
| forbidden_event_fail | wake_before_player | `event_order` | status | `PASS` | `PASS` | 是 |
| order_broken_fail | wake_before_asr | `event_order` | status | `FAIL` | `FAIL` | 是 |
| order_broken_fail | ordered_chain_broken | `sequence` | status | `FAIL` | `FAIL` | 是 |
| order_broken_fail | attribution_no_edge_when_reversed | `attribution_graph` | set | `SET:0` | `SET:0` | 是 |
| response_timeout_fail | request_response | `response` | status | `FAIL` | `FAIL` | 是 |
| count_range_exceeded_fail | wake_count | `count_range` | status | `FAIL` | `FAIL` | 是 |
| count_range_exceeded_fail | wake_present_min_4 | `event_exists` | status | `FAIL` | `FAIL` | 是 |

## 已知差异（未参与对照的原因）

- **缺少锚点事件**：lstest `event_within_ms`/`duration` 在 from/start 缺失时判 FAIL(`missing_event`)；Polaris `assert_event_within_ms` 在锚点缺失时返回 SKIP。两者语义不同，shipped fixtures 一律提供双侧事件，因此该差异不参与本次对照。
- **`response.match_capture` 关联**：Polaris 没有按 capture 关联请求/响应的原语，只有 `_assert_response` 的时间分量。本次只对照时间判定；关联相等（correlation）是 lstest 独有语义，仅在关联成立的 PASS 用例上与 Polaris 结论一致，关联不符的 FAIL 用例不在对照范围内。
- **通配因果规则**：lstest `DEFAULT_CAUSAL_RULES` 的 `RESTART|DEVICE_EXCEPTION -> *` 通配规则需要显式目标集合才能落到 Polaris 的 rule overlay；通配边不参与本次图对照，只对照显式 from/to 规则产生的边。
- **`coverage.py` 的角色/能力维度**：Polaris 没有 role 概念，也没有 capability matrix 原语（它从 env 推导 capability，语义不同），因此覆盖对照只比较状态维度（required states）的缺失集合。
- **缺少时间戳**：Polaris `Timeline._normalize_monotonic_clock` 会给无时间戳事件补一个序号时钟，而 lstest 的时序规则在缺 ts 时判 SKIP(`no_timestamp`)。两者不可比，fixtures 全部带 ts_ms。
- **状态映射**：`WARN`/`TIMING_AMBIGUOUS` 统一映射为 lstest `AMBIGUOUS`：它们都表示“证据不足以判定”而不是通过；未登记的新 Polaris 状态同样降级为 `AMBIGUOUS`，绝不静默转成 PASS。

## 检测器自检

带 `--expect-mismatch` 运行会临时注入一个故意写错的对照场景（不落盘为 fixture），
harness 必须报告该项不一致并以退出码 1 结束；`tests/test_parity_polaris.py` 会断言这一点。

```bash
<venv>/bin/python -m tools.parity.harness --expect-mismatch   # 期望 exit 1
```

## 复现

```bash
# 在 lstest 仓库根目录（推荐）
cd <repos>/lstest && PYTHONPATH=<repos> <venv>/bin/python -m tools.parity.harness --fixtures tools/parity/fixtures
# 或从 repos 目录用完整包名
cd <repos> && PYTHONPATH=<repos> <venv>/bin/python -m lstest.tools.parity.harness --fixtures lstest/tools/parity/fixtures
cd <repos> && PYTHONPATH=<repos> <venv>/bin/python -m unittest discover -s lstest/tests -t .
```

Polaris checkout 缺失时 harness 以退出码 2 结束并说明原因，不会伪造 Polaris 结论。

