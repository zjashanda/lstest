# 结果和工具日志标准

每次任务只生成：

```text
result/<timestamp>_<task>/
  serial_logs/*.log
  tool.log
  results.csv
  cases.csv
```

`serial_logs` 是连续原始证据；`tool.log` 是唯一人读执行账本；`results.csv` 每 case 一行；`cases.csv` 在设备动作前冻结。

所有 `tool.log` 行使用北京时间 `YYYY-MM-DD HH:mm:ss.SSS`，标签只能是 `[SYSTEM]`、`[CASE]`、`[ACTION]`、`[COMMAND]`、`[DEVICE]`、`[ONLINE]`、`[PLAYER]`、`[ERROR]`、`[RESULT]`、`[SUMMARY]`。冒号右侧必须是 profile 正则捕获的原始值。

```text
2026-08-17 10:00:00.000 [CASE 1/2] START 场景=<scenario> 文本=<text> case_id=<case>
2026-08-17 10:00:00.010 [ACTION] 播放: <text>，文件=<basename>
2026-08-17 10:00:00.120 [DEVICE] WAKE: <profile-capture>
2026-08-17 10:00:00.300 [PLAYER] PLAYER: <profile-capture>
2026-08-17 10:00:01.000 [ONLINE] ONLINE_ASR:
2026-08-17 10:00:01.010 [ERROR] 异常: <profile-reason>，处理=本轮收尾后继续，证据=<port>#<cursor>
2026-08-17 10:00:01.100 [RESULT] 本轮=FAIL，原因=<primary-reason>，逻辑事实=<key>=<count>
2026-08-17 10:00:01.101 [SUMMARY] 本轮异常=<count>，累计异常=<count>
```

不得输出正则、rule id、捕获字段名、内部播报/关联 ID、绝对路径或项目自行格式化文本。播放器 raw marker 不得转换成固定单词。异常后仍持续输出随后到达的事实；仅轮末输出一次本轮 `[RESULT]`。

`results.csv` 使用通用字段：原始/逻辑事实计数、关联结论、异常码、主要原因、profile 版本和完整事实 JSON；不得固定某项目的识别字段列。`ToolLogValidator` 必须验证格式、唯一 RESULT、事实顺序、空值、原样展示、持续观察、计数一致性和四类产物边界。
