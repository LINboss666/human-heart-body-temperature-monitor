# 人体心电体温监测仪

[English](README.md) · **中文**

> **Phase 1 —— 应用软件已全部写完，可通过编译，并在主机上测过；但从未在硬件上运行过。**
>
> 这是一个学生课程设计项目，**不是医疗器械**。它不做任何诊断，其输出不得用于任何临床
> 决策。本固件从未采集过任何人体心电或体温数据，也没有任何一块屏幕显示过它的任何一个
> 像素。哪一条结论来自宿主测试、哪一条仍在等待硬件，见
> [docs/COURSE_REQUIREMENTS.md](docs/COURSE_REQUIREMENTS.md)（中文说明见该文档正文）。

| 项目 | 数值 |
| --- | --- |
| MCU | STM32F103C8T6 —— Cortex-M3，64 KB Flash，20 KB SRAM |
| 工具链 | Keil MDK-ARM（AC5 / ARMCC V5.06）、STM32CubeMX 6.17.0、FW_F1 V1.8.7 |
| 固件编译结果 | `0 Error(s), 0 Warning(s)` |
| 占用 | `Code=38364 RO=3096 RW=380 ZI=7524` → **Flash 63.8 %，RAM 38.6 %** |
| 宿主测试 | 6 个 C 可执行文件、1050 条断言；Python 用例 256 个，全部 0 失败 |
| 已在硬件上验证 | **一项都没有。** 见 [docs/HARDWARE_TEST_PLAN.md](docs/HARDWARE_TEST_PLAN.md) |
| Phase 0 基线 | tag `v0.1-baseline`，commit `eb8a795` |

## 它做什么

STM32F103C8T6 以 **1000 点/秒** 采集调理后的心电信号与模拟体温信号，在设备端计算心率，
通过 KK_UI 库把心电波形、体温和日期时间显示在 128×64 OLED 上，接受三个按键输入，用 RTC
保持墙钟时间，并通过带 CRC 校验的二进制记录流送到 PC 端程序；PC 端实时绘图，并导出
CSV 与内嵌波形图的 Excel 工作簿。

## 总体架构

没有 RTOS，没有堆，信号链里没有浮点，协作式超级循环。完整数据流见
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

```
TIM3 TRGO 1 kHz ──► ADC1 扫描（CH0 心电、CH1 体温）──► DMA1_Channel1 循环模式
   └─ 半/全传输中断里只置一个标志位
        ▼
App_Loop ─► ecg_signal      ─► 三条通路：RAW（未滤波）· DISPLAY · QRS
         ─► ecg_hr          ─► 最近 5 个 RR 间区取中位数
         ─► temperature      ─► 250 ms 抽取 + 标定挂钩
         ─► ui_app           ─► KK_UI 页面 + min/max 抽取的 OLED 波形
         ─► protocol_service ─► ECG_BATCH（每帧 20 点）─► USART1 230400
pc_monitor/  PySide6 + pyqtgraph ─► 环形缓冲 ─► 25 FPS 绘图 ─► CSV / XLSX
```

| 层次 | 目录 |
| --- | --- |
| 应用层 | [`App/`](App/) —— acquisition、ecg、temperature、rtc_service、buttons、protocol、uart、display、ui、diagnostics |
| 第三方 UI | [`ThirdParty/kk_ui`](ThirdParty/kk_ui/)、[`ThirdParty/kk_oled`](ThirdParty/kk_oled/) —— MIT，见 [docs/UPSTREAM.md](docs/UPSTREAM.md) |
| CubeMX 生成物 | `Core/`、`Drivers/`、`.ioc` 文件 |
| PC 上位机 | [`pc_monitor/`](pc_monitor/) |
| 宿主测试脚手架 | [`tests/host/`](tests/host/)、[`tools/`](tools/) |

## 引脚分配

| 引脚 | 功能 | 引脚 | 功能 |
| --- | --- | --- | --- |
| PA0 | ADC1_IN0 —— 调理后心电输入 | PB6 | I2C1_SCL → OLED |
| PA1 | ADC1_IN1 —— 体温模拟输入 | PB7 | I2C1_SDA → OLED |
| PA9 | USART1_TX → PC | PA13 | SWDIO |
| PA10 | USART1_RX ← PC | PA14 | SWCLK |
| PB12 | KEY_UP（输入、上拉、低有效） | PC14/PC15 | LSE 32.768 kHz |
| PB13 | KEY_DOWN | PD0/PD1 | HSE 8 MHz |
| PB14 | KEY_OK / 启停 | | |

带验证结论的完整表格：[docs/PINMAP.md](docs/PINMAP.md)。

## 采集链 —— 以及为什么它不是软件采样

TIM3 以 1 kHz 溢出，它的 TRGO 脉冲触发 ADC 的外部触发源；ADC 扫描两个 rank；DMA 把每一
个转换结果搬进一个 1024 字节的环形缓冲。**定时器中断既没有使能，也不需要** —— 整条链路
在中断里做的全部工作就是置一个标志位。外设配置与实测滤波器响应见
[docs/CUBEMX_CONFIG.md](docs/CUBEMX_CONFIG.md)。

## 心电处理

从调理级出来的是三条彼此独立的路径，而"彼此独立"正是重点。
**RAW** 完全不经滤波，也是送给 PC 记录的那一路，因为课程指标要求 0.05–150 Hz 的记录
带宽；把记录截成心率提取所需的窄带，毁掉的正是被评分的东西。**DISPLAY** 去基线并经梳状
滤波，供 OLED 使用。**QRS** 是一个带通能量检测器（实测相对自身峰值的 −3 dB 带宽为
3.8–26.3 Hz）：微分 → 平方 → 120 ms 积分 → 自标定
门限 → 250 ms 不应期 → 5 个 RR 间区取中位数。

滤波器用的是 2 的幂次泄漏积分器和矩形窗滑动平均，**不是双二阶**。实测一个 Q14 双二阶
高通在完全平直的输入上稳定输出 339 码：半 LSB 的舍入项在直流处被一个接近零的分母放大
了约 1024 倍。而 20 抽头梳状滤波器把 50 Hz 精确对消（实测 −313 dB）。

在叠加了 0.3 Hz 基线漂移、50 Hz 工频和宽带噪声的合成心跳上，实测心率为
50→49、60→60、72→72、95→95、120→120、150→150 bpm。**这里不宣称对人体信号的任何精度。**

导联脱落：本设计没有脱落检测硬件，所以固件只会报 `SIGNAL_POOR` 或 `LEAD_UNKNOWN`，
**绝不**声称某个电极已断开。

## 体温

`temperature_raw` 与 `temperature_calibration` 是刻意分开的两层。模拟前端是组里其他人
的设计，不在本仓库内，因此当前模型是 `UNCALIBRATED`、`valid=false`，UI 显示
`--.- °C` —— **不会编造一个看起来很合理的数字**。原始码值与其引脚毫伏仍然可取，因为后
续标定需要的正是这些。替换一个头文件和一个函数即可完成标定接入。

## OLED 与 KK_UI

KK_UI（菜单、信息页、整数/布尔编辑器、toast、动画）叠加在 KK_OLED 之上，I2C1 跑
400 kHz，128×64 单色屏。KK_UI **没有波形控件**，上游也禁止往里加，所以心电曲线是一个自
定义页面，用 KK_OLED 的原语以 min/max 抽取绘制 —— 这样窄的 QRS 峰才不会在相邻列之间被
平均掉。

控制器型号（**SSD1306 / SH1106 / CH1116**）和地址（`0x3C` / `0x3D`）**未知**。仓库里带了
三份完整的配置，全部标注为未验证；地址在开机时探测并上报。上游自己的移植规范明确禁止
猜测这些值，而 I2C 有应答只能证明"有器件在应答"，不能证明它是屏。如果没有屏应答，固件
会继续采集、继续计时、继续往外送数据。见 [docs/KK_UI_NOTES.md](docs/KK_UI_NOTES.md)。

字库只有 ASCII，且由仓库内的 `tools/gen_oled_fonts.py` 自己生成，因为上游字形服务的输出
不在 MIT 授权范围内 —— 见 [docs/UPSTREAM.md](docs/UPSTREAM.md)。

## 二进制串口协议

帧头 `A5 5A`、版本、类型、序号、长度、设备时间戳、载荷、CRC16（CCITT-FALSE，校验值
`0x29B1`）；小端；可重新同步的解析器。`ECG_BATCH` 携带 20 个原始码值加当前体温、心率和
状态标志，并带一个绝对的 `first_sample_index`，PC 端据此重建精确的 1 kHz 时间轴，并发现
那些靠序号计数器根本看不出来的整块丢失。**实测带宽占用：3608 B/s ÷ 23040 B/s =
15.7 %。** 帧布局见 [docs/PROTOCOL.md](docs/PROTOCOL.md)，唯一定义在
`App/protocol/protocol.h`。

## 编译固件

在 uVision 中打开 `MDK-ARM/Human Heart and Body Temperature Monitor.uvprojx`，编译唯一的
那个 target；或者：

```
"C:\Keil_v5\UV4\UV4.exe" -j0 -b "Human Heart and Body Temperature Monitor.uvprojx" -o build.log
```

**每次 CubeMX 重新生成之后**，都要重新加回应用分组 —— `.uvprojx` 归 CubeMX 所有，它会把
手工添加的分组丢掉；这个脚本是幂等的：

```
python tools/add_keil_sources.py
```

细节与实测占用见 [docs/BUILD.md](docs/BUILD.md)。

## 运行 PC 上位机

```
cd pc_monitor
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python -m pytest -q             # 宿主测试
.venv/Scripts/python -m pc_monitor --demo     # 合成数据，不需要开发板
.venv/Scripts/python -m pc_monitor --port COM7 --baud 230400
```

`--demo` 不会伪装成真实数据：界面上有 DEMO/SYNTHETIC 横幅，合成数据行会被标记，因此无法
被当作一次测量导出。

## 验证状态，如实说明

| 已做到的、有证据的 | 未被声称的 |
| --- | --- |
| 干净编译通过，且没有关闭任何告警 | 任何来自人体的测量 |
| 1050 条宿主断言跑在实际出货的整数代码上 | RTC 晶振能否起振、VBAT 能否保持 |
| C 与 Python 两侧协议逐字节对齐（21 条 C 生成帧回放） | 屏幕上是否真的出现过任何一个像素 |
| 日历 1970→2099 逐小时往返测试 | ±2 bpm 的心率精度 |
| 字库能被厂商自己的解码器解出 | 前端增益或偏置是否正确 |
| Flash/RAM 各余 23 KB / 12 KB | 加载 UI 与 UART 后 1 kHz 是否还守得住 |

所有未完成项都配有可执行的步骤，见
[docs/HARDWARE_TEST_PLAN.md](docs/HARDWARE_TEST_PLAN.md)。

## 安全声明

* **教学课程设计项目。不是医疗器械。** 不做诊断，不做临床用途。
* 电极**绝对不能**直接接到 PA0。该 ADC 引脚期望的是由其他组员设计的、符合人体安全要求
  的、隔离合格的模拟调理电路的输出。
* 通过 PC 或市电供电的链路会带来超出本固件范围的隔离义务；在把人接上去之前请先考虑清楚。
* 软件滤波不能替代输入阻抗、共模抑制比、隔离、电气安全和模拟频响。

## 文档索引

| 文件 | 作用 |
| --- | --- |
| [docs/BASELINE.md](docs/BASELINE.md) | Phase 0：哪些已冻结、哪些在等硬件 |
| [docs/PINMAP.md](docs/PINMAP.md) | 引脚表，含 EXPECTED / ACTUAL / MISMATCH 结论 |
| [docs/CUBEMX_CONFIG.md](docs/CUBEMX_CONFIG.md) | 时钟树与每一项外设配置 |
| [docs/BUILD.md](docs/BUILD.md) | Keil target 设置与构建验证方式 |
| [docs/PROTOCOL.md](docs/PROTOCOL.md) | 线上格式与带宽推算 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 数据流、遵循的规则、已知弱点 |
| [docs/COURSE_REQUIREMENTS.md](docs/COURSE_REQUIREMENTS.md) | 逐条需求的状态 |
| [docs/HARDWARE_TEST_PLAN.md](docs/HARDWARE_TEST_PLAN.md) | 台架上手测试，阶段 A–O |
| [docs/KK_UI_NOTES.md](docs/KK_UI_NOTES.md) | 上游到底是什么，以及本项目的符合性 |
| [docs/UPSTREAM.md](docs/UPSTREAM.md) | 第三方来源与授权 |
| [docs/REVIEW_HANDOFF.md](docs/REVIEW_HANDOFF.md) · [PHASE1_REVIEW_HANDOFF.md](docs/PHASE1_REVIEW_HANDOFF.md) · [PHASE1_REVIEW_FIX_HANDOFF.md](docs/PHASE1_REVIEW_FIX_HANDOFF.md) · [PHASE1_FINAL_HANDOFF.md](docs/PHASE1_FINAL_HANDOFF.md) | 独立代码审查交接包，以及每一轮改了什么 |

Phase 0 冻结在 tag `v0.1-baseline`。Phase 1 软件冻结在分支 `phase1/final-fixes`，它是审查链
`phase1/full-system` → `phase1/review-fixes` → `phase1/final-fixes` 的末端。
两者都不构成"这是一台完成度合格的仪器"的声明。
