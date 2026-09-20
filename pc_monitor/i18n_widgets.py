"""Chinese strings for :mod:`pc_monitor.widgets.*` — see :mod:`pc_monitor.i18n`.

Every key is the English literal exactly as it appears at the call site in
``widgets/connection_panel.py``, ``widgets/ecg_plot.py``,
``widgets/metric_cards.py`` or ``widgets/status_bar.py``, including the format
specifiers: the call sites translate the *template* and interpolate afterwards,
so a value that already contains a number never reaches ``t()``.

What is deliberately not here is protocol vocabulary.  Packet names
(``HELLO``, ``STATUS``, ``ECG_BATCH``, ``SET_RTC``, ``GET_RTC``, ``PING``,
``PONG``, ``ACK``, ``NACK``), field names (``temp_state``, ``hr_bpm``,
``first_sample_index``, ``caps 0x…``), enum member names rendered straight off
the wire (``OK``, ``UNKNOWN``, ``DISCONNECTED``, ``PROBE_FAULT``), the card
values ``OK`` / ``UNCAL`` / ``FAULT`` / ``REPORTED n``, and the units
(``mV``, ``bpm``, ``°C``, ``Hz``, ``s``, ``B/s``) stay English in both
languages — the host's serial documentation is English and a
student has to be able to read the screen against ``docs/PROTOCOL.md``.
"""

from . import i18n

ZH = {
    # ------------------------------------------------------- widgets/connection_panel
    "Link & device": "链路与设备",
    "Link & device -- DEMO: no hardware, no serial port": "链路与设备 —— DEMO：无硬件、不占用串口",
    "synthetic ECG generated on this PC": "心电波形由本机合成，未经测量",
    "Serial port": "串口",
    "Ports are enumerated by the worker thread. Type a port name (e.g. COM7) if "
    "the list is empty or stale.": "端口由工作线程枚举。若列表为空或已过期，可直接输入端口名（如 COM7）。",
    "COMn / /dev/ttyUSBn": "COMn / /dev/ttyUSBn 端口名",
    "Refresh": "刷新",
    "Baud": "波特率",
    "Contract default is 230400 8N1 (UART_BAUD_RATE), 23040 byte/s of wire capacity.": (
        "合同默认值为 230400 8N1（UART_BAUD_RATE），线路容量 23040 字节/秒。"
    ),
    "Connect": "连接",
    "Disconnect": "断开连接",
    "idle": "空闲",
    "closed": "已关闭",
    "opening...": "正在打开……",
    "open: %s": "已打开：%s",
    "error: %s": "错误：%s",
    "no serial ports enumerated -- type a port name or use --demo": (
        "未枚举到任何串口 —— 请手动输入端口名，或使用 --demo"
    ),
    "enumerating ports...": "正在枚举串口……",
    "Device": "设备",
    "Identity:": "标识：",
    "Can do:": "能力：",
    "Now:": "当前：",
    "Device said:": "设备回应：",
    "no HELLO received": "未收到 HELLO",
    "unknown until HELLO": "收到 HELLO 之前无法确定",
    "waiting for STATUS": "等待 STATUS",
    "no command answered yet": "尚无命令得到回应",
    "fw %s, proto 0x%02X, %d Hz, batch<=%d, %d-bit ADC, caps 0x%04X": (
        "固件 %s，proto 0x%02X，%d Hz，batch<=%d，%d 位 ADC，caps 0x%04X"
    ),
    "present: %s\nabsent: %s": "具备：%s\n缺失：%s",
    "none": "无",
    "Device time (RTC)": "设备时间（RTC）",
    "SET_RTC carries calendar fields only: no zone, no UTC offset. The picker is "
    "local wall-clock time, matching what the OLED shows. See pc_monitor/rtc.py.": (
        "SET_RTC 只携带日历字段：不含时区、不含 UTC 偏移。时间选择框用的是本机墙上时间，"
        "与 OLED 显示一致。详见 pc_monitor/rtc.py。"
    ),
    "Sync Device Time": "同步设备时间",
    "Send the PC's current wall clock with SET_RTC.": "用 SET_RTC 把 PC 当前的墙上时间写进设备。",
    "Set picked time": "写入所选时间",
    "Query": "查询",
    "Send GET_RTC and show the RTC_RESPONSE.": "发送 GET_RTC 并显示 RTC_RESPONSE 的内容。",
    "Ping": "Ping 一下",
    "PKT_PING with a 4-byte token; the device echoes a PONG.": (
        "发送带 4 字节标记的 PKT_PING，设备会原样回一个 PONG。"
    ),
    "device clock not set from this host": "本机还未设置过设备时钟",
    "device reports %s": "设备报告 %s",
    "device reports %s (epoch %d = %s read as UTC)": "设备报告 %s（epoch %d = %s，按 UTC 解读）",
    "device reported an unreadable calendar: %s": "设备报告的日历无法解析：%s",
    "picker rejected, nothing sent: %s": "时间选择框未通过校验，没有发送任何内容：%s",
    # ------------------------------------------------------------- widgets/ecg_plot
    "ECG": "心电",
    "mV at MCU pin": "MCU 引脚上的 mV",
    "device time": "设备时间",
    "raw, unfiltered ADC code -- not a body-surface potential": (
        "原始未滤波的 ADC 码值 —— 不是体表电位"
    ),
    "assumed mid-rail 1650 mV (gain/offset UNVERIFIED)": "假定中点电位 1650 mV（增益/偏置 UNVERIFIED）",
    "Pause display": "暂停显示",
    "Resume display": "恢复显示",
    "Freeze the picture only. Acquisition and recording keep running, so nothing "
    "is lost: resuming shows the current window (Space).": (
        "只冻结画面。采集与记录照常运行，不会丢数据：恢复后看到的是当前窗口（Space）。"
    ),
    "Window": "窗口长度",
    "10 s default = 10000 samples at 1 kHz.": "默认 10 s，即 1 kHz 下 10000 个采样点。",
    "Auto Y": "Y 轴自适应",
    "Follow newest": "跟随最新",
    "Turn off to zoom/pan and keep that range.": "关闭此开关即可缩放/平移并保留该范围。",
    "Reset view": "重置视图",
    "no samples yet": "还没有采样点",
    "%s | %s pts | %s%s": "%s | %s 点 | %s%s",
    "%g s window": "%g 秒窗口",
    "PAUSED (data still acquired)": "已暂停（数据仍在采集）",
    " | %d gap(s) in window": " | 窗口内 %d 处断点",
    "DISPLAY PAUSED -- acquisition continues": "画面已暂停 —— 采集仍在继续",
    "DEMO / SYNTHETIC WAVEFORM -- generated on this PC, not measured": (
        "DEMO / 合成波形 —— 由本机生成，未经测量"
    ),
    # ---------------------------------------------------------- widgets/metric_cards
    "waiting for device": "等待设备数据",
    "Heart rate": "心率",
    "Only a rate the device certified is shown. hr_bpm, hr_state and "
    "SFLAG_HR_VALID must all agree, otherwise the value is treated as "
    "uncertified.": (
        "只显示设备已认证的心率。hr_bpm、hr_state 与 SFLAG_HR_VALID 必须一致，"
        "否则该数值按未认证处理。"
    ),
    "Temperature": "体温",
    "Blank as --.- while the device reports TEMP_UNCALIBRATED. The firmware's "
    "temperature model is uncalibrated in this baseline, so no degrees claim exists.": (
        "设备报告 TEMP_UNCALIBRATED 时显示为 --.-。本基线的固件温度模型尚未标定，因此不存在温度读数。"
    ),
    "Lead state": "导联状态",
    "UNKNOWN means no lead-off detection hardware is configured "
    "(CAP_LEAD_HW_DETECT clear). It is rendered differently from "
    "DISCONNECTED on purpose.": (
        "UNKNOWN 表示没有配置导联脱落检测硬件（CAP_LEAD_HW_DETECT 未置位）。"
        "它与 DISCONNECTED 的显示方式刻意不同。"
    ),
    "Probe state": "探头状态",
    "Derived from temp_state. Without CAP_PROBE_HW_DETECT the device can only "
    "infer a fault from the ADC range, which is a heuristic, not a probe test.": (
        "由 temp_state 推导。没有 CAP_PROBE_HW_DETECT 时，设备只能依据 ADC 量程推断故障，"
        "这是启发式猜测，不是探头检测。"
    ),
    "Recording": "录制",
    "Elapsed time of the local recording, from the first stored sample.": "本地录制的已用时长，从第一个落盘的采样点算起。",
    "Packet loss": "丢包",
    "Authoritative measure: first_sample_index discontinuity in ECG_BATCH, which "
    "also catches the device dropping its own DMA blocks. Sequence gaps are "
    "reported alongside as the link-level view.": (
        "权威口径：ECG_BATCH 中 first_sample_index 的不连续，它还能发现设备自行丢弃的 DMA 块。"
        "旁边同时给出按序号统计的链路层视角。"
    ),
    "CRC errors": "CRC 错误",
    "Frames rejected by CRC-16/CCITT-FALSE and resynchronised past.": "被 CRC-16/CCITT-FALSE 判为无效并跳过后重新同步的帧。",
    "%s: no certified rate from the device": "%s：设备未认证心率",
    "%s (certified)": "%s（已认证）",
    " | via %s": " | 经由 %s",
    "%s -- degrees are not claimed%s": "%s —— 不给出温度读数%s",
    "probe fault reported": "报告探头故障",
    " -- inferred from the ADC range, no probe hardware": " —— 由 ADC 量程推断，没有探头硬件",
    "temperature uncalibrated": "温度未标定",
    "; no probe-detect hardware": "；没有探头检测硬件",
    "in range": "量程正常",
    " -- heuristic only (CAP_PROBE_HW_DETECT clear)": " —— 仅为启发式判断（CAP_PROBE_HW_DETECT 未置位）",
    "outside lead_state_t": "超出 lead_state_t 取值范围",
    "no lead-off hardware configured": "未配置导联脱落检测硬件",
    "hardware present, reports UNKNOWN": "硬件存在，但报告 UNKNOWN",
    "software judgement, NOT an electrode verdict": "软件判断，不是电极结论",
    "reported by lead-off hardware": "由导联脱落检测硬件报告",
    "unexpected without hardware": "没有硬件时不该出现",
    "electrodes reported connected": "电极报告为已连接",
    "recording, %s rows": "录制中，已存 %s 行",
    "stopped, %s rows held": "已停止，保留 %s 行",
    "not recording": "未在录制",
    "%d index gap(s); %d packet(s) missing by sequence": "%d 处序号断点；按序号统计缺少 %d 个包",
    "%d byte(s) discarded while resyncing": "重新同步时丢弃 %d 字节",
    # ------------------------------------------------------------- widgets/status_bar
    "serial: %s": "串口: %s",
    "%.1f pkt/s": "%.1f 包/秒",
    "%s pkt": "%s 包",
    "%s dropped": "%s 丢包",
    "%s gaps / %s samples": "%s 断点 / %s 采样点",
    "%s CRC err / %s B": "%s CRC 错误 / %s B",
    "%s samples @ %.0f Hz": "%s 采样点 @ %.0f Hz",
}

i18n.register(ZH)
