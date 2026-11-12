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

## v2（结构化事件日志）

11. `events.jsonl` 一行一个事件，字段固定为 `lstest.event.v2` 清单；`event_seq` 从 1 连续，缺失值为 `null` 而非 `-`。
12. `raw` 全部外置到 `raw/e<seq>_<EVENT>.json`，事件只保留 `path`/`sha256`/`bytes`；`evidence` 为相对路径 + 行号 + 可选 sha256，产物中无绝对路径。
13. `run.log` 由 events 确定性渲染：每个事件 1 行，异常最多再展开 1 行；含顶部说明/结论与底部 `SUMMARY`（双成功率、异常、责任层、缺口、下一步）。
14. `summary.json` 同时给出 `round_success_rate` 与 `first_attempt_success_rate`、尝试分布、趋势、异常、首败、最差轮、责任层、gate 与 `test_outcome`/`delivery_status`/`final_status`。
15. `gate_pass=false` 时 `final_status` 不为 `PASS`；`TASK_END.status=PASS` 与 `gate_pass=false` 冲突必须写入 `conflicts[]` 并以 gate 为准；无 gate 时判 `NEEDS_REVIEW`。
16. `NO_WAKE` 记为一次 attempt 结果（不是工具 ERROR），并计入首轮成功率与尝试分布；缺 `TASK_END` 按中断判 `NEEDS_REVIEW`。
17. 旧 29 字段平铺日志可被 `eventlog build` 迁移：容错 CRLF/BOM/缺空行/多行 JSON/`-`/缺字段/中文/Windows 路径/超大 raw；迁移不修改源文件。
18. `eventlog verify` 对 seq、时间格式、summary/run.log 重算一致、gate 冲突、绝对路径、sha256 全部检查通过；`ToolLogValidator.validate_run` 能按 v1/v2 分流。
