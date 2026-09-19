# 引脚表 — 阶段 0 基线

[English](PINMAP.md) · **中文**

下面每一行都对照**两个**独立来源做过核验，而不是只对照项目任务书：

1. `Human Heart and Body Temperature Monitor.ioc`（CubeMX 工程文件）
2. `Core/Src/` 下生成的 C 源文件

判定图例：
`MATCH` = 预期与实际一致 · `MISMATCH` = 二者不一致 · `GAP` = 预期项在本工程中找不到对应内容 · `N/A` = 无法由源代码证明。

## 信号引脚

| 引脚 | 功能（预期） | EXPECTED | ACTUAL（证据） | 判定 |
| --- | --- | --- | --- | --- |
| PA0 (`PA0-WKUP`) | ECG 调理后的模拟输入 | ADC1_IN0, rank 1, 55.5 cyc | `.ioc`: `PA0-WKUP.Signal=ADCx_IN0`, `SH.ADCx_IN0.0=ADC1_IN0,IN0`; `adc.c`: `ADC_CHANNEL_0` / `ADC_REGULAR_RANK_1` / `ADC_SAMPLETIME_55CYCLES_5`; `HAL_ADC_MspInit` 设置了 `GPIO_MODE_ANALOG` | MATCH |
| PA1 | 温度模拟输入 | ADC1_IN1, rank 2, 55.5 cyc | `.ioc`: `PA1.Signal=ADCx_IN1`; `adc.c`: `ADC_CHANNEL_1` / `ADC_REGULAR_RANK_2` / `ADC_SAMPLETIME_55CYCLES_5` | MATCH |
| PB6 | OLED SCL | I2C1_SCL, AF 开漏 | `.ioc`: `PB6.Signal=I2C1_SCL`; `i2c.c`: `GPIO_MODE_AF_OD`, `GPIO_SPEED_FREQ_HIGH` | MATCH |
| PB7 | OLED SDA | I2C1_SDA, AF 开漏 | `.ioc`: `PB7.Signal=I2C1_SDA`; `i2c.c` 同一个 MspInit 代码块 | MATCH |
| PA9 | PC 链路 TX | USART1_TX, AF 推挽 | `.ioc`: `PA9.Signal=USART1_TX`, `Mode=Asynchronous`; `usart.c`: `GPIO_MODE_AF_PP`, `GPIO_SPEED_FREQ_HIGH` | MATCH |
| PA10 | PC 链路 RX | USART1_RX, 浮空输入 | `.ioc`: `PA10.Signal=USART1_RX`; `usart.c`: `GPIO_MODE_INPUT`, `GPIO_NOPULL` | MATCH |
| PB12 | `KEY_UP` | GPIO 输入，内部上拉，低电平有效接到 GND | `.ioc`: `PB12.Signal=GPIO_Input`, `PB12.GPIO_PuPd=GPIO_PULLUP`, `Locked=true`; `gpio.c`: `GPIO_MODE_INPUT` + `GPIO_PULLUP` | MATCH |
| PB13 | `KEY_DOWN` | 与 PB12 相同 | `.ioc`: `PB13.GPIO_PuPd=GPIO_PULLUP`; `gpio.c` 中该行与 PB12/PB14 归为同一组 | MATCH |
| PB14 | `KEY_OK` / ECG 启动 | 与 PB12 相同 | `.ioc`: `PB14.GPIO_PuPd=GPIO_PULLUP`; `gpio.c` 同一分组 | MATCH |
| PA13 | SWDIO | SYS_JTMS-SWDIO, Serial Wire | `.ioc`: `PA13.Signal=SYS_JTMS-SWDIO`, `Mode=Serial_Wire` | MATCH |
| PA14 | SWCLK | SYS_JTCK-SWCLK, Serial Wire | `.ioc`: `PA14.Signal=SYS_JTCK-SWCLK`, `Mode=Serial_Wire` | MATCH |
| PC14 | OSC32_IN | LSE 外部振荡器 | `.ioc`: `PC14-OSC32_IN.Mode=LSE-External-Oscillator`, `Signal=RCC_OSC32_IN` | MATCH |
| PC15 | OSC32_OUT | LSE 外部振荡器 | `.ioc`: `PC15-OSC32_OUT.Mode=LSE-External-Oscillator` | MATCH |
| PD0 | OSC_IN | HSE 外部振荡器 | `.ioc`: `PD0-OSC_IN.Mode=HSE-External-Oscillator`, `Signal=RCC_OSC_IN` | MATCH |
| PD1 | OSC_OUT | HSE 外部振荡器 | `.ioc`: `PD1-OSC_OUT.Mode=HSE-External-Oscillator` | MATCH |

全部 15 个引脚都在 `.ioc` 中以 `Mcu.Pin0` … `Mcu.Pin16` 列出，外加三个虚拟引脚
（`VP_RTC_VS_RTC_Activate`、`VP_SYS_VS_Systick`、`VP_TIM3_VS_ClockSourceINT`），
`Mcu.PinsNb=18`。

## GPIO 端口时钟

`gpio.c:MX_GPIO_Init()` 使能了 GPIOC、GPIOD、GPIOA 和 GPIOB 的时钟。这是让 C/D 端口上
的 OSC 引脚可用所必需的，也与上面的引脚列表一致。— MATCH

## 不属于引脚不一致、但必须记录在案的发现

| # | 发现 | 判定 |
| --- | --- | --- |
| 1 | **不存在任何 CubeMX User Labels。** `.ioc` 中没有任何 `*Label*` 键，所以生成的代码只能用光秃秃的 `GPIO_PIN_12/13/14` 指代按键，只能通过 `hadc1` 指代 ADC 通道。本文档中的 `KEY_UP`、`KEY_DOWN`、`KEY_OK` 这些名字来自项目任务书，**不是**来自本仓库。按照阶段 0 的指示，**未**修改 `.ioc` 去添加它们。 | GAP |
| 2 | **PA0 在引脚映射器中是双重用途的。** CubeMX 把该引脚记录为 `PA0-WKUP`（ADC 通道 0 与 `WKUP` 唤醒功能共用）。只选择了 ADC 功能；没有生成任何 EXTI/唤醒配置。 | MATCH（已注明） |
| 3 | **Keil 的器件名与 CubeMX 的型号名不同。** `.ioc` 写的是 `Mcu.CPN=STM32F103C8T6` / `Mcu.UserName=STM32F103C8Tx`；`MDK-ARM/*.uvprojx` 选择的是来自 pack `Keil.STM32F1xx_DFP.2.2.0` 的 `Device = STM32F103C8`。这是同一颗芯片 —— DFP 只是用了更短的名字。不是不一致。 | MATCH（已注明） |
| 4 | **没有添加、移动或删除任何外设引脚。** `PB12/13/14` 在 `.ioc` 中带有 `Locked=true`，那是 CubeMX 的引脚锁定标志，不是手工编辑的证据。 | MATCH |
| 5 | **未使用但已使能的时钟**：GPIOC/GPIOD 的时钟为振荡器引脚而打开。这是正常的 CubeMX 输出，不是缺陷。 | MATCH（已注明） |

## 无法由源代码证明（有待硬件）

| 项目 | 代码为何无法回答 |
| --- | --- |
| OLED 控制器身份（SSD1306 还是 SH1106）与 I2C 7 位地址 | 不存在驱动；`hi2c1.Init.OwnAddress1 = 0` 是 CubeMX 的默认值，不携带任何目标地址。 |
| ECG 前端的输出在 PA0 上是否真的在 0–3.3 V 之间摆动 | 模拟域；不涉及固件。 |
| PA1 信号调理的阻抗是否与 55.5 周期采样相容 | 需要温度前端真实的源阻抗。 |
| PB6/PB7 上外部 I2C 上拉电阻是否存在及其表现 | 开漏配置只能证明 STM32 这一侧。 |
| VBAT 引脚的连接与备份域的保持能力 | 属于供电接线问题，不是固件问题。 |
| 8 MHz HSE 与 32.768 kHz LSE 晶振是否真的贴装并且能起振 | 只有配置而已；如果它们无法启动，`Error_Handler()` 会陷入陷阱。 |
| 按键接线是否真的是一脚接 GND、另一脚接 STM32 引脚 | 固件中只有内部上拉这一侧。 |
