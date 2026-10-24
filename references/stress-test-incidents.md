# 长时间压测故障复盘

## 2026-10-24：9999 轮任务被外部清理误杀

- 任务：`20261024_065345`，5 台设备 × 9999 轮，模式 `powercycle + wake/ASR`。
- 现象：编排器在 06:57:07 输出 5 台 `DONE ... FAIL code=4294967295`；完成轮数分别为 5、5、5、0、4。已完成轮次全部为 PASS/hit，没有设备识别失败。
- 直接原因：人工清理进程时按命令行匹配执行了强制停止，误杀了该任务正在运行的 5 个 `run.py` 子进程；编排父进程仍在运行，随后把子进程退出码 `-1` 判为 FAIL。
- 进一步原因：父进程命令行使用相对路径 `scripts\run_multi.py`，人工匹配时只识别到了绝对路径的子进程，误以为父进程已经退出。
- 修复：
  - 压测启动前使用 `environment_cleanup.find_processes_by_command(...)` 检测活动 `run.py` / `run_multi.py`，存在活动任务时拒绝清理和启动。
  - 使用 `environment_cleanup.KillOnCloseJob()` 将子进程加入 Windows Job Object，父进程关闭或崩溃时自动终止整棵子进程树。
  - 使用 `environment_cleanup.register_process_cleanup(...)` 处理 Python 异常退出场景。
- 验证：修复后单台 1 轮 PASS；500 轮验证任务已启动。

## 2026-10-23：5 万轮任务统计 MemoryError

- 任务：`20261023_123222`，5 台设备 × 10000 轮，模式 `powercycle + wake/ASR`。
- 现象：`[TOTAL] rounds=2917/50000` 后，编排器在 `run_multi.py` 的 `summarize_result_dir()` 中触发 `MemoryError`。
- 根因：统计使用 `list(csv.DictReader(...))` 把每台设备的 `results.csv` 全量载入内存；单行包含 `facts_json`、`evidence` 等大字段，且每 0.5 秒对 5 台设备重复全量读取。
- 连带问题：编排器崩溃后，1 个 `run.py` 和 138 个 `listenai_play.py` 进程残留；停止时结果目录已累计约 5748 轮。
- 修复：
  - 新增 `scripts/result_stats.py` 的 `ResultSummary`，按文件偏移只读取新增完整行。
  - 多设备实时 `[TOTAL]`、30 秒进度和最终 `[OUTCOME]` 共用 `ResultSummary` 缓存。
  - 配套增加子进程退出清理，避免统计/编排异常后继续运行。
- 验证：大结果目录统计耗时从重复全量读取降为一次增量读取；追加行、二次读取、聚合统计均有单元测试。

## 复用规则

1. 禁止对 `results.csv` 使用 `list(csv.DictReader(...))` 做长时间压测统计；统一使用 `ResultSummary`。
2. 清理进程前必须先检测活动的 `run.py` / `run_multi.py`；有活动任务时拒绝清理，不要用命令行模糊匹配直接杀进程。
3. 编排器启动子进程后，使用 `KillOnCloseJob` 和 `register_process_cleanup` 管理父子进程生命周期。
4. 长任务必须通过日志、HTML 结果目录和 `summary_outcomes.csv` 判断，不允许仅凭编排器退出码判断设备失败。
