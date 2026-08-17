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
