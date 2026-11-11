<!-- migrated-from: Polaris/docs/wiki/voice-validation/failure-patterns/ws63-devicecontrol-empty-tts-url.md @ a4ef61478f343e599282ba3aac4cc8705fbdae4a -->
# WS63 DeviceControl 空 TTS URL 归因模式

## 触发条件

在 `venusws63` 项目中，空调/设备控制命令可能同时出现以下证据：

- 已唤醒：`wakeup_callback`、`online_wakeup`、`Pre Wakeup` 等。
- 已识别：`online_asr_callbak`、`MSpeech Cloud 3 evt`、`cloud.speech.trans.ack` 等。
- 控制回复存在：`mideaSkillId: DeviceControl`、`cloud.instructions.audioBroadcast`、`cloud.speech.reply`、`MSpeech Cloud 4/32 evt` 等。
- 播报链路异常：`TTS url is null`、`no valid tts url`、`empty.mp3` / `empty0.mp3`。

## 断言结论

- `control_reply`：如果存在 `DeviceControl` 或等价控制回复，控制回复段可以判 `PASS`。
- `tts_response`：如果响应文本存在但 TTS URL 为空或 `no valid tts url`，播报段判 `FAIL` 或整体 `PASS_WITH_WARNINGS/tts_response_chain`。
- `recognition`：不能因为 TTS URL 为空反推识别失败；是否识别成功仍以 ASR 文本、Cloud 3 事件、命令关键词和控制回复综合判断。
- `actuator_feedback`：没有明确蜂鸣器/执行机构 marker 时保持 `UNKNOWN`，不能伪造成物理蜂鸣器已响。

## 归因边界

| 现象 | 推荐归因 | 不应归因 |
| --- | --- | --- |
| 有 `DeviceControl`，但 `TTS url is null` | `tts_response_chain` | `asr_entry`、`command_domain_or_cloud_skill` |
| 有 ASR/控制回复，缺少有效 TTS URL | 播报链路或云端 TTS 资源问题 | 固件未识别命令 |
| 有控制回复，无蜂鸣器 marker | `actuator_feedback=UNKNOWN` 或 `evidence_gap` | 直接判蜂鸣器 PASS/FAIL |
| COM20/upper 口未打开，AP 口有证据 | `COVERAGE_DEGRADED` | 完整双口覆盖 |

## 复现与回归入口

建议使用小规模矩阵先覆盖以下命令，不重跑 343 条：

- `打开空调`
- `关闭空调`
- `制冷模式`
- `制热模式`
- `查询空调模式`
- `查询空调联网状态`
- `增大音量`
- `播报今日新闻`
- `播放音乐`

执行时需要先确认串口覆盖：

```powershell
$env:PYTHONIOENCODING='utf-8'
python satellite\cucumber-agent-testing\scripts\run_command_control_diagnosis.py `
  --allow-side-effects `
  --env-file satellite\cucumber-agent-testing\debug\goal_command_beep\20260528_201015\envs\venusws63.env.json `
  --matrix-file satellite\cucumber-agent-testing\references\diagnosis_matrices\venusws63_com20_supplement.example.json `
  --out-root satellite\cucumber-agent-testing\debug\ws63_com20_supplement
```

如果 `serial_coverage.status=COVERAGE_DEGRADED`，报告只能给 AP 侧降级结论；如果必需口被指定且打不开，则结果应为 `BLOCKED`。
