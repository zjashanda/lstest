# 回归样本来源与脱敏记录

## 文件

| 项 | 值 |
|---|---|
| 仓库内样本 | `tests/fixtures/legacy_flat_v1_smoke10.txt` |
| 样本 SHA-256 | `8c82651074b1f657528051e8ac8964a4cc943c3b784ccf1ab001dce5ae05ea48` |
| 原始日志 SHA-256 | `23880d1e7219244dd4366a507b9b6811cce2878a25a28791ef34527453864c18` |
| 原始大小 | 59,106 字节 / 1,595 行 / 55 个事件 |
| 采集时间 | 2026-11-12 17:01:56 – 17:07:09（北京时间） |
| 来源 | NOAH-68 附件 `pasted-text.txt`（用户提供的真实工具日志） |
| 样本大小 | 59,080 字节 / 55 个 TOOL 事件 |
| 设备 marker 表 | `tests/fixtures/legacy_flat_v1_smoke10.markers.json` |
| 迁移后事件 | 105（55 TOOL + 50 DEVICE），`tool.log` 105 行 |

## 为什么保留

这是"人可读性差 + 判定不一致"的真实回归样本：55 个事件被平铺成 1595 行，
10/10 轮第一次播报均 `NO_WAKE`、第二次成功，且 `TASK_END.status=PASS` 与
`gate.gate_pass=false` 同时出现。删除它会让 v2 的判定规则失去真实依据。

## 脱敏映射（确定性字符串替换，不改结构/时间戳/事件数）

| 原始值 | 替换为 | 原因 |
|---|---|---|
| 内网 PDU 地址（`10.<内网段>.*`） | `192.0.2.10` | 内网地址 → 文档保留地址（TEST-NET-1） |
| 内部节点名（`win<三位编号>`） | `node-a` | 内部节点名 |
| 本机账号名 | `<user>` | 账号名 |
| `D:\\<用户目录>\\...\\<任务>` | `D:\\tasks\\NOAH-67` | 用户目录 → 中性 Windows 路径（保留路径形态） |
| `hisense001` | `device-x` | 客户/项目标识 |
| `hisense` | `device-x` | profile id 中的客户标识 |
| `hai3 xin4 xiao3 ju4` | `da2 kai1 kong1 tiao2` | 唤醒词拼音 |
| `wake_xiaoxiao` | `wake_ref` | 音色名 |

未替换的内容：设备 VID/PID、PDU 工具 stdout、串口 marker、录音 SHA-256、轮次数字。
这些是判定所需的证据，且不含凭据。

## 复核方式

```bash
sha256sum tests/fixtures/legacy_flat_v1_smoke10.txt
python -m lstest.scripts.lstest eventlog build \
  --legacy tests/fixtures/legacy_flat_v1_smoke10.txt \
  --out-dir /tmp/v2-check \
  --meta tests/fixtures/legacy_flat_v1_smoke10.meta.json \
  --markers tests/fixtures/legacy_flat_v1_smoke10.markers.json
python -m lstest.scripts.lstest eventlog verify --run-dir /tmp/v2-check
```

期望：`test_outcome=PASS`、`delivery_status=DELIVERY_INCOMPLETE`、
`final_status=NEEDS_REVIEW`、`round_success_rate=100.0`、`first_attempt_success_rate=0.0`、
尝试分布第 2 次 = 10 轮。

## 结果目录布局 fixture（NOAH-71）

| 项 | 值 |
|---|---|
| 目录 | `tests/fixtures/legacy_layout_screenshot/` |
| 来源 | 用户截图中的旧结果目录列表（根目录多个 JSON + `serial_logs/` + `raw/` + `cloud_run/` + `recorder/` + `milestones/`） |
| 内容 | `preflight.json`、`status.json`、`validator.json`、`evidence_summary.json`、`finalize.json`、`rounds.jsonl`、`serial_logs/`、`raw/`、`cloud_run/`、`recorder/`、`milestones/stage-index.json` |
| 数据 | 全部为脱敏样例值（COM 口、round 状态、时间戳），不含设备数据、账号、内网地址或凭据 |
| 用途 | 与 `legacy_flat_v1_smoke10` 生成的 v2 bundle 合并，复现"根目录 JSON 散落"的真实布局，验证审计/迁移/resolver/validator |

`raw/e0999_SCREENSHOT_SAMPLE.json`、`recorder/round1.pcm` 只是路径形态样本，
不是真实录音或设备抓包；不使用真实设备证据。
