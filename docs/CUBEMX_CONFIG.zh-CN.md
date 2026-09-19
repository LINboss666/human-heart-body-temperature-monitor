# CubeMX 配置 — 阶段 0 基线

[English](CUBEMX_CONFIG.md) · **中文**

由 **STM32CubeMX 6.17.0**（`MxDb.Version=DB.6.0.170`）为 `STM32F103C8Tx` 生成，
封装 **LQFP48**，系列 **STM32F103**，`board=custom`
（不假设使用任何 ST 探索板）。

固件包：**STM32Cube FW_F1 V1.8.7**（`ProjectManager.FirmwarePackage`）。

本文件中的所有内容都从 `.ioc` 誊录而来，并与生成的 C 代码交叉核对过。
此处没有任何内容在应用代码中实现。

---

## 1. 时钟树

| 节点 | 设置 | `.ioc` 中的来源 | 在代码中已核验 |
| --- | --- | --- | --- |
| HSE | 8 MHz，外部晶振，prediv /1 | `RCC.VCOOutput2Freq_Value=8000000` | `main.c`: `RCC_HSE_ON`、`RCC_HSE_PREDIV_DIV1`；`stm32f1xx_hal_conf.h`: `HSE_VALUE 8000000U` |
| PLL 源 | HSE | `RCC.PLLSourceVirtual=RCC_PLLSOURCE_HSE` | `main.c`: `RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSE` |
| PLL 倍频 | ×9 | `RCC.PLLMUL=RCC_PLL_MUL9` | `main.c`: `RCC_PLL_MUL9` |
| SYSCLK | 来自 PLL 的 **72 MHz** | `RCC.SYSCLKFreq_VALUE=72000000`、`RCC.SYSCLKSource=RCC_SYSCLKSOURCE_PLLCLK` | `main.c`: `RCC_SYSCLKSOURCE_PLLCLK` |
| HCLK / FCLK | 72 MHz（AHB /1） | `RCC.HCLKFreq_Value=72000000`、`RCC.AHBFreq_Value=72000000` | `main.c`: `RCC_SYSCLK_DIV1` |
| APB1 (PCLK1) | **36 MHz**（HCLK /2） | `RCC.APB1CLKDivider=RCC_HCLK_DIV2`、`RCC.APB1Freq_Value=36000000` | `main.c`: `RCC_HCLK_DIV2` |
| APB1 定时器时钟 | **72 MHz**（因为 APB1 分频器 ≠ 1，故 ×2） | `RCC.APB1TimFreq_Value=72000000` | 由 RCC 配置隐含推出 |
| APB2 (PCLK2) | **72 MHz**（HCLK /1） | `RCC.APB2Freq_Value=72000000` | `main.c`: `RCC_HCLK_DIV1` |
| ADC 时钟 | **12 MHz** = PCLK2 / 6 | `RCC.ADCPresc=RCC_ADCPCLK2_DIV6`、`RCC.ADCFreqValue=12000000` | `main.c`: `RCC_ADCPCLK2_DIV6` |
| Flash 等待周期 | 2 个等待周期 | — | `main.c`: `HAL_RCC_ClockConfig(..., FLASH_LATENCY_2)` |
| LSE | 32.768 kHz 外部晶振 | `PC14/PC15 Mode=LSE-External-Oscillator` | `main.c`: `RCC_LSE_ON`；`stm32f1xx_hal_conf.h`: `LSE_VALUE 32768U`、`LSE_STARTUP_TIMEOUT 5000U` |
| RTC 时钟源 | LSE | `RCC.RTCClockSelection=RCC_RTCCLKSOURCE_LSE`、`RCC.RTCFreq_Value=32768` | `main.c`: `RCC_RTCCLKSOURCE_LSE` |
| SysTick | 由 HCLK 72 MHz 驱动 → 1 ms 节拍 | `VP_SYS_VS_Systick.Mode=SysTick` | `stm32f1xx_it.c`: `SysTick_Handler` → `HAL_IncTick()` |

因此 `HAL_Init()` 只有在 `SystemClock_Config()` 运行之后才能得到正确的 1 ms 节拍 ——
这正是 `main.c` 中的 CubeMX 顺序。ADC 时钟 12 MHz 处于 F1 ADC 的 14 MHz 上限之内。

---

## 2. ADC1

```c
hadc1.Instance               = ADC1;
hadc1.Init.ScanConvMode      = ADC_SCAN_ENABLE;
hadc1.Init.ContinuousConvMode= DISABLE;
hadc1.Init.DiscontinuousConvMode = DISABLE;
hadc1.Init.ExternalTrigConv  = ADC_EXTERNALTRIGCONV_T3_TRGO;
hadc1.Init.DataAlign         = ADC_DATAALIGN_RIGHT;
hadc1.Init.NbrOfConversion   = 2;
```

常规序列：

| 序位 | 通道 | 引脚 | 采样时间 |
| --- | --- | --- | --- |
| 1 | `ADC_CHANNEL_0` | PA0（ECG 调理后的模拟信号） | `ADC_SAMPLETIME_55CYCLES_5` |
| 2 | `ADC_CHANNEL_1` | PA1（温度模拟信号） | `ADC_SAMPLETIME_55CYCLES_5` |

GPIO 侧（`HAL_ADC_MspInit`）：PA0 + PA1 设置为 `GPIO_MODE_ANALOG`；ADC1 时钟通过
`__HAL_RCC_ADC1_CLK_ENABLE()` 使能。

**时序余量检查。** 一次转换 = (55.5 + 12.5) 个 ADC 周期 = 68 个周期。
在 12 MHz 下即每个通道 5.67 µs，2 通道扫描共 11.3 µs —— 约为 1000 µs 触发周期的
**1.1 %**。该序列总是在下一次 TIM3 TRGO 之前很久就完成，因此预计 1 kHz 下不会出现溢出。

未配置（CubeMX 默认值，在阶段 0 保持不动是正确的）：无看门狗，无注入/规则组溢出处理，
无 ADC 转换结束中断，DMA 传输次数未知（未声明缓冲区）。

---

## 3. TIM3 — 1 kHz 采样触发

```c
htim3.Instance                 = TIM3;
htim3.Init.Prescaler           = 71;
htim3.Init.Period              = 999;
htim3.Init.CounterMode         = TIM_COUNTERMODE_UP;
htim3.Init.ClockDivision       = TIM_CLOCKDIVISION_DIV1;
htim3.Init.AutoReloadPreload   = TIM_AUTORELOAD_PRELOAD_DISABLE;
sClockSourceConfig.ClockSource = TIM_CLOCKSOURCE_INTERNAL;
sMasterConfig.MasterOutputTrigger = TIM_TRGO_UPDATE;
sMasterConfig.MasterSlaveMode  = TIM_MASTERSLAVEMODE_DISABLE;
```

TIM3 挂在 APB1 上，其定时器时钟为 72 MHz（见 §1）：

```
f_update = 72 000 000 / (PSC + 1) / (ARR + 1)
         = 72 000 000 / (71 + 1) / (999 + 1)
         = 72 000 000 / 72 / 1000
         = 1 000 Hz          → period 1 ms
```

`HAL_TIM_Base_MspInit` 只调用 `__HAL_RCC_TIM3_CLK_ENABLE()`。**没有使能任何 TIM3 的 NVIC
条目，并且 `Core/Src/stm32f1xx_it.c` 中也不存在 `TIM3_IRQHandler`。** 这是有意为之：
常规采集不得依赖定时器 ISR。

`AutoReloadPreload` 为 **DISABLE**。在 PSC/ARR 固定为 71/999 时无害，但如果后续版本在运行时
改变采样率，对 ARR 的写入将在周期中途生效，而不是在下一次更新事件生效。已标注给评审者，
故意未做修改。

---

## 4. DMA1 通道 1（ADC1）

| 参数 | 值 | `.ioc` 键 |
| --- | --- | --- |
| 请求 | ADC1 | `Dma.Request0=ADC1`、`Dma.RequestsNb=1` |
| 实例 | `DMA1_Channel1` | `Dma.ADC1.0.Instance` |
| 方向 | 外设 → 存储器 | `DMA_PERIPH_TO_MEMORY` |
| 模式 | **循环** | `DMA_CIRCULAR` |
| 外设地址递增 | 关闭 | `DMA_PINC_DISABLE` |
| 存储器地址递增 | 开启 | `DMA_MINC_ENABLE` |
| 外设宽度 | 半字（16-bit） | `DMA_PDATAALIGN_HALFWORD` |
| 存储器宽度 | 半字（16-bit） | `DMA_MDATAALIGN_HALFWORD` |
| 优先级 | 低 | `DMA_PRIORITY_LOW` |

在 `HAL_ADC_MspInit()` 中通过 `__HAL_LINKDMA(adcHandle, DMA_Handle, hdma_adc1)`
绑定到 ADC。控制器的时钟与中断来自 `MX_DMA_Init()`：

```c
__HAL_RCC_DMA1_CLK_ENABLE();
HAL_NVIC_SetPriority(DMA1_Channel1_IRQn, 0, 0);
HAL_NVIC_EnableIRQ(DMA1_Channel1_IRQn);
```

`stm32f1xx_it.c` 提供了该向量：

```c
void DMA1_Channel1_IRQHandler(void) { HAL_DMA_IRQHandler(&hdma_adc1); }
```

**预期的未来存储器布局** —— 因为序位 1 是 ADC 通道 0、序位 2 是通道 1，
每次触发会追加两个半字，因此缓冲区按交织方式读取：

```
[ECG0][TEMP0][ECG1][TEMP1][ECG2][TEMP2] ...
 ^ even indices = PA0 ECG   ^ odd indices = PA1 temperature
```

未声明任何缓冲区，从未调用 `HAL_ADC_Start_DMA()`，因此在当前基线中 **不会有 DMA
中断触发**。该向量存在但处于休眠状态。

---

## 5. I2C1 — 计划中的 OLED 接口

```c
hi2c1.Instance         = I2C1;
hi2c1.Init.ClockSpeed  = 400000;                  /* 400 kHz Fast Mode */
hi2c1.Init.DutyCycle   = I2C_DUTYCYCLE_2;
hi2c1.Init.OwnAddress1 = 0;                       /* master-only, unused */
hi2c1.Init.AddressingMode = I2C_ADDRESSINGMODE_7BIT;
hi2c1.Init.DualAddressMode = I2C_DUALADDRESS_DISABLE;
hi2c1.Init.GeneralCallMode = I2C_GENERALCALL_DISABLE;
hi2c1.Init.NoStretchMode   = I2C_NOSTRETCH_DISABLE;
```

PB6 = SCL，PB7 = SDA，两者均为 `GPIO_MODE_AF_OD`，速度为 `GPIO_SPEED_FREQ_HIGH`。
`.ioc`：`I2C1.I2C_Mode=I2C_Fast`。

**I2C1 没有 NVIC 条目** —— 事件/错误中断未使能，因此未来的 OLED 驱动
必须使用阻塞式 `HAL_I2C_Master_Transmit()`/`HAL_I2C_Mem_Write()` 调用，或者由后续
CubeMX 版本使能 I2C1 中断。阶段 0 不添加任何驱动。

显示屏计划为 0.96 英寸单色 OLED。其控制器（**SSD1306 或 SH1106**）
及其 7 位地址**未**冻结 —— 本仓库中没有任何东西能证明其中任一种。

---

## 6. USART1 — 计划中的 PC 链路

```c
huart1.Instance       = USART1;
huart1.Init.BaudRate  = 230400;
huart1.Init.WordLength= UART_WORDLENGTH_8B;      /* 8 data bits */
huart1.Init.StopBits  = UART_STOPBITS_1;         /* 1 stop bit  */
huart1.Init.Parity    = UART_PARITY_NONE;        /* no parity   */
huart1.Init.Mode      = UART_MODE_TX_RX;
huart1.Init.HwFlowCtl = UART_HWCONTROL_NONE;     /* flow control disabled */
huart1.Init.OverSampling = UART_OVERSAMPLING_16;
```

PA9 = TX（`GPIO_MODE_AF_PP`，高速），PA10 = RX（`GPIO_MODE_INPUT`、`GPIO_NOPULL`）。
`.ioc`：`USART1.BaudRate=230400`、`USART1.VirtualMode=VM_ASYNC`。

**USART1 没有 NVIC 条目，也没有 TX/RX DMA 流** —— 除非后续版本改变它，否则该链路仅轮询。
阶段 0 未定义任何帧协议。

给评审者的波特率误差说明：在 `OVERSAMPLING_16` 下由 72 MHz APB2 时钟得到 230400
给出 `USARTDIV = 72e6/(16·230400) = 19.53125`，舍入为 19 + 8/16 → 230769 bit/s，
即 **+0.16 %** 的偏差。远在容差范围之内。

---

## 7. RTC

通过虚拟引脚 `VP_RTC_VS_RTC_Activate.Mode=RTC_Enabled` 使能。

```c
hrtc.Instance           = RTC;
hrtc.Init.AsynchPrediv  = RTC_AUTO_1_SECOND;
hrtc.Init.OutPut        = RTC_OUTPUTSOURCE_ALARM;
```

`HAL_RTC_MspInit()` 执行备份域解锁：

```c
HAL_PWR_EnableBkUpAccess();
__HAL_RCC_BKP_CLK_ENABLE();
__HAL_RCC_RTC_ENABLE();
```

时钟源为 LSE（见 §1）。计划的未来用途是保存、显示并设置日期/时间，并在主电源丢失时依靠
VBAT 继续运行。**不存在任何 `HAL_RTC_*` 应用调用**，也从未设置过日历时间。
`RTC_AUTO_1_SECOND` 让 HAL 自行选择 32768/1 的秒分频；
这是 F1 CubeMX 的默认值。

未使能任何 RTC 中断/Alarm 的 NVIC 条目。

---

## 8. SYS / 调试

`.ioc`：`PA13.Mode=Serial_Wire`、`PA14.Mode=Serial_Wire`，
`Mcu.IP` 列表包含 `SYS`，且 `VP_SYS_VS_Systick.Mode=SysTick`。
SWD 已开启，因此 PA13/PA14 保留给调试器，不能用作 GPIO。
Keil 工程选择 ST-Link 目标 DLL
（依据构建日志为 `SARMCM3.DLL` / `ST-LINKIII-KEIL_SWO.dll V3.3.1.0`）。

## 9. NVIC 汇总

| 向量 | 是否使能 | 抢占 / 子优先级 | 证据 |
| --- | --- | --- | --- |
| `DMA1_Channel1_IRQn` | **是** | 0 / 0 | `dma.c`、`stm32f1xx_it.c` |
| `SysTick_IRQn` | 是（HAL tick） | 15 / 0 | `.ioc` `NVIC.SysTick_IRQn=true\:15\:0...` |
| TIM3 更新 | **否** | — | `.ioc` 中没有 `TIM3_IRQn` 键，`stm32f1xx_it.c` 中没有处理函数 |
| ADC1 / EOC | **否** | — | 未请求 |
| USART1 global | **否** | — | 未请求 |
| I2C1 event/error | **否** | — | 未请求 |
| RTC alarm | **否** | — | 未请求 |
| 故障处理函数（Hard/Bus/Usage/MemManage/NMI/DebugMon） | 存在，全部为 `while(1)` | — | `stm32f1xx_it.c` |

`NVIC.PriorityGroup = NVIC_PRIORITYGROUP_4`（4 个抢占位，0 个子优先级位）。
`NVIC.ForceEnableDMAVector=true` 解释了为什么在不存在任何 DMA
应用代码的情况下 DMA 向量仍然是开启的。

## 10. `main()` 中的外设初始化顺序

`.ioc` 键 `ProjectManager.functionlistsort` 与 `Core/Src/main.c` 一致：

```
SystemClock_Config → MX_GPIO_Init → MX_DMA_Init → MX_ADC1_Init
→ MX_RTC_Init → MX_TIM3_Init → MX_I2C1_Init → MX_USART1_UART_Init
```

`MX_DMA_Init()` 排在 `MX_ADC1_Init()` 之前这一点很重要：`HAL_ADC_MspInit` 初始化 DMA
流和 `__HAL_LINKDMA`，而 NVIC 设置位于 `MX_DMA_Init` 中。

## 11. CubeMX 给出的 RAM/ROM 预算

| 条目 | 值 |
| --- | --- |
| 堆（`ProjectManager.HeapSize`） | `0x200` = 512 B |
| 栈（`ProjectManager.StackSize`） | `0x400` = 1 KB |
| 链接器实测的生成 ZI | 2000 B（包含以上两者） |

## 12. 值得了解的 `.ioc` 字段

| 字段 | 值 | 说明 |
| --- | --- | --- |
| `ProjectManager.TargetToolchain` | `MDK-ARM V5.32` | 生成器被要求输出一个 MDK 工程；本地 IDE 在基线构建中使用的是 5.43（见 [BUILD.zh-CN.md](BUILD.zh-CN.md)） |
| `ProjectManager.CompilerLinker` | `GCC` | CubeMX 记账用的默认值，**无影响** —— 实际的链接器是 ARMCC/ARMLink |
| `ProjectManager.KeepUserCode` | `true` | 重新生成时会保留 `USER CODE` 区域 |
| `ProjectManager.CoupleFile` | `true` | `.c`/`.h` 成对放在一起 |
| `ProjectManager.LibraryCopy` | `0` | HAL/CMSIS 被复制到 `Drivers/` 中而不是被引用 |
| `ProjectManager.MainLocation` | `Core/Src` | |
| `ProjectManager.AskForMigrate` | `true` | |
| `ProjectManager.DeletePrevious` | `true` | 重新生成时 CubeMX 会删除它自己上一次的输出 |
| `ProjectManager.FreePins` | `false` | |
| `GPIO.groupedBy` | `Group By Peripherals` | |
