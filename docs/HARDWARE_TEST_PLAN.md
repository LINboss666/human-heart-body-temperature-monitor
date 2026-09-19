# Hardware Test Plan — bring-up stages A…O

For use the first time a real board exists. Each stage says what to connect, what to
type or press, what a pass looks like, and what the most likely cause is when it does
not pass. Every stage has a **software-only fallback** so a missing oscilloscope or
signal generator never blocks bring-up entirely.

Order matters: A→B establish that anything runs at all; C→E prove the user interface;
F→H prove the analog input; I→K prove the algorithms; L→N prove the link and the
record. Do not start at I.

Safety first: **never connect electrodes to PA0 directly.** PA0 expects the output of a
conditioning circuit. Until Stage H is complete and its output swing is measured, drive
PA0 from a signal generator or a potentiometer divider, not from a person.

---

## Stage A — does it boot at all

**Wiring.** ST-Link to SWD (PA13 SWDIO, PA14 SWCLK, GND, 3.3 V). Nothing else connected.
**Action.** Flash `MDK-ARM/Human Heart and Body Temperature Monitor/<name>.hex`, reset,
attach the debugger and halt once; then run and halt again.
**Pass.** The program counter is inside `App_Loop()` (or one of its callees) on both halts,
and `s_sample_index` in `ecg_signal.c` is monotonically increasing.
**Software fallback.** With no debugger, halt-and-inspect is unavailable: go straight to
Stage L and read the UART, which reports `adc_running`, `ecg_samples` and `uptime_seconds`
in the STATUS packet. A moving `uptime_seconds` is proof of boot.
**Diagnosis.**
- Stuck in `Error_Handler()` (`__disable_irq(); while(1);`) → a `MX_*_Init()` or
  `SystemClock_Config()` failed; almost always HSE or LSE, so do Stage B next. Note that
  `Error_Handler` is reached *before* any output is possible, so a dead clock is invisible
  on the UART as well.
- `ecg_samples` stuck at 0 → Stage F/G.
- Runs but `uptime_seconds` never advances → check `uwTick` is incrementing, i.e. SysTick.

## Stage B — HSE and LSE actually oscillate

**Action.** Attach the debugger, run 5 s, halt, inspect `RCC->CR`.
**Pass.** `HSEORDY` (bit 17) = 1 and `LSEORDY` (bit 1) = 1; `PLLORDY` = 1;
`RCC->CFGR` shows `SWS = 10` (PLL as SYSCLK). Confirm the core clock by reading
`SystemCoreClock` = 72000000.
**Software fallback.** Set a breakpoint in `Error_Handler` and see whether it is hit; or
observe whether `rtc_service_is_valid()` ever becomes true and the STATUS packet's `RTC`
field says `SET` rather than `UNSET` after a time is written (an RTC with no LSE will not
advance).
**Diagnosis.**
- HSE not ready → wrong crystal load capacitance, missing crystal, or the enable is
  fighting an unmapped PD0/PD1. HSE_VALUE must be 8 MHz (`stm32f1xx_hal_conf.h`).
- LSE not ready → 32.768 kHz crystal absent or over-driven. **Consequence is severe and
  by design:** `SystemClock_Config()` calls `Error_Handler()`, so the board appears dead
  even though everything except RTC would work. If bring-up must continue before an LSE is
  fitted, that is a CubeMX change (RTC source to LSI or HSE-divided) and must go through
  the `.ioc`, not an edit of `main.c`.
- HSE ready but `SWS != 10` → PLL config; check `PLLMUL = ×9` and `PLLSRC = HSE`.

## Stage C — the three buttons

**Wiring.** PB12, PB13, PB14 each to GND through a momentary contact. Internal pull-ups are
already enabled by the frozen configuration.
**Action.** Halt inside `buttons_scan()` after a press, or simply advance to the STATUS page
and press each key, watching `uart_rx_packets`/the highlighted menu row move.
**Pass.** UP moves the highlight up, DOWN down, OK enters. A press registers once, not four
times.
**Pass — KEY_OK focus (added by the Phase 1 review fix, currently unexecuted).** On the ECG
page, OK must start recording and turn PC streaming on; press it again to stop. Then
navigate to SETTINGS, or DATE & TIME, and press OK on any entry: `recording` and
`streaming` in the outgoing `STATUS` packet must not change. Before that fix the action
never fired at all (`BTN_EVT_SHORT` had no producer) and the page predicate stayed true
forever, so neither direction was ever exercised in code.
**Software fallback.** No second serial port needed: the ECG page footer and the STATUS page
both react to keys, so button function is visible through the UART record of `STATUS`
packets while pressing.
**Diagnosis.**
- Inverted behaviour (highlight moves on release) → the board is wired active-high to 3V3,
  not active-low to GND. Fix in `read_raw()` in `App/buttons/buttons.c`, one line, and
  update `docs/PINMAP.md`.
- Multiple events per press → `DEBOUNCE_MS` too small for the switches; raise it.
- Nothing ever → confirm `gpio.c` still sets `GPIO_PULLUP` on PB12/13/14 (it does in the
  frozen baseline), then check the pin is not shorted to a rail, which the internal ~40 kΩ
  pull-up cannot fight.

## Stage D — find the panel

**Action.** Power the module on I2C1 (PB6 SCL, PB7 SDA, 3V3, GND). Reset the MCU. Attach the
debugger and inspect `oled_bus_address_8bit`, `oled_bus_scanned`, `oled_present`.
**Pass.** `oled_bus_scanned == 1` and `oled_bus_address_8bit` is `0x78` (0x3C<<1) or `0x7A`
(0x3D<<1).
**Software fallback.** The `HELLO` and `STATUS` packets carry `oled_address` and
`oled_present`, so the address is readable over the serial link with no debugger at all.
**What an address finding does *not* tell you.** Nothing about the controller, its column
offset, its resolution, or whether the address is expressed as 7- or 8-bit. Upstream's own
porting instructions forbid filling those gaps from a common default, and this plan
follows that: identify the part from its silkscreen or flex-printing, or from the vendor
product page, and only then pick a profile.
**Diagnosis.**
- No address answers → check 3V3/GND, and that external pull-ups exist on SCL/SDA (open
  drain will not self-bias). A 100 Hz scope on SCL is the quick check; without a scope,
  HAL returns `OLED_ERROR` and `oled_present` stays false, which is the same signal.
- Answers at 0x3C but the panel is dark → Stage E.

## Stage E — pixels, and choosing the controller profile

**Action.** Pick the profile from the module's markings, set `OLED_CONTROLLER_SELECTED`
(`SSD1306`, `SH1106` or `CH1116`) as a compiler define, rebuild, flash, reset.
**Pass.** The MAIN menu is legible, centred, not shifted horizontally, not mirrored, not
vertically smeared across page boundaries.
**Symptom → profile change.**
| Symptom | Meaning |
| --- | --- |
| Content shifted right by 2 px | Panel is 128-column (SSD1306) but a 132-column, offset-2 profile was selected: switch to `OLED_CTRL_SSD1306`, which uses offset 0 |
| Content shifted left / 2 leftmost columns missing | Real panel is SH1106-class: use `OLED_CTRL_SH1106`, offset 2 |
| Right half shows left half's content, duplicated | Page addressing mismatch — confirm the profile emits `0x20,0x02` |
| Every row inverted (dark ↔ light) | `0xA6` / `0xA7` pairing; swap it in the profile |
| Rows appear in scrambled order | COM scan direction: `0xC0` vs `0xC8`, and COM pin layout `0xDA` |
| Completely dark but I2C acknowledged | Charge pump: SSD1306 wants `0x8D,0x14`; CH1116 uses `0xAD,0x8B,0x33`; SH1106 uses `0x32` or external. This is the single most common cause of a dead-looking panel |
| Shows something but flickers badly | `0xD5` clock divide / `0xD9` pre-charge in the profile |
**Then measure the real bandwidth** with the display path: feed a 10 Hz sine into PA0 and
raise the frequency until the on-screen trace amplitude drops to 0.707 of its low-frequency
value. That number is the *software* display bandwidth and belongs in
[CUBEMX_CONFIG.md](CUBEMX_CONFIG.md) replacing the predicted 22.1 Hz.

## Stage F — ADC input range and the calibration constant

**Wiring.** Signal generator → PA0 through a 1 kΩ series resistor, common ground with the
board. Generator: 1 Hz triangle or sine, 0.5 Vpp offset to 1.65 V.
**Action.** Stream to the PC, record 10 s, inspect the `ecg_raw` column.
**Pass.** Codes sweep symmetrically about ~2040 for a 1.65 V input, and do not clip at
either end.
**Then re-derive the detector floor.** `ECG_THRESH_ABS_MIN` (currently 256) was chosen from
a synthetic 700-count R wave, which is a guess about a circuit that does not exist. Read the
`energy` value delivered by the STATUS packet while a beat-shaped input at a known rate is
applied, and set the floor between the loudest inter-beat energy and the quietest real peak.
`HARDWARE TEST PLAN` step: log energy, take max(inter-beat) × 2 and min(peak)/3, choose
inside that band.
**Diagnosis.** Codes pinned at 0 or 4095 → generator offset wrong, or the front-end output
swing exceeds 0–3.3 V (a real safety and range problem, not a software one).

## Stage G — is the sample rate really 1000 Hz

**Method 1 (scope).** Toggle a spare GPIO at the top of `App_Loop()` or in the DMA callback
and measure the period.
**Method 2 (software only, no instrument).** Record 30 s and count rows: the PC reconstructs
the time axis from `first_sample_index`, so `last - first + 1` over a measured wall-clock
interval is the true rate, and `DROP n blk` in STATUS counts any block the device could not
hand over in time.
**Pass.** 1000 ± 1 Hz with zero dropped blocks. A 30 s record should yield ~30000 samples
at 128-sample blocks: ~234 blocks.
**Diagnosis.** Rate slightly high/low → LSE accuracy (RTC-based timing is only as good as
the crystal). Dropped blocks → main-loop starvation; measure how long
`HAL_UART_Transmit` and the OLED flush take together, then reduce
`DIAG_STATUS_PERIOD_MS` or the UI frame rate.

## Stage H — the ECG analog front-end

**This is the stage that decides whether the algorithm assumptions are real.**
**Action.** With the finished front-end connected and **no person attached**, drive its
input from a signal generator producing a beat-like waveform (or use the PC demo stream as
the reference expectation). Record: output offset, output swing for a known input, and the
frequency response.
**Fill in.** `ECG_FRONTEND_GAIN`, `ECG_FRONTEND_OFFSET_MV`, set
`ECG_FRONTEND_PARAMS_VERIFIED` to 1.
**Pass.** The `mv` column matches a multimeter/scope at the same test point, and the R wave
occupies a useful fraction of the ADC range without clipping.
**Then re-run Stage F.** Gain changes the energy scale, and `ECG_THRESH_ABS_MIN` is on that
scale.
**Diagnosis.** R wave under 100 counts → gain too low, and the fixed detector floor will
miss beats; either raise front-end gain or lower the floor. Clipping → reduce gain.
Offset drifting → front-end bias network, not software.

## Stage I — heart rate on a real signal

**Action.** With a compliant front-end and a willing volunteer, compare the device's HR
against a reference they can produce (a counted radial pulse over 30 s is adequate for a
course project). Record 60 s at rest, then after gentle movement.
**Pass.** Agreement within the tolerance you choose to state publicly. **Do not write
"±2 bpm" until you have the data to support it**; the brief forbids claiming it.
**Also record.** Whether any R wave is missed or doubled, and the `SIGNAL POOR` rate during
motion.
**Diagnosis.** Consistently half the true rate → every other beat is falling below the
threshold: lower `ECG_THRESH_RATIO_PERCENT` from 40, or the front-end gain is marginal.
Double the rate → the T wave is being detected; `ECG_REFRACTORY_SAMPLES` (250 ms) is too
short for this waveform, or the QRS band is passing too much T-wave energy.

## Stage J — temperature calibration

**Action.** With the final sensor circuit built, record raw codes at two known, measured
temperatures (ice slush and body-warm water, with a reference thermometer) and fill in
`TEMP_SENSOR_MODEL` plus `TEMP_LINEAR_INTERCEPT_MV` / `TEMP_LINEAR_SLOPE_NV_PER_CENTI`,
or replace `TemperatureConvert()` with the real curve.
**Pass.** Device reading agrees with the reference within the tolerance you are willing to
publish, at both points, at 0.1 °C display resolution.
**Then re-run** the probe thresholds `TEMP_ADC_OPEN_THRESHOLD` / `TEMP_ADC_SHORT_THRESHOLD`
by actually opening and shorting the probe input and recording what the ADC sees.
**Until this is done** the firmware reports `TEMP_UNCALIBRATED` and the UI shows `--.-`,
which is correct behaviour, not a defect.

## Stage K — lead and probe disconnect behaviour

**Action.** With a real front-end: disconnect one electrode and observe. Disconnect the
temperature probe and observe.
**Pass.** Temperature probe removal reports `PROBE FAULT` after `TEMP_PROBE_FAULT_CONFIRM_WINDOWS`
consecutive fault windows (2 windows ≈ 500 ms), and clears after
`TEMP_PROBE_OK_CONFIRM_WINDOWS` consecutive good windows (4 windows ≈ 1000 ms). A single
outlier window never changes the indication in either direction.
**Lead-off:** unless the front-end provides an electrode-detection output that this
firmware is wired to, the device will report `SIGNAL POOR` or `UNKNOWN`, **not**
`DISCONNECTED`, and that is intended. Reporting a lead disconnection would require a
measurement that no part of this design makes. If the front-end later gains such an
output, it needs a CubeMX change to allocate an input, and only then should
`LEAD_DISCONNECTED` be produced.

## Stage L — the PC link

**Wiring.** USB-serial module to PA9 TX, PA10 RX, GND, common ground with the board.
**Action.** `python -m pc_monitor --port COMx --baud 230400`, then click Connect.
**Pass.** HELLO arrives (so the connection panel shows firmware and protocol version),
STATUS ticks at ~2 Hz, `uart_rx_packets` on the STATUS page increases when commands are
sent, and CRC errors stay at 0.
**Diagnosis.**
- Nothing at all → swap TX/RX (the classic), or the module is 5 V logic into a 3.3 V pin.
- Garbage then nothing → baud mismatch; check the frozen 230400.
- Occasional CRC errors → grounding/level problem; the parser resyncs by design, so the
  error counter is the diagnostic, not the stall.
- Commands accepted but no answers → the device only transmits from `App_Loop`; if it is
  wedged in a long blocking call, that is a real bug worth capturing with `DROP`/`CRC` counts.

## Stage M — 10 s continuous recording

**Action.** Start recording on the device (KEY_OK on the ECG page) and on the PC, capture
60 s, stop, export.
**Pass.** `last_index - first_index + 1` equals the row count (no gaps), dropped blocks is
0, CRC errors 0, the CSV has one row per millisecond of the session, and the waveform in
Excel matches the on-screen one.
**Also test.** Longest session the front-end can sustain, and what `DROP n blk` does under
UI-heavy load (the ANSWER tells you whether blocking UART TX plus blocking OLED flush leave
enough headroom; if not, the fix is a smaller UI rate, not a faster ADC).

## Stage N — Excel export

**Action.** Open the exported `.xlsx` in real Excel (not only in openpyxl).
**Pass.** It opens in reasonable time; `Data` has every row; `Summary` figures match; the
embedded chart renders a recognizable ECG and does not contain 100k points.
**Diagnosis.** Slow to open → chart series not decimated; check the decimation factor in
`pc_monitor/export.py`. Blank chart → the series range or anchor is wrong; openpyxl will
write a broken reference happily, which is exactly why the reopen-and-assert test exists,
and why this stage is still on the list.

## Stage O — RTC time continuity through reset and power loss

**When.** Run this immediately after Stage B; it needs no analog hardware, only a booting
board and either a debugger or the Stage L serial link.
**What is being separated.** These are distinct conditions that all look the same from the
screen:
- the RTC core is not counting (no LSE, or no VBAT);
- the RTC interface is not clocked: `RCC_BDCR` `RTCEN`. `HAL_RCCEx_PeriphCLKConfig()`
  writes only `RTCSEL`, and the HAL sets `RTCEN` later in `HAL_RTC_MspInit()`, so this is
  what a genuine cold start looks like before either has run — `rtc_clock_prepare()` does it
  on the pre-init path;
- no RTC clock source is selected at all (`RTCSEL == 0`), which the firmware treats as
  "do not touch an RTC register";
- `RSF` never arrived, so `CNTH`/`CNTL` are still the copies latched before this reset. A
  dead crystal and an unclocked interface are indistinguishable here, and separate only
  against `RCC_BDCR` `LSERDY`;
- the backup-register anchor is absent, torn, or disagrees with the counter.
The first four leave `s_counter_valid` clear, and the last one `s_anchor_available` clear;
every path ends at `rtc_service_is_valid() == false`, i.e. the clock reports `UNSET`.
**Action.** Set a known time, let it run 60 s, then (a) press NRST, (b) power off for 10 s
with VBAT applied, (c) power off with VBAT removed. Read the time after each.
**Pass.**
- (a) time is preserved to within a couple of seconds — the counter survived, the anchor
  reconstruction worked.
- (b) same, and that is the first real evidence that VBAT retention works on this board.
- (c) the clock reports `UNSET`, not a plausible wrong time. With the backup domain lost,
  the anchor is gone; guessing would be worse than admitting it.
- (d) **cold start**, i.e. after a deliberate backup-domain reset (`RCC_BDCR` `BDRST` from a
  debugger, or a board that has never had VBAT): the boot must complete with
  `s_sync_failed == 0`, no `DIAG_ERR_RTC_SYNC`, `RCC_BDCR` showing `RTCEN` and `RSF` both
  set, and a time the user then sets by hand must stick through (a). Before
  `rtc_clock_prepare()` this case reported a 1 s synchronisation timeout with a perfectly
  healthy crystal, because the bit that clocks the RTC interface is not written until
  `HAL_RTC_MspInit()`.
**Direct observation.** Halt and inspect `s_sync_failed`, `s_counter_valid`,
`s_anchor_available`, `s_saved_anchor` and `s_counter_at_boot` in `rtc_service.c`. Over
UART the STATUS packet shows only the consequences: the `rtc_valid` flag stays clear and
`protocol_errors` is one higher than at boot, because `diagnostics_note_error()` bumps that
counter and keeps the reason (`DIAG_ERR_RTC_SYNC`, code 201) in `last_error_code`, which no
screen renders in this build. Read the code with a debugger.
**Diagnosis.**
- `s_sync_failed == 1` after a normal reset → RSF never arrived, so `RTCCLK` is not
  running. Read `RCC->BDCR` first: `RTCEN` should already be 1 by the time this wait starts
  (a 0 there means the enable did not land, i.e. `DBP` was blocked), and `LSERDY` says
  whether the crystal itself is alive. Nothing was read from the counter in this state, by
  design.
- Anchor present but time jumps backwards → `RTC_EPOCH_MAX_SECOND` rejection did not fire,
  which would mean the counter did not actually reset; re-check with a debugger read of
  `RCC->BDCR` `BDRST` history and of `RTC->CNTH/CNTL`.
- Time is `UNSET` after (a) but survives (b) → the anchor write itself is being interrupted;
  check that `rtc_service_preserve()` really runs from `USER CODE BEGIN RTC_Init 0`.
**Not claimed.** None of this has been executed on silicon. The reconstruction logic is
`HOST VERIFIED` as a pure model, including a replay of every prefix of the real write
sequence; the register-level code around it is `BUILD VERIFIED` only.

---

## Recording what you found

Each completed stage should produce a commit that:
1. updates the corresponding row in [COURSE_REQUIREMENTS.md](COURSE_REQUIREMENTS.md) from
   `HARDWARE VERIFICATION PENDING` to a dated, measured result;
2. updates [PINMAP.md](PINMAP.md) / [CUBEMX_CONFIG.md](CUBEMX_CONFIG.md) where a predicted
   number is replaced by a measured one;
3. leaves the `UNVERIFIED` markers removed only where it is genuinely no longer
   unverified.

A stage that cannot be completed for lack of hardware should be written up as blocked,
not marked passed.
