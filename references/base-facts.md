# Base Interaction Facts

基础事实是设备运行时状态时间线中的逻辑节点。采集和验收必须分离：

- profile 事件规则匹配到的状态按串口到达顺序进入时间线；
- `tool.log` 展示状态节点；
- `facts_json` 保存结构化事实、顺序、来源和关联 ID；
- `required_facts` 只影响验收缺失判定，不影响采集或展示。

## 基础事实清单

| fact id | 建议事件类型 | 说明 |
| --- | --- | --- |
| `wake` | `wake_result` | 唤醒结果 |
| `offline_asr` | `offline_result` | 离线识别 |
| `online_request` | `online_request` | 在线请求/请求 ID |
| `online_response` | `online_response` | 在线响应/响应 ID |
| `online_result` | `online_result` | 在线识别 |
| `online_intent` | `online_intent` | 在线意图 |
| `offline_tts` | `tts_offline` | 离线 TTS |
| `online_tts` | `tts_online` | 在线 TTS |
| `tts_id` | `tts_id` | TTS ID |
| `player_url` | `player_url` | 播放地址 |
| `player_id` | `player_id` | 设备播报 ID |
| `player_state` | `player_state` | 播放状态切换 |
| `initialization` | `initialization_ready` | 初始化完成 |
| `restart` | `restart` | 重启 |
| `command_ack` | `command_ack` | 命令回执 |
| `command_evidence` | `command_evidence` | 命令旁证 |
| `device_exception` | `device_exception` | 设备异常 |

不同项目可能没有在线/离线、TTS 或某些 ID。缺失必须显式声明，不能静默：

```json
{
  "base_facts_not_applicable": ["online_request", "online_response", "online_tts"],
  "base_fact_event_map": {
    "offline_tts": "player_state"
  }
}
```

- `base_fact_event_map`：项目已有事件类型与规范事实不同时的映射。
- `base_facts_not_applicable`：本项目确实没有该场景。
- `player_state` 作为基础状态时必须使用 `render_policy: "all"`，确保 `tool.log` 不压缩状态切换。

## Preflight

```powershell
python -u lstest/scripts/lstest.py preflight `
  --profile <profile.json> `
  --cases <cases.json> `
  --requirements <requirements.json> `
  --corpus <corpus.json> `
  --capabilities <capabilities.json> `
  --require-base-facts
```

- 默认 `preflight` 输出 `base_fact_gaps` 和 `base_fact_coverage`，方便项目补全；
- profile 设置 `"base_facts_required": true` 或命令行传入 `--require-base-facts` 时，缺失基础事实会返回 `BLOCKED`；
- 缺失只表示无法复核对应设备状态，不应伪造事实。
