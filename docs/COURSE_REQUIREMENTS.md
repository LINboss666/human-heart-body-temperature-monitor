# Course Requirement Mapping — Phase 1

Every row is stated as **what the code does** and **what has been demonstrated**.
Nothing here carries the word PASS unless a host test proved it without hardware.
The two verdicts used are:

| Token | Meaning |
| --- | --- |
| `SOFTWARE IMPLEMENTED` | the code exists and is built into the firmware |
| `HOST VERIFIED` | executed by a host test in `tests/host/` or `pc_monitor/tests/` |
| `HARDWARE VERIFICATION PENDING` | cannot be confirmed until the bench work in [HARDWARE_TEST_PLAN.md](HARDWARE_TEST_PLAN.md) is done |

No requirement below that depends on a human body, a custom analog front-end, or a
physical display is marked as satisfied.

## Signal acquisition

| Requirement | Implementation | Status |
| --- | --- | --- |
| ECG sampling at ≥ 500 samples/s | TIM3 TRGO at **1000 Hz** triggers ADC1 scan; DMA1_Channel1 moves results in hardware | `SOFTWARE IMPLEMENTED` · `HARDWARE VERIFICATION PENDING` (Stage G) |
| Advanced requirement ≥ 1000 samples/s | Exactly 1000 Hz: 72 MHz / (71+1) / (999+1). Derivation in [CUBEMX_CONFIG.md](CUBEMX_CONFIG.md) | `SOFTWARE IMPLEMENTED` · `HARDWARE VERIFICATION PENDING` |
| Sampling must not depend on an ISR | No TIM3 interrupt is enabled and no `TIM3_IRQHandler` exists; the only ISR work is a flag set | `SOFTWARE IMPLEMENTED` · verified by inspection of `.ioc` and `stm32f1xx_it.c` |
| Two simultaneous channels without channel swap | Buffer length is an even multiple of the channel count (`128 frames × 2 blocks × 2 channels`); the interleave cannot slip at the circular wrap | `SOFTWARE IMPLEMENTED` · `HOST VERIFIED` by the frame-index continuity test |
| ECG and temperature must not use an off-the-shelf acquisition module | Front-ends are designed by other project members and are **not** in this repository; the MCU side only exposes an ADC input | Design constraint, owned outside this repo |

## ECG processing

| Requirement | Implementation | Status |
| --- | --- | --- |
| Raw record must keep its bandwidth | `RAW` path is the unfiltered 12-bit code, streamed to the PC and written to CSV/XLSX. Nothing in the filter chain touches it | `SOFTWARE IMPLEMENTED` · `HOST VERIFIED` ("the RAW path reports exactly the ADC code that went in") |
| Recording bandwidth 0.05–150 Hz | Set by the **analog** front-end, which does not exist yet. The software display path measures -3 dB at 22.1 Hz and the QRS band at 43.8 Hz; neither is claimed as the recording bandwidth | `HARDWARE VERIFICATION PENDING` (Stage H) |
| Baseline drift removal | Two cascaded ~0.62 Hz leaky integrators on the display and QRS paths | `SOFTWARE IMPLEMENTED` · `HOST VERIFIED` (beats detected with 300-count 0.3 Hz wander) |
| Mains interference | 20-tap comb, exact null at 50 Hz (**measured -313 dB**), selectable 60 Hz (~58.8 Hz null, -34 dB at 60 Hz) or off | `SOFTWARE IMPLEMENTED` · `HOST VERIFIED` (mean \|display\| at 50 Hz: 252 → 1) |
| Heart-rate calculation | Pan-Tompkins-style energy detector: HP 5 Hz → MA(20) → 3-point derivative → square → 120 ms MWI → self-calibrating threshold → 250 ms refractory → median of last 5 RR | `SOFTWARE IMPLEMENTED` · `HOST VERIFIED` |
| Heart-rate accuracy | 50→49, 60→60, 72→72, 95→95, 120→120, 150→150 bpm on synthetic beats with noise, mains and wander | `HOST VERIFIED` **on synthetic data only**. No claim of ±2 bpm on a human: the brief forbids it and nothing has been measured |
| Heart-rate alarm | `HR_INVALID / ACQUIRING / NORMAL / LOW / HIGH`; low and high bands editable on-device and over the link | `SOFTWARE IMPLEMENTED` · `HOST VERIFIED` (state machine tests) |
| No rate shown before there is a rate | Displays `--` until ≥3 valid RR intervals exist | `SOFTWARE IMPLEMENTED` · `HOST VERIFIED` ("no heart rate is reported before enough RR intervals exist") |
| Reading must not go stale silently | A valid reading expires after 3 s without a beat | `SOFTWARE IMPLEMENTED` · `HOST VERIFIED` |
| Lead-off / electrode detachment detection | **Not implemented and cannot be**: there is no lead-off hardware in this design. Software only reports `SIGNAL_POOR` for flatline or a railed input, and `LEAD_UNKNOWN` otherwise | `SOFTWARE IMPLEMENTED` (honest subset) · hardware interface `HARDWARE VERIFICATION PENDING` (Stage K) |

## Temperature

| Requirement | Implementation | Status |
| --- | --- | --- |
| Body temperature measurement | PA1 / ADC1_IN1 with a raw/calibration split. Only the raw layer is real today | `SOFTWARE IMPLEMENTED` · `HARDWARE VERIFICATION PENDING` (Stage J) |
| Conversion to degrees | `TEMP_SENSOR_MODEL` is `UNCALIBRATED`, so the state is `TEMP_UNCALIBRATED`, `valid=false`, and the UI shows `--.-` | Deliberate. No invented 36.5 anywhere — `HOST VERIFIED` |
| Refresh ≤ 500 ms | Decimation window is 250 samples at 1 kHz → a new reading every 250 ms | `SOFTWARE IMPLEMENTED` · `HOST VERIFIED` (update cadence) |
| 0.1 °C display resolution | Internal unit is centi-degC (`int16_t`), display 0.1 °C | `SOFTWARE IMPLEMENTED` |
| Probe disconnection detection | Range heuristics with `TEMP_ADC_OPEN/SHORT_THRESHOLD` and confirm counters, reported as `TEMP_PROBE_FAULT` | `SOFTWARE IMPLEMENTED` · thresholds `UNVERIFIED` · `HARDWARE VERIFICATION PENDING` |
| Sensor calibration | Replace `temperature_calibration.h` and `TemperatureConvert()` | Interface exists · `HOST VERIFIED` that the linear branch converts and clamps |

## Time keeping

| Requirement | Implementation | Status |
| --- | --- | --- |
| RTC date/time | `rtc_service` over the LSE-driven RTC, epoch seconds internally | `SOFTWARE IMPLEMENTED` |
| Must not reset on every power-up | CubeMX unconditionally writes 2000-01-01 in `MX_RTC_Init`; the service snapshots the epoch into backup registers each second and reinstates it | `SOFTWARE IMPLEMENTED` · `HARDWARE VERIFICATION PENDING` (Stage A/B) |
| Retains time without main power | Needs VBAT wired on the real board | `HARDWARE VERIFICATION PENDING` — never claimed by firmware (`CAP_RTC_BATTERY_BACKED` is always clear) |
| Calendar correctness | Hinnant civil-days arithmetic, unsigned epoch, range 1970–2099 | `HOST VERIFIED`: 119 assertions, hour-by-hour round-trip 1970→2099, leap/century rules, Dec-31→Jan-1, weekday continuity |
| Modifiable on device | `DATE & TIME` page with KK_UI integer editors and `SET RTC` / `READ RTC` actions | `SOFTWARE IMPLEMENTED` · `HARDWARE VERIFICATION PENDING` (no panel yet) |
| Modifiable remotely | `SET_RTC` packet; every field validated, invalid times answered with `NACK_BAD_VALUE` and the clock left untouched | `SOFTWARE IMPLEMENTED` · `HOST VERIFIED` on the Python side |

## Display and user interface

| Requirement | Implementation | Status |
| --- | --- | --- |
| OLED human interface | KK_UI + KK_OLED over I2C1 at 400 kHz, 128×64 | `SOFTWARE IMPLEMENTED` · `HARDWARE VERIFICATION PENDING` (Stages D/E) |
| Controller identity | Unknown. Three compile-time profiles (SSD1306 / SH1106 / CH1116) with different charge-pump bytes and column offsets; all marked UNVERIFIED | Deliberately unresolved, not guessed |
| Address | `0x3C` / `0x3D` probed at boot; the answer is reported over the link and on the STATUS page. An ACK proves presence only, never the controller type | `SOFTWARE IMPLEMENTED` |
| Missing display must not brick the device | `OLED_Init()` failure sets `oled_present=false`; acquisition, RTC and UART all continue | `SOFTWARE IMPLEMENTED` · `HARDWARE VERIFICATION PENDING` (Stage E) |
| Pages | MAIN menu, ECG (custom waveform), BODY TEMP, STATUS, DATE & TIME, SETTINGS, ABOUT | `SOFTWARE IMPLEMENTED` |
| Waveform on 128 px | min/max decimation, 8 samples per column, vertical segments so a narrow QRS cannot be averaged away | `SOFTWARE IMPLEMENTED` · `HARDWARE VERIFICATION PENDING` |
| UI must not block sampling | Display flush is partial-area and runs in the main loop; the ADC chain is hardware-driven with 128 ms of buffering | `SOFTWARE IMPLEMENTED` · `HARDWARE VERIFICATION PENDING` (Stage M) |
| Font licence | ASCII-only, authored in-repo by `tools/gen_oled_fonts.py`; no third-party typeface and no CJK table | `HOST VERIFIED` against the vendor decoder, 78 assertions |

## Communication and host software

| Requirement | Implementation | Status |
| --- | --- | --- |
| Data communication | Binary framed protocol over USART1 at 230400 8N1; no `printf` CSV on the wire | `SOFTWARE IMPLEMENTED` |
| Bandwidth must be proven, not assumed | 50 × 68 B ECG_BATCH + 2 × 57 B STATUS + 2 × 22 B TEMP_STATUS = **3558 B/s of 23040 B/s = 15.4 %** | `HOST VERIFIED` (arithmetic in [PROTOCOL.md](PROTOCOL.md)) |
| Integrity | Magic + version + length + sequence + CRC16; resync on corruption | `HOST VERIFIED`: 574 assertions across C and Python |
| PC real-time waveform | PySide6 + pyqtgraph, 10 s rolling window | See [../pc_monitor/README.md](../pc_monitor/README.md) |
| ≥ 10 s continuous recording | Recording is a state with a session timer; the 1 kHz stream is continuous | `SOFTWARE IMPLEMENTED` · `HARDWARE VERIFICATION PENDING` (Stage M) |
| Export to CSV | Every row, UTF-8 | `HOST VERIFIED` (Python tests) |
| Waveform viewable in Excel | XLSX with `Data` (all rows), `Summary`, and an embedded ECG `LineChart` decimated to a few thousand points so the file opens | `HOST VERIFIED` (test reopens the workbook and asserts both sheets and a chart exist) |

## Resource limits

| Constraint | Result |
| --- | --- |
| No RTOS | Cooperative super-loop only |
| No `malloc` / `free` | None in `App/` or in either vendored library (grep-verified) |
| No large Chinese font table | ASCII-only, 1113 bytes |
| No floating point in the signal chain | All filter and detector arithmetic is integer |
| Flash must not be faked by changing the part | Device remains `STM32F103C8`, ROM `0x08000000` size `0x10000`, RAM `0x20000000` size `0x5000` — unchanged from Phase 0 |
| Measured footprint | `Code=37940 RO=3076 RW=376 ZI=7560` → 41392 B flash (**63.2 %** of 64 KB), 7936 B RAM (**38.8 %** of 20 KB) |
| Compiler warnings | `0 Warning(s)` at Keil warning level 2, no `--diag_suppress`, no blanket warning suppression |

## Not satisfied, stated plainly

1. **No measurement from a human body exists.** Every heart-rate figure came from synthetic
   beats. The analog front-ends are not built.
2. **No pixels have ever been seen.** The panel, its controller, its address and its pull-ups
   are all unconfirmed; the UI is compile-verified only.
3. **No LSE start, no VBAT retention, no railed-input behaviour has been observed** on real
   silicon.
4. **Lead-off detection as the course describes it is not implemented**, because the required
   hardware interface does not exist. What exists is signal-quality reporting, named to avoid
   implying an electrode measurement that nobody took.
5. `±2 bpm` heart-rate accuracy is **not** claimed anywhere in this repository.
