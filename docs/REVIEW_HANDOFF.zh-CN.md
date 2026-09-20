# 审查交接包 — 阶段 0

[English](REVIEW_HANDOFF.md) · **中文**

这是一份交给独立审查者的自包含交接包。这里的一切都是从
`Human Heart and Body Temperature Monitor.ioc` 以及 `Core/Src/` 下生成的 C 代码里读出来的；
没有任何一处是仅凭项目任务书推断的。凡是任务书与代码树不一致的地方，都已明确点出。

---

## 来源

| 字段 | 值 |
| --- | --- |
| 仓库 | https://github.com/LINboss666/human-heart-body-temperature-monitor (PUBLIC) |
| 可见性 | PUBLIC |
| 分支 | `main` |
| 基线提交 | `eb8a795e153ad72cf96d0bf6a71792f5fbfe0427` |
| 标签 | `v0.1-baseline`（附注标签，tag 对象 `a8d206826de71c0b3a181b64cd12cb91cbb681e7`） |
| MCU | STM32F103C8T6, LQFP48, Cortex-M3, 64 KB Flash / 20 KB SRAM |
| STM32CubeMX 版本 | **6.17.0** (`MxCube.Version`)，DB `DB.6.0.170`，`.ioc` 格式 v6 |
| STM32CubeF1 固件包 | **STM32Cube FW_F1 V1.8.7** (`ProjectManager.FirmwarePackage`) |
| Keil target | `Human Heart and Body Temperature Monitor`（单一 target），器件 `STM32F103C8`，pack `Keil.STM32F1xx_DFP.2.2.0` |
| 编译器 | ARMCC V5.06 update 5 (build 528), MDK-ARM Plus 5.43.0.0, AC5, `-O3`, C99 |
| 构建状态 | 一次从零开始的 `UV4 -j0 -r` 重新编译得到 **`0 Error(s), 0 Warning(s)`**；`Code=6804 RO-data=328 RW-data=16 ZI-data=2000` |

至于构建状态为什么要重新核验而不是沿用，见 [BUILD.zh-CN.md](BUILD.zh-CN.md)。

**审查标签，而不是 `main`。** 一个 git 提交不可能包含它自己的哈希，所以基线是
提交 `eb8a795e…` —— 打上标签 `v0.1-baseline` —— 而本文件是在随后一次文档提交里拿到它的哈希和 URL 的。
请审查 `git checkout v0.1-baseline`；该标签与 `main` 头部之间的差异应当恰好就是上表里的那些来源值，
别无其他。用 `git diff v0.1-baseline..main` 确认。

---

## 外设汇总

| IP | 实例 | 是否使能 | 中断 | 备注 |
| --- | --- | --- | --- | --- |
| ADC | ADC1 | 是 | 无（EOC/JEOS 未使能） | 扫描模式，2 次常规转换，由 TIM3 TRGO 触发 |
| DMA | DMA1_Channel1 | 是 | `DMA1_Channel1_IRQn` @ (0,0) | ADC1 → RAM，循环模式，半字 |
| TIM | TIM3 | 是 | **无** | 内部时钟，主 TRGO = 更新事件 |
| I2C | I2C1 | 是 | **无** | 400 kHz 主机，7 位 |
| USART | USART1 | 是 | **无** | 230400 8N1，无流控 |
| RTC | RTC | 是 | **无** | LSE 时钟源，在 MspInit 中使能了备份域访问 |
| GPIO | PB12/13/14 | 是 | 无 | 带上拉的输入 |
| SYS | SWD + SysTick | 是 | SysTick @ (15,0) | Serial Wire 调试 |
| RCC | HSE, LSE, PLL | 是 | — | 8 MHz → 72 MHz；32.768 kHz |

`.ioc` `Mcu.IP0..IP8` = `ADC1, DMA, I2C1, NVIC, RCC, RTC, SYS, TIM3, USART1`.

## 完整引脚映射

| 引脚 | 信号 | 功能 | 电气配置 |
| --- | --- | --- | --- |
| PA0 (`PA0-WKUP`) | `ADC1_IN0` | ECG 调理后的模拟输入，ADC rank 1 | `GPIO_MODE_ANALOG` |
| PA1 | `ADC1_IN1` | 温度模拟输入，ADC rank 2 | `GPIO_MODE_ANALOG` |
| PA9 | `USART1_TX` | PC 链路 TX | `GPIO_MODE_AF_PP`，高速 |
| PA10 | `USART1_RX` | PC 链路 RX | `GPIO_MODE_INPUT`，`GPIO_NOPULL` |
| PA13 | `SYS_JTMS-SWDIO` | SWDIO | Serial Wire 模式，已保留 |
| PA14 | `SYS_JTCK-SWCLK` | SWCLK | Serial Wire 模式，已保留 |
| PB6 | `I2C1_SCL` | OLED SCL | `GPIO_MODE_AF_OD`，高速 |
| PB7 | `I2C1_SDA` | OLED SDA | `GPIO_MODE_AF_OD`，高速 |
| PB12 | GPIO 输入 | KEY_UP（任务书） | `GPIO_MODE_INPUT` + `GPIO_PULLUP` |
| PB13 | GPIO 输入 | KEY_DOWN（任务书） | `GPIO_MODE_INPUT` + `GPIO_PULLUP` |
| PB14 | GPIO 输入 | KEY_OK / ECG 启动（任务书） | `GPIO_MODE_INPUT` + `GPIO_PULLUP` |
| PC14 | `RCC_OSC32_IN` | LSE 输入 | `LSE-External-Oscillator` |
| PC15 | `RCC_OSC32_OUT` | LSE 输出 | `LSE-External-Oscillator` |
| PD0 | `RCC_OSC_IN` | HSE 输入 | `HSE-External-Oscillator` |
| PD1 | `RCC_OSC_OUT` | HSE 输出 | `HSE-External-Oscillator` |

已使能的 GPIO 端口时钟：GPIOA、GPIOB、GPIOC、GPIOD。

`KEY_UP` / `KEY_DOWN` / `KEY_OK` 是**由任务书推导出的名字**。`.ioc` 里**没有定义任何
User Labels**，所以生成的代码只使用 `GPIO_PIN_12/13/14`。见 [PINMAP.zh-CN.md](PINMAP.zh-CN.md)。

## 时钟配置

```
HSE 8 MHz ──┬── HSEPrediv /1 ──► PLL ×9 ──► SYSCLK 72 MHz
            │                                  │
            │                        AHB /1 ───┴── HCLK / FCLK 72 MHz
            │                                      ├── APB1 /2 → 36 MHz → TIM3 clk ×2 = 72 MHz
            │                                      └── APB2 /1 → 72 MHz → USART1, I2C1, ADC1
            │                                      Flash latency = 2 WS
            └── ADC prescaler PCLK2/6 → ADCCLK 12 MHz

LSE 32.768 kHz (PC14/PC15) ──► RTCCLK (RCC_RTCCLKSOURCE_LSE)
SysTick ← HCLK 72 MHz → 1 ms
```

`Core/Inc/stm32f1xx_hal_conf.h` 中的
`HSE_VALUE = 8000000U`, `LSE_VALUE = 32768U`, `LSE_STARTUP_TIMEOUT = 5000U`,
`HSE_STARTUP_TIMEOUT = 100U`。
`TimSysFreq_Value = 72000000`, `ADCFreqValue = 12000000`。`.ioc` 里把 USB 时钟配在 72 MHz，
但 USB 外设并未使能。

## ADC 配置

```c
Instance = ADC1;  ScanConvMode = ADC_SCAN_ENABLE;  ContinuousConvMode = DISABLE;
DiscontinuousConvMode = DISABLE;  ExternalTrigConv = ADC_EXTERNALTRIGCONV_T3_TRGO;
DataAlign = ADC_DATAALIGN_RIGHT;  NbrOfConversion = 2;
Rank 1 → ADC_CHANNEL_0 (PA0), ADC_SAMPLETIME_55CYCLES_5
Rank 2 → ADC_CHANNEL_1 (PA1), ADC_SAMPLETIME_55CYCLES_5
```

没有校准调用，没有看门狗，没有注入式转换，没有溢出处理。
`HAL_ADC_Start()` / `HAL_ADC_Start_DMA()` 在整棵树里**任何地方**都没有出现。

每通道转换时间 = (55.5 + 12.5) / 12 MHz ≈ **5.67 µs**；在 1 ms 一次触发下，2 通道扫描
≈ 11.3 µs → 占空比约 1.1 %，预期不会溢出。

## DMA 配置

```c
hdma_adc1.Instance            = DMA1_Channel1;
hdma_adc1.Init.Direction      = DMA_PERIPH_TO_MEMORY;   /* ADC1_DR -> RAM   */
hdma_adc1.Init.PeriphInc      = DMA_PINC_DISABLE;
hdma_adc1.Init.MemInc         = DMA_MINC_ENABLE;
hdma_adc1.Init.PeriphDataAlignment = DMA_PDATAALIGN_HALFWORD;
hdma_adc1.Init.MemDataAlignment    = DMA_MDATAALIGN_HALFWORD;
hdma_adc1.Init.Mode           = DMA_CIRCULAR;
hdma_adc1.Init.Priority       = DMA_PRIORITY_LOW;
__HAL_LINKDMA(adcHandle, DMA_Handle, hdma_adc1);
```

向量：`DMA1_Channel1_IRQHandler() → HAL_DMA_IRQHandler(&hdma_adc1)`，NVIC (0,0)，
优先级分组 4。链接建立在 `HAL_ADC_MspInit` 里，所以从上电起这个链接就存在，但由于从未启动过
任何传输，这个中断处于休眠状态。

## TIM3 触发配置

```c
Prescaler = 71;  Period = 999;  CounterMode = UP;  ClockDivision = DIV1;
AutoReloadPreload = DISABLE;  ClockSource = INTERNAL;
MasterOutputTrigger = TIM_TRGO_UPDATE;  MasterSlaveMode = DISABLE;
```

`72 000 000 / (71+1) / (999+1) = 1000 Hz`。

**采集链路是硬件触发的，不是软件轮询的：**

```
TIM3 counter overflow (update event)
   → TIM3 TRGO  (ADC1 Init.ExternalTrigConv = ADC_EXTERNALTRIGCONV_T3_TRGO)
      → ADC1 starts its 2-conversion regular scan
         → each converted sample → DR → DMA1_Channel1 (circular) → RAM
```

TIM3 ISR **并不**参与其中，而且一个都没使能 —— 这是设计如此。从定时器回调里
调用 `HAL_ADC_Start()` 会构成对本基线的一次**偏离**。

## RTC 配置

```c
hrtc.Instance = RTC;  Init.AsynchPrediv = RTC_AUTO_1_SECOND;
Init.OutPut = RTC_OUTPUTSOURCE_ALARM;
/* HAL_RTC_MspInit: */ HAL_PWR_EnableBkUpAccess(); __HAL_RCC_BKP_CLK_ENABLE(); __HAL_RCC_RTC_ENABLE();
```

时钟源为 LSE，经由 `RCC_PERIPHCLK_RTC` / `RCC_RTCCLKSOURCE_LSE`。是 F1 RTC
（不是 CubeMX 里暴露出来的那套 LSE 驱动的 BKP 预分频设置）。没有 `HAL_RTC_SetTime/GetTime`，没有闹钟，没有 SSRTC。

## I2C1 配置

```c
Instance = I2C1;  ClockSpeed = 400000;  DutyCycle = I2C_DUTYCYCLE_2;
OwnAddress1 = 0;  AddressingMode = I2C_ADDRESSINGMODE_7BIT;
DualAddressMode = DISABLE;  GeneralCallMode = DISABLE;  NoStretchMode = DISABLE;
```

PB6/PB7 为 AF 开漏、高速。没有中断，没有 DMA，没有总线恢复 / 超时处理。
**整棵树里既没有 OLED 驱动，也没有目标从机地址。**

## USART1 配置

```c
Instance = USART1;  BaudRate = 230400;  WordLength = UART_WORDLENGTH_8B;
StopBits = UART_STOPBITS_1;  Parity = UART_PARITY_NONE;
Mode = UART_MODE_TX_RX;  HwFlowCtl = UART_HWCONTROL_NONE;  OverSampling = UART_OVERSAMPLING_16;
```

在 72 MHz PCLK2 下算出的分频值得到 ≈230769 bit/s → 波特率误差 **+0.16 %**。
没有中断，没有 DMA，没有 `HAL_UART_Transmit/Receive` 调用。**没有定义任何数据包协议。**

## 按键配置

`gpio.c`：

```c
GPIO_InitStruct.Pin   = GPIO_PIN_12 | GPIO_PIN_13 | GPIO_PIN_14;
GPIO_InitStruct.Mode  = GPIO_MODE_INPUT;
GPIO_InitStruct.Pull  = GPIO_PULLUP;
HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);
```

任何地方都没有 `HAL_GPIO_ReadPin()`。没有 EXTI，所以按当前配置，按键无法把 MCU 从
stop/standby 唤醒。

## 已知警告

编译器一条都没产生。以下是本基线原样带下去的工程层面的提醒：

1. **`Error_Handler()` 是致命且无声的。** `__disable_irq(); while(1);`。任何
   `MX_*_Init()` 失败，或 `SystemClock_Config()` 启动 HSE **或 LSE** 失败，都会触发它。
   如果板上没有贴 32.768 kHz 晶振，固件会在任何输出之前挂死，看起来像死机。
   这是首次上电调试最可能遇到的失败，值得在进入阶段 1 之前就把它定下来。
2. **`assert_param` / `USE_FULL_ASSERT` 是关闭的**（`ProjectManager.HalAssertFull=false`），所以
   这次构建抓不到错误的 HAL 参数。
3. **`-O3` 配上 `Code=6804`** —— 干净的基线本来就很短小；优化级别并不是刻意选出来的，它只是
   恰好被设成了 CubeMX/uVision 的默认值。
4. **没有看门狗（IWDG/WWDG）**被配置。与第 (1) 条叠加起来，一块挂死的板子会一直挂死。
5. **只配了 4 个 GPIO 输入，没有任何输出**。上电调试期间没有任何 LED / UART 忙指示来表明还活着。
6. I2C1/USART1/TIM3/RTC 的 **NVIC 向量缺失**，日后会限制驱动风格
   （轮询还是中断）。要补上它们需要一次 CubeMX 修订，而修订会重新生成代码。
7. **重新生成的风险。** `ProjectManager.DeletePrevious=true` + `KeepUserCode=true` 意味着
   以后一次 CubeMX 重新生成会保留 `USER CODE` 区域，但会重写其他所有东西，并且
   会重写 `.uvprojx`/`.uvoptx`。任何阶段 1 的代码都必须待在 `USER CODE` 区域里面，或者放在之后
   加入该 target 的新文件里。
8. **Keil/CubeMX 版本漂移**：`.ioc` 的目标是 `MDK-ARM V5.32`，而验证是在 `5.43` 上做的。能
   干净编译；不把它当成问题。
9. **`Drivers/` 里带了约 65 MB** 复制过来的 CMSIS/HAL 内容，其中只有约 19 个 HAL 源文件和
   CMSIS 核心头文件参与编译。阶段 0 有意原封未动。
10. **路径里含空格**（`Human Heart and Body Temperature Monitor`），以后会把简陋的 CI 脚本和
    没加引号的 shell 命令弄挂。

## 已知假设

| # | 假设 | 依据 | 假设错掉的风险 |
| --- | --- | --- | --- |
| 1 | HSE 晶振是 8 MHz | `RCC.VCOOutput2Freq_Value=8000000`, `HSE_VALUE=8000000U`；`.ioc` 中没有 `RCC.HSE_VALUE` 覆盖项 | 每一个时钟频率都会跟着缩放；1 kHz 就变成错的了 |
| 2 | LSE 32.768 kHz 晶振已贴上并且能起振 | `LSE-External-Oscillator` + `RCC_LSE_ON` | 启动时卡在 `Error_Handler()` 里 |
| 3 | 按键是低电平有效、接到 GND | 输入上的内部 `GPIO_PULLUP` | 逻辑反转 / 一直读到按下 |
| 4 | ECG 前端输出为 0–3.3 V、单端、偏置在中轨 | 任务书；PA0 是 `GPIO_MODE_ANALOG` | ADC 削顶，信号地参考错误 |
| 5 | PA0 是一个**经过调理的**模拟输出，绝不是电极 | 任务书 + 安全性说明 | 患者安全 / 输入损坏 |
| 6 | 温度传感器仍留在 PA1/ADC1_IN1 上做模拟 | 仅凭当前基线；由另一位组员负责 | PA1 被重新分配，需要一次 `.ioc` 修订 |
| 7 | OLED 会挂在 I2C1 上跑 400 kHz | `I2C1` 已使能并路由到 PB6/PB7 | 总线或速率不对；模块可能只支持 100 kHz |
| 8 | OLED 控制器是 SSD1306 **或** SH1106，地址未知 | 无法由源代码证明 | 驱动重写 |
| 9 | PB6/PB7 上存在外部上拉电阻 | MCU 配置里没有这一项 | I2C 总线永远起不来 |
| 10 | 采样交错顺序是 ECG,TEMP,ECG,TEMP… | `adc.c` 里的 rank 顺序 | 下游所有地方的解交错索引都是错的 |
| 11 | DMA 缓冲区长度会是偶数个半字 | 要让对齐在循环回卷处保持住，这是必需的前提 | 每次回卷产生一个采样的通道错位 |
| 12 | 不假定任何 `board`（`board=custom`） | `.ioc` | 不得套用 ST 探索板的引脚事实 |

## 需要审查者核验的条目

**硅片 / 配置正确性**
1. 在 72 MHz 下重新算一遍 TIM3 → TRGO → ADC 并确认是 1 kHz，并确认 TIM3 确实收到了
   72 MHz（APB1 预分频 ≠ 1 → 定时器时钟 ×2 规则）。
2. 确认 ADCCLK 12 MHz ≤ F1 上限 14 MHz，并确认 55.5 周期采样对*两个*前端的
   源阻抗都足够（受约于 F1 ADC 的驱动点规格）。
3. 查清在 F1 上，非连续 ADC + 外部触发 + 循环 DMA 是否每个 TRGO 恰好产生
   2 个半字，以及一次扫描还在飞行途中时又来了一个 TRGO 会发生什么。
4. 核实 `HAL_ADC_Init()` 与 `__HAL_RCC_ADC_CONFIG()` 针对 12 MHz 预分频的先后顺序 ——
   该预分频是在 `SystemClock_Config()` 中通过 `HAL_RCCEx_PeriphCLKConfig()` 在 ADC
   init 之前设好的；确认 F1 不需要重新排序。
5. 确认没有为未使用的外设开启任何时钟，也没有哪个使用中的外设漏开时钟。

**启动 / 健壮性**
6. 定下 `Error_Handler()` 的策略 —— 对一个课程项目来说，一个无声的 `while(1)` 能接受吗？
7. 确认未贴 LSE 时的行为，以及 RTC 是否应当在启动时做成可选的。
8. 就是否应把 IWDG 纳入阶段 1 基线给出建议。

**资源分配**
9. 把 `DMA1_Channel1` 专门留给 ADC1 是否可以接受？在 F1 上通道 1 与
   SPI1/USART3/TIM1/TIM2 的请求源共用 —— 确认以后没有任何功能需要它。
10. 确认若传感器改走数字式，PA1 仍然空着给温度用；以及在 PA0/PA1 已经是仅剩两个
    还没被占用的 ADC 输入的前提下，PA1 有什么替代方案。
11. PB12/13/14 是没有 EXTI 的输入 —— 对计划中的菜单来说够吗？还是应当把其中一个做成
    EXTI 用于唤醒？
12. 确认 PA9/PA10 不需要被别的东西占用（USART1 同时也是天然的日志/printf 端口 —— 见风险 8）。

**文档准确性**
13. 把 [CUBEMX_CONFIG.zh-CN.md](CUBEMX_CONFIG.zh-CN.md) 与一次全新的 CubeMX 6.17.0 打开该 `.ioc` 所得的视图
    做交叉核对（审查者手头的 CubeMX 版本可能不同；若是，请注明）。
14. 确认"每个 `USER CODE` 区域都是空的"这一说法 —— 通过检查 `Core/` 来核验。
15. 确认 `Core/` 与 `Drivers/` 之下没有任何内容与 CubeMX 6.17.0 + FW_F1 1.8.7 针对本 `.ioc` 的
    原样输出存在差异。本仓库**没有先前的 git 历史**（是阶段 0 创建了它），
    所以这一点无法在仓库内部得到证明；它需要一次在仓库之外重新生成的 diff。

**明确不在本次审查范围之内**
* UI 设计、算法选型、PC 上位机软件、协议设计 —— 这些目前一物尚无。

---

阶段 0 没有实现任何应用功能。
