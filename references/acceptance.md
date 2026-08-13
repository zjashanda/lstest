# lstest 验收清单

1. `--help`、参数错误、无 profile、无端口和无声卡路径返回明确非零状态。
2. 模拟串口覆盖零/单/双串口、角色推断、命令门禁、停止、超时和断流。
3. 无硬件 smoke 仅生成 `serial_logs/`、`tool.log`、`results.csv`、`cases.csv`；终端与 `tool.log` 同步，且没有合并串口日志或旧专项日志。
4. 多轮压测的异常统计从首轮持续累计至末轮；每轮终端和 `tool.log` 均有当前累计快照与检查矩阵，相邻工具事件块之间空一行，串口重连或设备重启后计数不清零。
5. 串口采集只生成 `serial_logs/serial_<port>_<role>.log`，不生成同名 `.bin` 文件。
6. Edge TTS 合成必须使用显式文本或 CSV/JSONL manifest，输出经过 MP3、24 kHz、单声道、时长、响度、SHA-256 校验，并生成可解析的音频 manifest；合成失败不伪造可播放资产。
7. `audio scan`、`probe`、`play` 覆盖默认设备和稳定设备 key；未指定 key 时使用电脑当前默认 Render 声卡，显式 key 不存在、重复或探测失败时必须立即失败，不得回退至默认设备。音频先由 FFmpeg 归一化，Windows 实际播放使用 `pygame`/DirectSound，Linux 使用 `aplay`。`audio ensure-laid` 只在明确安装请求下修改用户 profile。
8. 每条最终识别结果都关联到唯一主机播报。无播报结果记录 `UNEXPECTED_RECOGNITION`，单次播报多结果记录 `MULTIPLE_RECOGNITIONS_FOR_PLAYBACK`，窗口关闭或跨 epoch 的结果记录 `LATE_RESULT_AFTER_CASE_CLOSE`；全部写入 `tool.log`、异常统计并使当前通过轮失败。
9. 离线 `keyword`、`intent` 等算法原始结果在 `tool.log` 与 `results.csv` 中原样可见；规范化中文不得替换原始拼音结果。在线结果保留云端原始文本、请求 ID 和响应 ID。严格原始匹配与 profile 显式允许变体的语义匹配必须分别可见。
10. 多唤醒词 profile 的 `wake_words` 逐项验证当前 `wake_word_id`、播报文本和 `expected_raw`；错配记录 `WAKE_WORD_MISMATCH`、错序记录 `WAKE_WORD_ORDER_VIOLATION`、无播报唤醒记录 `WAKE_WORD_WITHOUT_PLAYBACK`、单播报多唤醒结果记录 `WAKE_WORD_MULTIPLE_RESULTS_FOR_PLAYBACK`，均使当前通过轮失败。
11. 播放器状态写入 `tool.log`：主机请求、进程启动、结束、失败、超时或阻塞必须绑定用例和 `broadcast_id`；项目有设备播放器 marker 时，必须原样记录并映射生命周期。主机正常返回不得代替设备侧播放证据，设备 `ERROR` marker、主机失败、超时或阻塞均使当前通过轮失败。
12. 设备标记测试样例覆盖正例、反例、分段、乱码、ID 配对、播放器弱证据和不可覆盖的致命故障。
13. 已知项目 profile 通过短冒烟测试后，才允许真实场景和长压；短冒烟测试不替代长压结论。
14. 新项目必须创建独立项目适配测试 Skill，继承 `lstest` 的标准流程、日志与状态口径，仅维护项目 profile、适配器、场景和 fixture；不得通过删改或复制分叉 `lstest` 实现项目差异。
15. 每个 `safe_init` 命令只在观察到初始化完成 marker 和稳定窗口后发送；成功回执必须匹配 `success_patterns`，无回执时必须匹配发送后的 `evidence_patterns`。回执/旁证缺失或不匹配时按 `retries` 重试，耗尽后记录 `INITIALIZATION_RECOVERY_FAILED` 并默认继续可执行轮次；只有 profile 明确安全停止时才停止。
16. 配置 `restart_patterns` 的项目必须持续监控重启 marker；每次检测到重启后建立新 epoch、等待新的初始化 marker，再重发并验证所有 `safe_init` 命令。恢复过程中检测到第二次重启时，旧恢复必须取消且不能继续发送命令。重启后的恢复不得清零任务累计异常数据。
17. 场景在首个设备动作前冻结 `cases.csv`；每逻辑轮次只写 `results.csv` 一行，重试聚合为尝试次数。结束时从该 CSV 重算状态计数、有效样本分母、成功率、首个失败与最终结论。

## 结果复核口径

- `PASS` 只表示配置的 oracle 和所需证据均满足；`EVIDENCE_ONLY`、`WARN`、`BLOCKED` 不得统计为业务通过。
- 在线耗时起点是项目原生请求发送/注入事件，终点是同一关联 ID 的最终结果事件；没有完整端点时不写伪造耗时。
- 播放器必须分别记录请求、准备、开始、暂停、停止、结束和异常 marker；只有 `START`/`END` 等设备生命周期证据才能证明设备侧播放。
- 设备重启后的命令由 profile 恢复状态机发送：必须等初始化完成、按规则重试并校验回执或无回执旁证；公共库不固定 `flash.setloglev`、`player.setloglev` 或任何端口号。
