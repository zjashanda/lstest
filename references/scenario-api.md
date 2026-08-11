# lstest 场景接口规范

场景实现以下接口：

```python
preflight(runtime)
next_case(state)
run_case(case, runtime)
judge(observed)
cleanup(runtime)
```

`next_case` 必须按需生成，不能一次性生成百万条随机用例。场景声明预期语料、音频、业务域/意图/动作、前置状态和安全等级；公共 runtime 负责播放、串口关联、耗时、停止、证据和落盘。
