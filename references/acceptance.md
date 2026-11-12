# 验收清单

1. `--help`、profile 加载、formal fixture、无硬件 preflight 均通过。
2. formal profile 的每个规则均有来源、命名捕获、唯一展示捕获、阶段、关联、镜像、空值和正反 fixture。
3. 两个字段名和播放器 marker 不同的占位 profile 都能透传各自原始值，框架不做名称或文本猜测。
4. 每个活动 case 的事实按到达顺序显示；重复、播报前、异常后和迟到事实不可被静默压缩。
5. 每轮仅一个 `[RESULT]`，必需缺失事实有空值行，累计异常不因重连或重启清零。
6. 播放器内部 `state_class` 不得改写原始 marker；错误、未知、关联失败事件必须显示。
7. 初始化、重启、命令回执/旁证和重试均由 profile 规则验证；沉默不能算成功，恢复耗尽默认继续。
8. 普通工具/设备异常、解析异常、识别失败、超时和播放器失败完成本轮并继续；只有退出白名单可结束任务。
9. 结果目录只含 `serial_logs/*.log`、`tool.log`、`results.csv`、`cases.csv`，没有 `.bin`、task/tool JSONL 或额外日志。
10. `ToolLogValidator`、Python 编译、全部单元/fixture 测试及已授权的只读日志回放通过；真机测试风险单独说明。

## v2（完整动作审计）

11. `events.jsonl` 一行一个事件，字段固定为 `lstest.event.v2` 清单；`event_seq` 从 1 连续，缺失值为 `null` 而非 `-`，时间为北京时间毫秒（`now_log()`；记录字段仍用 `now_text()` 秒级）。
12. `raw` 全部外置到 `raw/e<seq>_<EVENT>.json`，事件只保留 `path`/`sha256`/`bytes`；`evidence` 为相对路径 + 行号 + 可选 sha256，产物中无绝对路径。
13. `tool.log` 是完整动作审计：TOOL 动作与 DEVICE 状态合并，按 host 毫秒升序，**每个事件恰好一行**；无续行、无空行、无段落汇总，message 换行已转义。
14. 每行自足：范围/动作/阶段/结果、做法与关键参数、前后状态、判断依据（expected/actual/hit/rule/阈值/next）、证据定位；不存在的字段不输出，不使用 `-` 占位。
15. 设备状态必须出现在 `tool.log`，统一为 `[范围][DEVICE][状态码]`（如 `[R01][DEVICE][POWER_OFF]`、`[R01/A02][DEVICE][WAKEUP]`、`[R01/A02][DEVICE][ASR_RESULT]`）：电源/BOOT/READY、串口、唤醒（含关键词/分数/阈值）、识别、TTS、播放、录音/VAD、异常恢复；连续重复状态合并并记 `repeat`/`last_hit`，未识别行记 `DEVICE_UNPARSED`（含 raw hash 与证据）。
16. `TASK_END` 行同时给出 `recorded`、`test_outcome`、`delivery`、`final`、`gate`、`rounds`；`gate_pass=false` 时 `final_status` 不为 `PASS`，状态冲突写入 `conflicts[]`；无 gate 判 `NEEDS_REVIEW`。
17. `NO_WAKE` 记为一次 attempt 结果（不是工具 ERROR），计入首轮成功率与尝试分布；缺 `TASK_END` 按中断判 `NEEDS_REVIEW`。
18. 旧 29 字段平铺日志可被 `eventlog build` 迁移：容错 CRLF/BOM/缺空行/多行 JSON/`-`/缺字段/中文/Windows 路径/超大 raw；`--markers` 派生 DEVICE 事件；迁移不修改源文件。
19. `eventlog verify` 对 seq、时间格式、summary 重算、`tool.log` 重算与单行语法、gate 冲突、绝对路径、sha256 全部检查通过；`ToolLogValidator.validate_run` 能按 v1/v2 分流。
20. 串口原始日志每行前缀为 host 接收毫秒时间，设备自身时钟保留在原文；`tool.log` 对应行可附 `device_ts`。
21. 节点接入按 `references/node-integration.md` 五步完成：结构化运行产出四件套、`TASK_END` 写 gate、`eventlog verify --check-files` 为 `ERROR=0`；未完成前只能报 `NEEDS_REVIEW`，不得宣称真机验证。

## 状态目录（v2 扩展）

22. 设备状态码必须登记在通用目录（`scripts/states.py`）或 profile/`--state-extension` 的
    `lstest.state-extension.v1` 扩展中；未登记码落 `DEVICE_UNPARSED`（WARN），
    `details.raw_code`、`raw_sha256`、证据定位齐全，不静默渲染成正式状态。
23. 项目扩展只能新增 `P_` 前缀码或显式别名；覆盖通用码、别名指向未登记码、
    `profile_id` 不一致均拒绝加载（`BLOCKED_PROFILE_CONTRACT`，退出码 2）。
    原始 marker 文本与捕获值不被改写，语义码与 `raw_code` 分离。
24. `state_report` 由事件确定性生成：按类别计数、目录状态（common/project/unregistered）、
    重复合并数、迟到、前置缺失、blocking/recovered/unrecovered、必需状态；
    `summary.json`/`summary.md`/`tool.log` 与之一致，手改会被 `verify` 判漂移。
25. profile 声明 `state_catalog.gate` 后，`required_states_present` 与
    `no_unrecovered_blocking` 进入 gate；任一为 false → `delivery_status=BLOCKED`、
    `final_status` 不得为 `PASS`。只有 `pass_role=positive_evidence` 可作通过证据。
26. 旧 v1/v2 目录在没有扩展时行为不变；`verify` 的 `STATE_*` 检查默认 WARN，
    `--strict-states` 提升为 ERROR；`state list/check/coverage` 可独立运行且不访问设备/网络。
27. 总目录包含用户给定的 7 个基础 marker 对应语义状态（`WAKE_KEYWORD`、`WAKE_TTS_CALLBACK`、
    `ASR_KEYWORD`、`TTS_PLAY_ID`、`ONLINE_ASR_TEXT`、`ONLINE_TTS_TEXT`、`ONLINE_SKILL_TEXT`），
    并覆盖 TTS/播放/在线请求/重启/版本/Wi-Fi/蓝牙/联网/音频链路/业务/解析等类别。
28. 设备行统一显示"类别：业务值"：不出现 `raw_marker=`、正则名、rule id、捕获字段名、
    `catalog_status=`、`raw_code=`、`correlation_id=`、`device_ts=`；业务值（keyword/zh/text/
    tts_id/addr/request_id）按脱敏规则保留；raw 原文只在 evidence。
29. `tts` 类的 request/start/first_packet/end/complete/interrupt/pause/resume/error/play
    阶段均可独立成行且同属 tts 类；播放类覆盖 prepare/play/pause/resume/interrupt/stop/play_end/error。
30. 至少两个 profile 验证：A 只有部分 marker（离线），B 另一套 marker（在线）；
    A 不输出 online_asr/online_tts/online_skill，不补假状态；B 正常输出；两者 verify ERROR=0。
31. `state profile-coverage` 区分 `SUPPORTED`/`EMITTED`/`NOT_EMITTED`/`NOT_APPLICABLE`/`UNPARSED`；
    未登记 marker 进 `UNPARSED`，tool.log 只显示一行"未识别设备状态"。
