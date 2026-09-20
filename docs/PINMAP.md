# PINMAP — Phase 0 Baseline

**English** · [中文](PINMAP.zh-CN.md)

Every row below was verified against **two** independent sources, not against the
project brief alone:

1. `Human Heart and Body Temperature Monitor.ioc` (CubeMX project file)
2. the generated C sources under `Core/Src/`

Verdict legend:
`MATCH` = expected and actual agree · `MISMATCH` = they differ · `GAP` = expected item
has no counterpart in the project · `N/A` = cannot be proven from source code.

## Signal pins

| Pin | Function (expected) | EXPECTED | ACTUAL (evidence) | Verdict |
| --- | --- | --- | --- | --- |
| PA0 (`PA0-WKUP`) | ECG conditioned analog input | ADC1_IN0, rank 1, 55.5 cyc | `.ioc`: `PA0-WKUP.Signal=ADCx_IN0`, `SH.ADCx_IN0.0=ADC1_IN0,IN0`; `adc.c`: `ADC_CHANNEL_0` / `ADC_REGULAR_RANK_1` / `ADC_SAMPLETIME_55CYCLES_5`; `HAL_ADC_MspInit` sets `GPIO_MODE_ANALOG` | MATCH |
| PA1 | Temperature analog input | ADC1_IN1, rank 2, 55.5 cyc | `.ioc`: `PA1.Signal=ADCx_IN1`; `adc.c`: `ADC_CHANNEL_1` / `ADC_REGULAR_RANK_2` / `ADC_SAMPLETIME_55CYCLES_5` | MATCH |
| PB6 | OLED SCL | I2C1_SCL, AF open-drain | `.ioc`: `PB6.Signal=I2C1_SCL`; `i2c.c`: `GPIO_MODE_AF_OD`, `GPIO_SPEED_FREQ_HIGH` | MATCH |
| PB7 | OLED SDA | I2C1_SDA, AF open-drain | `.ioc`: `PB7.Signal=I2C1_SDA`; `i2c.c` same MspInit block | MATCH |
| PA9 | PC link TX | USART1_TX, AF push-pull | `.ioc`: `PA9.Signal=USART1_TX`, `Mode=Asynchronous`; `usart.c`: `GPIO_MODE_AF_PP`, `GPIO_SPEED_FREQ_HIGH` | MATCH |
| PA10 | PC link RX | USART1_RX, floating input | `.ioc`: `PA10.Signal=USART1_RX`; `usart.c`: `GPIO_MODE_INPUT`, `GPIO_NOPULL` | MATCH |
| PB12 | `KEY_UP` | GPIO input, internal pull-up, active-low to GND | `.ioc`: `PB12.Signal=GPIO_Input`, `PB12.GPIO_PuPd=GPIO_PULLUP`, `Locked=true`; `gpio.c`: `GPIO_MODE_INPUT` + `GPIO_PULLUP` | MATCH |
| PB13 | `KEY_DOWN` | same as PB12 | `.ioc`: `PB13.GPIO_PuPd=GPIO_PULLUP`; `gpio.c` line grouped with PB12/PB14 | MATCH |
| PB14 | `KEY_OK` / ECG Start | same as PB12 | `.ioc`: `PB14.GPIO_PuPd=GPIO_PULLUP`; `gpio.c` same group | MATCH |
| PA13 | SWDIO | SYS_JTMS-SWDIO, Serial Wire | `.ioc`: `PA13.Signal=SYS_JTMS-SWDIO`, `Mode=Serial_Wire` | MATCH |
| PA14 | SWCLK | SYS_JTCK-SWCLK, Serial Wire | `.ioc`: `PA14.Signal=SYS_JTCK-SWCLK`, `Mode=Serial_Wire` | MATCH |
| PC14 | OSC32_IN | LSE external oscillator | `.ioc`: `PC14-OSC32_IN.Mode=LSE-External-Oscillator`, `Signal=RCC_OSC32_IN` | MATCH |
| PC15 | OSC32_OUT | LSE external oscillator | `.ioc`: `PC15-OSC32_OUT.Mode=LSE-External-Oscillator` | MATCH |
| PD0 | OSC_IN | HSE external oscillator | `.ioc`: `PD0-OSC_IN.Mode=HSE-External-Oscillator`, `Signal=RCC_OSC_IN` | MATCH |
| PD1 | OSC_OUT | HSE external oscillator | `.ioc`: `PD1-OSC_OUT.Mode=HSE-External-Oscillator` | MATCH |

All 15 pins are listed in `.ioc` as `Mcu.Pin0` … `Mcu.Pin16` plus the three virtual
pins (`VP_RTC_VS_RTC_Activate`, `VP_SYS_VS_Systick`, `VP_TIM3_VS_ClockSourceINT`),
with `Mcu.PinsNb=18`.

## GPIO port clocks

`gpio.c:MX_GPIO_Init()` enables GPIOC, GPIOD, GPIOA and GPIOB clocks. This is required
for the OSC pins on port C/D to be usable and is consistent with the pin list above. — MATCH

## Findings that are not pin mismatches but must be recorded

| # | Finding | Verdict |
| --- | --- | --- |
| 1 | **No CubeMX User Labels exist.** The `.ioc` contains no `*Label*` keys, so generated code refers to the buttons as bare `GPIO_PIN_12/13/14` and the ADC channels only through `hadc1`. The names `KEY_UP`, `KEY_DOWN`, `KEY_OK` in this document come from the project brief, **not** from the repository. Per the Phase 0 instruction the `.ioc` was **not** modified to add them. | GAP |
| 2 | **PA0 is dual-purpose in the pin mapper.** CubeMX records the pin as `PA0-WKUP` (ADC channel 0 shared with the `WKUP` wake-up function). Only the ADC function is selected; no EXTI/wake-up config is generated. | MATCH (noted) |
| 3 | **Keil device name differs from the CubeMX part name.** `.ioc` says `Mcu.CPN=STM32F103C8T6` / `Mcu.UserName=STM32F103C8Tx`; `MDK-ARM/*.uvprojx` selects `Device = STM32F103C8` from pack `Keil.STM32F1xx_DFP.2.2.0`. These are the same silicon — the DFP simply uses a shorter name. Not a mismatch. | MATCH (noted) |
| 4 | **No peripheral pins were added, moved or removed.** `PB12/13/14` carry `Locked=true` in the `.ioc`, which is CubeMX's pin-lock flag, not evidence of hand editing. | MATCH |
| 5 | **Unused-but-enabled clocks**: GPIOC/GPIOD clocks are switched on for the oscillator pins. This is normal CubeMX output, not a defect. | MATCH (noted) |

## Not provable from source (awaits hardware)

| Item | Why the code cannot answer it |
| --- | --- |
| OLED controller identity (SSD1306 vs SH1106) and I2C 7-bit address | No driver exists; `hi2c1.Init.OwnAddress1 = 0` is the CubeMX default and carries no target address. |
| Whether ECG front-end output actually swings 0–3.3 V at PA0 | Analog domain; no firmware involvement. |
| Whether the PA1 signal conditioning impedance is compatible with 55.5-cycle sampling | Requires the real source impedance of the temperature front-end. |
| Presence/behaviour of external I2C pull-up resistors on PB6/PB7 | Open-drain config only proves the STM32 side. |
| VBAT pin connection and backup-domain retention | Power-supply wiring, not firmware. |
| 8 MHz HSE and 32.768 kHz LSE crystals actually populated and starting | Configuration only; `Error_Handler()` would trap if they did not start. |
| Button wiring is truly one-pin-to-GND with the other to the STM32 pin | Only the internal pull-up side is in firmware. |
