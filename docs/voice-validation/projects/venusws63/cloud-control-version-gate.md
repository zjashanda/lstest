<!-- migrated-from: Polaris/docs/knowledge/venusws63/cloud-control-version-gate.md @ a4ef61478f343e599282ba3aac4cc8705fbdae4a -->

# venusws63 云端 API 控制前置与版本授权排查

本文沉淀 venusws63/WS63 项目在调用云端 API（如全双工、音量、夜间模式、唤醒配置等）前必须确认的环境、版本和在线态逻辑。结论用于区分“云控前置阻塞”和“固件功能失败”，避免把后台未授权或设备未上线误判为固件问题。

## 已知结论

- WS63 调云控前，设备端 AP/CSK 环境必须和本地配置 `cloud.api_environment` 一致。
- 环境命令：
  - UAT：AP 串口执行 `flash.set.int env@1`，必要时 `reboot`。
  - SIT：AP 串口执行 `flash.set.int env@2`，必要时 `reboot`。
  - PRO：AP 串口执行 `flash.set.int env@0`。
- 版本授权规则：
  - 已知可用于云控授权排查的目标版本：`35.03.01.01.18.26.05.04.00.01`。
  - 已知后台未授权 API 控制的版本：`35.03.01.01.18.26.05.04.00.02`。
- 如果设备是 `.00.02`，即使串口/语音基本功能正常，也不能直接跑云控断言；应先切换到 `.00.01`，再进入 UAT/SIT。
- HTTP 状态码为 200 不等于云控成功；必须继续检查业务返回 `result.returnData.code`。例如 `code=501` 仍属于前置阻塞。

## 标准排查顺序

1. 查版本：
   - AP 串口执行 `version`。
   - 关注 `Project Version:` 行。
   - 若是 `35.03.01.01.18.26.05.04.00.02`，先判定为 `BLOCKED_VERSION_NOT_AUTHORIZED`，需要切到 `.00.01` 后再测。
2. 查设备端环境：
   - AP 串口执行 `flash.show` 或 `flash.get.int env`。
   - `env=1` 表示 UAT，`env=2` 表示 SIT，`env=0` 表示 PRO。
   - 设备端环境必须与配置里的 `cloud.api_environment` 一致。
3. 查设备身份和联网：
   - AP 串口执行 `deviceinfo`。
   - 确认 `IoT ID`、`Mac`、`IP` 都存在，且 IoT ID 是当前 DUT。
4. 查云端业务结果：
   - 调用云控 API 后，除 HTTP 状态外，还要检查业务码。
   - `code=501` 且提示“设备未上线”时，归因为云端在线态/环境/授权前置阻塞，不判固件 FAIL。
   - `code=501` 且提示“未登录过的设备”时，优先怀疑设备没有在当前 API 环境完成注册/上线。

## 2026-05-28 本机验证记录

本次对当前接入的 venusws63 执行了诊断：

- AP 串口：`COM16@921600`
- 上位/WiFi 串口：`COM20@921600`
- 控制口：`COM17@115200`
- `version` 解析到 `Project Version: 35.03.01.01.18.26.05.04.00.01`
- `flash.show`/`flash.get.int env` 解析到 `env=1`，即 UAT
- `deviceinfo` 解析到 IoT ID `210006741088068`，IP `192.168.137.94`
- UAT 云端全双工设置返回 HTTP 200，但业务返回 `code=501` / “设备未上线，不可变更全双工状态！”
- SIT API 对同一 IoT ID 返回 `code=501` / “获取设备信息异常[未登录过的设备]！”

因此本机当前状态不是 `.00.02` 版本未授权，也不是设备端 env 与 UAT 配置不一致；剩余阻塞更像是目标云端认为该 IoT ID 未上线/未注册到对应控制链路，或后台授权/设备在线态仍未满足。该状态必须按 `BLOCKED` 记录，不能判固件全双工功能失败。

## 2026-05-28 10:38 复测记录

后续对同一台 WS63 重新执行云控诊断，云端在线态已恢复：

- 诊断目录：`satellite/cucumber-agent-testing/debug/cloud_diagnostics/20260528_103844_venusws63/`
- Session 日志目录：`satellite/cucumber-agent-testing/debug/ws63_cloud_control_probe_20260528_104059/`
- `Project Version` 仍为 `35.03.01.01.18.26.05.04.00.01`
- 设备端 `env=1/UAT`，与 `cloud.api_environment=uat` 一致
- `deviceinfo` 仍为 IoT ID `210006741088068`
- `polaris_cloud_diagnostics.py --probe-cloud` 返回 `PASS`
- v2 `set-full-duplex --enable 1 --timeout 15` 返回 HTTP 200 且业务成功
- 直接调用历史 v1 `fullDuplex_switch(onoroff=1, timeOut=15)` 也返回成功，AP 日志出现 `MSpeech Cloud ... fullDuplex`、`refresh algo timeout to 15 by fullduplex_refresh`、`fullduplex timeout refresh to 15s`
- 本轮同时验证的安全云控项：
  - `set-volume --value 80`：PASS，AP 日志出现 `audio{"volume":"80"}`
  - `set-mic --enable 1`：PASS，云端业务成功
  - `set-night-mode --enable 0`：PASS，AP/upper 日志出现 `nightmode off`
  - `set-log --status 1 --level 2`：PASS，日志出现 `set logLevel to 2` / `recv log level 2, status 1`

结论：当前 WS63 的 UAT 云控链路已经从此前 `501 设备未上线` 恢复为可控状态。后续若再次出现 `501`，优先按“设备在线态/云端注册态波动”排查；若版本变为 `.00.02`，仍按“后台未授权版本”优先归因。

补充工具修复：session logger 模式下，`deviceinfo` 日志会带统一前缀，旧版 `polaris_app_control.py` 只按行首解析 `IoT ID:`，导致 session 模式误报 `deviceinfo did not return IoT ID`。现已改为在日志行内查找字段，避免把工具解析问题误判为设备云控问题。

## 自动诊断入口

可使用下面命令生成诊断报告：

```powershell
python tools\cloud\polaris_cloud_diagnostics.py `
  --env-file satellite\cucumber-agent-testing\debug\local_envs\venusws63.polaris.local.json `
  --probe-cloud
```

脚本会输出：

- `Project Version`
- AP 侧 `env` 与配置 `cloud.api_environment` 是否一致
- `deviceinfo` 身份
- 云端业务码是否成功
- 最终 `PASS` / `PASS_WITH_WARNINGS` / `BLOCKED`

当结果为 `BLOCKED` 时，runner/报告层必须保留该归因，不要改写为固件 FAIL。
