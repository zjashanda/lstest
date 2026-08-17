# 项目适配 API

项目适配器只通过公开 API 提交原始记录、case 生命周期和主机动作。不得向 `tool.log` 写文本、提交预格式化行、传入自由 key，或调用 legacy 的识别/播放器格式化接口。

```python
from lstest.scripts.profile import RawLogRecord

runtime.freeze_cases(cases, profile_version=profile.schema_version, profile_sha256=profile.sha256)
runtime.open_case_window(case_id)
runtime.submit_raw_record(RawLogRecord(
    text=raw_line, source="<source>", port="<port>", role="<role>",
    cursor=cursor, sequence=sequence, phase="<stage>",
    evidence=("serial_logs/serial_<port>_<role>.log#<cursor>",),
), case_id=case_id)
runtime.record_case(result)
```

case 的 `metadata.timeline` 声明本轮的 `required_facts`、镜像策略、镜像窗口、重复策略和人读原因标签。框架在 case 关闭前透传所有命中事实，关闭时补空值、执行逻辑计数、输出唯一 `[RESULT]`，并写入一行 `results.csv`。

实际音频播报只通过 `runtime.play(...)` 触发；工具日志仅显示播报文本和音频 basename。项目 profile 的播放器规则从设备日志提取播放器事实，不能使用主机内部状态替代设备侧事实。
