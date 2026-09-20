"""Chinese strings for :mod:`pc_monitor.export` (and the row/recorder text it
writes). See :mod:`pc_monitor.i18n` for the contract: the key is the English
literal exactly as it appears at the point of writing, so ``--lang en`` still
produces the original English workbook.

What deliberately has **no** entry here, and therefore stays English in a
Chinese file: packet names, ``sample_index``/``lead_state``-style field names as
they appear inside a label, ``TempState``/``LeadState``/``Capability`` member
names, units, hex, timestamps, and the ``origin`` marker of a row -- a record
whose vocabulary stopped matching ``docs/PROTOCOL.md`` would no longer be a
record.  CSV/XLSX column labels are therefore additive: the Chinese label comes
first and the field name it translates stays behind it.
"""

from . import i18n

ZH: dict[str, str] = {
    # -- sheet tabs: exported files are read by people, not only by scripts --
    "Data": "数据",
    "Summary": "汇总",
    "ECG Series": "ECG 序列",
    # -- column labels, both the CSV header row and the Data sheet's first row.
    #    Order and column content never change; only this text does.
    "host_timestamp": "主机时间戳 host_timestamp",
    "host_epoch_s": "主机纪元秒 host_epoch_s",
    "device_ts_ms": "设备时间戳(ms) device_ts_ms",
    "sample_index": "样本序号 sample_index",
    "sample_time_s": "样本时刻(s) sample_time_s",
    "ecg_raw": "ECG 原始码值 ecg_raw",
    "ecg_pin_mv": "引脚电压(mV) ecg_pin_mv",
    "temp_raw": "温度原始码值 temp_raw",
    "temp_centi": "温度百分度 temp_centi",
    "temp_c": "温度摄氏度 temp_c",
    "hr_bpm": "心率(bpm) hr_bpm",
    "hr_valid": "心率有效标记 hr_valid",
    "hr_state": "心率状态 hr_state",
    "lead_state": "导联状态 lead_state",
    "probe_state": "探头状态 probe_state",
    "status_flags": "状态标志 status_flags",
    "origin": "数据来源 origin",
    "device_time_s": "设备时间(s) device_time_s",
    # -- the export summary line the event log and status bar show --
    "%s: %d rows": "%s：%d 行",
    "%s: %d rows, sheets %s, %d chart points": "%s：%d 行，工作表 %s，图表 %d 个绘制点",
    # -- Summary sheet: block heading, table head, warning --
    "ECG + body temperature recording -- summary": "心电 + 体温记录 —— 汇总表",
    "DEMO / SYNTHETIC DATA -- GENERATED ON THIS PC, NOT MEASURED": (
        "演示 / 合成数据 —— 由本机生成，未经测量，不得作为实测结果"
    ),
    "Item": "项目",
    "Value": "数值",
    # -- Summary key/value block --
    "Recording origin": "记录来源",
    "Synthetic data?": "是否为合成数据？",
    "YES -- DEMO MODE": "是 —— 演示模式生成",
    "yes": "是",
    "no": "否",
    "n/a": "无",
    "none": "无",
    "Source": "数据源",
    "Device identity (HELLO)": "设备标识（HELLO）",
    "Device capabilities": "设备能力",
    "no HELLO received: every capability is treated as absent": (
        "未收到 HELLO：所有能力一律按不具备处理"
    ),
    "present: %s | absent: %s": "具备：%s | 不具备：%s",
    "Started (PC clock)": "开始时间（PC 时钟）",
    "Stopped (PC clock)": "停止时间（PC 时钟）",
    "Duration": "时长",
    "Duration (h:mm:ss.s)": "时长（时:分:秒.秒）",
    "Sample rate (from ECG_BATCH)": "采样率（取自 ECG_BATCH）",
    "Rows in Data sheet": "“数据”工作表行数",
    "ECG samples": "ECG 采样点数",
    "First sample_index": "首个 sample_index",
    "Last sample_index": "末个 sample_index",
    "Index span": "序号跨度",
    "Samples missing (index span - stored)": "缺失样本数（序号跨度减已存数）",
    "Heart-rate reports valid": "有效心率报告数",
    "Heart rate mean (valid)": "平均心率（仅有效值）",
    "Heart rate min (valid)": "最低心率（仅有效值）",
    "Heart rate max (valid)": "最高心率（仅有效值）",
    "Temperature reports valid": "有效温度报告数",
    "Temperature mean (degC)": "平均温度（degC）",
    "Temperature min (degC)": "最低温度（degC）",
    "Temperature max (degC)": "最高温度（degC）",
    "Temperature certified?": "温度是否经过认证？",
    "no (TEMP_UNCALIBRATED)": "否（TEMP_UNCALIBRATED）",
    "Lead state": "导联状态",
    "UNKNOWN only (no lead-off hardware)": "始终为 UNKNOWN（无导联脱落检测硬件）",
    "see Data sheet": "见“数据”工作表",
    "Probe state": "探头状态",
    "no data": "无数据",
    " (temperature uncertified)": "（温度未经认证）",
    "ECG raw min / max (ADC code)": "ECG 原始码值最小 / 最大（ADC 码）",
    "Packets received": "接收报文数",
    "Sequence gaps": "序列号空洞数",
    "Packets missing by sequence": "按序列号推算的缺失报文数",
    "ECG index gaps (batches lost)": "ECG 序号跳变（丢失批次数）",
    # matches pc_monitor/i18n_widgets.py: the same label in two places must
    # not be rendered two ways (test_i18n fails on a cross-table collision).
    "CRC errors": "CRC 错误",
    "Discarded bytes (resync)": "重同步丢弃字节数",
    "Protocol version": "协议版本",
    "Frame layout": "帧结构",
    "ECG_BATCH payload = %d + 2n bytes (tail = %d)": "ECG_BATCH 负载 = %d + 2n 字节（尾部 = %d）",
    "Exported at (PC clock)": "导出时间（PC 时钟）",
    "Exporter": "导出工具",
    # -- the decimated series sheet and the embedded chart --
    "Decimated for the chart only. The complete record is on '%s'. "
    "1 plotted point ~= %d sample(s); each bin contributes its maximum and "
    "minimum so the QRS survives.": (
        "本表仅为绘图抽稀，完整记录见“%s”工作表。1 个绘制点约等于 %d 个采样点；"
        "每个分箱各取最大值与最小值，因此 QRS 波不会被抹平。"
    ),
    "%s ECG -- raw, millivolts at the MCU pin": "%s ECG —— 原始引脚电压（毫伏）",
    "%s ECG mV": "%s ECG（mV）",
    "DEMO / SYNTHETIC": "演示 / 合成数据",
    "Device": "设备",
    "device": "设备",
    "device time (s, from first_sample_index at 1 kHz)": (
        "设备时间（s，由 first_sample_index 按 1 kHz 推算）"
    ),
    "ECG pin voltage (mV, unfiltered)": "ECG 引脚电压（mV，未滤波）",
    # -- "how to read this file": recorder caveats, translated as written --
    "How to read this file (limits stated by the firmware contract)": (
        "如何阅读本文件（固件契约已声明的限制）"
    ),
    "SYNTHETIC DATA: this recording was generated by --demo. It is not a "
    "measurement and must not be presented as one.": (
        "合成数据（SYNTHETIC DATA）：本记录由 --demo 生成，不是测量结果，"
        "严禁作为测量结果呈现。"
    ),
    "ecg_pin_mv is raw, unfiltered ADC code converted to millivolts at the MCU pin. "
    "It is NOT a body-surface potential: ECG_FRONTEND_GAIN and _OFFSET_MV are marked "
    "UNVERIFIED in App/config/ecg_config.h.": (
        "ecg_pin_mv 是未经滤波的原始 ADC 码换算出的 MCU 引脚毫伏数，并非体表电位："
        "App/config/ecg_config.h 中 ECG_FRONTEND_GAIN 与 _OFFSET_MV 标注为 UNVERIFIED。"
    ),
    "Temperature degrees are blank wherever the device reported "
    "TEMP_UNCALIBRATED (TEMP_SENSOR_MODEL is uncalibrated in this baseline), "
    "so the temperature statistics cover only certified samples.": (
        "设备报告 TEMP_UNCALIBRATED 之处，温度摄氏度一栏留空（本基线的 "
        "TEMP_SENSOR_MODEL 尚未校准），因此温度统计只涵盖已认证样本。"
    ),
    "temp_c is blank whenever the device did not certify the temperature.": (
        "设备未认证温度时，temp_c 一律留空。"
    ),
    "lead_state is UNKNOWN throughout: CAP_LEAD_HW_DETECT is clear, so there is "
    "no lead-off detection hardware and no electrode verdict exists.": (
        "lead_state 全程为 UNKNOWN：CAP_LEAD_HW_DETECT 位为 0，即不存在导联脱落检测"
        "硬件，也就不存在任何电极状态结论。"
    ),
    "host_timestamp is per-batch arrival time; per-sample timing comes from "
    "first_sample_index at 1 kHz, which is the contract's authoritative time axis.": (
        "host_timestamp 是各批次的到达时刻；逐样本时间取自 first_sample_index（1 kHz），"
        "后者才是契约规定的时间基准。"
    ),
    "%d batch(es) disagreed about heart-rate validity across hr_bpm / hr_state / "
    "SFLAG_HR_VALID; those were recorded as invalid.": (
        "有 %d 个批次在 hr_bpm / hr_state / SFLAG_HR_VALID 三处对心率有效性的判定"
        "不一致，这些批次均按无效记录。"
    ),
}

i18n.register(ZH)
