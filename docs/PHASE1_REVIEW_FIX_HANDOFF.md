# Phase 1 review-fix handoff

**English** · [中文](PHASE1_REVIEW_FIX_HANDOFF.zh-CN.md)

| | |
| --- | --- |
| Branch | `phase1/review-fixes` (pushed; **not** merged to `main`) |
| Starting commit | `b10c07fc09629596e387c96c5f1424ad81392e20` |
| Review this | the branch tip. The six code fixes are `5f02b33..a805625`; `7993e1f` and `0b0adfc` after them change only documentation and one over-strict GUI assertion |
| Frozen baseline | tag `v0.1-baseline` → `eb8a795` (untouched) |
| Change set | 37 files, +1337 / −276, of which the six code+test fixes are 30 files |
| Firmware build | `0 Error(s), 0 Warning(s)` (full `-r` rebuild) |
| Footprint | `Code=38152 RO=3076 RW=380 ZI=7524` → flash 41608/65536 = **63.5 %**, RAM 7904/20480 = **38.6 %** |
| Host C tests | 6 binaries, **1004 assertions, 0 failures** (was 901) |
| Python tests | **256 cases, 0 failures** (was 251), incl. headless GUI |
| Golden vectors | regenerate with **no diff** → wire format unchanged → `PROTOCOL_VERSION` stays **2** |
| Hardware verified | **None.** Unchanged by this pass |

> **Superseded on three points by [`PHASE1_FINAL_HANDOFF.md`](PHASE1_FINAL_HANDOFF.md)**,
> which is the current head of the review chain: the footprint and host-test totals in the
> table above are the `phase1/review-fixes` numbers, and §1's temperature-probe timings
> ("31.25 s / 62.5 s", one window per count) describe the confirmation scheme this pass
> left in place, which the final pass deliberately retuned to 2 and 4 windows. Everything
> else here still describes the code as it stands.

Read [`PHASE1_REVIEW_HANDOFF.md`](PHASE1_REVIEW_HANDOFF.md) for the Phase 1 state
this pass started from. This document only records what the review found, whether
each claim held, and what changed.

---

## 1. Verdicts

Every claim was reproduced from source first. **Six of seven held; one held in its
code but not in its predicted consequence, and a more serious defect was sitting
behind it.**

| # | Claim | Verdict |
| --- | --- | --- |
| P0-1 | RTC loses elapsed VBAT time | **CONFIRMED** |
| P0-2 | ECG_BATCH temperature stays stale/zero | **CONFIRMED, and always zero** |
| P0-3 | Stale ECG page state makes KEY_OK fire on other pages | **CONFIRMED (stale state) / NOT REACHABLE (the consequence)** — the action never fired anywhere |
| P1-1 | Probe confirm counts are in updates, not ms | **CONFIRMED** |
| P1-2 | Signed-shift UB in the DSP | **CONFIRMED** at one site; audit found the rest already defined |
| P1-3 | OLED "PC stream" switch is dead | **CONFIRMED** |
| P1-4 | 20-tap boxcar documented at two different corners | **CONFIRMED**; generator was the cause |

### P0-1 — RTC power-off continuity

`preserve()` stored only the mirrored epoch. CubeMX's `HAL_RTC_SetTime()` then
overwrote the hardware counter with seconds-of-day, and `restore()` reinstated the
stale epoch. Every second the RTC counted on VBAT was discarded.

The claim understated the F1 situation. There is no persistent calendar at all:
`HAL_RTC_SetDate()` writes only `hrtc->DateToUpdate` (RAM), and
`HAL_RTC_GetTime()` folds whole days out of the counter back into that RAM. The raw
32-bit counter is therefore the *only* evidence across a power cycle — and it was
being read too late, or not at all.

**Fix.** Backup registers now hold an *anchor*: DR1 magic, DR2/DR3 epoch, DR4/DR5
counter, each 32-bit value split across two 16-bit F1 registers. `preserve()`
samples the raw counter directly through `RTC->CNTH/CNTL` (with the same
high/low/high re-read the HAL uses) before `hrtc.Instance` exists, and
`restore()` computes `epoch + (counter_now - counter_anchor)`. Unsigned subtraction
gives the counter's own 2³² wrap for free; a delta that would land past 2099 is
**refused**, which is how a counter reset under a surviving backup domain is caught
rather than reported as a date in the next century. Anchors are only rewritten at
instants where the pair is known coherent (immediately after a hardware read that
was adopted, or after a `hw_set()`); if the clock cannot be read, the previous
anchor is kept, since it still describes a counter that keeps advancing.

Requirements met: power-on no longer erases elapsed VBAT time; NRST preserves it;
backup-domain loss is detectable via the magic; first boot stays distinguishable;
no invented timestamp; 1970–2099 retained.

**Host tested** (`rtc_anchor_restore`, pure arithmetic in `rtc_calendar.c`):
same-boot progression, software reset, 1-hour and 36-hour outages, counter wrap,
leap-day crossing, new-year crossing, implausible-delta rejection, NULL refusal,
and the documented epoch ceiling checked against `rtc_to_epoch()` rather than
trusted as a literal. **`rtc_service.c` itself is BUILD VERIFIED only** — it needs
the HAL. VBAT retention is still unmeasured hardware; the fix makes the interval
recoverable *if* the part holds, and does not claim that it does.

### P0-2 — ECG_BATCH temperature

Worse than "stale". `send_ecg_batch()` read `s_temp_raw_latest` /
`s_temp_centi_latest`, whose only writer was `if (temp != NULL)` inside
`protocol_service_push_sample()` — and the only caller passed `NULL`. Both fields
were **zero in every frame ever constructed**, while `TEMP_STATUS` carried the
truth, so the live view looked correct and the record was not.

Currently masked by `TEMP_MODEL_UNCALIBRATED` blanking the degrees column. The
moment a probe is calibrated, every exported row would read **0.00 °C for a human
body** — a plausible number from nothing. It was silent because the trailer was a
set of *optional* arguments: `NULL` is a valid call that means "leave it stale".

**Fix.** The trailer now reads from the diagnostics snapshot — already the single
source for the STATUS packet and the OLED — with `temp_raw` added so the raw code
stays available even while uncalibrated. The nullable parameter is **removed**, so
the failure mode cannot be reintroduced. Once per acquisition block (128 ms) is
coarser than a 20 ms batch and irrelevant for a 250 ms-decimated channel.

Also found while here: the PC's `temp_valid_rows` counts *batches*, and the XLSX
Summary label read "Temperature samples valid" — understating by the batch size.
Renamed to `temp_valid_reports` / "Temperature reports valid".

**Tested:** `pkt_write_batch_tail()`/`pkt_read_batch_tail()` now apply `ECGT_*` in
one place; host tests cover round-trip, negative centi values, and never writing
outside `ECGP_TAIL`. Python tests prove each batch contributes its own temperature
to its own rows, a device-side stuck zero stays zero and is never turned into
degrees, calibrated and uncalibrated batches stay distinct, and the C-generated
golden vector survives into the recorder.

### P0-3 — ECG page state and KEY_OK

The mirrored `s_current_page` really was never cleared, and
`ui_app_on_ecg_page()` really did stay true after navigation. But the consequence
the review predicted — confirming a menu entry toggling recording — **could not
occur**: `handle_buttons()` required `ev.kind == BTN_EVT_SHORT`, and `buttons.c`
only ever pushed `PRESS`, `RELEASE` and `LONG`. `SHORT` was declared, documented in
the header as the application's action source, and emitted by no code path. The
condition was never true, so **KEY_OK never started or stopped anything, on any
page.** Both the benefit and the hazard were absent.

**Fix.** The action moved to `KK_UI_CustomOnInput`, which `KK_UI_DispatchInput()`
only calls while a `KK_UI_PAGE_CUSTOM` page has focus — the one component that
actually knows focus, rather than a copy of it. `KK_UI_CustomOnLeave` now clears the
mirror, so `ui_app_on_ecg_page()` survives only as a waveform-drawing
optimisation, and its remaining lag (KK_UI defers `OnLeave` until the slide
animation ends) is documented as harmless at `ui_app.h`.

`buttons.c`'s half-built event queue went with its only consumer. Debounce and the
raw key mask — all KK_UI's porting contract asks for — stay.

**Verification status: BUILD VERIFIED + STATIC REVIEWED, not host tested.**
`ui_app.c` needs KK_UI, KK_OLED and the HAL. The evidence for the fix is
`kk_ui.c:506-518` (dispatch switches on live page type; `CUSTOM` → `CustomInput`)
and `kk_ui_nav.c:44-50,133-139` (`leave_custom` set only when the outgoing route is
`CUSTOM`), both cited at file:line. The acceptance matrix in §4 is therefore
**not** machine-checked and is listed as a hardware-plan item.

### P1-1 — probe confirmation units

The streaks increment only after `temperature_feed()`'s window guard, so
`TEMP_PROBE_FAULT_CONFIRM 125` is 125 × 250 ms = **31.25 s**, not the "125 ms at
1 kHz" its comment claimed, and not "consecutive samples".

**Counts deliberately unchanged.** Re-tuning a threshold that has never seen the
hardware it describes, under cover of a comment fix, would swap a documented
mistake for an undocumented change — and the open/short thresholds themselves stay
**UNVERIFIED**.

`TEMP_UPDATE_PERIOD_MS` was a second hand-written `250U` in `app_config.h` sitting
beside `TEMP_AVERAGE_WINDOW 250U`; the duplication is gone and the period is
derived, with three compile-time assertions (whole milliseconds, ≤ 500 ms course
requirement, confirm product meaningful). Tests latch a fault on exactly the
CONFIRM-th update, assert one update earlier does not, and state the cost as
31.25 s.

### P1-2 — signed shifts

`d = ((bandlimited << 1) + s_deriv_x1 - s_deriv_x2) >> ECG_DERIV_SHIFT;` shifts a
signed value left — undefined in the C the module claims to obey, and the file's own
header states signed shifts are avoided because two compilers must agree. Every
other stage uses `idiv_pow2()`; this line was the exception.

Now `bandlimited * 2` then `idiv_pow2`. That moves negative intermediates by ≤1
count (flooring shift → truncating divide) before a squaring stage. **Measured
response is unchanged**: the detector reproduces 50→49, 60→60, 72→72, 95→95,
120→120, 150→150 with 97 checks passing and flatline still yielding no phantom
QRS.

Audited all shift sites, not just the reported one:
`remove_baseline`'s input is a 12-bit code widened through `uint16_t` — provably
non-negative with room to spare (changed to multiplication anyway); the squaring
`>>` runs after `|d|`; `box_run()` ends in `clamp_i16()`, which bounds the
derivative's four-term sum far inside int32 and makes the `-d` negation safe;
`ema_step()` shifts magnitudes only, bounded by the `0xFFFF` energy clamp.

### P1-3 — the PC-stream switch

`case EVT_STREAM_TOGGLE:` was an empty statement behind a comment claiming
`App_Loop` polls `v_stream`. It did not, and `ui_app_stream_switch()` had no
callers. The switch was a dead control.

`protocol_service` is now the single streaming state. The switch issues a real
command — and note KK_UI has already committed the new value through the binding
before the event is polled (`kk_ui_dialog.c:335-346`), so the handler reads the
*request*, not the old state. The UI re-reads the protocol's value every update, so
a PC `START_STREAM` is displayed rather than fought.

Documented conflict rule: **last explicit command wins**, one boolean. KEY_OK on the
ECG page still couples recording→streaming; the switch and the PC move streaming
alone, which is the legitimate "watch live without saving" mode.

### P1-4 — filter response documentation

`ECG_QRS_LP_M3DB_HZ 43.8` vs `ECG_COMB_50HZ_M3DB_HZ 22.1` for the same 20 taps at
the same fs — one had to be wrong. The generator was: it measured `h_qrs` (5 Hz
high-pass cascaded with the low-pass) against **its own gain at 1 Hz**, inside the
high-pass stop band where the cascade is down 14 dB. Arithmetically correct,
semantically meaningless, and then mislabelled as the low-pass corner.

Each figure now states its reference. Boxcar on its own against unity: **22.2 Hz**
(both sections, matching). The cascade is described as the band-pass it is: peak
11.2 Hz at −1.7 dB, −3 dB relative to peak at **3.8–26.3 Hz**. The prose claiming a
**5–44 Hz** QRS band was therefore also wrong — the cascade is down 17.7 dB at
44 Hz — and is corrected in `README.md`, `README.zh-CN.md` and `ecg_config.h`.

Runtime DSP untouched. A new host test drives the shipping filter and checks the
generated header against it: structure and fs pinned, the two 20-tap sections
forced to share one corner, the band required to stay under 40 Hz so the old claim
cannot return, measured gain within 0.9 dB of analytic MA(20) at 10/22/26/30 Hz,
and the null at FS/20. **Method note:** `ECG_NOTCH_OFF` selects a one-tap
passthrough, so a response measurement must use the 50 Hz comb — an earlier draft
of the test measured flatness and passed for the wrong reason.

---

## 2. Defects found that the review did not list

1. **`BTN_EVT_SHORT` never emitted** (P0-3 above). A declared, documented,
   compared-against event with no producer. Severity above the reported symptom:
   the primary start/stop control was entirely dead.
2. **`temp_valid_rows` counted batches but was exported as "Temperature samples
   valid"** — a number in the XLSX Summary that did not mean its label.
3. **`TEMP_UPDATE_PERIOD_MS` duplicated `TEMP_AVERAGE_WINDOW`** as an unchecked
   second literal in another header — the same duplicate-size-constant class the
   review asked to look for.
4. **A response test that could not fail.** Worth recording because it is the
   interesting kind: my first version measured the display path with the notch
   `OFF`, which is a one-tap passthrough, so the filter looked flat and every
   "it rolls off" assertion would have failed for the right reason while a
   "it is flat" assertion would have passed for the wrong one.

## 3. Things deliberately not done

- No `PROTOCOL_VERSION` bump. The 21 golden vectors regenerate byte-identical,
  including the `ecg_batch_*` ones, so the wire did not move.
- No CubeMX regeneration. Only `Core/Src/rtc.c`'s existing `USER CODE BEGIN
  RTC_Init 0` comment changed; no generated line outside a user region was touched
  in this pass.
- No probe-fault retuning (see P1-1).
- No vendored-file changes. KK_UI and KK_OLED are byte-for-byte as `UPSTREAM.md`
  records; `KK_UI_CurrentPageType()` was *not* called from application code because
  it is declared only in `src/kk_ui_internal.h`, and reaching into a private header
  is a worse dependency than the one-shot it would have saved.
- No change to `main.c`, the pin map, the `.ioc`, or Keil target settings.
- `tools/_restamp_golden.py` kept: it is the provenance for hand-written golden hex.

## 4. Acceptance matrix for P0-3, and its actual status

Requested: initial page ≠ ECG; navigate to ECG → predicate true; ECG→MAIN,
ECG→TEMP, ECG→SETTINGS → false; KEY_OK on RTC/settings pages must not toggle;
KEY_OK on ECG must still act.

All seven are **STATIC REVIEWED against KK_UI's dispatch code and BUILD VERIFIED,
but not host-tested**. `ui_app.c` cannot be compiled on the host without KK_UI +
KK_OLED + HAL stubs, and building that scaffolding was judged out of proportion to
the remaining risk. **Stage C** (*the three buttons*) and **Stage E** (*pixels, and
choosing the controller profile*) of
[`HARDWARE_TEST_PLAN.md`](HARDWARE_TEST_PLAN.md) cover it on a real panel with real
buttons; until then this is the one fix in this document whose behaviour has not
been executed. Add to Stage C: "start a recording with KEY_OK on the ECG page, then
navigate to SETTINGS and press KEY_OK again — the recording state must not change."

## 5. Remaining known weaknesses

Carried forward unchanged, since nothing here was addressed by this pass: partial
`ECG_BATCH` is never flushed on stop; blocking UART and I2C share the main loop;
ASCII-only single-font UI; the demo's 3 s `ACQUIRING` mirror; `looks_like_demo()`
as a heuristic; XLSX charts never opened in real Excel; no CubeMX regeneration has
been run since the RTC fix, so that is a prediction, not an observation.

New in this pass:

1. **The RTC anchor is ±1 s coherent, not exactly.** The epoch and counter halves of
   an anchor are read back-to-back rather than atomically, so reconstruction can be
   off by up to one second per power cycle. Bounded and non-accumulating. Not
   measured on hardware.
2. **`ui_app_on_ecg_page()` still lags by one page transition** (≤ KK_UI's
   `KK_UI_PAGE_MS`). Only a drawing optimisation depends on it now.
3. **`rtc_service.c` ordering is asserted by review, not by test.**
   `preserve()` before, `restore()` after, and the re-anchor inside `init()` all
   depend on where CubeMX placed the user regions.

## 6. Reproduce

```
# firmware: full rebuild, log must end "0 Error(s), 0 Warning(s)"
"C:\Keil_v5\UV4\UV4.exe" -j0 -r MDK-ARM/"Human Heart and Body Temperature Monitor".uvprojx -o build.log

# 1004 assertions across 6 host binaries
pc_monitor/.venv/Scripts/python.exe tools/run_host_tests.py

# 256 cases, headless GUI included
QT_QPA_PLATFORM=offscreen pc_monitor/.venv/Scripts/python.exe -m pytest pc_monitor/tests -q

# must rewrite tests/host/protocol_vectors.json with NO git diff
pc_monitor/.venv/Scripts/python.exe tools/gen_protocol_vectors.py

# regenerates App/config/ecg_filter_coeff.h; prints every measured figure
python tools/gen_ecg_filters.py
```

All four were run for this document; the results in the table at the top are from
those runs, not from inference.

## 7. Testing honesty

| Category | This pass |
| --- | --- |
| `HOST VERIFIED` | RTC anchor arithmetic, batch-tail accessors, probe-confirm timing in both units, MA(20) response of the shipping filter, temperature-per-batch propagation through the PC recorder, framing, CRC, calendar, font decode, CSV/XLSX export |
| `BUILD VERIFIED` | `rtc_service.c`, `protocol_service.c`, `app.c`, `ui_app.c`, `buttons.c`, `ecg_signal.c` — compile clean with 0 warnings |
| `STATIC REVIEWED` | P0-3 page-focus behaviour against KK_UI's dispatch; CubeMX-regeneration safety |
| `HARDWARE VERIFIED` | **None.** No board was connected. Nothing about VBAT retention, LSE start-up, OLED controller or address, real 1 kHz timing, front-end gain, or human measurement accuracy changed in this pass, and none of it may be claimed. |

---

PHASE 1 REVIEW FIX READY FOR GPT REVIEW
