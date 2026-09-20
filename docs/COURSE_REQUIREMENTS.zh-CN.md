# 课程需求映射 — 阶段 1

[English](COURSE_REQUIREMENTS.md) · **中文**

每一行都同时陈述**代码做了什么**与**已经证明了什么**。
除非某个宿主测试在没有硬件的情况下证明了它，否则这里不会出现 PASS 这个词。
所使用的两个判定词是：

| 判定词 | 含义 |
| --- | --- |
| `SOFTWARE IMPLEMENTED` | 代码存在，并已构建进固件 |
| `HOST VERIFIED` | 由 `tests/host/` 或 `pc_monitor/tests/` 中的宿主测试执行过 |
| `HARDWARE VERIFICATION PENDING` | 在 [HARDWARE_TEST_PLAN.zh-CN.md](HARDWARE_TEST_PLAN.zh-CN.md) 的台架工作完成之前无法确认 |

下面任何依赖人体、定制模拟前端或实体显示屏的需求，都没有被标记为满足。

## 信号采集

| 需求 | 实现 | 状态 |
| --- | --- | --- |
| ECG 采样 ≥ 500 samples/s | TIM3 TRGO 以 **1000 Hz** 触发 ADC1 扫描；DMA1_Channel1 在硬件中搬运结果 | `SOFTWARE IMPLEMENTED` · `HARDWARE VERIFICATION PENDING` (Stage G) |
| 进阶需求 ≥ 1000 samples/s | 正好 1000 Hz：72 MHz / (71+1) / (999+1)。推导见 [CUBEMX_CONFIG.zh-CN.md](CUBEMX_CONFIG.zh-CN.md) | `SOFTWARE IMPLEMENTED` · `HARDWARE VERIFICATION PENDING` |
| 采样不得依赖 ISR | 没有使能任何 TIM3 中断，也不存在 `TIM3_IRQHandler`；ISR 里唯一的工作就是置一个标志 | `SOFTWARE IMPLEMENTED` · 通过审阅 `.ioc` 与 `stm32f1xx_it.c` 验证 |
| 两个同时进行的通道且无通道交换 | 缓冲区长度是通道数的偶数倍（`128 frames × 2 blocks × 2 channels`）；交错在环形回卷处不可能错位 | `SOFTWARE IMPLEMENTED` · `HOST VERIFIED`（帧索引连续性测试） |
| ECG 与温度不得使用成品采集模块 | 前端由项目组其他成员设计，**不**在本仓库中；MCU 侧只暴露一个 ADC 输入 | 设计约束，归属在本仓库之外 |

## ECG 处理

| 需求 | 实现 | 状态 |
| --- | --- | --- |
| 原始记录必须保持其带宽 | `RAW` 通路是未滤波的 12-bit 码值，流向 PC 并写入 CSV/XLSX。滤波链中的任何环节都不触碰它 | `SOFTWARE IMPLEMENTED` · `HOST VERIFIED` ("the RAW path reports exactly the ADC code that went in") |
| 记录带宽 0.05–150 Hz | 由**模拟**前端决定，而它尚不存在。软件显示通路实测 -3 dB 在 22.1 Hz，QRS 带通相对自身峰值为 3.8–26.3 Hz；两者都不被声称为记录带宽 | `HARDWARE VERIFICATION PENDING` (Stage H) |
| 基线漂移去除 | 显示通路与 QRS 通路上两级级联的 ~0.62 Hz 泄漏积分器 | `SOFTWARE IMPLEMENTED` · `HOST VERIFIED`（在 300 计数、0.3 Hz 漂移下仍能检出搏动） |
| 市电干扰 | 20 抽头梳状滤波器，50 Hz 处为精确零点（**实测 -313 dB**），可选 60 Hz（零点约 58.8 Hz，60 Hz 处 -34 dB）或关闭 | `SOFTWARE IMPLEMENTED` · `HOST VERIFIED`（50 Hz 处 \|display\| 均值：252 → 1） |
| 心率计算 | Pan-Tompkins 风格的能量检测器：HP 5 Hz → MA(20) → 3 点微分 → 平方 → 120 ms MWI → 自标定阈值 → 250 ms 不应期 → 最近 5 个 RR 的中位数 | `SOFTWARE IMPLEMENTED` · `HOST VERIFIED` |
| 心率精度 | 50→49, 60→60, 72→72, 95→95, 120→120, 150→150 bpm，针对带噪声、市电与漂移的合成搏动 | `HOST VERIFIED` **仅在合成数据上**。不作出任何关于人体的 ±2 bpm 的声称：任务书禁止，且没有任何东西被测量过 |
| 心率报警 | `HR_INVALID / ACQUIRING / NORMAL / LOW / HIGH`；高低区间可在设备端以及通过链路修改 | `SOFTWARE IMPLEMENTED` · `HOST VERIFIED`（状态机测试） |
| 尚无心率时不得显示心率 | 在存在 ≥3 个有效 RR 间期之前显示 `--` | `SOFTWARE IMPLEMENTED` · `HOST VERIFIED` ("no heart rate is reported before enough RR intervals exist") |
| 读数不得无声地过期 | 有效读数在无搏动 3 s 之后过期 | `SOFTWARE IMPLEMENTED` · `HOST VERIFIED` |
| 导联脱落 / 电极脱落检测 | **未实现，也无法实现**：本设计中不存在导联脱落硬件。软件只对平线或触及轨的输入报告 `SIGNAL_POOR`，其余情况报告 `LEAD_UNKNOWN` | `SOFTWARE IMPLEMENTED`（诚实的子集）· 硬件接口 `HARDWARE VERIFICATION PENDING` (Stage K) |

## 温度

| 需求 | 实现 | 状态 |
| --- | --- | --- |
| 体温测量 | PA1 / ADC1_IN1，采用 raw/标定 拆分。目前只有 raw 层是真实的 | `SOFTWARE IMPLEMENTED` · `HARDWARE VERIFICATION PENDING` (Stage J) |
| 换算为度数 | `TEMP_SENSOR_MODEL` 是 `UNCALIBRATED`，因此状态为 `TEMP_UNCALIBRATED`、`valid=false`，UI 显示 `--.-` | 有意如此。任何地方都没有臆造的 36.5 —— `HOST VERIFIED` |
| 刷新 ≤ 500 ms | 抽取窗口在 1 kHz 下为 250 samples → 每 250 ms 一个新读数 | `SOFTWARE IMPLEMENTED` · `HOST VERIFIED`（更新节奏） |
| 0.1 °C 显示分辨率 | 内部单位是百分度（`int16_t`），显示 0.1 °C | `SOFTWARE IMPLEMENTED` |
| 探头断开检测 | 基于 `TEMP_ADC_OPEN/SHORT_THRESHOLD` 的范围启发式，经 `TEMP_PROBE_FAULT_CONFIRM_WINDOWS`（2 个窗口 ≈ 500 ms）确认，经 `TEMP_PROBE_OK_CONFIRM_WINDOWS`（4 个窗口 ≈ 1000 ms）清除；报告为 `TEMP_PROBE_FAULT` | `SOFTWARE IMPLEMENTED` · 阈值 `UNVERIFIED` · `HOST VERIFIED`（逐窗口确认）· `HARDWARE VERIFICATION PENDING` |
| 传感器标定 | 替换 `temperature_calibration.h` 与 `TemperatureConvert()` | 接口存在 · `HOST VERIFIED`：线性分支会换算并钳位 |

## 时间保持

| 需求 | 实现 | 状态 |
| --- | --- | --- |
| RTC 日期/时间 | `rtc_service` 运行在由 LSE 驱动的 RTC 之上，内部使用 epoch 秒。保持彼此分层的几层：备份域秒计数器、RTC 接口时钟（`RCC_BDCR` `RTCEN`，HAL 只在 `HAL_RTC_MspInit()` 中拉起它，而本条 pre-init 路径自行拉起它）、APB 可见的 `CNTH`/`CNTL` 副本（在信任任何读数之前，通过 `RSF` 以有界超时重新获取），以及锚定到某次计数器读数的软件 epoch | `SOFTWARE IMPLEMENTED` |
| 不得每次上电都复位 | CubeMX 在 `MX_RTC_Init` 中无条件写入 2000-01-01；该服务以 blank-commit → payload → valid-commit 事务写入一个锚点（epoch + 计数器，五个 16-bit 备份寄存器），并从中恢复 epoch | `SOFTWARE IMPLEMENTED` · 在纯锚点模型上 `HOST VERIFIED`（对每一种写入前缀做重放）· `HARDWARE VERIFICATION PENDING` (Stages A/B/O) |
| 主电断开时仍保留时间 | 需要在真实板上把 VBAT 接好 | `HARDWARE VERIFICATION PENDING` —— 固件从不声称（`CAP_RTC_BATTERY_BACKED` 始终为清零） |
| 拒绝做无法自证合理的重建 | 同步超时、锚点缺失、计数器不可读或差值不合理时，时钟被报告为**未设置**而不是猜测：丢失时间比给出一个错误的时钟更安全 | `SOFTWARE IMPLEMENTED` · `HOST VERIFIED` |
| 日历正确性 | Hinnant civil-days 运算、无符号 epoch、范围 1970–2099 | `HOST VERIFIED`：`tests/host/test_rtc_calendar.c` 中的 189 条断言 —— 1970→2099 逐小时往返、闰年/世纪规则、Dec-31→Jan-1、星期连续性，外加锚点模型 |
| 可在设备端修改 | `DATE & TIME` 页面，配 KK_UI 整数编辑器与 `SET RTC` / `READ RTC` 动作 | `SOFTWARE IMPLEMENTED` · `HARDWARE VERIFICATION PENDING`（尚无面板） |
| 可远程修改 | `SET_RTC` 包；每个字段都经过校验，无效时间以 `NACK_BAD_VALUE` 回应且时钟保持不动 | `SOFTWARE IMPLEMENTED` · Python 侧 `HOST VERIFIED` |

## 显示与人机界面

| 需求 | 实现 | 状态 |
| --- | --- | --- |
| OLED 人机界面 | KK_UI + KK_OLED，走 I2C1，400 kHz，128×64 | `SOFTWARE IMPLEMENTED` · `HARDWARE VERIFICATION PENDING` (Stages D/E) |
| 控制器身份 | 未知。三个编译期配置（SSD1306 / SH1106 / CH1116），电荷泵字节与列偏移各不相同；全部标注为 UNVERIFIED | 有意悬而未决，而非猜测 |
| 地址 | 开机时探测 `0x3C` / `0x3D`；结果通过链路以及在 STATUS 页面上报告。ACK 只证明存在，绝不证明控制器型号 | `SOFTWARE IMPLEMENTED` |
| 缺少显示屏不得让设备变砖 | `OLED_Init()` 失败会置 `oled_present=false`；采集、RTC 与 UART 全部继续运行 | `SOFTWARE IMPLEMENTED` · `HARDWARE VERIFICATION PENDING` (Stage E) |
| 页面 | MAIN 菜单、ECG（自定义波形）、BODY TEMP、STATUS、DATE & TIME、SETTINGS、ABOUT | `SOFTWARE IMPLEMENTED` |
| 128 px 上的波形 | min/max 抽取，每列 8 samples，以垂直线段绘制，因此窄 QRS 不会被平均掉 | `SOFTWARE IMPLEMENTED` · `HARDWARE VERIFICATION PENDING` |
| UI 不得阻塞采样 | 显示刷新是局部分区的，并在主循环中运行；ADC 链路由硬件驱动，并有 128 ms 的缓冲 | `SOFTWARE IMPLEMENTED` · `HARDWARE VERIFICATION PENDING` (Stage M) |
| 字体授权 | 仅 ASCII，由 `tools/gen_oled_fonts.py` 在本仓库内自行编写；无第三方字体，也无 CJK 字表 | `HOST VERIFIED`，针对 vendor 解码器，78 条断言 |

## 通信与上位机软件

| 需求 | 实现 | 状态 |
| --- | --- | --- |
| 数据通信 | USART1 上的二进制分帧协议，230400 8N1；链路上没有 `printf` CSV | `SOFTWARE IMPLEMENTED` |
| 带宽必须被证明，而不是被假定 | 50 × 69 B ECG_BATCH + 2 × 57 B STATUS + 2 × 22 B TEMP_STATUS = **3608 B/s（23040 B/s 中）= 15.7 %** | `HOST VERIFIED`（算术见 [PROTOCOL.zh-CN.md](PROTOCOL.zh-CN.md)） |
| 完整性 | Magic + version + length + sequence + CRC16；损坏时重新同步 | `HOST VERIFIED`：C 与 Python 两侧共 574 条断言 |
| PC 实时波形 | PySide6 + pyqtgraph，10 s 滚动窗口 | 见 [../pc_monitor/README.zh-CN.md](../pc_monitor/README.zh-CN.md) |
| ≥ 10 s 连续录制 | 录制是一个带会话计时器的状态；1 kHz 流是连续的 | `SOFTWARE IMPLEMENTED` · `HARDWARE VERIFICATION PENDING` (Stage M) |
| 导出为 CSV | 每一行，UTF-8 | `HOST VERIFIED`（Python 测试） |
| 波形可在 Excel 中查看 | XLSX 含 `Data`（所有行）、`Summary`，以及一张内嵌的 ECG `LineChart`，抽取到几千个点以便文件能打开 | `HOST VERIFIED`（测试重新打开工作簿，并断言两个工作表和一张图表存在） |

## 资源限制

| 约束 | 结果 |
| --- | --- |
| 不使用 RTOS | 仅协作式超级循环 |
| 不使用 `malloc` / `free` | `App/` 中以及两个随仓携带的库中都没有（grep 已核实） |
| 不携带大型中文字体表 | 仅 ASCII，1113 bytes |
| 信号链中不使用浮点 | 所有滤波与检测算术都是整数 |
| 不得靠更换器件来伪造 flash 占用 | 器件仍是 `STM32F103C8`，ROM `0x08000000` 大小 `0x10000`，RAM `0x20000000` 大小 `0x5000` —— 与阶段 0 相比未变 |
| 实测占用 | `Code=38392 RO=3096 RW=380 ZI=7524` → 41868 B flash（占 64 KB 的 **63.9 %**），7904 B RAM（占 20 KB 的 **38.6 %**） |
| 编译器告警 | Keil 告警级别 2 下 `0 Warning(s)`，无 `--diag_suppress`，无整类告警抑制 |

## 未满足之处，直说如下

1. **不存在任何来自人体的测量。** 每一个心率数字都来自合成搏动。模拟前端并未做出。
2. **从未见过任何像素。** 面板、它的控制器、它的地址与它的上拉电阻全部未经确认；UI 仅通过
   编译验证。
3. 在真实硅片上，**LSE 起振、VBAT 保持与触及轨的输入行为都尚未被观察到**。
4. **课程所描述的那种导联脱落检测并未实现**，因为所需的硬件接口不存在。现有的是信号质量
   报告，其命名刻意避免暗示做过某种没人做过的电极测量。
5. 本仓库的任何位置都**不**声称 `±2 bpm` 心率精度。
