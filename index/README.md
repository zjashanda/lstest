# lstest/index · 测试能力固定索引

> Skill：`lstest`（仓库 `zjashanda/lstest`）
> Schema：`ark.skill-index.v1`（机器）/ `ark.skill-route.v1`（单条能力）
> 维护：只加不删；改能力必须同时改 `index.json` 与 `SKILL.md`。

## 1. 智能体必须先读索引

```
lstest/index/manifest.json → lstest/index/index.json → capability → project/profile → workflow → cases
```

禁止按项目名猜 profile、case pack 或判定阈值；`cases/` 是期望值唯一来源，`profiles/` 是项目差异唯一来源。

## 2. 分流：测试 vs 烧录

| 任务 | 归属 |
|---|---|
| 测试逻辑、用例、断言、稳定性、音频、网络、串口证据、报告编排 | `lstest` |
| 固件写入、换版本写入、FTM 恢复 | `burner` |
| 烧录 + 验证 | `burner` → `lstest`（能力不复制） |

`lstest` 不实现烧录；需要烧录时由编排层先调 `burner`，再把烧录回执作为前置证据。

## 3. 当前能力

| capability_id | workflow | 状态 | 备注 |
|---|---|---|---|
| `test.profile-admission` | `wf.lstest.profile-admission` | active | 只读；draft profile 只到 preflight |
| `test.init-smoke` | `wf.lstest.init-smoke` | active | 必须 `--hardware` |
| `test.wake-command` | `wf.lstest.wake-command` | active | 期望值来自 `cases/packs` |
| `test.online-duplex` | `wf.lstest.online-duplex` | active | 仅 cskwb01 有 pack |
| `test.false-wake` | `wf.lstest.false-wake` | active | 静默窗口 |
| `test.stability-soak` | `wf.lstest.stability-soak` | active | 必须声明停止条件 |
| `test.audio-material` | `wf.lstest.audio-material` | active | 合成只走 lstts |
| `test.serial-evidence` | `wf.lstest.serial-evidence` | active | events.jsonl 才是真源 |
| `test.result-orchestration` | `wf.lstest.result-orchestration` | active | v3 根目录白名单 |
| `test.state-coverage` | `wf.lstest.state-coverage` | active | 不按项目名推断能力 |
| `test.ota-upgrade` | `wf.lstest.ota-upgrade` | **gap** | 只有方法文档，`tools/ota/*.py` 未迁入 |

## 4. 项目与 profile 现状（2026-11-19）

| 项目 | profile | contract_mode | 证据等级 | 能否正式压测 |
|---|---|---|---|---|
| `cskwb01` | 有 | draft | marker-strings-only，未采集真机日志 | 否（BLOCKED_PROFILE_CONTRACT） |
| `venusws63` | 有 | draft | 同上；且没有 case pack | 否 |
| `midea-fa2` | 无 | absent | 只有命令词与期望表 | 否（不得创建 profile） |

结论要如实写进回执：**框架能力 active ≠ 该项目可以正式压测**。

## 5. 前置检查（任何执行之前）

1. profile 存在、`schema_version=2`、`contract_mode` 明确。
2. 端口只来自 `device-io` 快照/role-map 或 profile；解析失败即 BLOCKED，不回落。
3. `coverage_policy` 的 minimum 角色缺失 → `BLOCKED`。
4. 正式压测要求 `contract_mode=formal`；否则只跑 fixture/preflight。
5. `--hardware` 才允许打开串口与声卡。

## 6. 证据

- 机器真源：`events/events.jsonl`；`events/tool.log` 是渲染物
- 根目录：`README.md`、`manifest.json`、`summary.md`、`receipt.json` + 六个固定子目录
- 禁止：`.bin`、`task.log`、`tool_logs`、临时结果文件
- 关联：`ark-kb` 条目的 `skill_ref=lstest`、`capability`、`profile`、`workflow`

## 7. 新增能力的最小步骤

1. `index.json` 的 `capabilities` 加一条（14 字段全填），状态只能是 `active` / `draft` / `gap`，缺工具就写 `gap`。
2. 新流程加 `workflows`，`entry` 必须是仓库里真实可执行的命令。
3. 新项目：profile + case pack + adapter 三件套齐全后才登记 `case_packs`。
4. 跑 `python -m unittest discover -s tests -v` 与 `python -u scripts/lstest.py preflight --profile profiles/<p>/profile.json`。
