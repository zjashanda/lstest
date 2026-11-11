<!-- migrated-from: Polaris/docs/knowledge/venusws63/autobus-burn-ota-report.md @ a4ef61478f343e599282ba3aac4cc8705fbdae4a -->

# VenusA+WS63 自动烧录、OTA 与报告集成说明

## 来源能力

本说明沉淀自 `X:\skillhub\autobus` 的 Autobus skill。当前 Polaris 已将关键能力迁移到本仓库：

- 自动烧录工具目录：`tools\VenusA+WS63`
- Polaris 烧录包装入口：`tools\firmware\polaris_venusws63_auto_burn.py`
- OTA 轮次统计工具：`tools\ota\venus_ota_stats.py`
- OTA 邮件 HTML 报告工具：`tools\ota\build_venus_ota_html_report.py`

## 当前设备串口映射

当前 `polaris.local.json` 的 `venusws63` 项目映射为：

- VenusA/AP：`COM11@921600`
- WS63/upper/asr：`COM12@921600`
- 控制口：`COM13@115200`

Autobus 原台架默认是 `COM22/COM21/COM23`。在 Polaris 中执行时必须优先读取 `polaris.local.json`，不要沿用 Autobus 原默认端口。

## 自动烧录逻辑

烧录包装入口会完成以下动作：

1. 读取 `polaris.local.json`，提取当前项目串口。
2. 接收固件 zip 或已解压目录；zip 会解压到 `tools\fw\extracted\<固件名>`。
3. 定位 VenusA 固件：优先 `fw.hex`，必要时使用 `fw.img`。
4. 定位 WS63 固件：优先 `ws63-liteos-app_all.fwpkg` 或匹配 `ws63/ws53 + all` 的 fwpkg。
5. 调用 `tools\VenusA+WS63\auto_burn.py`。
6. Windows 下通过 `cmd /c chcp 936` 启动，避免 WS63 `optLog` 里 `烧写结果：成功` 变成乱码。
7. 保存 `preflight.json`、`auto_burn_stdout.log`、`summary.json`、`summary.md` 到 debug 目录。

真实烧录必须显式携带 `--allow-side-effects`；只验证命令构造时使用 `--dry-run`。
给压缩包烧录时，版本判断必须来自压缩包内部内容，不允许只看 zip 文件名。包装脚本会读取：

- `BuildInfo.txt`：`Firmware Version`、`VenusA BuildInfo`、`WS63 BuildInfo`。
- `config.json` / `config_hex.json`：`fw.img`、`fw.hex`、`ws63-liteos-app_all.fwpkg` 的内部版本、大小和 md5。
- `Other/VenusA_build_*.log`、`Other/WS63_build_*.log`：实际构建 commit/time。
- 固件文件存在性：`fw.hex`/`fw.img` 和 `ws63-liteos-app_all.fwpkg`。

Windows 路径中包含空格、括号时，必须由包装脚本生成临时 batch 后再进入 `chcp 936`，不要手工拼接 `cmd /c ... "<path (2)>"`，否则底层参数可能被拆分。

示例：

```powershell
python tools\firmware\polaris_venusws63_auto_burn.py `
  --firmware tools\fw\Midea_VenusA_WS63_35.03.01.01.18.26.06.04.00.04_20260616_171724.zip `
  --dry-run

python tools\firmware\polaris_venusws63_auto_burn.py `
  --firmware tools\fw\Midea_VenusA_WS63_35.03.01.01.18.26.06.04.00.04_20260616_171724.zip `
  --allow-side-effects

python tools\firmware\polaris_venusws63_auto_burn.py `
  --firmware <固件zip或解压目录> `
  --allow-side-effects `
  --verify-after-burn
```

## 烧录成功标准

- VenusA 烧录 stdout 出现 `MD5 CHECK SUCCESS` 和 `FLASH DOWNLOAD SUCCESS`。
- WS63 最新 `tools\VenusA+WS63\BurnTool_Gold\optLog\optLog_*.txt` 出现 `烧写结果：成功`。
- WS63 退出产测模式时，`AT+FTM=0` 返回 `+FTM SWITCH: start` 和 `OK`。
- `auto_burn.py` 返回码为 0。
- 烧录后执行 `version`，`Project Version` 必须等于包内 `BuildInfo.txt` 的 `Firmware Version`，或等于 `config.json`/`config_hex.json` 中 `fw.img`/`fw.hex` 的版本。

## 烧录后版本核对口径

`--verify-after-burn` 会在真实烧录成功后自动读取 AP `version` 和 `deviceinfo`，并把结果写到 run 目录：

- `package_metadata`：包内版本、构建信息和固件文件清单。
- `post_burn_verify/COM*_version.log`：设备 `Project Version` 原始日志。
- `post_burn_verify/COM*_deviceinfo.log`：设备 SN、IoT ID、MAC、IP。
- `post_burn_verify/post_burn_verify.json` / `.md`：期望版本、设备版本和 PASS/FAIL。

如果需要进一步证明 WS63 运行固件也来自同一个包，烧录后重启并同时抓 COM11/COM12 启动日志：

1. COM11/AP 关注 `ListenAI APP Build Info`、`Project Version`、`Boot Reason`。
2. COM12/upper 关注 `ListenAI APP Build Info`、`ws63 SDK Version`、联网初始化日志。
3. VenusA 运行 BuildInfo 必须与包内 `VenusA BuildInfo` 或 VenusA build log 一致。
4. WS63 运行 BuildInfo 以 commit 为主、时间为辅，与 `Other/WS63_build_*.log` 对齐；若 `BuildInfo.txt` header 与 build log 有秒级差异，优先看 build log 和提交号。

如果 VenusA 已成功但 WS63 失败，优先用 `--skip-venusa` 只重烧 WS63；不要无证据地反复重烧 VenusA。

## OTA 自动化逻辑

Autobus OTA 主脚本 `otaPartitaForMidea-test_burn.py` 的核心逻辑如下，当前先沉淀为 Polaris 知识和统计/报告能力：

- `-b 1`：下载阶段设备断电。
- `-b 2`：升级阶段设备断电。
- `-b 3`：下载阶段路由器/网络断电。
- `-b 4`：下载阶段设备断电 + 网络断电。
- `-b 5`：每轮随机选择下载阶段或升级阶段设备断电。
- `-b 6`：每轮随机选择下载阶段设备断电、升级阶段设备断电或下载阶段网络断电。
- `-e uat`：每轮前后通过 `flash.set.int env@1` 保持 UAT 环境。
- 工作日 12:30 至 13:30 只暂停启动新轮次，不强行中断正在执行的 OTA。

## OTA 统计与报告

统计：

```powershell
python tools\ota\venus_ota_stats.py <TASK_DIR>\stdout.log --csv <TASK_DIR>\ota_break_stats.csv
```

邮件 HTML 报告：

```powershell
python tools\ota\build_venus_ota_html_report.py --task-dir <TASK_DIR>
```

HTML 报告遵守以下口径：

1. 标题/header。
2. `重点结论`。
3. `一、测试项`。
4. `二、测试步骤`。
5. `三、测试结果`。
6. `四、测试分析`。
7. `五、日志附件路径`。

邮件版使用 inline CSS 和 table 布局，不依赖 `<style>`、CSS 变量、flex/grid、外部字体或脚本。

## 已知异常归因

- `hasNewVer:false` 且没有 `otaStart/fw_url`：优先归因云端未推送，不判脚本失败。
- `OTA update success`、版本正确、环境正确但脚本判失败：归因自动化判定问题。
- `not support flash` -> `Exception on CORE1` / `CORE1 HALTED` -> `RESET=0x1/AON`：归因 flash 型号/安装流程支持问题。
- WS63 `csk_flash_drv.c:294` ASSERT 后长期停在 `ota_step=2`：归因设备/固件 OTA 状态恢复问题。
- WS63 `optLog` 结果为 `????`：优先归因 code page，不直接判烧录失败；需用 `chcp 936` 重新执行。

## 后续扩展

如果要把完整 OTA 执行也迁移为 Polaris 原生入口，应先解决 `otaPartitaForMidea-test_burn.py` 对 `pyaudio` 等历史依赖的导入问题，再将 OTA 真机执行包装成 side-effect gated 工具，避免与语音验证串口 session 冲突。
