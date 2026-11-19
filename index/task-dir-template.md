# 任务目录模板（lstest 负责建）

> 归属：`lstest` 建目录与结果结构；`burner` 只往 `result/` 写烧录产物。
> 与 NoahsArk 通用工作目录规则一致：每个任务一个目录，用 Issue 编号，不共用、不套用。

## 1. 目录

```text
tasks/<task-id>/
  plan.md              # 计划、知识查询、阶段状态、证据路径
  manifest.json        # 任务身份：issue/run/agent/node/skill 版本/profile/固件
  assets/              # 输入附件与材料清单（大文件只登记路径+sha256）
  work/                # 仓库 checkout、临时工作区
  logs/                # 执行日志（后台通道输出、启动器记录）
  result/              # 结果目录（v3 布局）
  receipts/            # 回执（run.receipt.json、events/、candidates/、evidence-gaps/）
```

`result/` 采用 v3：

```text
result/
  README.md
  manifest.json
  summary.md
  receipt.json
  events/events.jsonl
  events/tool.log
  logs/
  reports/
  artifacts/
  evidence/
  milestones/
```

## 2. 烧录任务的产物落点（burner）

```text
result/
  steps/{preflight,boot-*,burn-*,verify-*,recover-*}.json
  tool_logs/tool_*.log
  serial_logs/*.log
  reset_log.jsonl
  run.receipt.json
```

burner 不建 `result/` 根结构，也不写 `summary.*`；它只写自己的步骤回执与日志，
收口判定由 lstest 的 `eventlog`/`result` 命令完成。

## 3. 回执必须记录

- `knowledge_snapshot`（source_commit / indexed_commit / freshness / entries / degraded）
- skill 与版本（`burner@x`、`lstest@y`）与 `capability_id`
- `profile` 与固件 `sha256`
- 拓扑与端口来源（device-io 文档路径 + schema）
- 命令入口、退出码
- 证据引用三件套：`uri + bytes + sha256`
