# 人体心电体温监测仪 / Human Heart and Body Temperature Monitor

> **Status: Phase 0 — STM32CubeMX hardware-configuration baseline.**
> This repository is **not a finished product**. It contains only a CubeMX-generated
> STM32F103C8T6 project whose peripherals are configured and whose Keil MDK-ARM
> project compiles clean. **No application software exists yet.**

| Item | Value |
| --- | --- |
| MCU | STM32F103C8T6 (LQFP48, 64 KB Flash / 20 KB SRAM) |
| Toolchain | Keil MDK-ARM (uVision project under [`MDK-ARM/`](MDK-ARM/)) |
| Generator | STM32CubeMX 6.17.0 · STM32Cube FW_F1 V1.8.7 |
| Phase | 0 / CubeMX Baseline |
| Keil build result | `0 Error(s), 0 Warning(s)` |
| Program size | `Code=6748  RO-data=328  RW-data=16  ZI-data=2000` |

## What is actually in this repository

* A complete, unmodified STM32CubeMX code-generation output (`Core/`, `Drivers/`, `MDK-ARM/`, `.ioc`).
* Peripheral **configuration only**: ADC1, TIM3, DMA1 Channel 1, I2C1, USART1, RTC, GPIO buttons, SWD debug.
* Baseline documentation under [`docs/`](docs/).

`Core/Src/main.c` is the CubeMX template: it calls `HAL_Init()`, `SystemClock_Config()`,
the seven `MX_*_Init()` functions, and then spins in an empty `while (1)`. Every
`USER CODE` region in the project is empty. **There is no application logic of any kind.**

## What is NOT implemented (deliberately)

The following are planned but **not present** in this codebase. Do not read this
repository as if they worked.

* ECG acquisition / buffering
* Body-temperature acquisition
* Heart-rate calculation
* Lead-off detection
* OLED display / UI (including the KK_UI framework)
* UART packet protocol and PC host software
* Real-time visualisation, CSV/XLSX export
* RTC set/read application logic and button handling

## Hardware resource map (configured, not exercised)

| Function | Peripheral | Pins |
| --- | --- | --- |
| ECG conditioned analog input | ADC1_IN0, rank 1 | PA0 |
| Temperature analog input | ADC1_IN1, rank 2 | PA1 |
| 1 kHz sample trigger | TIM3 TRGO (update event) | — |
| Sample transport | DMA1_Channel1, circular, periph→mem, half-word | — |
| OLED I2C bus | I2C1, 400 kHz Fast | PB6 SCL, PB7 SDA |
| PC link | USART1, 230400 8N1 | PA9 TX, PA10 RX |
| Buttons | GPIO input, pull-up | PB12, PB13, PB14 |
| Time base / backup | RTC, LSE 32.768 kHz | PC14, PC15 |
| Debug | Serial Wire (SWD) | PA13 SWDIO, PA14 SWCLK |

See [docs/PINMAP.md](docs/PINMAP.md) for the full table and
[docs/CUBEMX_CONFIG.md](docs/CUBEMX_CONFIG.md) for the clock tree and the
hardware-triggered acquisition chain.

## Safety note on the analog front-end

PA0 is intended for the **output of an analog ECG conditioning circuit**, not for
raw electrodes. Human electrodes must never be connected directly to the STM32 ADC pin.
The analog front-end is designed separately by other project members.

This project is a **student course project**. It is **not a medical device** and its
output must not be used for diagnosis or any clinical decision.

## Future plan (description only — no code)

1. Freeze the sampling path: TIM3 TRGO → ADC1 scan → DMA1 circular double-buffer in RAM.
2. Port the temperature channel once the sensor interface is finalised (PA1/ADC1_IN1
   is the current baseline assumption and may change).
3. Add an OLED driver once the display controller (SSD1306 / SH1106) and I2C address are confirmed.
4. Introduce a UI layer (candidate: KK_UI) on top of the display driver.
5. Implement heart-rate extraction from the buffered ECG samples, plus lead-off detection.
6. Define a framed UART protocol to the PC at the already-configured 230400 baud.
7. Build the PC host tool: live waveform display and CSV/XLSX export.
8. Wire up RTC set/display and the three buttons into a menu.

## Build

See [docs/BUILD.md](docs/BUILD.md). Open
`MDK-ARM/Human Heart and Body Temperature Monitor.uvprojx` in uVision and build the
single target of the same name.

## Repository layout

```
.
├── Human Heart and Body Temperature Monitor.ioc   CubeMX project file (source of truth)
├── .mxproject                                    CubeMX/Keil file list, do not edit by hand
├── Core/{Inc,Src}                                Generated peripheral init + user sections
├── Drivers/                                      STM32Cube FW_F1 V1.8.7 HAL + CMSIS
├── MDK-ARM/                                      uVision project, startup, RTE
└── docs/                                         Baseline documentation
```

## Documentation index

| File | Purpose |
| --- | --- |
| [docs/BASELINE.md](docs/BASELINE.md) | What is frozen, what is not, what awaits hardware |
| [docs/PINMAP.md](docs/PINMAP.md) | Pin table with EXPECTED / ACTUAL / MISMATCH verdicts |
| [docs/CUBEMX_CONFIG.md](docs/CUBEMX_CONFIG.md) | Clock tree and every peripheral setting, verified against generated code |
| [docs/BUILD.md](docs/BUILD.md) | Keil target settings and how the baseline build was confirmed |
| [docs/REVIEW_HANDOFF.md](docs/REVIEW_HANDOFF.md) | Handoff package for independent code review |
