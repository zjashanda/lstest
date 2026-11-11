# cases 数据层合同

本目录保存从 Polaris 迁移来的**声明式测试用例数据**。用例只描述“应该观察到什么”，不描述“怎么采集”。
采集与解析留在 `profiles/**`，判定与归因留在 `scripts/**`，本目录只消费它们产出的 fact。

## 目录布局

| 路径 | 内容 | 数量 |
| --- | --- | --- |
| `cases/packs/<name>.json` | 7 个 Polaris 验证包（pack）的声明式断言 | 7 |
| `cases/examples/<name>.json` | Polaris `tasks/examples/*.json`（含 `tasks/templates/new_feature.template.json`）逐任务迁移 | 29 |
| `cases/fa2/` | FA2 343 条命令-蜂鸣**期望**表，逐字节复制 | 1 表 |
| `cases/README.md` | 本文件：合同与溯源约定 | — |

## `lstest.case.v1` 合同

每个 case JSON 的顶层键固定为以下 8 个，不得增删改名：

```json
{
  "schema": "lstest.case.v1",
  "case_id": "polaris-pack-first-wake",
  "title": "首次唤醒验证包",
  "profile": "cskwb01",
  "stage": "wake",
  "metadata": {
    "timeline": {
      "required_facts": ["HOST_AUDIO_START", "WAKE"],
      "mirror": "distinct",
      "duplicate": "reject"
    },
    "polaris_source": {
      "task": "first-wake",
      "path": "docs/wiki/voice-validation/packs/first-wake.md",
      "commit": "a4ef61478f343e599282ba3aac4cc8705fbdae4a"
    }
  },
  "assertions": [
    {"name": "wake_marker_present", "kind": "event_exists", "event_type": "WAKE"}
  ],
  "notes": ["原文：……", "来源：docs/wiki/voice-validation/packs/first-wake.md"]
}
```

### 字段约束

| 字段 | 约束 |
| --- | --- |
| `schema` | 固定 `lstest.case.v1` |
| `case_id` | 全局唯一；pack 用 `polaris-pack-<pack-name>`，任务用 `polaris-task-<task-name>`，kebab-case |
| `title` | 面向人的中文标题 |
| `profile` | 只能是 `cskwb01` 或 `venusws63` |
| `stage` | 只能是 `wake` / `recognition` / `command` / `duplex` / `false_wake` / `network` / `attribution` |
| `metadata.timeline.required_facts` | 该用例期望出现的事件 key；纯禁止类用例可为空数组 |
| `metadata.timeline.mirror` | 当前统一 `distinct` |
| `metadata.timeline.duplicate` | 当前统一 `reject` |
| `metadata.polaris_source.task` | Polaris 任务/包的短名 |
| `metadata.polaris_source.path` | Polaris 仓库内相对路径 |
| `metadata.polaris_source.commit` | 固定为 `a4ef61478f343e599282ba3aac4cc8705fbdae4a` |
| `assertions` | 断言数组；元素必须有 `name` 与 `kind`，`name` 在单个 case 内唯一 |
| `notes` | 源文档原文摘录与溯源说明；必须至少有一条以 `来源：` 开头 |

## 断言 kind 白名单

`kind` 只能取以下 11 个值，字段与语义以 `artifacts/absorption-api-contract.md` 的 `scripts/assertions.py` 表为准：

| kind | 主要字段 | 语义 |
| --- | --- | --- |
| `event_exists` | `event_type`, `min_count`, `source` | 事件计数不足则 FAIL |
| `event_absent` | `event_type` | 事件出现则 FAIL |
| `event_order` | `before`, `after`, `strict` | 两个事件顺序反了则 FAIL |
| `event_within_ms` | `from`, `to`, `max_ms`, `min_ms` | 配对事件时延越界则 FAIL |
| `event_not_during` | `event_type`, `window_from`, `window_to` | 窗口内出现禁止事件则 FAIL |
| `sequence` | `steps`, `allow_gaps`, `max_span_ms` | 有序步骤断裂则 FAIL |
| `duration` | `start_event`, `end_event`, `max_ms`, `min_ms` | 区间时长越界则 FAIL |
| `response` | `request`, `response`, `max_ms`, `match_capture` | 请求/响应关联缺失或不匹配则 FAIL |
| `count_range` | `event_type`, `min`, `max` | 计数越界则 FAIL |
| `state_required` | `state_class`, `event_type` | 缺少必需状态则 FAIL |
| `no_reboot` | 无 | 等价于禁止 `RESTART` |

事件名使用 lstest `EVENT_REGISTRY` 的 key（`WAKE`、`ONLINE_ASR`、`COMMAND_ACK` 等）；
播放锚点 `HOST_AUDIO_START` 是采集侧锚点，不是设备业务事件。

## 溯源约定

- `metadata.polaris_source` 必须同时记录 `task`、`path`、`commit`，commit 固定为 Polaris 证据提交
  `a4ef61478f343e599282ba3aac4cc8705fbdae4a`；lstest 侧基线为 `8f800b40e4742921d21f9a4c3afb8fa77d58a78f`。
- pack 的 `path` 指向 `docs/wiki/voice-validation/packs/<name>.md`；
  任务的 `path` 指向 `satellite/cucumber-agent-testing/tasks/examples/<name>.example.json`；
  模板的 `path` 指向 `satellite/cucumber-agent-testing/tasks/templates/new_feature.template.json`。
- 迁移到 `docs/voice-validation/**` 的 markdown 首行统一带
  `<!-- migrated-from: Polaris/<path> @ a4ef614... -->` 溯源注释，由 `tests/test_migrated_cases.py` 校验。
- `notes` 必须保留源文档的中文原文（如“播放返回 0；窗口内出现 wake marker；无 reboot/crash”），
  并至少给出一条 `来源：<path>`。改写或转述不能替代原文。

## 取值原则

1. **只表达源文档明确写出的期望**：顺序、禁止、计数、阈值。
2. **没有文档阈值就不填数字**：不要给 `max_ms`、`max_span_ms`、百分比通过线编造数值；
   宁可只断言顺序/存在性，也不要制造伪 PASS。
3. **配置值不是断言阈值**：任务里的 `observe_ms`、`half_duplex_timeout_s`、`full_duplex_timeout_s`、
   `max_rounds` 只有被源文档明确当作判定条件时才写入断言（例如识别窗口二次唤醒使用
   `recognition_timeout_s=15`）。
4. **不可观测的概念不硬编事件**：云控下发结果、coverage 状态、VAD 切句、蜂鸣器物理反馈等
   当前事件注册表没有对应 key，只写入 `notes`，不伪造断言。
5. **profile 选择**：任务名以 `ws63` 开头，或源任务 `environment.project` / `project_id` 为 `venusws63`，
   归 `venusws63`；其余归 `cskwb01`。

## 自检

```bash
cd <repos>
PYTHONPATH=<repos> <venv>/bin/python -m unittest discover -s lstest/tests -t .
```

`tests/test_migrated_cases.py` 会遍历本目录所有 JSON，校验 schema、必填键、kind 白名单、
`case_id` 唯一性、pack/example 数量（7/29）、FA2 行数与 counts 一致性，以及迁移文档首行溯源标记。
