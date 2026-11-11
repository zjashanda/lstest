# FA2 命令-蜂鸣器期望表：溯源与边界

本目录保存 FA2 命令-蜂鸣器期望表的逐字节副本：

- `fa2-command-beep-expectation.json`
- `fa2-command-beep-expectation.csv`
- `fa2-command-beep-expectation.md`

## 溯源

| 项 | 值 |
| --- | --- |
| Polaris 来源 | `docs/wiki/voice-validation/fa2-command-beep-expectation.json` |
| Polaris 证据提交 | `a4ef61478f343e599282ba3aac4cc8705fbdae4a` |
| lstest 基线提交 | `8f800b40e4742921d21f9a4c3afb8fa77d58a78f` |
| 原始数据来源 | `docs/fa2命令词.txt` |
| JSON schema | `polaris.fa2_command_beep_expectation.v1` |
| 上传方式 | 文件内容原样复制，未改写、未补充、未删除行 |

`json_output` / `csv_output` 字段仍保留 Polaris 原始 Windows 路径，作为产物来源线索；不要据此访问文件。

## 行数与计数证明

JSON 顶层 `command_count` 为 **343**，`rows` 数组长度也是 **343**；CSV 在 UTF-8 BOM 表头之外有 **343** 条数据行。
JSON `counts` 与按 `rows` 重算的分布逐项一致：

```json
{
  "command_kind": {
    "power_control": 2,
    "mode_control": 6,
    "temperature_control": 38,
    "airflow_control": 102,
    "feature_toggle": 83,
    "volume_control": 13,
    "timer_control": 19,
    "online_or_general": 75,
    "query": 4,
    "network_query_or_setup": 1
  },
  "beep_expectation": {
    "expected_if_state_changes": 263,
    "not_required": 80
  },
  "noop_expectation": {
    "not_expected_if_noop": 263,
    "not_required": 80
  }
}
```

各类别合计分别为 343、343、343。`tests/test_migrated_cases.py` 会断言：

- `command_count == 343`；
- `counts` 中每个分布等于按 `rows` 重算的 `Counter`；
- CSV 数据行数等于 JSON `rows` 长度，并与 JSON 的 `case_id` 顺序一致；
- 343 条 `case_id`（`F001`…`F343`）无重复。

## 这张表是什么

它是**命令与蜂鸣器反馈的期望口径表**：

- `expected_if_state_changes`：控制类命令只有发生状态变化时才期望执行/蜂鸣反馈；
- `not_expected_if_noop`：重复打开/重复关闭、已是当前模式等 no-op 场景不强制蜂鸣器；
- `not_required`：查询、联网状态、媒体/问答等不要求执行机构或蜂鸣器；
- `unknown_need_project_rule`：需要项目私有规则或更多日志 marker 后再提升为强断言。

## 这张表不能证明什么

1. **不是实测结果**：表中没有一条来自物理蜂鸣器、声学回采或设备日志的“已蜂鸣”记录。
2. **不能证明 PASS**：缺少蜂鸣器证据时只能归为 `UNKNOWN` 或 `evidence_gap`，不能伪造成 PASS。
3. **不是识别率**：它不记录 ASR 是否正确、命令是否被识别，只记录命令类型对应的蜂鸣**期望**。
4. **不是无条件要求**：`expected_if_state_changes` 是条件期望；no-op、拒绝、不支持时不强制蜂鸣。
5. **没有设备/项目绑定**：表未声明具体项目、固件版本或设备拓扑，不能据此推断某台设备的表现。
6. **没有 FA2 profile**：`profiles/` 下不存在 FA2 profile，也没有 FA2 串口/声卡/云端配置，
   因此这张表目前不能作为可执行 profile 的断言来源；它只是数据与口径基线。
7. **没有阈值**：表不包含时延、成功率、误唤醒率等阈值，迁移后的用例也不得从该表编造阈值。
