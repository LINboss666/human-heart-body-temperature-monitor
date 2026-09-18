# 人体心电体温监测仪 · Human Heart and Body Temperature Monitor

**English** · [中文](README.zh-CN.md)

> **Phase 1 — full application software, built and host-tested, never run on hardware.**
>
> This is a student course project, **not a medical device**. It performs no diagnosis
> and its output must not be used for any clinical decision. No ECG or temperature
> measurement from a human body has ever been taken with this firmware, and no display
> has ever shown a pixel from it. Read
> [docs/COURSE_REQUIREMENTS.md](docs/COURSE_REQUIREMENTS.md) for which claims rest on
> host tests and which are pending hardware.

| Item | Value |
| --- | --- |
| MCU | STM32F103C8T6 — Cortex-M3, 64 KB flash, 20 KB SRAM |
| Toolchain | Keil MDK-ARM (AC5 / ARMCC V5.06), STM32CubeMX 6.17.0, FW_F1 V1.8.7 |
| Firmware build | `0 Error(s), 0 Warning(s)` |
| Footprint | `Code=37940 RO=3076 RW=376 ZI=7560` → **63.2 % flash, 38.8 % RAM** |
| Host tests | 6 C binaries, 901 assertions · 238 pytest cases, 0 failures |
| Hardware verified | **None.** See [docs/HARDWARE_TEST_PLAN.md](docs/HARDWARE_TEST_PLAN.md) |
| Phase 0 baseline | tag `v0.1-baseline`, commit `eb8a795` |

## What it does

STM32F103C8T6 acquires a conditioned ECG signal and an analog temperature signal at
**1000 samples/s**, computes heart rate on-device, shows both plus date/time on a
128×64 OLED through the KK_UI library, accepts three buttons, keeps wall-clock time in
the RTC, and streams a CRC-guarded binary record to a PC application that plots it live
and exports CSV and Excel workbooks containing the data plus an embedded waveform chart.

## Architecture

No RTOS, no heap, no floating point in the signal chain, cooperative super-loop.
See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full data flow.

```
TIM3 TRGO 1 kHz ──► ADC1 scan (CH0 ECG, CH1 TEMP) ──► DMA1_Channel1 circular
   └─ half/full IRQ set a flag only
        ▼
App_Loop ─► ecg_signal      ─► three paths: RAW (unfiltered) · DISPLAY · QRS
         ─► ecg_hr          ─► median of last 5 RR
         ─► temperature      ─► 250 ms decimation + calibration hook
         ─► ui_app           ─► KK_UI pages + min/max-decimated OLED trace
         ─► protocol_service ─► ECG_BATCH (20 samples/frame) ─► USART1 230400
pc_monitor/  PySide6 + pyqtgraph ─► ring buffer ─► 25 FPS plot ─► CSV / XLSX
```

| Layer | Directory |
| --- | --- |
| Application | [`App/`](App/) — acquisition, ecg, temperature, rtc_service, buttons, protocol, uart, display, ui, diagnostics |
| Vendored UI | [`ThirdParty/kk_ui`](ThirdParty/kk_ui/), [`ThirdParty/kk_oled`](ThirdParty/kk_oled/) — MIT, see [docs/UPSTREAM.md](docs/UPSTREAM.md) |
| CubeMX output | `Core/`, `Drivers/`, the `.ioc` file |
| PC host tool | [`pc_monitor/`](pc_monitor/) |
| Host test harness | [`tests/host/`](tests/host/), [`tools/`](tools/) |

## Pin map

| Pin | Function | Pin | Function |
| --- | --- | --- | --- |
| PA0 | ADC1_IN0 — ECG conditioned input | PB6 | I2C1_SCL → OLED |
| PA1 | ADC1_IN1 — temperature analog input | PB7 | I2C1_SDA → OLED |
| PA9 | USART1_TX → PC | PA13 | SWDIO |
| PA10 | USART1_RX ← PC | PA14 | SWCLK |
| PB12 | KEY_UP (input, pull-up, active low) | PC14/PC15 | LSE 32.768 kHz |
| PB13 | KEY_DOWN | PD0/PD1 | HSE 8 MHz |
| PB14 | KEY_OK / start-stop | | |

Full table with verification verdicts: [docs/PINMAP.md](docs/PINMAP.md).

## Acquisition chain — and why it is not software-sampled

TIM3 overflows at 1 kHz and its TRGO pulses the ADC external trigger; the ADC scans two
ranks; the DMA moves every result into a 1024-byte circular ring. **No timer ISR is
enabled and none is needed** — the only ISR work in the whole chain is setting a flag.
Peripherals and the measured filter response: [docs/CUBEMX_CONFIG.md](docs/CUBEMX_CONFIG.md).

## ECG processing

Three separate paths leave the conditioning stage, and the separation is the point.
**RAW** is unfiltered and is what the PC records, because the course metric is a
0.05–150 Hz recording bandwidth and truncating the record to a heart-rate band would
destroy what is being graded. **DISPLAY** is baseline-removed and comb-filtered for the
OLED. **QRS** is a 5–44 Hz energy detector: derivative → square → 120 ms integration →
self-calibrating threshold → 250 ms refractory → median of 5 RR intervals.

Filters are power-of-two leaky integrators and boxcar moving averages, **not biquads**. A
Q14 biquad high-pass was measured producing a steady 339-count output on a dead-flat
input, because the half-LSB rounding term is amplified ~1024× by a denominator that is
near zero at DC. The 20-tap comb instead nulls 50 Hz exactly (measured −313 dB).

On synthetic beats carrying 0.3 Hz wander, 50 Hz mains and broadband noise, measured
rates came out 50→49, 60→60, 72→72, 95→95, 120→120, 150→150 bpm. **No accuracy figure is
claimed for human signals.**

Lead-off: there is no lead-off hardware in this design, so the firmware reports
`SIGNAL_POOR` or `LEAD_UNKNOWN` and **never** claims an electrode is disconnected.

## Temperature

`temperature_raw` and `temperature_calibration` are deliberately separate layers. The
front-end is another member's design and is not in this repository, so the model is
`UNCALIBRATED`, `valid=false`, and the UI shows `--.- °C` — **no plausible-looking
invented number**. The raw code and its pin millivolts stay available, because that is
what a later calibration needs. Replacing one header and one function is sufficient.

## OLED and KK_UI

KK_UI (menu, info, integer/bool editors, toast, animation) over KK_OLED, I2C1 at 400 kHz,
128×64 monochrome. KK_UI has **no waveform widget** and upstream forbids adding one, so the
ECG trace is a custom page drawn with KK_OLED primitives using min/max decimation, so a
narrow QRS cannot be averaged away between columns.

The controller (**SSD1306 / SH1106 / CH1116**) and the address (`0x3C` / `0x3D`) are
**unknown**. Three complete profiles ship, all marked unverified; the address is probed at
boot and reported. Upstream's own porting rules forbid guessing these values, and an I2C
acknowledge proves presence only. If no panel answers, the firmware keeps acquiring,
timing and streaming. See [docs/KK_UI_NOTES.md](docs/KK_UI_NOTES.md).

Fonts are ASCII-only and authored in-repo by `tools/gen_oled_fonts.py`, because the
upstream glyph service's output is not covered by MIT — see [docs/UPSTREAM.md](docs/UPSTREAM.md).

## Binary serial protocol

Magic `A5 5A`, version, type, sequence, length, device timestamp, payload, CRC16
(CCITT-FALSE, check value `0x29B1`); little-endian; resynchronising parser. `ECG_BATCH`
carries 20 raw codes plus current temperature, heart rate and flags, and an absolute
`first_sample_index` so the PC can rebuild an exact 1 kHz axis and detect dropped blocks
that no sequence counter would reveal. **Measured utilisation: 3608 B/s of 23040 B/s =
15.7 %.** Layout in [docs/PROTOCOL.md](docs/PROTOCOL.md), defined once in
`App/protocol/protocol.h`.

## Build the firmware

Open `MDK-ARM/Human Heart and Body Temperature Monitor.uvprojx` in uVision and build the
single target, or:

```
"C:\Keil_v5\UV4\UV4.exe" -j0 -b "Human Heart and Body Temperature Monitor.uvprojx" -o build.log
```

**After any CubeMX regeneration**, re-add the application groups — CubeMX owns the
`.uvprojx` and drops hand-added groups, and this script is idempotent:

```
python tools/add_keil_sources.py
```

Details and the measured footprint: [docs/BUILD.md](docs/BUILD.md).

## Run the PC tool

```
cd pc_monitor
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python -m pytest -q             # host tests
.venv/Scripts/python -m pc_monitor --demo     # synthetic data, no board needed
.venv/Scripts/python -m pc_monitor --port COM7 --baud 230400
```

`--demo` refuses to look like real data: a DEMO/SYNTHETIC banner, and synthetic rows are
marked, so they cannot be exported as a measurement.

## Verification status, honestly

| Done and demonstrated | Not claimed |
| --- | --- |
| Compiles clean with no warning suppression | Any measurement from a human body |
| 901 host C assertions on the shipping integer code | That the RTC crystal starts, or VBAT retention |
| 238 Python cases; 21 frames replayed from the C encoder | That any pixel ever appeared on a panel |
| Calendar round-trip 1970→2099, hour by hour | ±2 bpm heart-rate accuracy |
| Font decodes under the vendor's own decoder | That the front-end gain or bias is right |
| Flash/RAM fit with 24 KB / 12 KB spare | That 1 kHz holds with UI and UART loaded |

Everything pending is itemised with a procedure in
[docs/HARDWARE_TEST_PLAN.md](docs/HARDWARE_TEST_PLAN.md).

## Safety

* **Educational course project. Not a medical device.** No diagnosis, no clinical use.
* Electrodes must **never** connect to PA0. The ADC pin expects the output of a properly
  designed, patient-safe, isolated analog front-end, built by other project members.
* A PC- or mains-powered link introduces isolation obligations beyond this firmware's
  scope; consider them before connecting a person.
* Software filtering does not substitute for input impedance, CMRR, isolation, electrical
  safety or analog frequency response.

## Documentation

| File | Purpose |
| --- | --- |
| [docs/BASELINE.md](docs/BASELINE.md) | Phase 0: what is frozen and what awaits hardware |
| [docs/PINMAP.md](docs/PINMAP.md) | Pin table with EXPECTED / ACTUAL / MISMATCH verdicts |
| [docs/CUBEMX_CONFIG.md](docs/CUBEMX_CONFIG.md) | Clock tree and every peripheral setting |
| [docs/BUILD.md](docs/BUILD.md) | Keil target settings and how builds were verified |
| [docs/PROTOCOL.md](docs/PROTOCOL.md) | Wire format and bandwidth arithmetic |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Data flow, rules followed, known weaknesses |
| [docs/COURSE_REQUIREMENTS.md](docs/COURSE_REQUIREMENTS.md) | Requirement-by-requirement status |
| [docs/HARDWARE_TEST_PLAN.md](docs/HARDWARE_TEST_PLAN.md) | Bench bring-up, stages A–N |
| [docs/KK_UI_NOTES.md](docs/KK_UI_NOTES.md) | What the upstream really is, and conformance |
| [docs/UPSTREAM.md](docs/UPSTREAM.md) | Third-party provenance and licences |
| [docs/REVIEW_HANDOFF.md](docs/REVIEW_HANDOFF.md) · [PHASE1_REVIEW_HANDOFF.md](docs/PHASE1_REVIEW_HANDOFF.md) | Independent review handoffs |

Phase 0 is frozen at tag `v0.1-baseline`. Phase 1 lives on branch `phase1/full-system`.
Neither is a claim of a finished instrument.
