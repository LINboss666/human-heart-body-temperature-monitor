"""Chinese strings for :mod:`pc_monitor.app`. See :mod:`pc_monitor.i18n` for the
contract: every key is the English literal exactly as it appears at the call
site, and an unlisted string falls back to English and is recorded as missed.

This is a measurement tool whose vocabulary is a wire protocol, so the tables
below are split three ways:

* **UI copy** -- buttons, titles, dialogs, the status bar.  Translated.
* **Sentences that carry protocol tokens** -- ``HELLO rejected: %s``,
  ``%s not sent: port is closed``.  The words are translated, the packet names
  (``HELLO``, ``STATUS``, ``ECG_BATCH``, ``SET_RTC``, ``PONG``,
  ``START_STREAM``...), the units (``Hz``, ``ms``, ``s``, ``mV``) and the values
  (ports, hex payloads, timestamps) are not.  A placeholder is never dropped or
  reordered, because ``t(...) % args`` would then raise at the call site.
* **Pure notation** -- ``flags 0x%04X lead=...``, ``ADC %s``,
  ``UART tx %d rx %d``.  These are the field names of ``protocol.h`` and the
  enum members printed from it, so they map to themselves.  They are still
  looked up through ``t()``, and the identity entries at the end record that as
  a decision rather than an oversight.
"""

from . import i18n

ZH = {
    # ---------------------------------------------------------------- window
    "Human Heart & Body Temperature Monitor -- PC host": "人体心电体温监测仪 —— PC 上位机",
    "  [DEMO / SYNTHETIC DATA]": "  【演示 / 合成数据】",
    "ready": "就绪",
    # Mirrors ``demo_source.DEMO_BANNER``, the full-width red strip.
    "DEMO / SYNTHETIC DATA -- NOT FROM HARDWARE.  NOTHING ON SCREEN IS A MEASUREMENT.": (
        "演示 / 合成数据 —— 并非来自硬件。屏幕上的任何内容都不是测量结果。"
    ),
    # The plot corner note keeps ``DEMO / SYNTHETIC`` verbatim: it is the marker
    # a screenshot is judged by.
    "DEMO / SYNTHETIC -- generated on this PC": "DEMO / SYNTHETIC —— 由本机生成",
    # ------------------------------------------------------- acquisition group
    "Acquisition": "采集",
    "Start acquisition": "开始采集",
    "Stop acquisition": "停止采集",
    "Start recording": "开始记录",
    "Stop recording": "停止记录",
    "New": "新建",
    "Export CSV": "导出 CSV",
    "Export XLSX": "导出 XLSX",
    "idle": "空闲",
    "Sends START_STREAM / STOP_STREAM. Those control *reporting* only: the ADC, TIM3 "
    "and the DMA run from boot, so stopping never stops the converter.": (
        "发送 START_STREAM / STOP_STREAM。二者只控制“上报”：ADC、TIM3 与 DMA 从上电起就一直运行，"
        "因此停止采集并不会关掉转换器。"
    ),
    "Rows are only written to the export buffer while this is on.": "只有开启本项时，数据行才会写入导出缓冲区。",
    "Discard the held rows so the next export cannot mix two sessions.": "丢弃已保留的数据行，避免下一次导出把两段记录混在一起。",
    # ------------------------------------------------------------------- log
    "Events": "事件",
    "%s: %s": "%s：%s",
    "WARNING %s": "警告：%s",
    "WARNING device protocol 0x%02X, host mirrors 0x%02X": "警告：设备协议为 0x%02X，上位机为 0x%02X",
    "HELLO rejected: %s": "HELLO 被拒绝：%s",
    "STATUS rejected: %s": "STATUS 被拒绝：%s",
    "TEMP_STATUS rejected: %s": "TEMP_STATUS 被拒绝：%s",
    "ECG_BATCH rejected: %s": "ECG_BATCH 被拒绝：%s",
    "RTC_RESPONSE rejected: %s": "RTC_RESPONSE 被拒绝：%s",
    "HELLO fw %s, %d Hz, batch<=%d, %d-bit, caps 0x%04X": "HELLO 固件 %s，%d Hz，batch<=%d，%d 位，caps 0x%04X",
    "device looks synthetic (firmware 0.0.0)": "设备数据疑似合成（固件 0.0.0）",
    "fw %s proto 0x%02X, %d Hz": "固件 %s，协议 0x%02X，%d Hz",
    "no HELLO received": "未收到 HELLO",
    "device clock: %s": "设备时钟：%s",
    "PONG %s (%s ms round trip)": "PONG %s（往返 %s ms）",
    # ------------------------------------------------------------------- link
    "port open: %s": "串口已打开：%s",
    "port closed: %s": "串口已关闭：%s",
    "link error: %s": "链路错误：%s",
    "opening %s": "正在打开 %s",
    "open failed: %s": "打开失败：%s",
    "%s of seq %d: %s": "%s 序号 %d：%s",
    "accepted": "已接受",
    "rejected (%s)": "已拒绝（%s）",
    "%s sent (seq %d)": "%s 已发送（序号 %d）",
    "%s not sent: port is closed": "%s 未发送：串口已关闭",
    "SET_RTC %s (%s)": "SET_RTC %s（%s）",
    "SET_RTC %s (picked)": "SET_RTC %s（手动选择）",
    "PC wall clock": "PC 系统时间",
    "PC wall clock -> demo device": "PC 系统时间 -> 演示设备",
    # -------------------------------------------------------------- recording
    "recording started at %s": "已于 %s 开始记录",
    "recording stopped: %s rows, %.1f s": "记录已停止：%s 行，共 %.1f s",
    "recording buffer cleared": "记录缓冲区已清空",
    "Still recording": "仍在记录中",
    "Stop the recording first.": "请先停止记录。",
    "Discard rows": "丢弃数据行",
    "Discard the %s rows held for export?": "确定丢弃已保留待导出的 %s 行数据吗？",
    # ----------------------------------------------------------------- export
    "Nothing recorded": "没有已记录的数据",
    "No rows have been recorded, so there is nothing to export.\n"
    "Start recording and let ECG_BATCH frames arrive first.": (
        "尚未记录任何数据行，因此没有可导出的内容。\n请先开始记录，并等待 ECG_BATCH 帧到达。"
    ),
    "CSV files (*.csv);;All files (*)": "CSV 文件 (*.csv);;所有文件 (*)",
    "Excel workbook (*.xlsx);;All files (*)": "Excel 工作簿 (*.xlsx);;所有文件 (*)",
    "Export failed": "导出失败",
    "export failed: %s": "导出失败：%s",
    "wrote %s": "已写出 %s",
    "last export: %s": "上次导出：%s",
    # ------------------------------------------------- status strip / details
    "demo (synthetic)": "演示（合成数据）",
    "demo closed": "演示已停止",
    "closed": "已关闭",
    "%s | %s samples buffered for the plot | %s": "%s | 已为绘图缓存 %s 个采样点 | %s",
    "%d malformed batch(es)": "%d 个格式错误的批次",
    "%d temp_state route disagreements": "%d 次 temp_state 两路读数不一致",
    "%d byte(s) awaiting the rest of a frame": "%d 字节正在等待同一帧的剩余部分",
    # ------------------------------------------------------ STATUS device line
    "waiting for STATUS": "等待 STATUS",
    "uptime %s": "已运行 %s",
    "running": "运行中",
    "stopped": "已停止",
    "DMA %d blocks / %d dropped": "DMA %d 块 / 丢弃 %d 块",
    "%d ECG samples": "%d 个 ECG 采样点",
    "absent": "未检测到位",
    "valid": "有效",
    "not set": "未设置",
}

i18n.register(ZH)

# ============================ protocol notation: shown as written =============
# Field names from ``protocol.h`` / ``status_t`` and the ``flags`` shorthand the
# contract uses. Translating them would break the correspondence with the
# firmware source and with docs/PROTOCOL.md, so they are declared exempt rather
# than entered in the table as their own translation -- an identity entry would
# silence the completeness gate while proving nothing.
i18n.exempt(
    {
        "ADC %s",
        "OLED %s",
        "RTC %s",
        "UART tx %d rx %d crc %d proto %d",
        "flags 0x%04X",
        "flags 0x%04X lead=%s temp=%s hr_valid=%d notch=%s",
    }
)


