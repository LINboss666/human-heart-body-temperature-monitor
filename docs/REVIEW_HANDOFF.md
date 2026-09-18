# REVIEW HANDOFF — Phase 0

Self-contained handoff for an independent reviewer. Everything here was read out of
`Human Heart and Body Temperature Monitor.ioc` and the generated C under `Core/Src/`;
nothing was inferred from the project brief alone. Where the brief and the tree disagreed,
it is called out.

---

## Provenance

| Field | Value |
| --- | --- |
| Repository | `PENDING` (filled in the commit that records the pushed URL) |
| Visibility | PUBLIC |
| Branch | `main` |
| Baseline commit | `PENDING` |
| Tag | `v0.1-baseline` (annotated) |
| MCU | STM32F103C8T6, LQFP48, Cortex-M3, 64 KB Flash / 20 KB SRAM |
| STM32CubeMX version | **6.17.0** (`MxCube.Version`), DB `DB.6.0.170`, `.ioc` format v6 |
| STM32CubeF1 firmware | **STM32Cube FW_F1 V1.8.7** (`ProjectManager.FirmwarePackage`) |
| Keil target | `Human Heart and Body Temperature Monitor` (single target), device `STM32F103C8`, pack `Keil.STM32F1xx_DFP.2.2.0` |
| Compiler | ARMCC V5.06 update 5 (build 528), MDK-ARM Plus 5.43.0.0, AC5, `-O3`, C99 |
| Build status | **`0 Error(s), 0 Warning(s)`** on a from-scratch `UV4 -j0 -r` rebuild; `Code=6804 RO-data=328 RW-data=16 ZI-data=2000` |

See [BUILD.md](BUILD.md) for why the build status is re-verified rather than inherited.

---

## Peripheral summary

| IP | Instance | Enabled | Interrupts | Notes |
| --- | --- | --- | --- | --- |
| ADC | ADC1 | yes | none (EOC/JEOS not enabled) | scan, 2 regular conversions, TIM3 TRGO triggered |
| DMA | DMA1_Channel1 | yes | `DMA1_Channel1_IRQn` @ (0,0) | ADC1 → RAM, circular, half-word |
| TIM | TIM3 | yes | **none** | internal clock, master TRGO = update |
| I2C | I2C1 | yes | **none** | 400 kHz master, 7-bit |
| USART | USART1 | yes | **none** | 230400 8N1, no flow control |
| RTC | RTC | yes | **none** | LSE source, backup access enabled in MspInit |
| GPIO | PB12/13/14 | yes | none | inputs with pull-up |
| SYS | SWD + SysTick | yes | SysTick @ (15,0) | Serial Wire debug |
| RCC | HSE, LSE, PLL | yes | — | 8 MHz → 72 MHz; 32.768 kHz |

`.ioc` `Mcu.IP0..IP8` = `ADC1, DMA, I2C1, NVIC, RCC, RTC, SYS, TIM3, USART1`.

## Complete pin map

| Pin | Signal | Function | Electrical config |
| --- | --- | --- | --- |
| PA0 (`PA0-WKUP`) | `ADC1_IN0` | ECG conditioned analog input, ADC rank 1 | `GPIO_MODE_ANALOG` |
| PA1 | `ADC1_IN1` | Temperature analog input, ADC rank 2 | `GPIO_MODE_ANALOG` |
| PA9 | `USART1_TX` | PC link TX | `GPIO_MODE_AF_PP`, high speed |
| PA10 | `USART1_RX` | PC link RX | `GPIO_MODE_INPUT`, `GPIO_NOPULL` |
| PA13 | `SYS_JTMS-SWDIO` | SWDIO | Serial Wire mode, reserved |
| PA14 | `SYS_JTCK-SWCLK` | SWCLK | Serial Wire mode, reserved |
| PB6 | `I2C1_SCL` | OLED SCL | `GPIO_MODE_AF_OD`, high speed |
| PB7 | `I2C1_SDA` | OLED SDA | `GPIO_MODE_AF_OD`, high speed |
| PB12 | GPIO input | KEY_UP (brief) | `GPIO_MODE_INPUT` + `GPIO_PULLUP` |
| PB13 | GPIO input | KEY_DOWN (brief) | `GPIO_MODE_INPUT` + `GPIO_PULLUP` |
| PB14 | GPIO input | KEY_OK / ECG start (brief) | `GPIO_MODE_INPUT` + `GPIO_PULLUP` |
| PC14 | `RCC_OSC32_IN` | LSE in | `LSE-External-Oscillator` |
| PC15 | `RCC_OSC32_OUT` | LSE out | `LSE-External-Oscillator` |
| PD0 | `RCC_OSC_IN` | HSE in | `HSE-External-Oscillator` |
| PD1 | `RCC_OSC_OUT` | HSE out | `HSE-External-Oscillator` |

GPIO port clocks enabled: GPIOA, GPIOB, GPIOC, GPIOD.

`KEY_UP` / `KEY_DOWN` / `KEY_OK` are **brief-derived names**. The `.ioc` defines **no
User Labels**, so generated code uses `GPIO_PIN_12/13/14` only. See [PINMAP.md](PINMAP.md).

## Clock configuration

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

`HSE_VALUE = 8000000U`, `LSE_VALUE = 32768U`, `LSE_STARTUP_TIMEOUT = 5000U`,
`HSE_STARTUP_TIMEOUT = 100U` in `Core/Inc/stm32f1xx_hal_conf.h`.
`TimSysFreq_Value = 72000000`, `ADCFreqValue = 12000000`. USB clock configured at 72 MHz
in the `.ioc` but the USB peripheral is not enabled.

## ADC configuration

```c
Instance = ADC1;  ScanConvMode = ADC_SCAN_ENABLE;  ContinuousConvMode = DISABLE;
DiscontinuousConvMode = DISABLE;  ExternalTrigConv = ADC_EXTERNALTRIGCONV_T3_TRGO;
DataAlign = ADC_DATAALIGN_RIGHT;  NbrOfConversion = 2;
Rank 1 → ADC_CHANNEL_0 (PA0), ADC_SAMPLETIME_55CYCLES_5
Rank 2 → ADC_CHANNEL_1 (PA1), ADC_SAMPLETIME_55CYCLES_5
```

No calibration call, no watchdog, no injected conversions, no overrun handling.
`HAL_ADC_Start()` / `HAL_ADC_Start_DMA()` appear **nowhere** in the tree.

Conversion time per channel = (55.5 + 12.5) / 12 MHz ≈ **5.67 µs**; 2-channel scan ≈
11.3 µs per 1 ms trigger → ~1.1 % duty, no expected overrun.

## DMA configuration

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

Vector: `DMA1_Channel1_IRQHandler() → HAL_DMA_IRQHandler(&hdma_adc1)`, NVIC (0,0),
priority group 4. Linked in `HAL_ADC_MspInit`, so the link exists from boot, but with no
transfer ever started the interrupt is dormant.

## TIM3 trigger configuration

```c
Prescaler = 71;  Period = 999;  CounterMode = UP;  ClockDivision = DIV1;
AutoReloadPreload = DISABLE;  ClockSource = INTERNAL;
MasterOutputTrigger = TIM_TRGO_UPDATE;  MasterSlaveMode = DISABLE;
```

`72 000 000 / (71+1) / (999+1) = 1000 Hz`.

**The acquisition chain is hardware-triggered, not software-polled:**

```
TIM3 counter overflow (update event)
   → TIM3 TRGO  (ADC1 Init.ExternalTrigConv = ADC_EXTERNALTRIGCONV_T3_TRGO)
      → ADC1 starts its 2-conversion regular scan
         → each converted sample → DR → DMA1_Channel1 (circular) → RAM
```

A TIM3 ISR is **not** involved, and none is enabled — by design. Calling
`HAL_ADC_Start()` from a timer callback would be a **deviation** from this baseline.

## RTC configuration

```c
hrtc.Instance = RTC;  Init.AsynchPrediv = RTC_AUTO_1_SECOND;
Init.OutPut = RTC_OUTPUTSOURCE_ALARM;
/* HAL_RTC_MspInit: */ HAL_PWR_EnableBkUpAccess(); __HAL_RCC_BKP_CLK_ENABLE(); __HAL_RCC_RTC_ENABLE();
```

Source LSE via `RCC_PERIPHCLK_RTC` / `RCC_RTCCLKSOURCE_LSE`. F1 RTC (not LSE-driven BKP
prescaler setup exposed in CubeMX). No `HAL_RTC_SetTime/GetTime`, no alarm, no SSRTC.

## I2C1 configuration

```c
Instance = I2C1;  ClockSpeed = 400000;  DutyCycle = I2C_DUTYCYCLE_2;
OwnAddress1 = 0;  AddressingMode = I2C_ADDRESSINGMODE_7BIT;
DualAddressMode = DISABLE;  GeneralCallMode = DISABLE;  NoStretchMode = DISABLE;
```

PB6/PB7 AF open-drain high-speed. No interrupts, no DMA, no bus-recovery / timeout
handling. **No OLED driver and no target slave address anywhere in the tree.**

## USART1 configuration

```c
Instance = USART1;  BaudRate = 230400;  WordLength = UART_WORDLENGTH_8B;
StopBits = UART_STOPBITS_1;  Parity = UART_PARITY_NONE;
Mode = UART_MODE_TX_RX;  HwFlowCtl = UART_HWCONTROL_NONE;  OverSampling = UART_OVERSAMPLING_16;
```

Computed divisor at 72 MHz PCLK2 gives ≈230769 bit/s → **+0.16 %** baud error.
No interrupts, no DMA, no `HAL_UART_Transmit/Receive` call. **No packet protocol defined.**

## Button configuration

`gpio.c`:

```c
GPIO_InitStruct.Pin   = GPIO_PIN_12 | GPIO_PIN_13 | GPIO_PIN_14;
GPIO_InitStruct.Mode  = GPIO_MODE_INPUT;
GPIO_InitStruct.Pull  = GPIO_PULLUP;
HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);
```

No `HAL_GPIO_ReadPin()` anywhere. No EXTI, so buttons cannot wake the MCU from stop/standby
as configured.

## Known warnings

The compiler emitted none. These are engineering cautions the baseline carries forward:

1. **`Error_Handler()` is fatal and silent.** `__disable_irq(); while(1);`. Triggered by any
   `MX_*_Init()` failure or by `SystemClock_Config()` failing to start HSE **or LSE**.
   If the board has no 32.768 kHz crystal fitted, the firmware hangs before any output and
   looks dead. The most likely first bring-up failure, and worth deciding about before Phase 1.
2. **`assert_param` / `USE_FULL_ASSERT` is off** (`ProjectManager.HalAssertFull=false`), so
   bad HAL arguments are not caught in this build.
3. **`-O3` with `Code=6804`** — a clean baseline is small; optimisation level was not chosen
   deliberately, it is the CubeMX/uVision default that happens to be set.
4. **No watchdog (IWDG/WWDG)** is configured. Combined with (1), a hung board stays hung.
5. **Only 4 GPIO inputs and no outputs** are configured. There is no LED/UART-busy indicator
   to show life during bring-up.
6. **Missing NVIC vectors** for I2C1/USART1/TIM3/RTC constrain driver style later
   (polling vs interrupt). Adding them requires a CubeMX revision, which regenerates code.
7. **Regeneration hazard.** `ProjectManager.DeletePrevious=true` + `KeepUserCode=true` means a
   future CubeMX re-generate preserves `USER CODE` regions but rewrites everything else, and
   rewrites `.uvprojx`/`.uvoptx`. Any Phase-1 code must live inside `USER CODE` regions or in
   new files added to the target afterwards.
8. **Keil/CubeMX version drift**: `.ioc` targets `MDK-ARM V5.32`, verified on `5.43`. Builds
   clean; not treated as an issue.
9. **`Drivers/` ships ~65 MB** of copied CMSIS/HAL content of which only ~19 HAL sources and
   the CMSIS core header are compiled. Intentionally left untouched in Phase 0.
10. **Paths contain spaces** (`Human Heart and Body Temperature Monitor`), which will break
    naive CI scripts and unquoted shell commands later.

## Known assumptions

| # | Assumption | Basis | Risk if wrong |
| --- | --- | --- | --- |
| 1 | HSE crystal is 8 MHz | `RCC.VCOOutput2Freq_Value=8000000`, `HSE_VALUE=8000000U`; no `RCC.HSE_VALUE` override in `.ioc` | every clock frequency scales; 1 kHz becomes wrong |
| 2 | LSE 32.768 kHz crystal is fitted and starts | `LSE-External-Oscillator` + `RCC_LSE_ON` | boot hangs in `Error_Handler()` |
| 3 | Buttons are active-low to GND | internal `GPIO_PULLUP` on inputs | inverted logic / always-pressed reads |
| 4 | ECG front-end output is 0–3.3 V, single-ended, biased mid-rail | brief; PA0 is `GPIO_MODE_ANALOG` | ADC clipping, wrong signal ground reference |
| 5 | PA0 is a **conditioned** analog output, never an electrode | brief + safety note | patient safety / input damage |
| 6 | Temperature sensor stays analog on PA1/ADC1_IN1 | current baseline only; owned by another member | PA1 reallocated, `.ioc` revision needed |
| 7 | OLED will sit on I2C1 at 400 kHz | `I2C1` enabled and routed to PB6/PB7 | wrong bus or speed; module may be 100 kHz-only |
| 8 | OLED controller is SSD1306 **or** SH1106, address unknown | not provable from source | driver rewrite |
| 9 | External pull-ups exist on PB6/PB7 | not in MCU config | I2C bus never works |
| 10 | Sample interleave is ECG,TEMP,ECG,TEMP… | rank order in `adc.c` | de-interleave indexing wrong everywhere downstream |
| 11 | DMA buffer length will be an even number of half-words | required to keep alignment across circular wrap | channel skew of one sample every wrap |
| 12 | No `board` assumed (`board=custom`) | `.ioc` | ST discovery-board pin facts must not be applied |

## Items requiring reviewer verification

**Silicon / config correctness**
1. Recompute TIM3 → TRGO → ADC at 72 MHz and confirm 1 kHz, and confirm TIM3 really receives
   72 MHz (APB1 prescaler ≠ 1 → ×2 timer clock rule).
2. Confirm ADCCLK 12 MHz ≤ 14 MHz F1 max, and that 55.5-cycle sampling is adequate for the
   source impedance of *both* front-ends (F1 ADC driving-point spec is the constraint).
3. Check whether non-continuous ADC + external trigger + circular DMA produces exactly
   2 half-words per TRGO on F1, and what happens if a TRGO arrives during an in-flight scan.
4. Verify `HAL_ADC_Init()` ordering vs `__HAL_RCC_ADC_CONFIG()` for the 12 MHz prescaler —
   the prescaler is set in `SystemClock_Config()` via `HAL_RCCEx_PeriphCLKConfig()` before
   ADC init; confirm no re-ordering is required for F1.
5. Confirm no clock is enabled for an unused peripheral and none is missing for a used one.

**Startup / robustness**
6. Decide the `Error_Handler()` policy — is a silent `while(1)` acceptable for a course project?
7. Confirm LSE-not-fitted behaviour and whether RTC should be made optional at boot.
8. Recommend whether an IWDG should be part of the Phase 1 baseline.

**Resource allocation**
9. Is reserving `DMA1_Channel1` exclusively for ADC1 acceptable? On F1 channel 1 is shared
   with SPI1/USART3/TIM1/TIM2 request sources — confirm no future feature needs it.
10. Confirm PA1 stays free for temperature if the sensor goes digital, and what PA1's
    alternatives are given PA0/PA1 are the only ADC inputs not already spoken for.
11. PB12/13/14 are inputs without EXTI — is that enough for the planned menu, or should one
    be EXTI for wake-up?
12. Confirm PA9/PA10 are not needed for anything else (USART1 is also the natural log/printf
    port — see risk 8).

**Documentation accuracy**
13. Cross-check [CUBEMX_CONFIG.md](CUBEMX_CONFIG.md) against a fresh CubeMX 6.17.0 view of the
    `.ioc` (the reviewer may have a different CubeMX version; note it if so).
14. Confirm the claim "every `USER CODE` region is empty" — verify by inspection of `Core/`.
15. Confirm nothing under `Core/` or `Drivers/` differs from stock CubeMX 6.17.0 + FW_F1 1.8.7
    output for this `.ioc`. This repository has **no prior git history** (Phase 0 created it),
    so this cannot be proven from within the repo; it needs an out-of-tree re-generate diff.

**Explicitly out of scope for this review**
* UI design, algorithm choice, PC software, protocol design — none exist yet.

---

NO APPLICATION FEATURES WERE IMPLEMENTED IN PHASE 0.
