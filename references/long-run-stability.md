# 长跑内存与串口日志约束

适用于所有新项目和项目适配器。目标是让长时间压测的内存与句柄使用有明确上界，不依赖硬件可靠性。

## 串口日志

- 原始串口行只能追加到 `serial_logs/*.log`；禁止在内存中永久保留全量行列表。
- 需要按游标查询最近事实时，使用有界事实缓存（每端口最多 20,000 条）；必须保存 `event_base` 或等价偏移，保证绝对游标在淘汰后仍然正确。
- parser 必须限制单行缓冲（建议 4MiB）。超限时丢弃当前缓冲并上报明确错误码；禁止让无换行输入无限增长。
- reader、parser、日志文件和串口句柄必须有成对的 start/stop/join/close 路径；异常退出时使用 `environment_cleanup.register_process_cleanup(...)` 或 `KillOnCloseJob` 清理子进程树。

## 结果与日志读取

- 禁止 `list(csv.DictReader(...))` 全量加载 `results.csv`；使用 `scripts/result_stats.py::ResultSummary` 增量读取。
- 最终对账必须逐行统计；只允许保留首个失败行等常量级状态，禁止保留全量 row list。
- `tool.log` 校验必须使用 `ToolLogValidator.validate_path(...)` 或等价的流式读取；禁止对运行中持续增长的文件调用 `read_text()`。
- 任何多 GB 文件的读取路径都要在代码审查中标注内存上界。

## 每 case / 每轮生命周期

- case 关闭后必须释放 facts、anomalies、window、attempts、online/player 临时状态、metadata 和 position。
- 使用 `TimelineLedger`、`ScenarioRuntime`、`TaskArtifacts` 提供的清理路径；不要在项目适配器里另建无限增长的 per-case 容器。
- 关闭 case 后仍保留的 ID 集合必须是有界的小字符串集合，并在文档中说明其增长上限。

## 验证要求

新增或修改串口/统计/结果代码时，至少提供：

```powershell
python -m unittest tests.test_long_run_cleanup tests.test_result_stats tests.test_environment_cleanup -v
```

- 100,000 条串口事实：事实缓存有界，最后事实可查询。
- 10,000 个 case：关闭后不保留 case 对象/facts/anomalies。
- 20,000 行 tool.log：流式校验。
- 4MiB+ 无换行输入：parser 缓冲有界并上报错误。

## 边界

USB、串口驱动、声卡、电源、设备固件、声学环境和外部启动工具的运行时长上限不属于本规范保证范围。1000 小时稳定性必须通过分级 soak 验证，不能用代码审查替代。
