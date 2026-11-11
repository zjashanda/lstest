<!-- migrated-from: Polaris/docs/knowledge/* @ a4ef61478f343e599282ba3aac4cc8705fbdae4a -->

# 项目知识与需求摄入（从 Polaris 迁入）

本目录承接 Polaris `docs/knowledge/**` 与 `docs/intake/**` 中属于**测试/语音域**的知识资产。
它们不是可执行引擎，只作为 profile / case 编写时的事实来源与判读依据；任何数值结论都必须回到 profile 与 case 契约里声明，不得只存在于文档里。

## 迁移清单

| 目标文件 | 来源 | 内容要点 |
|---|---|---|
| `knowledge-index.md` | `docs/knowledge/README.md` | Polaris 知识库索引原貌 |
| `common/online_full_duplex_validation.md` | `docs/knowledge/common/online_full_duplex_validation.md` | 在线全双工验证的通用判读口径 |
| `cskwb01/event-coverage-notes.md` | `docs/knowledge/cskwb01/event-coverage-notes.md` | WB01 三端口（ap/cp/asr）事件覆盖经验 |
| `venusws63/event-coverage-notes.md` | `docs/knowledge/venusws63/event-coverage-notes.md` | WS63 无 CP 拓扑的事件覆盖经验 |
| `venusws63/cloud-control-version-gate.md` | `docs/knowledge/venusws63/cloud-control-version-gate.md` | 云控版本闸门：不满足授权版本不下结论 |
| `venusws63/venusa-cloud-repo-access.md` | `docs/knowledge/venusws63/venusa-cloud-repo-access.md` | VenusA 云端仓库访问方式（凭据不入库） |
| `venusws63/autobus-burn-ota-report.md` | `docs/knowledge/venusws63/autobus-burn-ota-report.md` | 烧录/OTA 侧报告（属烧录域，仅作对照） |
| `intake/README.md` | `docs/intake/README.md` | 需求摄入流程 |
| `intake/templates/feature_test_contract.template.md` | `docs/intake/templates/feature_test_contract.template.md` | 特性测试契约模板 |
| `intake/templates/learning_manifest.template.json` | `docs/intake/templates/learning_manifest.template.json` | 学习清单模板（原样搬运，未加注释行） |

## 边界

- 文档里的设备行为描述是**线索**，不是断言：写入 `profiles/*/profile.json` 的 `event_rules` 必须有真实日志依据或明确标注为 `draft` + `verified: false`。
- `autobus-burn-ota-report.md` 属于烧录域，lstest 不承接烧录执行；保留在此仅为阅读相邻证据链，实际归属见 `docs/migration/polaris-capability-mapping.md` 的 CROSS-REPO 项。
- 迁入文件均保留首行 `<!-- migrated-from: ... -->` 溯源标记，便于回溯到 Polaris 提交 `a4ef614`。
