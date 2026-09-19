# `pc_monitor` — PC 上位机工具

[English](README.md) · **中文**

面向 STM32 ECG + 体温监测器的实时显示、录制与导出。它通过 USART1 以 230400 波特率讲
[`docs/PROTOCOL.md`](../docs/PROTOCOL.zh-CN.md) 中的二进制帧格式，并在 [`protocol.py`](protocol.py)
中逐字节镜像 [`App/protocol/protocol.h`](../App/protocol/protocol.h)。

> **状态：已写完，并已验证它能在无界面模式下对着自己内置的合成数据设备运行。从未连接过真实
> 板子。** 没有任何硬件向本应用发送过哪怕一个字节。见下文 [验证](#验证)。

## 安装与运行

```
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt

.venv/Scripts/python -m pc_monitor --demo            # no board needed
.venv/Scripts/python -m pc_monitor --port COM7       # a real device
.venv/Scripts/python -m pytest tests -q              # 251 cases
```

`--demo` 合成 1 kHz ECG，因此每一条路径 —— 组帧、绘图、指标卡、录制、CSV/XLSX、RTC 设置 ——
都能在没有硬件的情况下走一遍。它不可能被误当成一次测量：

* 窗口带有 `DEMO / SYNTHETIC` 横幅；
* `RecordingSession.origin` 在构造时就由该标志固定下来，而不是从字节里推断，并且会被写进
  **每一行导出数据**；
* `export.default_stem()` 会在文件名里放上 `demo`；
* `caveats()` 会前置 `SYNTHETIC DATA: … must not be presented as one`。

`--demo-uncalibrated` 复现出货固件实际报告的内容：`TEMP_UNCALIBRATED`，于是温度卡显示
`--.-` 而不是一个数字。

## 各个文件是什么

| 文件 | 作用 |
| --- | --- |
| `protocol.py` | 线路镜像：偏移、CRC-16、组帧、带类型的载荷解码器、`StreamTracker`。最大的文件，也是应当最先读的文件。 |
| `rtc.py` | 日历运算，刻意与 `App/rtc_service/rtc_calendar.c` 相互独立。 |
| `ring_buffer.py` | `IndexedRingBuffer`（器件的采样轴）、`EventRateMeter`。 |
| `serial_worker.py` | 读线程；独占全部组帧状态，因此 GUI 从不接触字节。 |
| `recorder.py` | `RecordingSession` → 逐样本的行，以及那些阻止导出过度声明的告诫。 |
| `export.py` | CSV 与 XLSX（Summary / Data / Chart 工作表，含一张内嵌折线图）。 |
| `demo_source.py` | `DemoDevice`，一个讲真实协议的合成对端。 |
| `app.py` | Qt 窗口与 `AcquisitionEngine`，从协议到像素的唯一桥梁。 |
| `widgets/` | `EcgPane`（pyqtgraph 波形），`MetricStrip`、`ConnectionPanel`、`StatusStrip`。 |

## 值得知道的设计决策

**记录的是未滤波信号。** `ECG_BATCH` 携带原始 12 位 ADC 码，因为课程指标是 0.05–150 Hz 的
记录带宽。本工具从不对自己存下来的东西重新滤波，而 `ecg_pin_mv` 被标注为引脚电压，
不是体表电位 —— `ECG_FRONTEND_GAIN`/`_OFFSET_MV` 未经证实。

**缺口保持可见。** 每个批量包都携带绝对的 `first_sample_index`，因此丢失一个块会表现为导出
轴上的一个不连续点，以及 Summary 里的 `missing_samples`，而不是被拼接掩盖。`sample_time_s`
由器件轴推导，而不是由上位机计数器。

**未校准就是空白。** 当器件报 `TEMP_UNCALIBRATED` 时，`temp_centi` 与 `temp_c` 写成空单元格。
原始码值仍然被记录，因为那正是后续校准需要的东西。

**滤波器用 `label` + `labelOpts`。** 不是塞在 `label` 里的 dict。见
[`widgets/ecg_plot.py`](widgets/ecg_plot.py) —— 错误的写法会在 pyqtgraph 的构造函数内部抛错，
而应用的其他部分根本不会运行。

## 验证

`tests/` 中有 251 个用例。三组测试各自因不同原因而重要：

| 文件 | 它锁定什么 |
| --- | --- |
| `test_protocol_vectors.py` | 21 个帧**由固件自己的 `crc16.c` 和 `protocol.c` 生成**（见 `tools/gen_protocol_vectors.py`），在此重放。其中包含一项陈旧快照检查，会重新编译 C 并与 JSON 做差异比对。 |
| `test_framing.py` | CRC 的性质、重新同步，以及每一个 `status_flags_t` 位都能往返 —— 包括那个曾经被丢掉的高字节 `ECGP_TAIL`。 |
| `test_gui_smoke.py` | 在 `QT_QPA_PLATFORM=offscreen` 下构建真实的窗口，点击 *Start acquisition* 和 *Record*，并断言样本、被绘出的点和导出的行都出现了。 |

当你改动 `app.py` 或 `widgets/` 下的任何东西时，显式跑一遍 GUI 组：

```
QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest tests/test_gui_smoke.py -q
```

构建这些测试的过程发现并修掉了四个缺陷，而无论怎么 import 或做类型检查都不会把它们暴露出来：
PySide6 6.11 中不存在 `QtGui.ColorRole`，`pg.InfiniteLine` 拒绝 dict 形式的 `label`，
`DEFAULT_WINDOW_SECONDS` 被导入却从未定义，以及 `closeEvent()` 会让一个会话永久停留在录制状态。
前三个意味着**应用根本无法启动**。

## 尚未证实的部分

* 从未涉及真实串口、USB-串口的怪癖或驱动程序。
* 从未涉及真实器件时序：pyqtgraph 在一台负载较重的笔记本上 25 FPS 是否平滑。
* 未对硬件跑过 `--port`，包括固件在 UI 与 UART 同时繁忙时是否仍保持 1 kHz。
* XLSX 里的图表只被程序化读回过，从未在 Excel 中打开。

## 安全

教学课程项目，**不是医疗器械**。此处任何东西都不得用于诊断。在没有认真考虑过隔离问题之前，
不要把人体接入这条链路 —— 见 [项目 README](../README.md#safety)。
