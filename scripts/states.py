"""设备状态目录：登记、校验、时序分析和覆盖率。

目录是 lstest 的"合法状态码"唯一来源。项目差异通过
``lstest.state-extension.v1`` 扩展表达：只允许新增 ``P_`` 前缀码或显式别名，
不能覆盖通用条目；未登记的码在事件层落 ``DEVICE_UNPARSED`` 并保留原始 marker。

本模块不做设备动作、不访问网络。
"""

from __future__ import annotations

#: 通用状态目录（由 NOAH-69 盘点固化；新增码只能追加，改名必须 supersedes）。
_COMMON_ROWS = (
    {
        'code': 'POWER_OFF',
        'category': 'power',
        'kind': 'bench',
        'label_zh': '设备掉电完成（PDU 插座断开）',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'merge',
        'recoverable': True,
        'raw_marker': ('断电成功', 'PDU 断电 20s'),
        'prerequisites': (),
        'successors': ('POWER_ON',),
    },
    {
        'code': 'POWER_ON',
        'category': 'power',
        'kind': 'bench',
        'label_zh': '设备上电完成',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'merge',
        'recoverable': True,
        'raw_marker': ('设备硬重启完成', '上电完成'),
        'prerequisites': ('POWER_OFF',),
        'successors': ('BOOT_START', 'READY'),
    },
    {
        'code': 'POWER_CYCLE',
        'category': 'power',
        'kind': 'bench',
        'label_zh': '一次完整断电-保持-上电循环',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': ('PDU 断电 20s 后上电',),
        'prerequisites': ('POWER_OFF',),
        'successors': ('POWER_ON',),
    },
    {
        'code': 'POWER_PASS',
        'category': 'power',
        'kind': 'judgement',
        'label_zh': '台架电源验证通过（NOAH-57 口径）',
        'pass_role': 'positive_evidence',
        'scope': 'bench',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': (),
        'prerequisites': ('POWER_OFF', 'POWER_ON'),
        'successors': (),
    },
    {
        'code': 'POWER_FAIL',
        'category': 'power',
        'kind': 'judgement',
        'label_zh': '台架电源验证失败',
        'pass_role': 'blocking',
        'scope': 'bench',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': (),
        'prerequisites': ('POWER_OFF',),
        'successors': (),
    },
    {
        'code': 'POWER_BLOCKED',
        'category': 'power',
        'kind': 'judgement',
        'label_zh': '台架电源验证阻塞（无控制入口/权限）',
        'pass_role': 'blocking',
        'scope': 'bench',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': (),
        'prerequisites': (),
        'successors': (),
    },
    {
        'code': 'POWER_UNVERIFIED',
        'category': 'power',
        'kind': 'bench',
        'label_zh': 'PDU 写命令返回成功但读回未确认',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('POWER_ON', 'POWER_OFF'),
        'successors': ('POWER_CYCLE',),
    },
    {
        'code': 'PDU_WRITE_REFUSED',
        'category': 'power',
        'kind': 'tool',
        'label_zh': 'PDU 写被策略拒绝（未加 --apply/--confirm-write）',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'merge',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': (),
        'successors': (),
    },
    {
        'code': 'PDU_BACKEND_UNAVAILABLE',
        'category': 'power',
        'kind': 'bench',
        'label_zh': 'PDU 通道不可用（SNMP/Modbus 后端缺失）',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': (),
        'successors': (),
    },
    {
        'code': 'BOOT_START',
        'category': 'boot',
        'kind': 'device',
        'label_zh': '设备开始启动',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'merge',
        'recoverable': True,
        'raw_marker': ('boot #', 'BOOT'),
        'prerequisites': (),
        'successors': ('BOOT_STAGE', 'READY'),
    },
    {
        'code': 'BOOT_STAGE',
        'category': 'boot',
        'kind': 'device',
        'label_zh': '启动阶段推进（bootloader/kernel/app）',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'merge',
        'recoverable': True,
        'raw_marker': ('[O][boot #',),
        'prerequisites': ('BOOT_START',),
        'successors': ('BOOT_COMPLETE', 'READY'),
    },
    {
        'code': 'BOOT_COMPLETE',
        'category': 'boot',
        'kind': 'device',
        'label_zh': '启动完成（应用层进入运行态）',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'merge',
        'recoverable': False,
        'raw_marker': ('boot complete', '系统启动完成'),
        'prerequisites': ('BOOT_START',),
        'successors': ('READY', 'WAKEUP'),
    },
    {
        'code': 'READY',
        'category': 'ready',
        'kind': 'device',
        'label_zh': '语音算法/服务就绪',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'merge',
        'recoverable': True,
        'raw_marker': ('INIT_READY', 'ready'),
        'prerequisites': ('BOOT_COMPLETE', 'POWER_ON'),
        'successors': ('WAKEUP',),
    },
    {
        'code': 'INIT_READY',
        'category': 'ready',
        'kind': 'device',
        'label_zh': '初始化就绪（v1 profile 事件类）',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'merge',
        'recoverable': True,
        'raw_marker': ('初始化完成',),
        'prerequisites': ('BOOT_START',),
        'successors': ('READY',),
    },
    {
        'code': 'BOOT_FAIL',
        'category': 'boot',
        'kind': 'device',
        'label_zh': '启动失败（卡 boot / Flash Init Fail）',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': ('Flash Init Fail 0x80001341',),
        'prerequisites': ('POWER_ON',),
        'successors': ('ERROR',),
    },
    {
        'code': 'FTM_MODE_ENTER',
        'category': 'mode',
        'kind': 'device',
        'label_zh': '芯片进入 FTM 工厂模式',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': ('FTM SWITCH: start',),
        'prerequisites': ('BOOT_START',),
        'successors': ('FTM_MODE_EXIT',),
    },
    {
        'code': 'FTM_MODE_EXIT',
        'category': 'mode',
        'kind': 'device',
        'label_zh': 'FTM 工厂模式退出成功',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': ('FTM SWTICH:0x230000, size:0x240000 OK.', 'OK'),
        'prerequisites': ('FTM_MODE_ENTER',),
        'successors': ('READY',),
    },
    {
        'code': 'REBOOT',
        'category': 'boot',
        'kind': 'device',
        'label_zh': '设备重启（正常/受控）',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'state_change',
        'recoverable': True,
        'raw_marker': ('reboot', 'restart'),
        'prerequisites': ('READY',),
        'successors': ('BOOT_START',),
    },
    {
        'code': 'DEVICE_RESTART_DETECTED',
        'category': 'boot',
        'kind': 'framework',
        'label_zh': '框架检测到设备重启（epoch 变化）',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('READY',),
        'successors': ('READY',),
    },
    {
        'code': 'CRASH',
        'category': 'boot',
        'kind': 'device',
        'label_zh': '设备崩溃/异常复位',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': ('crash', 'panic', 'fault'),
        'prerequisites': ('READY',),
        'successors': ('BOOT_START', 'ERROR'),
    },
    {
        'code': 'WATCHDOG',
        'category': 'boot',
        'kind': 'device',
        'label_zh': '看门狗复位',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': ('watchdog',),
        'prerequisites': ('READY',),
        'successors': ('BOOT_START',),
    },
    {
        'code': 'RESET',
        'category': 'boot',
        'kind': 'bench',
        'label_zh': '复位动作/复位回执',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'state_change',
        'recoverable': True,
        'raw_marker': ('uut-switch1.off', 'reset'),
        'prerequisites': (),
        'successors': ('BOOT_START',),
    },
    {
        'code': 'BOOT_STRAP_ON',
        'category': 'flash',
        'kind': 'bench',
        'label_zh': '启动 strap 打开',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'state_change',
        'recoverable': True,
        'raw_marker': ('uut-csk-boot.on',),
        'prerequisites': ('RESET',),
        'successors': ('BOOT_STRAP_OFF',),
    },
    {
        'code': 'BOOT_STRAP_OFF',
        'category': 'flash',
        'kind': 'bench',
        'label_zh': '启动 strap 关闭',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'state_change',
        'recoverable': True,
        'raw_marker': ('uut-csk-boot.off',),
        'prerequisites': ('BOOT_STRAP_ON',),
        'successors': ('POWER_ON',),
    },
    {
        'code': 'SERIAL_OPEN',
        'category': 'serial',
        'kind': 'tool',
        'label_zh': '工具打开串口',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': ('COM4 921600',),
        'prerequisites': (),
        'successors': ('SERIAL_UP',),
    },
    {
        'code': 'SERIAL_CLOSE',
        'category': 'serial',
        'kind': 'tool',
        'label_zh': '工具关闭串口',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('SERIAL_OPEN',),
        'successors': (),
    },
    {
        'code': 'SERIAL_UP',
        'category': 'serial',
        'kind': 'device',
        'label_zh': '串口链路建立/有数据',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'state_change',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('SERIAL_OPEN',),
        'successors': ('READY', 'BOOT_START'),
    },
    {
        'code': 'SERIAL_DOWN',
        'category': 'serial',
        'kind': 'device',
        'label_zh': '串口链路断开/无数据',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'state_change',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('SERIAL_UP',),
        'successors': ('SERIAL_UP',),
    },
    {
        'code': 'SERIAL_BUSY',
        'category': 'serial',
        'kind': 'tool',
        'label_zh': '串口被其他进程占用',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': ('Access is denied', '拒绝访问'),
        'prerequisites': (),
        'successors': (),
    },
    {
        'code': 'SERIAL_TIMEOUT',
        'category': 'serial',
        'kind': 'tool',
        'label_zh': '串口读取超时',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('SERIAL_OPEN',),
        'successors': (),
    },
    {
        'code': 'USB_ENUM',
        'category': 'usb',
        'kind': 'device',
        'label_zh': 'USB 设备枚举成功',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'state_change',
        'recoverable': True,
        'raw_marker': ('VID_', 'USB\\VID_'),
        'prerequisites': (),
        'successors': ('USB_UAC_READY',),
    },
    {
        'code': 'USB_MISSING',
        'category': 'usb',
        'kind': 'device',
        'label_zh': 'USB 设备未枚举/掉线',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'state_change',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('POWER_ON',),
        'successors': ('USB_ENUM',),
    },
    {
        'code': 'USB_UAC_READY',
        'category': 'usb',
        'kind': 'device',
        'label_zh': 'UAC 8 通道声卡可用',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'merge',
        'recoverable': True,
        'raw_marker': ('UAC Audio', '8 通道'),
        'prerequisites': ('USB_ENUM',),
        'successors': ('RECORD_START',),
    },
    {
        'code': 'USB_RECONNECT',
        'category': 'usb',
        'kind': 'device',
        'label_zh': 'USB 重新连接（断电后恢复）',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'state_change',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('USB_MISSING',),
        'successors': ('USB_ENUM',),
    },
    {
        'code': 'NET_UP',
        'category': 'net',
        'kind': 'device',
        'label_zh': '网络可用（DNS/HTTPS 探测通过）',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'state_change',
        'recoverable': True,
        'raw_marker': ('dns: true', 'https: true'),
        'prerequisites': (),
        'successors': ('CLOUD_CONNECTED',),
    },
    {
        'code': 'NET_DOWN',
        'category': 'net',
        'kind': 'device',
        'label_zh': '网络不可用',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'state_change',
        'recoverable': True,
        'raw_marker': ('dns: false',),
        'prerequisites': ('NET_UP',),
        'successors': ('NET_UP',),
    },
    {
        'code': 'WIFI_CONNECTED',
        'category': 'net',
        'kind': 'device',
        'label_zh': '设备 Wi-Fi 已连接',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'state_change',
        'recoverable': True,
        'raw_marker': ('wifi connected',),
        'prerequisites': (),
        'successors': ('CLOUD_CONNECTED',),
    },
    {
        'code': 'WIFI_DISCONNECTED',
        'category': 'net',
        'kind': 'device',
        'label_zh': '设备 Wi-Fi 断开',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'state_change',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('WIFI_CONNECTED',),
        'successors': ('WIFI_CONNECTED',),
    },
    {
        'code': 'CLOUD_CONNECTED',
        'category': 'net',
        'kind': 'device',
        'label_zh': '云端通道建立',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'state_change',
        'recoverable': True,
        'raw_marker': ('cloud connected',),
        'prerequisites': ('NET_UP', 'WIFI_CONNECTED'),
        'successors': ('CLOUD_DISCONNECTED',),
    },
    {
        'code': 'CLOUD_DISCONNECTED',
        'category': 'net',
        'kind': 'device',
        'label_zh': '云端通道断开',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'state_change',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('CLOUD_CONNECTED',),
        'successors': ('CLOUD_CONNECTED',),
    },
    {
        'code': 'PROTOCOL_HANDSHAKE',
        'category': 'net',
        'kind': 'device',
        'label_zh': '协议握手/会话建立',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'merge',
        'recoverable': True,
        'raw_marker': ('APP client create',),
        'prerequisites': ('CLOUD_CONNECTED',),
        'successors': ('HEARTBEAT',),
    },
    {
        'code': 'HEARTBEAT_TIMEOUT',
        'category': 'net',
        'kind': 'device',
        'label_zh': '心跳超时',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('PROTOCOL_HANDSHAKE',),
        'successors': ('RECONNECTING',),
    },
    {
        'code': 'ONLINE_ASR_START',
        'category': 'asr',
        'kind': 'device',
        'label_zh': '在线识别请求开始',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'merge',
        'recoverable': True,
        'raw_marker': ('use_fixed_asr:0', '_asr_server_choose'),
        'prerequisites': ('NET_UP',),
        'successors': ('ONLINE_ASR_RESULT',),
    },
    {
        'code': 'ONLINE_ASR_RESULT',
        'category': 'asr',
        'kind': 'device',
        'label_zh': '在线识别返回结果',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': (),
        'prerequisites': ('ONLINE_ASR_START',),
        'successors': ('INTENT',),
    },
    {
        'code': 'ONLINE_ASR_ERROR',
        'category': 'asr',
        'kind': 'device',
        'label_zh': '在线识别失败（网络/服务）',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('ONLINE_ASR_START',),
        'successors': ('ERROR',),
    },
    {
        'code': 'PRE_WAKEUP',
        'category': 'wake',
        'kind': 'device',
        'label_zh': '预唤醒（候选，未确认）',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'merge',
        'recoverable': False,
        'raw_marker': ('Pre Wakeup', 'ncmThreshold'),
        'prerequisites': ('READY',),
        'successors': ('WAKEUP', 'WAKE_REJECT'),
    },
    {
        'code': 'WAKEUP',
        'category': 'wake',
        'kind': 'device',
        'label_zh': '主唤醒确认',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': ('Wakeup:', 'wakeup result'),
        'prerequisites': ('PRE_WAKEUP', 'READY'),
        'successors': ('ASR_START', 'PLAYER_START'),
    },
    {
        'code': 'WAKE_REJECT',
        'category': 'wake',
        'kind': 'device',
        'label_zh': '唤醒被拒绝（分数/关键词不达标）',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('PRE_WAKEUP',),
        'successors': ('NO_WAKE',),
    },
    {
        'code': 'WAKE_TIMEOUT',
        'category': 'wake',
        'kind': 'device',
        'label_zh': '等待唤醒超时',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('PLAYER_COMPLETE',),
        'successors': ('NO_WAKE',),
    },
    {
        'code': 'NO_WAKE',
        'category': 'wake',
        'kind': 'tool',
        'label_zh': '一次播报未唤醒（attempt 结果）',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': ('wake_seen=false',),
        'prerequisites': ('PLAYER_COMPLETE',),
        'successors': (),
    },
    {
        'code': 'WAKE_FALSE_POSITIVE',
        'category': 'wake',
        'kind': 'framework',
        'label_zh': '预唤醒被误判为唤醒',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('PRE_WAKEUP',),
        'successors': ('WAKEUP',),
    },
    {
        'code': 'WAKE_LATE',
        'category': 'wake',
        'kind': 'framework',
        'label_zh': '迟到唤醒（窗口外到达）',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('PLAYER_COMPLETE',),
        'successors': ('WAKEUP',),
    },
    {
        'code': 'WAKE_CANCELLED',
        'category': 'wake',
        'kind': 'tool',
        'label_zh': '唤醒后取消后续播报',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('WAKEUP',),
        'successors': ('PLAYER_COMPLETE',),
    },
    {
        'code': 'VAD_START',
        'category': 'vad',
        'kind': 'device',
        'label_zh': '检测到人声开始',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'merge',
        'recoverable': True,
        'raw_marker': ('vad start',),
        'prerequisites': ('RECORD_START',),
        'successors': ('VAD_END',),
    },
    {
        'code': 'VAD_END',
        'category': 'vad',
        'kind': 'device',
        'label_zh': '人声结束',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'merge',
        'recoverable': True,
        'raw_marker': ('vad end',),
        'prerequisites': ('VAD_START',),
        'successors': ('ASR_START',),
    },
    {
        'code': 'VAD_TIMEOUT',
        'category': 'vad',
        'kind': 'device',
        'label_zh': 'VAD 等待人声超时',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('RECORD_START',),
        'successors': ('NO_WAKE',),
    },
    {
        'code': 'VAD_NO_SPEECH',
        'category': 'vad',
        'kind': 'device',
        'label_zh': '录音窗口内无人声',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('RECORD_START',),
        'successors': (),
    },
    {
        'code': 'ASR_START',
        'category': 'asr',
        'kind': 'device',
        'label_zh': '开始识别',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'merge',
        'recoverable': True,
        'raw_marker': ('asr start',),
        'prerequisites': ('WAKEUP', 'VAD_END'),
        'successors': ('ASR_RESULT',),
    },
    {
        'code': 'ASR_RESULT',
        'category': 'asr',
        'kind': 'device',
        'label_zh': '识别结果返回',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': ('intent', 'text'),
        'prerequisites': ('ASR_START',),
        'successors': ('INTENT',),
    },
    {
        'code': 'ASR_EMPTY',
        'category': 'asr',
        'kind': 'device',
        'label_zh': '识别为空结果',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('ASR_START',),
        'successors': (),
    },
    {
        'code': 'ASR_ERROR',
        'category': 'asr',
        'kind': 'device',
        'label_zh': '识别失败（引擎/超时/异常）',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('ASR_START',),
        'successors': ('ERROR',),
    },
    {
        'code': 'ASR_TIMEOUT',
        'category': 'asr',
        'kind': 'device',
        'label_zh': '识别超时',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('ASR_START',),
        'successors': (),
    },
    {
        'code': 'INTENT',
        'category': 'command',
        'kind': 'device',
        'label_zh': '意图/命令解析完成',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': ('intent',),
        'prerequisites': ('ASR_RESULT',),
        'successors': ('COMMAND_ACK',),
    },
    {
        'code': 'SLOT_MISMATCH',
        'category': 'command',
        'kind': 'framework',
        'label_zh': '槽位/参数错误',
        'pass_role': 'blocking',
        'scope': 'tcl',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': ('ci wo kong tiao → 次卧',),
        'prerequisites': ('INTENT',),
        'successors': ('FAIL',),
    },
    {
        'code': 'COMMAND_SENT',
        'category': 'command',
        'kind': 'tool',
        'label_zh': '下发业务命令',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('INTENT',),
        'successors': ('COMMAND_ACK',),
    },
    {
        'code': 'COMMAND_ACK',
        'category': 'command',
        'kind': 'device',
        'label_zh': '业务命令直接回执',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': ('ACK', 'OK'),
        'prerequisites': ('COMMAND_SENT',),
        'successors': ('COMMAND_EVIDENCE',),
    },
    {
        'code': 'COMMAND_EVIDENCE',
        'category': 'command',
        'kind': 'device',
        'label_zh': '命令执行旁证',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': (),
        'prerequisites': ('COMMAND_ACK',),
        'successors': ('EXEC_END',),
    },
    {
        'code': 'COMMAND_TIMEOUT',
        'category': 'command',
        'kind': 'tool',
        'label_zh': '命令回执超时',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('COMMAND_SENT',),
        'successors': (),
    },
    {
        'code': 'COMMAND_RETRY',
        'category': 'command',
        'kind': 'tool',
        'label_zh': '命令重试',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('COMMAND_TIMEOUT',),
        'successors': ('COMMAND_ACK',),
    },
    {
        'code': 'EXEC_FAIL',
        'category': 'command',
        'kind': 'device',
        'label_zh': '业务执行失败/设备回报失败码',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('INTENT', 'COMMAND_ACK'),
        'successors': ('ERROR',),
    },
    {
        'code': 'TTS_REQUEST',
        'category': 'tts',
        'kind': 'tool',
        'label_zh': 'TTS 合成请求',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': ('tts request',),
        'prerequisites': (),
        'successors': ('TTS_START',),
    },
    {
        'code': 'TTS_START',
        'category': 'tts',
        'kind': 'device',
        'label_zh': 'TTS 开始合成',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'merge',
        'recoverable': True,
        'raw_marker': ('tts start',),
        'prerequisites': ('TTS_REQUEST',),
        'successors': ('TTS_END',),
    },
    {
        'code': 'TTS_FIRST_PACKET',
        'category': 'tts',
        'kind': 'device',
        'label_zh': 'TTS 首包到达',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'merge',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('TTS_START',),
        'successors': ('TTS_END',),
    },
    {
        'code': 'TTS_END',
        'category': 'tts',
        'kind': 'device',
        'label_zh': 'TTS 合成结束',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'merge',
        'recoverable': False,
        'raw_marker': ('tts end',),
        'prerequisites': ('TTS_START',),
        'successors': ('PLAYER_START',),
    },
    {
        'code': 'TTS_ERROR',
        'category': 'tts',
        'kind': 'device',
        'label_zh': 'TTS 合成失败',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('TTS_START',),
        'successors': ('ERROR',),
    },
    {
        'code': 'TTS_TIMEOUT',
        'category': 'tts',
        'kind': 'device',
        'label_zh': 'TTS 合成超时',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('TTS_START',),
        'successors': (),
    },
    {
        'code': 'PLAYER_REQUESTED',
        'category': 'player',
        'kind': 'tool',
        'label_zh': '请求设备播放',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': ('play_request',),
        'prerequisites': ('TTS_END',),
        'successors': ('PLAYER_START',),
    },
    {
        'code': 'PLAYER_START',
        'category': 'player',
        'kind': 'device',
        'label_zh': '设备开始播放/响应播报',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'merge',
        'recoverable': False,
        'raw_marker': ('start player',),
        'prerequisites': ('PLAYER_REQUESTED',),
        'successors': ('PLAYER_COMPLETE',),
    },
    {
        'code': 'PLAYER_COMPLETE',
        'category': 'player',
        'kind': 'device',
        'label_zh': '设备播报结束',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'merge',
        'recoverable': False,
        'raw_marker': ('Refresh PA to OFF',),
        'prerequisites': ('PLAYER_START',),
        'successors': ('WAKEUP', 'NO_WAKE'),
    },
    {
        'code': 'PLAYER_PAUSE',
        'category': 'player',
        'kind': 'device',
        'label_zh': '播放暂停',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'state_change',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('PLAYER_START',),
        'successors': ('PLAYER_RESUME', 'PLAYER_COMPLETE'),
    },
    {
        'code': 'PLAYER_RESUME',
        'category': 'player',
        'kind': 'device',
        'label_zh': '播放恢复',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'state_change',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('PLAYER_PAUSE',),
        'successors': ('PLAYER_COMPLETE',),
    },
    {
        'code': 'PLAYER_STOP',
        'category': 'player',
        'kind': 'device',
        'label_zh': '播放停止',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'state_change',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('PLAYER_START',),
        'successors': ('PLAYER_COMPLETE',),
    },
    {
        'code': 'PLAYER_ERROR',
        'category': 'player',
        'kind': 'device',
        'label_zh': '播放器错误',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('PLAYER_START',),
        'successors': ('ERROR',),
    },
    {
        'code': 'PLAYER_TIMEOUT',
        'category': 'player',
        'kind': 'tool',
        'label_zh': '播放器无响应/超时',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': ('PLAYER_PROBE_TIMEOUT',),
        'prerequisites': ('PLAYER_REQUESTED',),
        'successors': (),
    },
    {
        'code': 'PLAYER_BLOCKED',
        'category': 'player',
        'kind': 'device',
        'label_zh': '播放被阻塞（设备忙/权限）',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('PLAYER_REQUESTED',),
        'successors': (),
    },
    {
        'code': 'HOST_AUDIO_START',
        'category': 'player',
        'kind': 'tool',
        'label_zh': '主机开始播放音频',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': (),
        'successors': ('HOST_AUDIO_END',),
    },
    {
        'code': 'HOST_AUDIO_END',
        'category': 'player',
        'kind': 'tool',
        'label_zh': '主机播放结束',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('HOST_AUDIO_START',),
        'successors': ('WAKEUP', 'NO_WAKE'),
    },
    {
        'code': 'HOST_AUDIO_ERROR',
        'category': 'player',
        'kind': 'tool',
        'label_zh': '主机播放失败/无 ffmpeg',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': ('ffmpeg not found',),
        'prerequisites': ('HOST_AUDIO_START',),
        'successors': ('ERROR',),
    },
    {
        'code': 'HOST_AUDIO_TIMEOUT',
        'category': 'player',
        'kind': 'tool',
        'label_zh': '主机播放超时',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('HOST_AUDIO_START',),
        'successors': (),
    },
    {
        'code': 'RECORD_START',
        'category': 'record',
        'kind': 'tool',
        'label_zh': '开始录音',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'merge',
        'recoverable': True,
        'raw_marker': ('record start',),
        'prerequisites': ('USB_UAC_READY',),
        'successors': ('RECORD_END',),
    },
    {
        'code': 'RECORD_END',
        'category': 'record',
        'kind': 'tool',
        'label_zh': '录音结束',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'merge',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('RECORD_START',),
        'successors': ('VAD_START', 'ASR_START'),
    },
    {
        'code': 'RECORD_PASS',
        'category': 'record',
        'kind': 'judgement',
        'label_zh': '录音链路验证通过（160001Hz/8ch/16bit）',
        'pass_role': 'positive_evidence',
        'scope': 'bench',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': (),
        'prerequisites': ('RECORD_END',),
        'successors': (),
    },
    {
        'code': 'RECORD_FAIL',
        'category': 'record',
        'kind': 'judgement',
        'label_zh': '录音链路验证失败',
        'pass_role': 'blocking',
        'scope': 'bench',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': (),
        'prerequisites': ('RECORD_END',),
        'successors': (),
    },
    {
        'code': 'RECORD_BLOCKED',
        'category': 'record',
        'kind': 'judgement',
        'label_zh': '录音验证阻塞（端点缺失/驱动不支持）',
        'pass_role': 'blocking',
        'scope': 'bench',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': (),
        'prerequisites': (),
        'successors': (),
    },
    {
        'code': 'RECORD_OVERFLOW',
        'category': 'record',
        'kind': 'device',
        'label_zh': '录音溢出/丢帧',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('RECORD_START',),
        'successors': ('RECORD_END',),
    },
    {
        'code': 'RECORD_EMPTY',
        'category': 'record',
        'kind': 'framework',
        'label_zh': '录音文件为空（0 字节/无有效波形）',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': ('serial log empty',),
        'prerequisites': ('RECORD_END',),
        'successors': ('EVIDENCE_GAP',),
    },
    {
        'code': 'FLASH_PREFLIGHT',
        'category': 'flash',
        'kind': 'bench',
        'label_zh': '烧录前置检查完成',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': ('preflight',),
        'prerequisites': (),
        'successors': ('BURN_START',),
    },
    {
        'code': 'BURN_START',
        'category': 'flash',
        'kind': 'bench',
        'label_zh': '开始烧录',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': ('Uart_Burn_Tool', 'BurnTool'),
        'prerequisites': ('FLASH_PREFLIGHT',),
        'successors': ('BURN_SUCCESS',),
    },
    {
        'code': 'BURN_SUCCESS',
        'category': 'flash',
        'kind': 'bench',
        'label_zh': '烧录成功（工具回执）',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': ('MD5 CHECK SUCCESS', 'FLASH DOWNLOAD SUCCESS', '写入完成，成功'),
        'prerequisites': ('BURN_START',),
        'successors': ('VERIFY_VERSION',),
    },
    {
        'code': 'BURN_FAIL',
        'category': 'flash',
        'kind': 'bench',
        'label_zh': '烧录失败',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': ('FLASH DOWNLOAD FAIL', '600s timeout'),
        'prerequisites': ('BURN_START',),
        'successors': ('ERROR',),
    },
    {
        'code': 'BURN_TIMEOUT',
        'category': 'flash',
        'kind': 'bench',
        'label_zh': '烧录超时',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('BURN_START',),
        'successors': ('BURN_FAIL',),
    },
    {
        'code': 'VERIFY_VERSION',
        'category': 'flash',
        'kind': 'bench',
        'label_zh': '版本回读/交叉校验一致',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': ('SYS_VER:', 'asr_dsp/'),
        'prerequisites': ('BURN_SUCCESS',),
        'successors': ('READY',),
    },
    {
        'code': 'VERSION_MISMATCH',
        'category': 'flash',
        'kind': 'bench',
        'label_zh': '版本回读与目标不一致',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': (),
        'prerequisites': ('VERIFY_VERSION',),
        'successors': ('FAIL',),
    },
    {
        'code': 'DEVICE_MODE',
        'category': 'mode',
        'kind': 'device',
        'label_zh': '设备运行模式切换（正常/工厂/测试）',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'state_change',
        'recoverable': True,
        'raw_marker': ('setloglev', 'factory mode'),
        'prerequisites': ('READY',),
        'successors': ('READY',),
    },
    {
        'code': 'OTA_START',
        'category': 'ota',
        'kind': 'device',
        'label_zh': 'OTA 升级开始',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('READY', 'NET_UP'),
        'successors': ('OTA_SUCCESS',),
    },
    {
        'code': 'OTA_SUCCESS',
        'category': 'ota',
        'kind': 'device',
        'label_zh': 'OTA 升级成功',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': (),
        'prerequisites': ('OTA_START',),
        'successors': ('REBOOT',),
    },
    {
        'code': 'OTA_FAIL',
        'category': 'ota',
        'kind': 'device',
        'label_zh': 'OTA 升级失败',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('OTA_START',),
        'successors': ('ERROR',),
    },
    {
        'code': 'ERROR',
        'category': 'error',
        'kind': 'device',
        'label_zh': '设备上报错误（带原始错误码）',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': ('E42', 'error'),
        'prerequisites': ('READY',),
        'successors': ('RECOVERED',),
    },
    {
        'code': 'WARN',
        'category': 'error',
        'kind': 'device',
        'label_zh': '设备/工具告警（未阻断）',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'merge',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': (),
        'successors': (),
    },
    {
        'code': 'RECOVERED',
        'category': 'recovery',
        'kind': 'device',
        'label_zh': '设备从错误状态恢复',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': ('recovered', 'ready'),
        'prerequisites': ('ERROR',),
        'successors': ('READY',),
    },
    {
        'code': 'RECOVERY_FAILED',
        'category': 'recovery',
        'kind': 'device',
        'label_zh': '恢复失败/重试耗尽',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': (),
        'prerequisites': ('ERROR',),
        'successors': ('BLOCKED',),
    },
    {
        'code': 'RESOURCE_WARN',
        'category': 'resource',
        'kind': 'device',
        'label_zh': '资源越阈值告警（CPU/内存/磁盘）',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'merge',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': (),
        'successors': (),
    },
    {
        'code': 'RESOURCE_CRITICAL',
        'category': 'resource',
        'kind': 'device',
        'label_zh': '资源耗尽（导致功能不可用）',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('RESOURCE_WARN',),
        'successors': ('ERROR',),
    },
    {
        'code': 'DEVICE_UNPARSED',
        'category': 'unknown',
        'kind': 'framework',
        'label_zh': '未识别的设备原始行（保留摘要+sha256+证据）',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'merge',
        'recoverable': True,
        'raw_marker': ('任意未匹配 marker 的串口行',),
        'prerequisites': (),
        'successors': (),
    },
    {
        'code': 'DEVICE_MARKER_ERROR',
        'category': 'unknown',
        'kind': 'framework',
        'label_zh': 'marker 规则/正则加载失败',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': (),
        'successors': (),
    },
    {
        'code': 'EVIDENCE_GAP',
        'category': 'recovery',
        'kind': 'framework',
        'label_zh': '证据缺口（空日志/缺文件/缺哈希）',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': ('serial log empty',),
        'prerequisites': (),
        'successors': (),
    },
    {
        'code': 'STATE_UNREGISTERED',
        'category': 'unknown',
        'kind': 'framework',
        'label_zh': '状态码未在目录/项目扩展中登记',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': (),
        'successors': ('DEVICE_UNPARSED',),
    },
    {
        'code': 'WAKE_KEYWORD',
        'category': 'wake',
        'kind': 'device',
        'label_zh': '唤醒关键词回调',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': ('wakeup_callback, keyword:',),
        'prerequisites': ('READY',),
        'successors': ('WAKE_TTS_CALLBACK', 'ASR_START'),
    },
    {
        'code': 'WAKE_TTS_CALLBACK',
        'category': 'tts',
        'kind': 'device',
        'label_zh': '唤醒后 TTS 回调',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': ('wakeup_tts_callback, tts:',),
        'prerequisites': ('WAKE_KEYWORD',),
        'successors': ('TTS_PLAY',),
    },
    {
        'code': 'ASR_KEYWORD',
        'category': 'asr',
        'kind': 'device',
        'label_zh': '本地 ASR 关键词结果',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': ('keyword:',),
        'prerequisites': ('ASR_START',),
        'successors': ('INTENT',),
    },
    {
        'code': 'TTS_PLAY_ID',
        'category': 'tts',
        'kind': 'device',
        'label_zh': 'TTS/播放标识',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'merge',
        'recoverable': False,
        'raw_marker': ('play id : ',),
        'prerequisites': ('TTS_START',),
        'successors': ('PLAYER_START',),
    },
    {
        'code': 'ONLINE_ASR_TEXT',
        'category': 'asr',
        'kind': 'device',
        'label_zh': '在线 ASR 文本回调',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': ('online_asr_callbak, text:',),
        'prerequisites': ('ONLINE_REQUEST_START',),
        'successors': ('ONLINE_SKILL_TEXT',),
    },
    {
        'code': 'ONLINE_TTS_TEXT',
        'category': 'tts',
        'kind': 'device',
        'label_zh': '在线 ASR TTS 回调',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': ('online_asr_tts_callbak, tts:',),
        'prerequisites': ('ONLINE_ASR_TEXT',),
        'successors': ('PLAYER_START',),
    },
    {
        'code': 'ONLINE_SKILL_TEXT',
        'category': 'command',
        'kind': 'device',
        'label_zh': '在线 Skill 文本回调',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': ('online_skill_callbak, text:',),
        'prerequisites': ('ONLINE_ASR_TEXT',),
        'successors': ('DEVICE_ACTION',),
    },
    {
        'code': 'TTS_COMPLETE',
        'category': 'tts',
        'kind': 'device',
        'label_zh': 'TTS 合成/播放完成',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': (),
        'prerequisites': ('TTS_END',),
        'successors': ('PLAYER_COMPLETE',),
    },
    {
        'code': 'TTS_INTERRUPTED',
        'category': 'tts',
        'kind': 'device',
        'label_zh': 'TTS 被中断',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('TTS_START',),
        'successors': ('TTS_ERROR', 'RECOVERED'),
    },
    {
        'code': 'TTS_PAUSED',
        'category': 'tts',
        'kind': 'device',
        'label_zh': 'TTS 暂停',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'state_change',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('TTS_START',),
        'successors': ('TTS_RESUMED',),
    },
    {
        'code': 'TTS_RESUMED',
        'category': 'tts',
        'kind': 'device',
        'label_zh': 'TTS 恢复',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'state_change',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('TTS_PAUSED',),
        'successors': ('TTS_COMPLETE',),
    },
    {
        'code': 'TTS_PLAY',
        'category': 'tts',
        'kind': 'device',
        'label_zh': 'TTS 音频开始播放',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': ('play id : ',),
        'prerequisites': ('TTS_START', 'TTS_PLAY_ID'),
        'successors': ('PLAYER_START',),
    },
    {
        'code': 'PLAYER_PREPARE',
        'category': 'player',
        'kind': 'device',
        'label_zh': '播放器准备',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'state_change',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('TTS_COMPLETE', 'TTS_END'),
        'successors': ('PLAYER_START',),
    },
    {
        'code': 'PLAYER_INTERRUPTED',
        'category': 'player',
        'kind': 'device',
        'label_zh': '播放被中断',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('PLAYER_START',),
        'successors': ('PLAYER_STOP', 'ERROR'),
    },
    {
        'code': 'ONLINE_REQUEST_START',
        'category': 'net',
        'kind': 'device',
        'label_zh': '在线请求开始',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': (),
        'prerequisites': ('NET_UP',),
        'successors': ('ONLINE_REQUEST_ID',),
    },
    {
        'code': 'ONLINE_REQUEST_ID',
        'category': 'net',
        'kind': 'device',
        'label_zh': '在线请求 ID 返回',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': ('request_id',),
        'prerequisites': ('ONLINE_REQUEST_START',),
        'successors': ('ONLINE_RESPONSE',),
    },
    {
        'code': 'ONLINE_RESPONSE',
        'category': 'net',
        'kind': 'device',
        'label_zh': '在线请求响应',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': (),
        'prerequisites': ('ONLINE_REQUEST_ID',),
        'successors': ('ONLINE_ASR_TEXT', 'ONLINE_SKILL_TEXT'),
    },
    {
        'code': 'ONLINE_TIMEOUT',
        'category': 'net',
        'kind': 'device',
        'label_zh': '在线请求超时',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('ONLINE_REQUEST_START',),
        'successors': ('ONLINE_RETRY',),
    },
    {
        'code': 'ONLINE_ERROR',
        'category': 'net',
        'kind': 'device',
        'label_zh': '在线请求错误',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('ONLINE_REQUEST_START',),
        'successors': ('ONLINE_RETRY',),
    },
    {
        'code': 'ONLINE_CANCEL',
        'category': 'net',
        'kind': 'device',
        'label_zh': '在线请求取消',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('ONLINE_REQUEST_START',),
        'successors': ('WAKEUP',),
    },
    {
        'code': 'ONLINE_RETRY',
        'category': 'net',
        'kind': 'device',
        'label_zh': '在线请求重试',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('ONLINE_TIMEOUT', 'ONLINE_ERROR'),
        'successors': ('ONLINE_REQUEST_START',),
    },
    {
        'code': 'REBOOT_REQUEST',
        'category': 'boot',
        'kind': 'bench',
        'label_zh': '请求重启',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('READY',),
        'successors': ('REBOOT',),
    },
    {
        'code': 'REBOOT_REASON',
        'category': 'boot',
        'kind': 'device',
        'label_zh': '重启原因',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('REBOOT',),
        'successors': ('BOOT_START',),
    },
    {
        'code': 'FIRMWARE_VERSION',
        'category': 'version',
        'kind': 'device',
        'label_zh': '固件版本',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': ('SYS_VER:',),
        'prerequisites': ('READY',),
        'successors': ('READY',),
    },
    {
        'code': 'APP_VERSION',
        'category': 'version',
        'kind': 'device',
        'label_zh': '应用版本',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': (),
        'prerequisites': ('READY',),
        'successors': ('READY',),
    },
    {
        'code': 'ALGO_VERSION',
        'category': 'version',
        'kind': 'device',
        'label_zh': '算法版本',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': ('asr_dsp/',),
        'prerequisites': ('READY',),
        'successors': ('READY',),
    },
    {
        'code': 'PROTOCOL_VERSION',
        'category': 'version',
        'kind': 'device',
        'label_zh': '协议版本',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': (),
        'prerequisites': ('PROTOCOL_HANDSHAKE',),
        'successors': ('READY',),
    },
    {
        'code': 'PROFILE_VERSION',
        'category': 'version',
        'kind': 'tool',
        'label_zh': 'profile 版本',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': (),
        'prerequisites': (),
        'successors': (),
    },
    {
        'code': 'WIFI_SCAN',
        'category': 'net',
        'kind': 'device',
        'label_zh': 'Wi-Fi 扫描',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'merge',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('READY',),
        'successors': ('WIFI_CONNECTING',),
    },
    {
        'code': 'WIFI_CONNECTING',
        'category': 'net',
        'kind': 'device',
        'label_zh': 'Wi-Fi 连接中',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'state_change',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('WIFI_SCAN',),
        'successors': ('WIFI_CONNECTED',),
    },
    {
        'code': 'WIFI_IP',
        'category': 'net',
        'kind': 'device',
        'label_zh': 'Wi-Fi 获取 IP',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': ('ip',),
        'prerequisites': ('WIFI_CONNECTED',),
        'successors': ('NET_UP',),
    },
    {
        'code': 'DNS_OK',
        'category': 'net',
        'kind': 'device',
        'label_zh': 'DNS 解析成功',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'merge',
        'recoverable': True,
        'raw_marker': ('dns: true',),
        'prerequisites': ('NET_UP',),
        'successors': ('CLOUD_ONLINE',),
    },
    {
        'code': 'DNS_FAIL',
        'category': 'net',
        'kind': 'device',
        'label_zh': 'DNS 解析失败',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': ('dns: false',),
        'prerequisites': ('NET_UP',),
        'successors': ('CLOUD_OFFLINE',),
    },
    {
        'code': 'CLOUD_ONLINE',
        'category': 'net',
        'kind': 'device',
        'label_zh': '云端在线',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'state_change',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('NET_UP', 'DNS_OK'),
        'successors': ('CLOUD_OFFLINE',),
    },
    {
        'code': 'CLOUD_OFFLINE',
        'category': 'net',
        'kind': 'device',
        'label_zh': '云端离线',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'state_change',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('CLOUD_ONLINE',),
        'successors': ('RECONNECTING',),
    },
    {
        'code': 'BT_ENABLED',
        'category': 'bluetooth',
        'kind': 'device',
        'label_zh': '蓝牙打开',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'state_change',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('READY',),
        'successors': ('BT_SCANNING',),
    },
    {
        'code': 'BT_DISABLED',
        'category': 'bluetooth',
        'kind': 'device',
        'label_zh': '蓝牙关闭',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'state_change',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('BT_ENABLED',),
        'successors': ('BT_ENABLED',),
    },
    {
        'code': 'BT_SCANNING',
        'category': 'bluetooth',
        'kind': 'device',
        'label_zh': '蓝牙扫描',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'merge',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('BT_ENABLED',),
        'successors': ('BT_CONNECTING',),
    },
    {
        'code': 'BT_CONNECTING',
        'category': 'bluetooth',
        'kind': 'device',
        'label_zh': '蓝牙连接中',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'state_change',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('BT_SCANNING',),
        'successors': ('BT_CONNECTED',),
    },
    {
        'code': 'BT_CONNECTED',
        'category': 'bluetooth',
        'kind': 'device',
        'label_zh': '蓝牙已连接',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'state_change',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('BT_CONNECTING', 'BT_PAIRING'),
        'successors': ('BT_DISCONNECTED',),
    },
    {
        'code': 'BT_DISCONNECTED',
        'category': 'bluetooth',
        'kind': 'device',
        'label_zh': '蓝牙断开',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'state_change',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('BT_CONNECTED',),
        'successors': ('BT_CONNECTING',),
    },
    {
        'code': 'BT_PAIRING',
        'category': 'bluetooth',
        'kind': 'device',
        'label_zh': '蓝牙配对',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'state_change',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('BT_CONNECTING',),
        'successors': ('BT_CONNECTED',),
    },
    {
        'code': 'BT_ERROR',
        'category': 'bluetooth',
        'kind': 'device',
        'label_zh': '蓝牙错误',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('BT_CONNECTING', 'BT_PAIRING'),
        'successors': ('RECOVERED',),
    },
    {
        'code': 'MIC_ERROR',
        'category': 'audio',
        'kind': 'device',
        'label_zh': '麦克风异常',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('RECORD_START',),
        'successors': ('RECOVERED',),
    },
    {
        'code': 'SOUNDCARD_MISSING',
        'category': 'audio',
        'kind': 'device',
        'label_zh': '声卡缺失',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': ('device-not-found',),
        'prerequisites': ('USB_ENUM',),
        'successors': ('USB_UAC_READY',),
    },
    {
        'code': 'AUDIO_BUFFER',
        'category': 'audio',
        'kind': 'device',
        'label_zh': '音频缓冲状态',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'merge',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('RECORD_START',),
        'successors': ('AUDIO_UNDERRUN',),
    },
    {
        'code': 'AUDIO_UNDERRUN',
        'category': 'audio',
        'kind': 'device',
        'label_zh': '音频欠载/丢帧',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('AUDIO_BUFFER',),
        'successors': ('RECOVERED',),
    },
    {
        'code': 'DEVICE_LOST',
        'category': 'audio',
        'kind': 'device',
        'label_zh': '设备链路丢失',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('READY',),
        'successors': ('DEVICE_RECOVERED',),
    },
    {
        'code': 'DEVICE_RECOVERED',
        'category': 'audio',
        'kind': 'device',
        'label_zh': '设备链路恢复',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('DEVICE_LOST',),
        'successors': ('READY',),
    },
    {
        'code': 'LOCAL_SKILL',
        'category': 'command',
        'kind': 'device',
        'label_zh': '本地技能/意图',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': (),
        'prerequisites': ('INTENT',),
        'successors': ('DEVICE_ACTION',),
    },
    {
        'code': 'ONLINE_SKILL',
        'category': 'command',
        'kind': 'device',
        'label_zh': '在线技能结果',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': (),
        'prerequisites': ('ONLINE_SKILL_TEXT',),
        'successors': ('DEVICE_ACTION',),
    },
    {
        'code': 'DEVICE_ACTION',
        'category': 'command',
        'kind': 'device',
        'label_zh': '设备动作',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': (),
        'prerequisites': ('INTENT', 'LOCAL_SKILL', 'ONLINE_SKILL'),
        'successors': ('DEVICE_ACK',),
    },
    {
        'code': 'DEVICE_ACK',
        'category': 'command',
        'kind': 'device',
        'label_zh': '设备动作回执',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': (),
        'prerequisites': ('DEVICE_ACTION',),
        'successors': ('EXEC_END',),
    },
    {
        'code': 'DEVICE_NACK',
        'category': 'command',
        'kind': 'device',
        'label_zh': '设备动作拒绝',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('DEVICE_ACTION',),
        'successors': ('ERROR',),
    },
    {
        'code': 'STATE_CHANGED',
        'category': 'command',
        'kind': 'device',
        'label_zh': '业务状态变化',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': (),
        'prerequisites': ('DEVICE_ACK',),
        'successors': ('EXEC_END',),
    },
    {
        'code': 'MARKER_UNMATCHED',
        'category': 'unknown',
        'kind': 'framework',
        'label_zh': 'marker 未匹配',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'merge',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': (),
        'successors': ('DEVICE_UNPARSED',),
    },
    {
        'code': 'MARKER_SCOPE_REJECTED',
        'category': 'unknown',
        'kind': 'framework',
        'label_zh': 'marker 被 scope 拒绝',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': (),
        'successors': ('DEVICE_UNPARSED',),
    },
    {
        'code': 'EVENT_DUPLICATE',
        'category': 'unknown',
        'kind': 'framework',
        'label_zh': '事件重复',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'merge',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': (),
        'successors': (),
    },
    {
        'code': 'EVENT_LATE',
        'category': 'unknown',
        'kind': 'framework',
        'label_zh': '事件迟到',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': (),
        'successors': (),
    },
    {
        'code': 'EVIDENCE_HASH_MISMATCH',
        'category': 'recovery',
        'kind': 'framework',
        'label_zh': '证据哈希不符',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': (),
        'successors': ('EVIDENCE_GAP',),
    },
    {
        'code': 'BROADCAST',
        'category': 'wake',
        'kind': 'tool',
        'label_zh': '一次播报尝试（TOOL 动作）',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': ('play-R…-A…',),
        'prerequisites': ('PLAYER_REQUESTED',),
        'successors': ('NO_WAKE', 'WAKEUP'),
    },
    {
        'code': 'HEARTBEAT',
        'category': 'net',
        'kind': 'device',
        'label_zh': '协议心跳',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'merge',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('PROTOCOL_HANDSHAKE',),
        'successors': ('CLOUD_CONNECTED',),
    },
    {
        'code': 'RECONNECTING',
        'category': 'net',
        'kind': 'device',
        'label_zh': '链路重连中',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'state_change',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('HEARTBEAT_TIMEOUT', 'CLOUD_DISCONNECTED'),
        'successors': ('CLOUD_CONNECTED',),
    },
    {
        'code': 'EXEC_END',
        'category': 'command',
        'kind': 'device',
        'label_zh': '业务执行结束（成功路径）',
        'pass_role': 'positive_evidence',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': (),
        'prerequisites': ('COMMAND_ACK', 'COMMAND_EVIDENCE'),
        'successors': ('ROUND_RESULT',),
    },
    {
        'code': 'TOOL_ERROR',
        'category': 'tool',
        'kind': 'tool',
        'label_zh': '工具自身异常（非设备）',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': (),
        'successors': (),
    },
    {
        'code': 'DEVICE_NOT_FOUND',
        'category': 'tool',
        'kind': 'tool',
        'label_zh': '设备/端点未找到（role missing、声卡不存在）',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': ('device-not-found', 'no port matched'),
        'prerequisites': (),
        'successors': (),
    },
    {
        'code': 'DEVICE_AMBIGUOUS',
        'category': 'tool',
        'kind': 'tool',
        'label_zh': '设备/端点歧义（多条命中无法区分）',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': ('device-ambiguous', 'several ports matched'),
        'prerequisites': (),
        'successors': (),
    },
    {
        'code': 'BACKEND_UNAVAILABLE',
        'category': 'tool',
        'kind': 'tool',
        'label_zh': '运行时后端不可用（ffmpeg/声卡/引擎缺失）',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': ('backend-unavailable', 'UNSUPPORTED'),
        'prerequisites': (),
        'successors': (),
    },
    {
        'code': 'TOOL_TIMEOUT',
        'category': 'tool',
        'kind': 'tool',
        'label_zh': '工具/动作超时',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': (),
        'successors': (),
    },
    {
        'code': 'TOOL_RETRY',
        'category': 'tool',
        'kind': 'tool',
        'label_zh': '工具重试',
        'pass_role': 'diagnostic',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': (),
        'prerequisites': ('TOOL_TIMEOUT',),
        'successors': ('PASS',),
    },
    {
        'code': 'PROFILE_BLOCKED_CONTRACT',
        'category': 'tool',
        'kind': 'framework',
        'label_zh': 'profile 合同不满足，正式准入拒绝',
        'pass_role': 'blocking',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': True,
        'raw_marker': ('BLOCKED_PROFILE_CONTRACT',),
        'prerequisites': (),
        'successors': (),
    },
    {
        'code': 'TASK_START',
        'category': 'task',
        'kind': 'framework',
        'label_zh': '任务开始（测什么/为什么/范围）',
        'pass_role': 'none',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': (),
        'prerequisites': (),
        'successors': ('TASK_END',),
    },
    {
        'code': 'TASK_END',
        'category': 'task',
        'kind': 'framework',
        'label_zh': '任务结束（同时给出 recorded/test_outcome/delivery/final/gate）',
        'pass_role': 'none',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': (),
        'prerequisites': ('TASK_START',),
        'successors': (),
    },
    {
        'code': 'ROUND_RESULT',
        'category': 'task',
        'kind': 'framework',
        'label_zh': '单轮判定',
        'pass_role': 'none',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': (),
        'prerequisites': ('BROADCAST',),
        'successors': (),
    },
    {
        'code': 'MILESTONE',
        'category': 'task',
        'kind': 'framework',
        'label_zh': '阶段里程碑统计',
        'pass_role': 'none',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': (),
        'prerequisites': (),
        'successors': (),
    },
    {
        'code': 'PASS',
        'category': 'judgement',
        'kind': 'judgement',
        'label_zh': '通过',
        'pass_role': 'none',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': (),
        'prerequisites': (),
        'successors': (),
    },
    {
        'code': 'FAIL',
        'category': 'judgement',
        'kind': 'judgement',
        'label_zh': '失败',
        'pass_role': 'none',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': (),
        'prerequisites': (),
        'successors': (),
    },
    {
        'code': 'BLOCKED',
        'category': 'judgement',
        'kind': 'judgement',
        'label_zh': '阻塞（前置条件不满足）',
        'pass_role': 'none',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': (),
        'prerequisites': (),
        'successors': (),
    },
    {
        'code': 'NEEDS_REVIEW',
        'category': 'judgement',
        'kind': 'judgement',
        'label_zh': '需人工复核（交付不完整/无 gate/中断）',
        'pass_role': 'none',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': (),
        'prerequisites': (),
        'successors': (),
    },
    {
        'code': 'DELIVERY_INCOMPLETE',
        'category': 'delivery',
        'kind': 'delivery',
        'label_zh': '交付件缺失',
        'pass_role': 'none',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': (),
        'prerequisites': (),
        'successors': (),
    },
    {
        'code': 'INCOMPLETE',
        'category': 'delivery',
        'kind': 'delivery',
        'label_zh': '测试未完成（中断/未跑满）',
        'pass_role': 'none',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': (),
        'prerequisites': (),
        'successors': (),
    },
    {
        'code': 'UNKNOWN',
        'category': 'judgement',
        'kind': 'judgement',
        'label_zh': '无法判定',
        'pass_role': 'none',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': (),
        'prerequisites': (),
        'successors': (),
    },
    {
        'code': 'SKIPPED',
        'category': 'judgement',
        'kind': 'judgement',
        'label_zh': '明确跳过',
        'pass_role': 'none',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': (),
        'prerequisites': (),
        'successors': (),
    },
    {
        'code': 'CANCELLED',
        'category': 'judgement',
        'kind': 'judgement',
        'label_zh': '取消（人为/被新任务取代）',
        'pass_role': 'none',
        'scope': 'common',
        'duplicate_policy': 'keep',
        'recoverable': False,
        'raw_marker': ('VOID_SUPERSEDED_TIMING',),
        'prerequisites': (),
        'successors': (),
    },
)

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

CATALOG_SCHEMA = "lstest.state-catalog.v1"
EXTENSION_SCHEMA = "lstest.state-extension.v1"
REPORT_SCHEMA = "lstest.state-report.v1"
COVERAGE_SCHEMA = "lstest.state-coverage.v1"

CATEGORIES = (
    "power", "boot", "ready", "mode", "flash", "serial", "usb", "pdu", "net",
    "wake", "vad", "asr", "tts", "player", "record", "command", "ota",
    "resource", "error", "recovery", "unknown", "tool", "task", "judgement",
    "delivery", "version", "bluetooth", "audio",
)
KINDS = ("device", "bench", "tool", "framework", "judgement", "delivery")
#: 可以出现在设备时间线上的 kind；judgement/delivery 是结论，不得伪装成设备状态。
DEVICE_KINDS = ("device", "bench", "tool", "framework")
PASS_ROLES = ("positive_evidence", "blocking", "diagnostic", "none")
DUPLICATE_POLICIES = ("merge", "state_change", "keep")
COMMON_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
PROJECT_CODE_RE = re.compile(r"^P_[A-Z0-9_]+$")
PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

DEFAULT_DUPLICATE_WINDOW_S = 2.0
DEFAULT_RECOVERY_WINDOW_S = 30.0
TIME_FORMAT = "%Y-%m-%d %H:%M:%S.%f"
#: 这些码可以结束一个 blocking 状态（同为设备类别或显式恢复）。
RECOVERY_CODES = ("RECOVERED", "READY", "SERIAL_UP", "USB_ENUM", "WAKEUP")
UNPARSED_CODE = "DEVICE_UNPARSED"

#: tool.log 显示层：类别中文名。显示层不暴露内部状态码、marker 名或 rule id。
DISPLAY_CATEGORY = {
    "power": "电源", "boot": "启动", "ready": "就绪", "mode": "模式",
    "flash": "烧录", "serial": "串口", "usb": "USB", "pdu": "PDU",
    "net": "网络", "wake": "唤醒", "vad": "VAD", "asr": "asr",
    "tts": "tts", "player": "播放", "record": "录音", "command": "业务",
    "ota": "升级", "resource": "资源", "error": "异常", "recovery": "恢复",
    "unknown": "未识别", "tool": "工具", "task": "任务", "version": "版本",
    "bluetooth": "蓝牙", "audio": "音频",
}

#: 特殊显示类别（按码前缀覆盖 category 的默认中文名）。
DISPLAY_CODE_CATEGORY = {
    "WAKE_TTS_CALLBACK": "tts",
    "ONLINE_ASR_TEXT": "online_asr",
    "ONLINE_ASR_RESULT": "online_asr",
    "ONLINE_ASR_START": "online_asr",
    "ONLINE_ASR_ERROR": "online_asr",
    "ONLINE_TTS_TEXT": "online_tts",
    "ONLINE_SKILL_TEXT": "online_skill",
    "ONLINE_SKILL": "online_skill",
    "LOCAL_SKILL": "业务",
    "DEVICE_ACTION": "业务",
    "DEVICE_ACK": "业务",
    "DEVICE_NACK": "业务",
    "STATE_CHANGED": "业务",
    "WIFI_SCAN": "WiFi",
    "WIFI_CONNECTING": "WiFi",
    "WIFI_CONNECTED": "WiFi",
    "WIFI_DISCONNECTED": "WiFi",
    "WIFI_IP": "WiFi",
    "DNS_OK": "云端",
    "DNS_FAIL": "云端",
    "CLOUD_ONLINE": "云端",
    "CLOUD_OFFLINE": "云端",
    "CLOUD_CONNECTED": "云端",
    "CLOUD_DISCONNECTED": "云端",
    "PROTOCOL_HANDSHAKE": "云端",
    "HEARTBEAT": "云端",
    "HEARTBEAT_TIMEOUT": "云端",
    "RECONNECTING": "云端",
    "MIC_ERROR": "音频",
    "SOUNDCARD_MISSING": "音频",
    "AUDIO_BUFFER": "音频",
    "AUDIO_UNDERRUN": "音频",
    "DEVICE_LOST": "音频",
    "DEVICE_RECOVERED": "音频",
}

#: 显示阶段（英文短词，跟用户示例保持一致）。
DISPLAY_STAGE = {
    "POWER_OFF": "断电", "POWER_ON": "上电", "POWER_CYCLE": "断电上电",
    "POWER_UNVERIFIED": "未确认",
    "BOOT_START": "boot", "BOOT_STAGE": "boot", "BOOT_COMPLETE": "boot_complete",
    "READY": "ready", "BOOT_FAIL": "boot_fail", "REBOOT": "reboot",
    "REBOOT_REQUEST": "reboot_request", "REBOOT_REASON": "reboot_reason",
    "FTM_MODE_ENTER": "ftm_enter", "FTM_MODE_EXIT": "ftm_exit",
    "DEVICE_MODE": "mode",
    "PRE_WAKEUP": "pre", "WAKE_REJECT": "reject", "WAKE_TIMEOUT": "timeout",
    "WAKE_LATE": "late", "WAKE_FALSE_POSITIVE": "false_positive",
    "WAKE_CANCELLED": "cancel", "NO_WAKE": "no_wake",
    "ASR_START": "start", "ASR_EMPTY": "empty", "ASR_ERROR": "error",
    "ASR_TIMEOUT": "timeout",
    "TTS_REQUEST": "request", "TTS_START": "start", "TTS_FIRST_PACKET": "first_packet",
    "TTS_END": "end", "TTS_COMPLETE": "complete", "TTS_INTERRUPTED": "interrupt",
    "TTS_PAUSED": "pause", "TTS_RESUMED": "resume", "TTS_ERROR": "error",
    "TTS_PLAY": "play",
    "PLAYER_PREPARE": "prepare", "PLAYER_START": "play",
    "PLAYER_COMPLETE": "play_end", "PLAYER_PAUSE": "pause",
    "PLAYER_RESUME": "resume", "PLAYER_STOP": "stop",
    "PLAYER_INTERRUPTED": "interrupt", "PLAYER_ERROR": "error",
    "PLAYER_TIMEOUT": "timeout", "PLAYER_BLOCKED": "blocked",
    "PLAYER_REQUESTED": "request",
    "ONLINE_REQUEST_START": "request_start", "ONLINE_REQUEST_ID": "request_id",
    "ONLINE_RESPONSE": "response", "ONLINE_TIMEOUT": "timeout",
    "ONLINE_CANCEL": "cancel", "ONLINE_RETRY": "retry", "ONLINE_ERROR": "error",
    "WIFI_SCAN": "scan", "WIFI_CONNECTING": "connecting",
    "WIFI_DISCONNECTED": "disconnected", "WIFI_IP": "ip",
    "DNS_OK": "dns_ok", "DNS_FAIL": "dns_fail",
    "BT_ENABLED": "enabled", "BT_DISABLED": "disabled", "BT_SCANNING": "scanning",
    "BT_CONNECTING": "connecting", "BT_CONNECTED": "connected",
    "BT_DISCONNECTED": "disconnected", "BT_PAIRING": "pairing", "BT_ERROR": "error",
    "RECORD_START": "start", "RECORD_END": "end",
    "VAD_START": "start", "VAD_END": "end",
    "SERIAL_OPEN": "open", "SERIAL_CLOSE": "close", "SERIAL_UP": "up",
    "SERIAL_DOWN": "down",
}

#: 显示主值（不写成 k=v，直接作为业务文本）。
DISPLAY_PRIMARY = {
    "ASR_KEYWORD": "text", "ASR_RESULT": "text",
    "ONLINE_ASR_TEXT": "text", "ONLINE_ASR_RESULT": "text",
    "ONLINE_SKILL_TEXT": "text", "ONLINE_SKILL": "text",
}

#: 各类别允许显示的业务字段（白名单；内部字段永不出现）。
DISPLAY_FIELDS = {
    "唤醒": ("keyword", "zh", "score", "ncm", "threshold"),
    "asr": ("zh", "intent", "text_sha"),
    "online_asr": ("zh", "request_id", "text_sha"),
    "tts": ("tts_id", "addr", "mode", "voice", "text_sha", "request_id"),
    "online_tts": ("tts_id", "addr", "request_id"),
    "online_skill": ("zh", "intent", "request_id"),
    "播放": ("tts_id", "file", "asset_id", "volume"),
    "版本": ("version", "commit"),
    "业务": ("intent", "command", "action", "reason", "code"),
    "异常": ("code", "reason"),
    "电源": ("socket",),
    "录音": ("format", "channels", "rate"),
    "WiFi": ("ssid", "ip"),
    "云端": ("reason",),
}

#: 显示层永不输出的字段名（内部解析/关联信息）。
DISPLAY_FORBIDDEN = {
    "raw_marker", "marker", "rule", "rule_id", "ruleid", "catalog_status",
    "raw_code", "source_event", "source_event_seq", "event_seq", "correlation_id",
    "corr", "device_ts", "audit_category", "audit_kind", "device", "unparsed",
    "raw_sha256", "raw_excerpt", "parameters", "state_class",
}


class StateCatalogError(ValueError):
    """状态目录或项目扩展不合法；加载必须失败关闭。"""


@dataclass(frozen=True)
class StateDef:
    code: str
    category: str
    kind: str
    label_zh: str
    raw_marker: tuple[str, ...] = ()
    prerequisites: tuple[str, ...] = ()
    successors: tuple[str, ...] = ()
    pass_role: str = "diagnostic"
    recoverable: bool = False
    duplicate_policy: str = "merge"
    scope: str = "common"
    supersedes: str | None = None
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "category": self.category,
            "kind": self.kind,
            "label_zh": self.label_zh,
            "raw_marker": list(self.raw_marker),
            "prerequisites": list(self.prerequisites),
            "successors": list(self.successors),
            "pass_role": self.pass_role,
            "recoverable": self.recoverable,
            "duplicate_policy": self.duplicate_policy,
            "scope": self.scope,
            "supersedes": self.supersedes,
            "version": self.version,
        }


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _upper_list(value: Any) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        values = (value,)
    elif isinstance(value, (list, tuple, set, frozenset)):
        values = tuple(value)
    else:
        raise StateCatalogError(f"期望字符串列表，得到 {type(value).__name__}")
    result = []
    for item in values:
        text = _text(item).upper()
        if text and text not in result:
            result.append(text)
    return tuple(result)


def _build_catalog() -> dict[str, StateDef]:
    catalog: dict[str, StateDef] = {}
    for row in _COMMON_ROWS:
        code = str(row["code"])
        if not COMMON_CODE_RE.match(code):
            raise StateCatalogError(f"非法通用状态码: {code!r}")
        if code in catalog:
            raise StateCatalogError(f"重复通用状态码: {code}")
        category = str(row["category"])
        kind = str(row["kind"])
        if category not in CATEGORIES:
            raise StateCatalogError(f"{code}: 未知 category {category!r}")
        if kind not in KINDS:
            raise StateCatalogError(f"{code}: 未知 kind {kind!r}")
        if row["pass_role"] not in PASS_ROLES:
            raise StateCatalogError(f"{code}: 未知 pass_role {row['pass_role']!r}")
        if row["duplicate_policy"] not in DUPLICATE_POLICIES:
            raise StateCatalogError(f"{code}: 未知 duplicate_policy {row['duplicate_policy']!r}")
        catalog[code] = StateDef(
            code=code,
            category=category,
            kind=kind,
            label_zh=str(row["label_zh"]),
            raw_marker=_upper_list(row.get("raw_marker")),
            prerequisites=_upper_list(row.get("prerequisites")),
            successors=_upper_list(row.get("successors")),
            pass_role=str(row["pass_role"]),
            recoverable=bool(row["recoverable"]),
            duplicate_policy=str(row["duplicate_policy"]),
            scope=str(row["scope"]),
        )
    return catalog


CATALOG: dict[str, StateDef] = _build_catalog()


@dataclass(frozen=True)
class StateExtension:
    profile_id: str
    states: Mapping[str, StateDef]
    aliases: Mapping[str, str]
    gate: Mapping[str, Any]
    enabled_markers: tuple[str, ...] = ()
    not_applicable: tuple[str, ...] = ()

    def lookup(self, code: str) -> tuple[StateDef | None, str]:
        """返回 (状态定义, catalog_status)；catalog_status ∈ common/project/''。"""
        raw = _text(code).upper()
        mapped = self.aliases.get(raw, raw)
        if mapped in self.states:
            return self.states[mapped], "project"
        if mapped in CATALOG:
            return CATALOG[mapped], "common"
        return None, ""

    def known(self, code: str) -> bool:
        return self.lookup(code)[0] is not None


def _state_from_extension(item: Mapping[str, Any], *, profile_id: str, index: int) -> StateDef:
    code = _text(item.get("code")).upper()
    if not PROJECT_CODE_RE.match(code):
        raise StateCatalogError(f"states[{index}] 项目状态码必须是 P_ 前缀: {code!r}")
    if code in CATALOG:
        raise StateCatalogError(f"states[{index}] 不能覆盖通用状态 {code}")
    category = _text(item.get("category"))
    if category not in CATEGORIES:
        raise StateCatalogError(f"{code}: 未知 category {category!r}")
    kind = _text(item.get("kind") or "device")
    if kind not in KINDS:
        raise StateCatalogError(f"{code}: 未知 kind {kind!r}")
    pass_role = _text(item.get("pass_role") or "diagnostic")
    if pass_role not in PASS_ROLES:
        raise StateCatalogError(f"{code}: 未知 pass_role {pass_role!r}")
    duplicate = _text(item.get("duplicate_policy") or "merge")
    if duplicate not in DUPLICATE_POLICIES:
        raise StateCatalogError(f"{code}: 未知 duplicate_policy {duplicate!r}")
    label = _text(item.get("label_zh") or "")
    if not label:
        raise StateCatalogError(f"{code}: 必须提供 label_zh")
    return StateDef(
        code=code,
        category=category,
        kind=kind,
        label_zh=label,
        raw_marker=_upper_list(item.get("raw_marker")),
        prerequisites=_upper_list(item.get("prerequisites")),
        successors=_upper_list(item.get("successors")),
        pass_role=pass_role,
        recoverable=bool(item.get("recoverable", False)),
        duplicate_policy=duplicate,
        scope=f"profile:{profile_id}",
    )


def validate_extension(payload: Mapping[str, Any] | str | Path | None) -> StateExtension | None:
    if payload is None:
        return None
    if isinstance(payload, (str, Path)):
        path = Path(payload)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise StateCatalogError(f"无法读取状态扩展 {path}: {error}") from error
    if not isinstance(payload, Mapping):
        raise StateCatalogError("状态扩展必须是 JSON 对象")
    schema = _text(payload.get("schema"))
    if schema and schema != EXTENSION_SCHEMA:
        raise StateCatalogError(f"状态扩展 schema 必须是 {EXTENSION_SCHEMA}，得到 {schema!r}")
    profile_id = _text(payload.get("profile_id")).lower()
    if not PROFILE_ID_RE.match(profile_id):
        raise StateCatalogError(f"profile_id 不合法: {profile_id!r}")

    states: dict[str, StateDef] = {}
    for index, item in enumerate(payload.get("states") or []):
        if not isinstance(item, Mapping):
            raise StateCatalogError(f"states[{index}] 必须是对象")
        state = _state_from_extension(item, profile_id=profile_id, index=index)
        if state.code in states:
            raise StateCatalogError(f"重复项目状态码: {state.code}")
        states[state.code] = state

    aliases: dict[str, str] = {}
    for index, item in enumerate(payload.get("aliases") or []):
        if not isinstance(item, Mapping):
            raise StateCatalogError(f"aliases[{index}] 必须是对象")
        raw_code = _text(item.get("raw_code")).upper()
        maps_to = _text(item.get("maps_to")).upper()
        if not raw_code or not maps_to:
            raise StateCatalogError(f"aliases[{index}] 需要 raw_code 与 maps_to")
        if raw_code in CATALOG:
            raise StateCatalogError(
                f"aliases[{index}] 不能用别名改写通用状态 {raw_code}；请改用项目 P_ 码"
            )
        if raw_code == maps_to:
            raise StateCatalogError(f"aliases[{index}] raw_code 与 maps_to 相同")
        if raw_code in aliases and aliases[raw_code] != maps_to:
            raise StateCatalogError(f"aliases[{index}] raw_code 重复: {raw_code}")
        if maps_to not in CATALOG and maps_to not in states:
            raise StateCatalogError(f"aliases[{index}] maps_to 未登记: {maps_to}")
        aliases[raw_code] = maps_to

    known = set(CATALOG) | set(states)
    for state in states.values():
        for ref in (*state.prerequisites, *state.successors):
            if ref not in known:
                raise StateCatalogError(f"{state.code} 引用了未登记状态: {ref}")

    gate: dict[str, Any] = {}
    raw_gate = payload.get("gate")
    if raw_gate is not None:
        if not isinstance(raw_gate, Mapping):
            raise StateCatalogError("gate 必须是对象")
        required = _upper_list(raw_gate.get("required_states"))
        for code in required:
            if code not in known:
                raise StateCatalogError(f"gate.required_states 引用了未登记状态: {code}")
        gate["required_states"] = list(required)
        if raw_gate.get("max_unrecovered_blocking") is not None:
            try:
                limit = int(raw_gate["max_unrecovered_blocking"])
            except (TypeError, ValueError) as error:
                raise StateCatalogError("gate.max_unrecovered_blocking 必须是整数") from error
            if limit < 0:
                raise StateCatalogError("gate.max_unrecovered_blocking 不能为负")
            gate["max_unrecovered_blocking"] = limit
        if raw_gate.get("recovery_window_s") is not None:
            try:
                window = float(raw_gate["recovery_window_s"])
            except (TypeError, ValueError) as error:
                raise StateCatalogError("gate.recovery_window_s 必须是数字") from error
            if window <= 0:
                raise StateCatalogError("gate.recovery_window_s 必须大于 0")
            gate["recovery_window_s"] = window
        if raw_gate.get("strict_prerequisites") is not None:
            if not isinstance(raw_gate["strict_prerequisites"], bool):
                raise StateCatalogError("gate.strict_prerequisites 必须是布尔值")
            gate["strict_prerequisites"] = raw_gate["strict_prerequisites"]
    enabled_markers: list[str] = []
    for index, item in enumerate(payload.get("enabled_markers") or []):
        if isinstance(item, Mapping):
            name = _text(item.get("marker") or item.get("name"))
            category = _text(item.get("category"))
            if category and category not in CATEGORIES and category not in DISPLAY_CATEGORY:
                raise StateCatalogError(f"enabled_markers[{index}] 未知 category: {category}")
        else:
            name = _text(item)
        if not name:
            raise StateCatalogError(f"enabled_markers[{index}] 缺少 marker 名称")
        if name not in enabled_markers:
            enabled_markers.append(name)
    not_applicable = []
    for item in payload.get("not_applicable") or []:
        text = _text(item)
        if text and text.casefold() not in not_applicable:
            not_applicable.append(text.casefold())
    return StateExtension(profile_id=profile_id, states=states, aliases=aliases, gate=gate,
                          enabled_markers=tuple(enabled_markers),
                          not_applicable=tuple(not_applicable))


def resolve_device_code(code: Any, extension: StateExtension | None = None) -> tuple[str, StateDef | None, str]:
    """把 marker/适配器提交的码解析为（语义码, 定义, catalog_status）。

    未登记、或把 judgement/delivery 结论当设备状态时，返回 ``DEVICE_UNPARSED``；
    原始码由调用方保留在 ``details.raw_code``。
    """
    raw = _text(code).upper()
    state: StateDef | None
    status: str
    if extension is not None:
        state, status = extension.lookup(raw)
    else:
        state, status = CATALOG.get(raw), ("common" if raw in CATALOG else "")
    if state is None or state.kind not in DEVICE_KINDS:
        return UNPARSED_CODE, CATALOG[UNPARSED_CODE], "unregistered"
    return state.code, state, status


def _parse_time(value: Any) -> datetime | None:
    text = _text(value).replace("T", " ")
    if not text:
        return None
    try:
        return datetime.strptime(text, TIME_FORMAT)
    except ValueError:
        return None


def _details(record: Any) -> Mapping[str, Any]:
    details = getattr(record, "details", None)
    return details if isinstance(details, Mapping) else {}


def _is_device(record: Any) -> bool:
    return bool(_details(record).get("device"))


def _state_label(record: Any) -> str:
    return _text(_details(record).get("state"))


def _record_code(record: Any) -> str:
    return _text(getattr(record, "event", "")).upper()


PRIVATE_IP_RE = re.compile(r"\b(?:10|127)\.\d{1,3}\.\d{1,3}\.\d{1,3}\b|\b192\.168\.\d{1,3}\.\d{1,3}\b|\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b")
ABS_PATH_DISPLAY_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|/)[^\s]*$")


def _display_value(value: Any) -> str:
    """显示层取值：单行、去绝对路径、内网地址脱敏、限长。"""
    text = _text(value)
    if not text:
        return ""
    text = text.replace("\r", " ").replace("\n", " ").strip()
    if ABS_PATH_DISPLAY_RE.match(text):
        text = text.replace("\\", "/").rsplit("/", 1)[-1] or text
    text = PRIVATE_IP_RE.sub("<ip>", text)
    return text if len(text) <= 80 else text[:79] + "…"


def display_category(code: str, state: StateDef | None) -> str:
    if code in DISPLAY_CODE_CATEGORY:
        return DISPLAY_CODE_CATEGORY[code]
    if state is not None:
        return DISPLAY_CATEGORY.get(state.category, state.label_zh)
    return "未识别"


def describe_device(code: str, parameters: Any = None,
                    display: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """把内部状态码翻译成 tool.log 的显示描述：类别 + 业务值 + 阶段。

    ``display`` 来自 marker/profile 的显式声明（category/stage/primary/fields），
    框架不根据字段名猜测语义；未声明时按目录的显示规则取值。
    """
    state = CATALOG.get(code)
    if state is None:
        state = CATALOG[UNPARSED_CODE]
    override = display if isinstance(display, Mapping) else {}
    category = _text(override.get("category")) or display_category(code, state)
    stage = _text(override.get("stage")) or DISPLAY_STAGE.get(code, "")
    params: dict[str, Any] = {}
    if isinstance(parameters, Mapping):
        params.update({str(key): value for key, value in parameters.items()})
    extra = override.get("fields")
    labels: dict[str, str] = {}
    if isinstance(extra, Mapping):
        for key, label in extra.items():
            labels[str(key)] = _text(label) or str(key)
            if key in params:
                continue
    elif isinstance(extra, (list, tuple)):
        for key in extra:
            labels[str(key)] = str(key)
    if not labels:
        for key in DISPLAY_FIELDS.get(category, ()):
            labels[key] = key
    primary_name = _text(override.get("primary")) or DISPLAY_PRIMARY.get(code, "")
    primary = ""
    if primary_name:
        primary = _display_value(params.get(primary_name))
    fields: list[tuple[str, str]] = []
    for key, label in labels.items():
        if key.casefold() in DISPLAY_FORBIDDEN or label.casefold() in DISPLAY_FORBIDDEN:
            continue
        value = _display_value(params.get(key))
        if not value:
            continue
        fields.append((label, value))
    return {"category": category, "stage": stage, "primary": primary, "fields": fields}


def profile_coverage(
    extension: StateExtension | None,
    observed_codes: Iterable[str],
    unparsed_codes: Iterable[str] = (),
) -> dict[str, Any]:
    """按 profile 统计 SUPPORTED / EMITTED / NOT_EMITTED / NOT_APPLICABLE / UNPARSED。

    总目录是能力注册表；某个 profile 没有的能力不补假状态，只标 NOT_APPLICABLE。
    """
    registered: dict[str, StateDef] = dict(CATALOG)
    if extension is not None:
        registered.update(extension.states)
    observed = sorted({_text(code).upper() for code in observed_codes if _text(code)})
    emitted = [code for code in observed if code in registered]
    unparsed = sorted({_text(code).upper() for code in unparsed_codes if _text(code)})
    na_keys = set(extension.not_applicable) if extension else set()

    def is_not_applicable(code: str) -> bool:
        state = registered[code]
        return (state.category.casefold() in na_keys
                or display_category(code, state).casefold() in na_keys)

    not_applicable = sorted(code for code in registered if is_not_applicable(code))
    required = [str(code).upper() for code in ((extension.gate.get("required_states") if extension else None) or [])]
    relevant_categories = {
        registered[code].category for code in emitted if code in registered
    } | {registered[code].category for code in required if code in registered}
    if not relevant_categories:
        relevant_categories = {state.category for state in registered.values()
                               if state.kind in DEVICE_KINDS}
    not_emitted = sorted(
        code for code, state in registered.items()
        if state.category in relevant_categories
        and code not in emitted
        and code not in not_applicable
    )
    return {
        "schema": "lstest.state-profile-coverage.v1",
        "profile_id": extension.profile_id if extension else None,
        "supported": len(registered),
        "emitted": emitted,
        "not_emitted": not_emitted,
        "not_applicable": not_applicable,
        "unparsed": unparsed,
        "counts": {
            "SUPPORTED": len(registered),
            "EMITTED": len(emitted),
            "NOT_EMITTED": len(not_emitted),
            "NOT_APPLICABLE": len(not_applicable),
            "UNPARSED": len(unparsed),
        },
    }


def analyze(
    records: Sequence[Any],
    extension: StateExtension | None = None,
    *,
    recovery_window_s: float | None = None,
    duplicate_window_s: float = DEFAULT_DUPLICATE_WINDOW_S,
) -> dict[str, Any]:
    """对设备事件做目录校验、重复/迟到/前置/恢复分析。

    只读：不修改事件，不重排事件；结论供 summary/verify 使用。
    """
    device = [record for record in records if _is_device(record)]
    seen_events: set[str] = set()
    for record in records:
        code = _record_code(record)
        if code:
            seen_events.add(code)
        if extension is not None:
            mapped = extension.aliases.get(code)
            if mapped:
                seen_events.add(mapped)

    window = recovery_window_s
    if window is None:
        window = float((extension.gate.get("recovery_window_s") if extension else None)
                       or DEFAULT_RECOVERY_WINDOW_S)

    observations: list[dict[str, Any]] = []
    unregistered: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    late: list[dict[str, Any]] = []
    by_category: dict[str, int] = {}
    by_code: dict[str, int] = {}
    status_counts = {"common": 0, "project": 0, "unregistered": 0}
    # 设备时钟会随断电/重启复位，迟到判定只在同一轮同一设备内比较。
    previous_device_ts: dict[tuple[Any, str], datetime] = {}
    pending: dict[str, Any] | None = None

    for record in device:
        raw_code = _record_code(record)
        details = _details(record)
        state, catalog_status = (extension.lookup(raw_code) if extension else
                                 (CATALOG.get(raw_code), "common" if raw_code in CATALOG else ""))
        if state is None or state.kind not in DEVICE_KINDS:
            catalog_status = "unregistered"
            state = CATALOG[UNPARSED_CODE]
            unregistered.append({
                "code": raw_code or "UNKNOWN",
                "event_seq": getattr(record, "event_seq", None),
                "time": _text(getattr(record, "time", "")),
                "raw_code": _text(details.get("raw_code")) or raw_code,
            })
        elif state.code == UNPARSED_CODE and _text(details.get("raw_code")).upper() not in ("", UNPARSED_CODE):
            # 写入时已把未登记码降级为 DEVICE_UNPARSED；原始码仍在 raw_code 里，必须继续可见。
            catalog_status = "unregistered"
            unregistered.append({
                "code": _text(details.get("raw_code")).upper(),
                "event_seq": getattr(record, "event_seq", None),
                "time": _text(getattr(record, "time", "")),
                "raw_code": _text(details.get("raw_code")).upper(),
            })
        status_counts[catalog_status] = status_counts.get(catalog_status, 0) + 1
        code = state.code
        by_code[code] = by_code.get(code, 0) + 1
        by_category[state.category] = by_category.get(state.category, 0) + 1
        moment = _parse_time(getattr(record, "time", ""))
        state_value = _state_label(record)
        marker_parameters = details.get("parameters")
        signature = (code, state_value, json.dumps(marker_parameters, sort_keys=True, default=str)
                     if marker_parameters is not None else "")

        if (pending is not None and state.duplicate_policy == "merge"
                and pending["signature"] == signature and moment is not None
                and pending["moment"] is not None
                and abs((moment - pending["moment"]).total_seconds()) <= duplicate_window_s):
            pending["repeat"] += 1
            pending["last_hit"] = _text(getattr(record, "time", ""))
            duplicates.append({"code": code, "time": pending["last_hit"],
                               "repeat": pending["repeat"]})
            duplicate_ts = _parse_time(details.get("device_ts"))
            if duplicate_ts is not None:
                previous_device_ts[clock_key] = duplicate_ts
            continue

        device_ts = _parse_time(details.get("device_ts"))
        clock_key = (getattr(record, "round", None), _text(getattr(record, "device", "")))
        is_late = False
        previous = previous_device_ts.get(clock_key)
        if device_ts is not None and previous is not None:
            if (device_ts - previous).total_seconds() < -0.5:
                is_late = True
                late.append({"code": code, "time": _text(getattr(record, "time", "")),
                             "device_ts": _text(details.get("device_ts"))})
        if device_ts is not None:
            previous_device_ts[clock_key] = device_ts

        observation = {
            "code": code,
            "category": state.category,
            "kind": state.kind,
            "pass_role": state.pass_role,
            "state": state_value,
            "time": _text(getattr(record, "time", "")),
            "event_seq": getattr(record, "event_seq", None),
            "round": getattr(record, "round", None),
            "catalog_status": catalog_status,
            "late": is_late,
            "repeat": 1,
            "correlation_id": getattr(record, "correlation_id", None),
        }
        observations.append(observation)
        pending = {"signature": signature, "moment": moment, "repeat": 1,
                   "last_hit": _text(getattr(record, "time", "")), "index": len(observations) - 1}

    # 前置状态：只要求"更早出现过"，不要求紧邻；缺失只告警，不改写事件。
    missing_prerequisites: list[dict[str, Any]] = []
    for index, observation in enumerate(observations):
        definition = CATALOG.get(observation["code"])
        if definition is None and extension is not None:
            definition = extension.states.get(observation["code"])
        if definition is None:
            continue
        earlier = {item["code"] for item in observations[:index]}
        missing = [code for code in definition.prerequisites
                   if code not in earlier and code not in seen_events]
        if missing:
            missing_prerequisites.append({
                "code": observation["code"], "time": observation["time"], "missing": missing,
            })

    # 恢复：blocking 状态之后，窗口内出现恢复码或同类别非 blocking 状态。
    unrecovered: list[dict[str, Any]] = []
    recovered_count = 0
    blocking_count = 0
    for index, observation in enumerate(observations):
        if observation["pass_role"] != "blocking":
            continue
        blocking_count += 1
        start = _parse_time(observation["time"])
        recovered = False
        for later in observations[index + 1:]:
            moment = _parse_time(later["time"])
            if start is not None and moment is not None and (moment - start).total_seconds() > window:
                break
            if later["code"] in RECOVERY_CODES:
                recovered = True
                break
            if later["category"] == observation["category"] and later["pass_role"] != "blocking":
                recovered = True
                break
        if recovered:
            recovered_count += 1
        else:
            unrecovered.append({"code": observation["code"], "time": observation["time"],
                                "category": observation["category"]})

    # 关联：同一 correlation_id 的设备状态应能串成一条链路；缺关联也要可见。
    correlation_groups: dict[str, list[str]] = {}
    for observation in observations:
        key = _text(observation.get("correlation_id"))
        correlation_groups.setdefault(key, []).append(observation["code"])
    with_id = sum(1 for observation in observations if _text(observation.get("correlation_id")))
    correlation = {
        "with_id": with_id,
        "without_id": len(observations) - with_id,
        "groups": {
            key: codes[:12]
            for key, codes in sorted(correlation_groups.items())[:20]
            if key
        },
    }

    required = list((extension.gate.get("required_states") if extension else None) or [])
    present = [code for code in required if code in by_code]
    missing_required = [code for code in required if code not in by_code]

    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "catalog": {
            "schema": CATALOG_SCHEMA,
            "common_states": len(CATALOG),
            "profile_id": extension.profile_id if extension else None,
            "project_states": len(extension.states) if extension else 0,
            "aliases": dict(extension.aliases) if extension else {},
        },
        "device_events": len(device),
        "by_category": dict(sorted(by_category.items())),
        "by_code": dict(sorted(by_code.items())),
        "catalog_status": status_counts,
        "unregistered": unregistered,
        "correlation": correlation,
        "duplicates": {"collapsed_events": len(duplicates), "entries": duplicates},
        "late": late,
        "prerequisite_missing": missing_prerequisites,
        "prerequisites_strict": bool((extension.gate.get("strict_prerequisites") if extension else False)),
        "blocking": {
            "count": blocking_count,
            "recovered": recovered_count,
            "unrecovered": unrecovered,
        },
        "required_states": {
            "declared": required,
            "present": present,
            "missing": missing_required,
        },
    }
    gate_checks: dict[str, bool] = {}
    if required:
        gate_checks["required_states_present"] = not missing_required
    if extension is not None and extension.gate.get("max_unrecovered_blocking") is not None:
        gate_checks["no_unrecovered_blocking"] = (
            len(unrecovered) <= int(extension.gate["max_unrecovered_blocking"])
        )
    elif extension is not None and unrecovered:
        # 声明了项目状态目录但没声明容忍度时，默认不得有未恢复阻断。
        gate_checks["no_unrecovered_blocking"] = False
    report["gate_checks"] = gate_checks
    return report


def coverage_report(
    inventory: Mapping[str, Any] | str | Path,
    extension: StateExtension | None = None,
) -> dict[str, Any]:
    """对比"知识库/回执已知状态"与 lstest 已登记/已产出状态。"""
    if isinstance(inventory, (str, Path)):
        payload = json.loads(Path(inventory).read_text(encoding="utf-8"))
    else:
        payload = dict(inventory)
    states = list(payload.get("states") or [])
    known: dict[str, dict[str, Any]] = {}
    for item in states:
        code = _text(item.get("code")).upper()
        if code:
            known[code] = item

    registered = set(CATALOG)
    if extension is not None:
        registered |= set(extension.states)
        registered |= set(extension.aliases.values())

    supported: list[str] = []
    missing: list[dict[str, Any]] = []
    project_only: list[str] = []
    for code, item in sorted(known.items()):
        scope = _text(item.get("scope"))
        if scope and scope not in ("common", "framework", "bench"):
            project_only.append(code)
            continue
        if code in registered:
            supported.append(code)
        else:
            missing.append({
                "code": code,
                "category": item.get("category"),
                "lstest_status": item.get("lstest_status"),
                "label_zh": item.get("label_zh"),
            })
    emitted = [code for code, item in known.items() if item.get("lstest_status") == "emitted"]
    unproven = [code for code, item in known.items()
                if item.get("lstest_status") in {"documented", "absent"}]
    unmapped = sorted(registered - set(known))
    total = len(supported) + len(missing)
    emitted_registered = [code for code in emitted if code in registered]
    return {
        "schema": COVERAGE_SCHEMA,
        "inventory_states": len(known),
        "registered_states": len(registered),
        "supported_by_catalog": len(supported),
        "missing_from_catalog": len(missing),
        "coverage_pct": round(len(supported) * 100.0 / total, 1) if total else 0.0,
        "emitted_by_lstest": len(emitted_registered),
        "evidence_pct": round(len(emitted_registered) * 100.0 / len(known), 1) if known else 0.0,
        "unproven": sorted(unproven),
        "supported": supported,
        "missing": missing,
        "project_only": project_only,
        "catalog_only": unmapped,
    }


def schema_document() -> dict[str, Any]:
    return {
        "$schema": CATALOG_SCHEMA,
        "categories": list(CATEGORIES),
        "kinds": list(KINDS),
        "device_kinds": list(DEVICE_KINDS),
        "pass_roles": list(PASS_ROLES),
        "duplicate_policies": list(DUPLICATE_POLICIES),
        "extension": {
            "schema": EXTENSION_SCHEMA,
            "profile_id_pattern": PROFILE_ID_RE.pattern,
            "project_code_pattern": PROJECT_CODE_RE.pattern,
            "aliases": "raw_code -> maps_to；不得指向通用码同名改写",
            "gate": ["required_states", "max_unrecovered_blocking", "recovery_window_s",
                     "strict_prerequisites"],
        },
        "common_states": len(CATALOG),
    }


def _print_list(category: str | None, as_json: bool) -> int:
    items = [state for state in CATALOG.values()
             if category is None or state.category == category]
    items.sort(key=lambda state: (state.category, state.code))
    if as_json:
        print(json.dumps([state.to_dict() for state in items], ensure_ascii=False, indent=2))
        return 0
    for state in items:
        print(f"{state.category:<9} {state.code:<28} {state.pass_role:<18} {state.label_zh}")
    print(f"共 {len(items)} 条（通用目录 {len(CATALOG)} 条）")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="states", description="设备状态目录：登记、校验、覆盖率")
    sub = parser.add_subparsers(dest="command", required=True)
    list_cmd = sub.add_parser("list", help="列出通用状态目录")
    list_cmd.add_argument("--category", choices=CATEGORIES)
    list_cmd.add_argument("--json", action="store_true")
    check = sub.add_parser("check", help="校验项目状态扩展（lstest.state-extension.v1）")
    check.add_argument("--extension", type=Path, required=True)
    check.add_argument("--json", action="store_true")
    cover = sub.add_parser("coverage", help="对比状态盘点与目录登记，输出覆盖率")
    cover.add_argument("--inventory", type=Path, required=True)
    cover.add_argument("--extension", type=Path)
    cover.add_argument("--output", type=Path)
    cover.add_argument("--json", action="store_true")
    pcov = sub.add_parser("profile-coverage", help="按 profile 统计 5 类状态覆盖")
    pcov.add_argument("--extension", type=Path)
    pcov.add_argument("--events", type=Path, help="events.jsonl；自动提取设备状态与未解析 raw_code")
    pcov.add_argument("--observed", default="", help="逗号分隔的已产出状态码")
    pcov.add_argument("--unparsed", default="", help="逗号分隔的未解析 raw marker 名")
    pcov.add_argument("--output", type=Path)
    pcov.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.command == "list":
            return _print_list(args.category, args.json)
        if args.command == "check":
            extension = validate_extension(args.extension)
            payload = {
                "status": "PASS",
                "profile_id": extension.profile_id,
                "project_states": len(extension.states),
                "aliases": len(extension.aliases),
                "gate": dict(extension.gate),
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        if args.command == "profile-coverage":
            extension = validate_extension(args.extension) if args.extension else None
            observed = [item.strip() for item in str(args.observed or "").split(",") if item.strip()]
            unparsed = [item.strip() for item in str(args.unparsed or "").split(",") if item.strip()]
            if args.events is not None:
                for line_no, line in enumerate(args.events.read_text(encoding="utf-8").splitlines(), start=1):
                    if not line.strip():
                        continue
                    try:
                        payload = json.loads(line)
                    except ValueError as error:
                        raise StateCatalogError(f"{args.events}:{line_no} 不是合法 JSON: {error}") from error
                    details = payload.get("details") or {}
                    if not details.get("device"):
                        continue
                    code = _text(payload.get("event")).upper()
                    raw = _text(details.get("raw_code")).upper()
                    if code == UNPARSED_CODE and raw:
                        unparsed.append(raw)
                    elif code:
                        observed.append(code)
            report = profile_coverage(extension, observed, unparsed)
            text = json.dumps(report, ensure_ascii=False, indent=2)
            if args.output:
                args.output.write_text(text + "\n", encoding="utf-8")
                print(str(args.output))
            else:
                print(text)
            return 0
        extension = validate_extension(args.extension) if args.extension else None
        report = coverage_report(args.inventory, extension)
        text = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output:
            args.output.write_text(text + "\n", encoding="utf-8")
            print(str(args.output))
            return 0
        print(text)
        return 0
    except StateCatalogError as error:
        print(json.dumps({"status": "FAIL", "reason": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
