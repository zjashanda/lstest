<!-- 本文件由 NOAH-54 交付。Polaris 侧证据 commit: a4ef61478f343e599282ba3aac4cc8705fbdae4a；lstest 基线 commit: 8f800b40e4742921d21f9a4c3afb8fa77d58a78f -->

# Polaris → lstest 吸收映射（逐项）

## 0. 结论摘要

| 类别 | 条目数 | 说明 |
|---|---|---|
| MIGRATE-ENGINE（迁入 lstest 引擎，项目无关） | 5 | 时序断言原语、断言 DSL 语义、事件图归因、状态/能力覆盖、仿真样例概念 |
| MIGRATE-DATA（迁入 lstest 数据层，不改代码） | 6 | 语音验证 wiki、验证包、失败模式、FA2 期望表、项目知识、intake 模板 |
| ADAPT（薄适配，落在 `adapters/`） | 2 | 串口/播放原始记录采集、项目 case 计划 |
| CROSS-REPO（不在 lstest 落地，转其它 canonical） | 6 | device-io、ark-node、ark-dispatch、venusahisiburner、asset-tools、ark-archive |
| REPLACE（被 lstest 既有机制等价取代，不迁移代码） | 4 | 日志解析器、play 插件、polaris.local 机器配置、逐用例 evaluator |
| NON-MIGRATABLE（明确不迁移，附原因） | 7 | 厂商二进制、xlsx、Gherkin runner、完整 Replay VM、分布式运行时、项目名嗅探、Polaris 专属批量 runner |
| DEFER（本轮不迁，等待 canonical 归属） | 3 | 云控工具、OTA 报告工具、分析趋势 |

**净效果**：lstest 增加 3 个引擎模块 + 2 个数据目录 + 2 个薄适配目录；Polaris 不被删除、不被冻结，
在功能验证通过前保持原样可回退。

## 1. 判定原则

1. **能力归位**：项目差异（marker、正则、端口、命令词、业务值）只能进 `profiles/`、`cases/`、`adapters/`；
   公共引擎只保留"测试流程类别"。
2. **不复制公共引擎**：Polaris 中与 lstest 重叠的实现一律不复制；能等价表达的就 REPLACE，
   属于其它 canonical 职责的就 CROSS-REPO。
3. **无证据不迁移**：没有真实日志/实测数据支撑的规则不写成 `formal`，一律 `contract_mode: draft` + `verified: false`。
4. **可回退**：本轮不改 Polaris 任何文件，不删分支、不改 main；lstest 侧只新增文件。

## 2. Polaris 独有能力清单（逐项）

"独有"= 在该 Polaris commit 中存在、且 lstest 基线中无等价实现。

| ID | 能力 | 证据路径 | 规模 | 是否 Polaris 独有 |
|---|---|---|---|---|
| P-A1 | 语音验证方法 wiki（唤醒/命令/全双工/误唤醒/归因/用例设计） | `docs/wiki/voice-validation/*.md` | 14 篇 | 是（lstest 只有 profile 合同，无测试方法知识） |
| P-A2 | 验证包（pack）定义与索引 | `docs/wiki/voice-validation/packs/` + `validation-pack-index.json` | 7 包 | 是 |
| P-A3 | 失败模式库 | `docs/wiki/voice-validation/failure-patterns/` | 3 篇 | 是 |
| P-A4 | FA2 命令-蜂鸣期望表 | `docs/wiki/voice-validation/fa2-command-beep-expectation.{json,csv,md}` | 343 行 | 是 |
| P-A5 | 项目知识（CSK/WB01、VenusA/WS63） | `docs/knowledge/{cskwb01,venusws63}/*.md` | 6 篇 | 是 |
| P-A6 | 新需求 intake 合同模板 | `docs/intake/templates/*` | 2 个 | 是 |
| P-B1 | Gherkin/Cucumber 用例入口 | `features/polaris_voice_core.feature`, `scripts/run_cucumber.py` | 1 feature + runner | 是 |
| P-B2 | step/action/assertion/voice-core 注册表 | `references/{step_registry,action_registry,assertion_registry,voice_core_mapping,feature_contracts}.json` | ~2750 行 | 是 |
| P-B3 | 任务样例（可执行意图） | `tasks/examples/*.json` | 29 个 | 是 |
| P-B4 | 场景 DAG 策略与样例 | `references/scene_strategy_pool.json`, `references/scenes/*` | 1 + 3 | 是 |
| P-C1 | 时序断言原语（存在/窗口/顺序/禁止/聚类/媒体与音频窗口） | `runtime/assertion_engine.py`（原语部分） | ~700 行 | 部分（lstest 只有 judge_* 与 EventClock，无声明式断言） |
| P-C2 | 逐测试项 evaluator | `runtime/assertion_engine.py`（`evaluate_*` 共 9 个） | ~1100 行 | 是（但属项目/测试项逻辑） |
| P-C3 | 断言 DSL-lite（EXPECT/FORBID/SEQUENCE/RESPONSE/DURATION） | `runtime/assertion_dsl.py` | 213 行 | 是 |
| P-C4 | 事件图因果归因 + 规则 overlay + 失败指纹 | `runtime/event_graph.py`, `runtime/failure_analysis.py` | 530 行 | 是 |
| P-C5 | 状态机/状态守卫/覆盖率策略 | `runtime/state_machine.py`, `runtime/state_coverage_policy.py`, `references/optimization/state_assertion_policy.json` | 561 行 | 是（lstest 无状态覆盖概念） |
| P-C6 | 能力推导与能力矩阵 | `runtime/capabilities.py`, `runtime/capability_runtime.py`, `scripts/build_capability_matrix.py` | 246 行 | 是 |
| P-C7 | 资源占用（串口/声卡/网络/云/电源 claim 与锁） | `runtime/resource_runtime.py` | 267 行 | 是 |
| P-C8 | 约束引擎（项目/场景/副作用/拓扑/云环境 guard） | `runtime/constraint_engine.py` | 208 行 | 是 |
| P-C9 | 设备适配层 + 动作执行器 + 流程映射 + 审计 | `runtime/device_adapter.py`, `runtime/adapter_executor.py`, `references/adapter_flow_map.json`, `scripts/audit_adapter_only.py` | ~1200 行 | 是 |
| P-C10 | 验证内核 / 验证 IR / 场景调度 | `runtime/{validation_kernel,validation_ir,scene_engine}.py`, `scripts/run_kernel_scene.py` | ~1200 行 | 是 |
| P-C11 | Replay 包、Replay VM-lite、Simulation-lite | `runtime/{replay,replay_vm,simulation}.py` | 306 行 | 部分是（lstest 无 replay 概念） |
| P-C12 | 执行记录趋势分析 | `runtime/analytics_trend.py` | 94 行 | 是 |
| P-D1 | 串口/上下电/网络编排工具 | `tools/device/*` | 4 个脚本 | 部分（与 lstest serial_capture/playback 重叠） |
| P-D2 | 云控 API 工具（10 个已验证指令） | `tools/cloud/polaris_app_control.py` | 1 个脚本 | 是 |
| P-D3 | 文档用例批跑与报告导出 | `tools/execution/*`, `tools/reporting/*` | ~6 个脚本 | 是（Polaris 专属产物模型） |
| P-D4 | VenusA/WS63 自动烧录 + OTA 统计与 HTML 报告 | `tools/firmware/*`, `tools/ota/*` | 4 个脚本 | 是（属烧录域） |
| P-D5 | VenusA/WS63 厂商烧录二进制 | `tools/VenusA+WS63/**` | 243 文件 / 56MB | 是（厂商产物） |
| P-D6 | laid（声卡链路）安装 | `tools/audio/laid/*` | 6 文件 | **否**：lstest `scripts/install_laid_{linux.sh,windows.ps1}` 已覆盖，属重复 |
| P-E1 | 机器级配置 `polaris.local.json` 与 env schema | `polaris.local.example.json`, `configs/projects/*.env.example.json` | 3 文件 | 是（但与 lstest 的 profile/env 分离原则冲突） |

## 3. 迁移映射（逐项处置）

### 3.1 MIGRATE-ENGINE → `scripts/`

| Polaris 来源 | lstest 目标 | 映射方式 | 证据/验收 |
|---|---|---|---|
| P-C1 `assert_event_exists / within_ms / order / no_event_during / no_event_exists / no_runtime_reboot / cluster_wake_events / build_media_windows / build_audio_windows / aggregate_result` | `scripts/assertions.py` | 重写为 profile fact 驱动的声明式规则集，11 种 rule kind，数据来自 case 合同而非 Python 分支 | `tests/test_assertions.py` 正/负例 |
| P-C3 断言 DSL-lite | `scripts/assertions.py` 的 `sequence` / `response` / `duration` / `event_absent` 规则 | 语义对齐，表达式下沉到 JSON | 同上 + 与 Polaris 结果对照 |
| P-C4 事件图与失败指纹 | `scripts/attribution.py` | `DEFAULT_CAUSAL_RULES` 数据化；项目私有 overlay 走 `rules=` 参数 | `tests/test_attribution.py` |
| P-C5 覆盖率策略 | `scripts/coverage.py::evaluate_policy` | 复用 `lstest.coverage_policy.v1` 结构（roles.full/minimum、missing_minimum） | `tests/test_coverage.py` |
| P-C6 能力矩阵 | `scripts/coverage.py::capability_matrix` | 从 profile 的 `ports[].capabilities` 显式推导；**删除按项目名推断**（`infer_from_project_name`） | 同上 |
| P-C11 Simulation-lite | `tools/parity/fixtures/`（事件序列 fixture） | 只迁移"可确定性回放的事件序列"概念，不迁代码 | parity 报告 |

### 3.2 MIGRATE-DATA → `docs/`、`cases/`、`profiles/`

| Polaris 来源 | lstest 目标 | 说明 |
|---|---|---|
| P-A1 14 篇 wiki | `docs/voice-validation/*.md` | 原文迁移，首行加 `migrated-from` 溯源注释；内容不改写 |
| P-A2 7 包 + schema + 索引 | `cases/packs/*.json` + `docs/voice-validation/packs/` | 期望值转声明式 `assertions`；无阈值不编造 |
| P-A3 失败模式 | `docs/voice-validation/failure-patterns/` | 原文迁移 |
| P-A4 FA2 343 行期望表 | `cases/fa2/` | 逐字节复制，行数与 counts 由测试比对 |
| P-A5 项目知识 | `docs/voice-validation/projects/{cskwb01,venusws63}/` | 原文迁移，含版本闸门与归因规则 |
| P-A6 intake 模板 | `docs/voice-validation/intake/` | 原文迁移 |
| P-B3 29 个任务 | `cases/examples/*.json` | 转 `lstest.case.v1`；只表达文档明确声明的期望 |
| P-E1 端口/波特率/角色/超时/版本闸门 | `profiles/<project>/profile.json` 的 `provenance`/`coverage_policy`/ports | 值迁入；文件本身不迁（机器级配置不跨机器复制） |

### 3.3 ADAPT → `adapters/`

| Polaris 来源 | lstest 目标 | 边界 |
|---|---|---|
| P-D1 串口采集与短语播放 | `adapters/<project>/adapter.py` | 只采集原始行并提交 `RawLogRecord`；不解析、不写 `tool.log` |
| P-B3/P-B4 用例步骤意图 | `adapters/<project>/case_plan.py` | 只声明 case 顺序与播放动作；断言留在 `cases/` |

### 3.4 CROSS-REPO（不在 lstest 落地）

| Polaris 来源 | 目标 canonical | 理由 |
|---|---|---|
| P-C9 设备适配层/动作执行器/流程映射/审计 | `device-io`（NOAH-51 已交付） | 设备枚举与动作属 device-io 边界；lstest 只消费接口 |
| P-C7 资源 claim 与锁 | `ark-node`（NOAH-49 已交付）+ lstest 既有串口锁 | 节点级资源与身份属 ark-node |
| P-C10 验证内核/IR/场景调度 | `ark-dispatch`（NOAH-45 规划项） | 决策与调度是 ark-dispatch 职责，lstest 只执行单轮 case |
| P-D4 烧录与 OTA 工具 | `venusahisiburner` + `ark-archive` | 烧录域与归档域，均已有 canonical |
| P-D5 厂商二进制 | `asset-tools`（资产 manifest/OSS 引用） | 56MB 二进制不入任何 skill 仓库 |
| P-C12 趋势分析 | `ark-archive` 的报告/归档能力 | lstest 不建长期指标库 |

### 3.5 REPLACE（等价取代，不迁代码）

| Polaris 来源 | lstest 既有机制 | 说明 |
|---|---|---|
| P-B2 五大 registry（step/action/assertion/voice_core/feature_contracts，~2750 行） | `profiles/` 的 `event_rules` + `cases/` 的 `assertions` | 保留会形成两套并行真相；判定为 REPLACE |
| P-C2 9 个 `evaluate_*` evaluator | `cases/*.json` 的声明式断言 | 避免把测试项写死进引擎 |
| 日志解析器（`parsers/*`，含 marker 分类） | `scripts/profile.py` 的 profile 正则 + `RawLogRecord` | lstest 的 profile 合同更严格（唯一展示捕获、fixture 准入） |
| play/asr/media/network/wake/reboot 插件 | `EVENT_REGISTRY` 的 event_type + profile 规则 | 插件层与 event_type 层职责重复 |
| P-D6 laid 安装 | `scripts/install_laid_{linux.sh,windows.ps1}` | 直接判重复，不迁移 |
| P-E1 `polaris.local.json` | profile + 环境变量名分离（lstest SKILL.md 约定） | 机器配置不跨节点复制 |

## 4. 不可迁移项（NON-MIGRATABLE）与原因

| ID | 项 | 原因 | 后续 |
|---|---|---|---|
| N1 | `tools/VenusA+WS63/**`（243 文件 / 56MB） | 厂商 Windows 二进制（BurnTool、Qt DLL、openocd）与固件包；与测试引擎无关，入库会污染 lstest 体积 | 由 asset-tools manifest 引用；Polaris 保留或后续归档提案 |
| N2 | `docs/requirements/*.xlsx`、`docs/cases/*.xlsx` | 二进制工作簿，无法在 git 中有效 diff/审查；且含客户需求原文 | 保留在 Polaris；需要时人工导出 CSV 再入 `cases/` |
| N3 | `features/*.feature` + `run_cucumber.py` | lstest 不引入 cucumber/behave 依赖；用例入口统一为 `cases/*.json` | 意图已迁入 `cases/`；Gherkin 文本留在 Polaris 作历史参照 |
| N4 | 完整 Replay VM / 分布式运行时 / 完整 DSL 编译器 / 插件沙箱 | Polaris 自身标记为"明确暂缓"；无实现可迁 | 不在本轮范围 |
| N5 | `infer_from_project_name()` 项目名嗅探 | 与"项目差异只进 profile"冲突，且会隐式改变断言口径 | 由 profile 显式声明 capabilities 取代 |
| N6 | `tools/execution/*`、`tools/reporting/*` 文档用例批跑与导出 | 产物模型是 Polaris 专属（doc-case/audit/status sync），与 lstest 的 `results.csv`/`cases.csv` 契约不兼容 | 需要批跑时基于 `cases/` 重写，而不是迁移 |
| N7 | `tools/cloud/polaris_app_control.py`（10 个云控指令） | 属设备控制而非测试引擎；目前无 canonical 归属（device-io 边界只到 USB/串口） | DEFER：先登记到 ark-kb，待确定归属后再落 canonical |

## 5. 目标结构（lstest 新 canonical 形态）

```text
lstest/
  scripts/            # 公共引擎（新增 assertions/attribution/coverage，均为项目无关）
  profiles/<project>/ # 项目差异：端口、marker 正则、fixture、覆盖策略、溯源
  cases/              # 声明式用例：packs、examples、fa2 数据
  adapters/<project>/ # 薄适配：只提交原始记录与用例计划
  docs/voice-validation/  # 测试方法与项目知识
  docs/migration/     # 本次迁移的映射与对照证据
  tools/parity/       # 与 Polaris 的 dry-run 对照夹具与脚本
  references/         # 原有合同文档（不变）
  tests/              # 原有 + 新增
```

## 6. 验证策略

1. **单元层**：`python -m unittest discover -s lstest/tests -t .` 全绿；新增模块必须有负例（证明会 FAIL/SKIP）。
2. **数据层**：FA2 343 行与 counts 逐项比对；29 个 case、7 个 pack、迁移文档计数由测试断言。
3. **对照层（dry-run）**：同一事件序列分别走 Polaris 运行时原语与 lstest 移植后的断言层，
   逐条比对 PASS/FAIL/SKIP 判定；不一致即失败（非零退出）。证据见 `docs/migration/parity-report.md`。
4. **边界层**：`profiles/` 全部 `contract_mode: draft` + `verified: false`；无真实日志支撑的项目（FA2）不建 profile。

## 7. 回滚

- lstest 侧全部为新增文件，回退 = 关闭 PR/删除分支，`main` 从未被修改。
- Polaris 侧本轮零改动、零删除、零归档；旧仓库保持可执行。
- 若后续发现某条 migrated 规则有误：删对应 `profiles/` 或 `cases/` 条目即可，引擎不需要回滚。

## 8. 待决策 / 遗留

1. `docs/knowledge/venusws63/autobus-burn-ota-report.md` 属烧录域，迁到 venusahisiburner 还是仅保留引用，需要郑保山确认。
2. 云控工具（N7）与批量报告工具（N6）的 canonical 归属未定。
3. FA2 若要成为正式 profile，需要至少一条真实串口/工具日志作为 fixture 来源（当前仅有期望表）。
