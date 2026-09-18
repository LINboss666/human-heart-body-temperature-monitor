# BASELINE — Phase 0

| Field | Value |
| --- | --- |
| Phase | **0** |
| Status | CubeMX hardware-configuration baseline, frozen |
| Scope of this freeze | hardware resource allocation + a clean-compiling Keil project |
| Not in scope | every line of application software |

## Frozen (present in the committed tree, proven from `.ioc` **and** generated C)

| Area | Frozen content |
| --- | --- |
| **MCU** | STM32F103C8T6, LQFP48, Cortex-M3, 64 KB Flash @ `0x08000000`, 20 KB SRAM @ `0x20000000` |
| **Clock** | HSE 8 MHz → PLL ×9 → SYSCLK 72 MHz; AHB /1 = 72 MHz; APB1 /2 = 36 MHz (timer clock 72 MHz); APB2 /1 = 72 MHz; flash latency 2; ADC = PCLK2/6 = 12 MHz |
| **Low-speed clock** | LSE 32.768 kHz external crystal on PC14/PC15; RTC clocked from LSE |
| **ADC** | ADC1, scan enabled, 2 regular conversions, non-continuous, right-aligned, triggered by **TIM3 TRGO**; rank 1 = CH0/PA0 @ 55.5 cyc, rank 2 = CH1/PA1 @ 55.5 cyc |
| **TIM3** | internal clock, PSC = 71, ARR = 999, TRGO = update event → 1 kHz; no ISR required |
| **DMA** | ADC1 → `DMA1_Channel1`, peripheral→memory, circular, half-word/half-word, periph-inc off, mem-inc on, low priority; `DMA1_Channel1_IRQn` enabled at priority (0,0) |
| **I2C1** | enabled, 400 kHz Fast Mode, 7-bit addressing, PB6 SCL / PB7 SDA (AF open-drain) |
| **USART1** | enabled, 230400 baud, 8 data bits, no parity, 1 stop bit, async, no hardware flow control, PA9 TX / PA10 RX |
| **RTC** | peripheral initialised, backup-domain access enabled in `HAL_RTC_MspInit` |
| **GPIO buttons** | PB12, PB13, PB14 as `GPIO_MODE_INPUT` with `GPIO_PULLUP` (active-low to GND expected) |
| **Debug** | Serial Wire (SWD) on PA13/PA14 |
| **Build** | Keil MDK-ARM, AC5, single target, `0 Error(s), 0 Warning(s)` on a from-scratch rebuild — see [BUILD.md](BUILD.md) |
| **Code content** | every `USER CODE` region is empty; `main()` initialises peripherals then spins in an empty `while (1)` |

The last row is the load-bearing one: the tree is unmodified CubeMX output plus
documentation. Nothing was hand-tuned.

## Not done (all of it, by design)

No application layer exists. Explicitly **absent**:

* starting the ADC / DMA acquisition (`HAL_ADC_Start_DMA()` is never called)
* any sample buffer in RAM
* ECG signal processing, R-peak detection, heart-rate calculation
* lead-off / electrode-off detection
* temperature scaling, calibration or ADC-to-degree conversion
* OLED driver, display buffers, fonts
* KK_UI integration (not even vendored)
* menu/navigation bound to PB12/PB13/PB14
* RTC set/read/format logic
* UART framing, packet protocol, checksums
* PC host application, real-time plot, CSV/XLSX export
* low-power modes, watchdog, error reporting beyond the CubeMX `Error_Handler()` trap
* unit tests, CI, host-side simulation harness

No claim of any monitoring function should be read into this repository.

## Awaiting physical confirmation before Phase 1

These cannot be settled from source code. Each one blocks a later feature.

| # | Open item | Why it is open | Blocks |
| --- | --- | --- | --- |
| 1 | **OLED controller identity and address** — SSD1306 or SH1106, and `0x3C` / `0x3D` | No driver in tree; `OwnAddress1 = 0` is only the CubeMX default. SH1306-class modules differ in the 128×64 vs 132×64 addressing, which changes the driver. | display driver, UI |
| 2 | **ECG analog front-end output level and offset** at PA0 | The front-end is another member's design and is not in this repo. ADC input range, bias point, gain and bandwidth are unverified. | ECG acquisition, heart-rate algorithm |
| 3 | **Final temperature-sensing interface** | PA1 / ADC1_IN1 (analog) is the current baseline assumption only. If the design becomes digital (e.g. 1-Wire or I²C), PA1's role changes and the `.ioc` needs a revision. The course rules forbid a ready-made acquisition module, so the front-end is member-designed. | temperature acquisition, pin allocation |
| 4 | **VBAT / backup-domain behaviour** | `HAL_PWR_EnableBkUpAccess()` and the RTC enable are generated, but whether VBAT is actually wired, and whether RTC survives power removal, is a board question. | time-keeping across power loss |
| 5 | **Physical lead-off detection interface** | Requires front-end hardware support (injected current or electrode impedance sensing). Nothing in the MCU config provides it. | lead-off detection |
| 6 | **HSE/LSE crystals start reliably** on the real board | `SystemClock_Config()` calls `Error_Handler()` → `__disable_irq(); while(1);` if either fails to start. A board with an unpopulated 32.768 kHz crystal will therefore hang at boot, before any output. Not yet exercised on hardware. | boot |
| 7 | **External I²C pull-ups present** on PB6/PB7 | Open-drain config proves only the MCU side. | OLED |
| 8 | **Button polarity and debounce** | Internal pull-ups + active-low is the *assumed* wiring; the schematic is not in this repo. | UI navigation |
| 9 | **3.3 V rail and ADC source impedance** | 55.5-cycle sampling assumes the driving stage can charge the S/H cap within spec. | ADC accuracy |

## Known risks already visible in the generated code

Recorded, **not** fixed — fixing them would mean changing the baseline this phase exists to freeze.

1. `Error_Handler()` is a bare `__disable_irq(); while(1);`. Any peripheral init failure
   (LSE not starting is the most likely) bricks the board with no indication.
2. Neither **I2C1** nor **USART1** has an NVIC vector enabled, so future drivers must poll
   or a CubeMX revision must add interrupts.
3. There is no **ADC end-of-conversion / overrun** handling configured; only the DMA
   transfer interrupt exists. With non-continuous ADC + circular DMA, an overrun would be
   silent.
4. **TIM3 `AutoReloadPreload` is disabled** — fine for a fixed 1 kHz, surprising if the
   rate is ever changed at runtime.
5. The **DMA half-buffer boundary is not defined** because no buffer exists. When a buffer
   is added, its length must be an even multiple of 2 so the ECG/temperature interleave
   stays aligned across the circular wrap.
6. **No CubeMX User Labels** — see [PINMAP.md](PINMAP.md) finding 1.

## Baseline integrity statement

* `Human Heart and Body Temperature Monitor.ioc` — **not** modified in this phase.
* Every CubeMX-generated `.c` / `.h` under `Core/` — **not** modified in this phase.
* `MDK-ARM/*.uvprojx`, `*.uvoptx`, startup and `Drivers/` — **not** modified in this phase.
* Files added in this phase: `README.md`, `.gitignore`, `docs/*.md`. Nothing else.
* The only build performed in this phase wrote exclusively into the git-ignored output
  directory. Its effect on tracked files: none (verified by timestamp sweep — see [BUILD.md](BUILD.md)).

## Phase exit criteria

Phase 0 is complete when:

1. this tree is committed on `main`, tagged `v0.1-baseline`, and published;
2. the documentation above matches the code;
3. an independent reviewer has read [REVIEW_HANDOFF.md](REVIEW_HANDOFF.md) and returned findings.

Phase 1 does not start here.
