# Phase 1 — independent review handoff

**English** · [中文](PHASE1_REVIEW_HANDOFF.zh-CN.md)

| | |
| --- | --- |
| Repository | https://github.com/LINboss666/human-heart-body-temperature-monitor (public) |
| Branch under review | `phase1/full-system` |
| Code HEAD | `9e3f435` — review everything up to and including this commit; this document is committed on top of it and changes no code |
| Phase 0 baseline | tag `v0.1-baseline` → commit `eb8a795` (**do not move**) |
| Change since baseline | 116 files, +24318 / −248 |
| Firmware build | `0 Error(s), 0 Warning(s)` — ARMCC V5.06 update5, `-O3`, warning level 2, **no warnings suppressed** |
| Footprint | superseded by `phase1/review-fixes`: `Code=38152 RO=3076 RW=380 ZI=7524` → flash **63.5 %**, RAM **38.6 %** |
| Host C tests | 6 binaries, **1004 assertions, 0 failures** (was 901 at this review) |
| Python tests | **256 cases, 0 failures** (incl. headless GUI; was 251 here) |
| Hardware verified | **None.** See §5 |

> **Historical.** This is the handoff *into* the review of `phase1/full-system`. The
> footprint and test counts above are no longer current, and §4's anchor description predates
> two later changes: the DR1 word is now a commit marker written blank-then-valid around the
> payload rather than a constant magic, and the counter read that feeds it is preceded by a
> bounded RSF re-acquisition. See
> [`PHASE1_REVIEW_FIX_HANDOFF.md`](PHASE1_REVIEW_FIX_HANDOFF.md) and
> [`PHASE1_FINAL_HANDOFF.md`](PHASE1_FINAL_HANDOFF.md) for the current state.

Read [`README.md`](../README.md) first for what the project claims, and
[`docs/COURSE_REQUIREMENTS.md`](COURSE_REQUIREMENTS.md) for the per-requirement
status table. This document is only about *where to look for problems*.

---

## 1. The one-sentence honesty position

Every algorithm in this repository has been executed and asserted on a host
compiler; **not one line has ever run on the target silicon**. Anything described
as `HOST VERIFIED` is a claim about arithmetic, not about a measurement.

## 2. Two deviations from the Phase 0 ground rules — both deliberate

Phase 0 said: never edit CubeMX-generated code outside `USER CODE` regions, and
never change the `.ioc`.

**(a) One line in `Core/Src/rtc.c` is outside a `USER CODE` region.**

```c
hrtc.Init.OutPut = RTC_OUTPUTSOURCE_NONE;
```

CubeMX had generated `RTC_OUTPUTSOURCE_ALARM` routed to the tamper pin, which is
not what anyone wanted and is not recoverable from inside a `USER CODE` block
because `HAL_RTC_Init()` consumes the struct. The matching change was made **in
the `.ioc` as well** — `VP_RTC_No_RTC_Output.Mode=RTC_OUT_NO` — so regeneration
reproduces it and the two remain consistent. Commit `488521b`.

Verify by regenerating in CubeMX 6.17.0 with FW_F1 V1.8.7 and diffing; expected
result is no diff to `Core/Src/rtc.c`.

**(b) `Mcu.PinsNb` went 18 → 19.** That is CubeMX re-indexing for a new *virtual*
pin plus the `Mcu.PinNN` keys shifting. **No physical pin was added or changed.**
[`PINMAP.md`](PINMAP.md) is still authoritative; check it against
`.ioc` → `Mcu.Pin*` and `*.ioc` signal assignments.

Everything else under `Core/` is untouched outside `USER CODE` regions. `main.c`
gained three lines (`#include "app.h"`, `App_Init()`, `App_Loop()`); `rtc.c`
gained the preserve/restore calls inside `RTC_Init 0` and `RTC_Init 2`.

## 3. Where the interesting bugs are likely to be

Ranked by how much damage they do if I got them wrong.

**(1) `App/protocol/` and `pc_monitor/protocol.py` — the wire contract.**
Already found once: `ECGP_TAIL` was the hand-counted `7U` while the field table
needs `8`, so `pkt_put_u16(&p[off + ECGT_FLAGS], flags)` wrote a byte past the
declared length and the CRC — computed over the same short length — happily passed.
Half of `status_flags_t` never reached the host. Fixed in `a2573ee`; the constant
is now *derived* on both sides. Look for other places a size is stated twice.

**(2) `App/ecg/ecg_signal.c` — integer fixed-point.**
The chain is derivative → square → leaky integrator → threshold → refractory, all
in integers. Two bug classes already bit here and both were silent:

* `acc -= acc >> k` **stalls** once `|acc| < 2^k` (floor of a small positive
  quotient is 0). It caused a permanent peak-hold, then froze the energy floor at
  779. Both were fixed with `ema_step()`, which uses ceil-magnitude decay.
  Grep for `>>` inside any accumulator and ask whether it can stall.
* Signed `>>` on a negative value is implementation-defined. Division by `2^k` is
  not. The remaining shifts should all be on non-negative operands — check that.

A Q14 biquad was replaced by integrators + boxcars after it measured a steady
**339-count output on a dead-flat input**: the half-LSB rounding term is amplified
~1024× by a denominator that is near zero at DC. If you see a filter with a small
denominator, look for rounding bias, not coefficient error.

**(3) Sampling timing.** TIM3 TRGO → ADC external trigger → DMA1 circular. No
timer ISR is enabled. Half/full callbacks only set a bitmask. The question worth
attacking: can `App_Loop()` fall behind 1 kHz? The ring is 512 half-words = 256 ms
of slack, but the UART transmit is **blocking** and a 69-byte frame takes 3.0 ms;
50/s is 15 % of the loop. With the OLED (blocking I2C at 400 kHz) in the same loop,
count the worst-case path yourself.

**(4) `App/rtc_service/rtc_service.c` — fighting CubeMX's clock reset.**
CubeMX calls `HAL_RTC_SetTime/SetDate` to 1970-01-01 unconditionally on every boot.
Since `hrtc.Instance` is still `NULL` at `RTC_Init 0`, the registers cannot be read
there through the HAL, so the service reads the raw backup-domain counter directly
and stores an **anchor**: BKP_DR1 magic `0x2B1C`, DR2/DR3 the epoch and DR4/DR5 the
counter, each as two 16-bit halves (F1 BKP registers are 16-bit, not 32-bit).
Elapsed time is then reconstructed from the counter delta instead of assumed to be
zero. `docs/PHASE1_REVIEW_FIX_HANDOFF.md` supersedes this paragraph's earlier
epoch-mirror-only description.

**(5) `App/display/oled_bus.c` — three controller profiles, all unverified.**
SSD1306 / SH1106 / CH1116 init tables and the 0x3C/0x3D address scan. We do not
know which panel arrives. Upstream's own porting rules forbid guessing, and an I2C
acknowledge proves only that *something* answers.

**(6) `App/ui/ui_app.c` — KK_UI page tables.**
The convention is `routes[0] = page ID`, and in `MENU_PAGE` the ref field is a page
**ID** while in every other row it is a table **index**. That asymmetry is
inherited from KK_UI and is the most likely place for a wrong-but-plausible
navigation target.

**(7) `Core/Src/main.c` `Error_Handler()`.**
Unchanged from the template: infinite loop with interrupts. Whether that is right
for a device attached to a person is a design question I have not answered.

## 4. What is *not* implemented, on purpose

* **Temperature is `UNCALIBRATED`.** `TEMP_SENSOR_MODEL` defaults to
  `TEMP_MODEL_UNCALIBRATED`, so `valid=false` and the UI shows `--.- °C`. The
  front-end is another member's design and is not in this repository. Two host
  test binaries cover both branches (uncalibrated, and a linear model injected
  with `-DTEMP_SENSOR_MODEL=TEMP_MODEL_LINEAR_MV`) so the calibration hook is
  known to work, not assumed to.
* **No lead-off detection.** There is no such hardware. The firmware reports
  `SIGNAL_POOR` or `LEAD_UNKNOWN` and never claims an electrode is disconnected.
* **Capability bits stay clear** for `LEAD_HW_DETECT`, `PROBE_HW_DETECT` and
  `RTC_BATTERY_BACKED`, so the PC cannot render a feature the board lacks.
* `ECG_FRONTEND_PARAMS_VERIFIED` is `0` and `ECG_FRONTEND_GAIN/OFFSET_MV` are
  placeholders. `ecg_pin_mv` in every export is a *pin* voltage.

## 5. Verification: what each claim rests on

| Claim | Evidence | Strength |
| --- | --- | --- |
| Compiles clean | `UV4 -j0 -b`, exit 0, log ends `0 Error(s), 0 Warning(s)` | Strong |
| Fits the part | Linker map sizes, arithmetic on 64 KB/20 KB | Strong |
| Framing + CRC agree between C and Python | 21 golden frames **generated by the shipping C** (`tools/gen_protocol_vectors.py` → `tests/host/protocol_vectors.json`) and replayed in pytest, incl. a stale-snapshot recompile check | Strong, host only |
| Calendar is right | C: hourly round-trip 1970→2099. Python: independently against `datetime`. Plus `pc_monitor/rtc.py` as a third implementation | Strong |
| Detector finds beats | Synthetic beats with 0.3 Hz wander + 50 Hz + noise: 50→49, 60→60, 72→72, 95→95, 120→120, 150→150 bpm | **Weak for real signals** |
| Font is decodable | The vendor's unmodified `kk_oled_font.c` run against our arrays on the host | Strong for format, silent on appearance |
| GUI works | `QT_QPA_PLATFORM=offscreen`, real widgets clicked, >1000 rows recorded and exported | Strong for wiring, **zero for looks** |
| Any measurement | none | **Unverified** |
| 1 kHz holds under UI+UART load | none | **Unverified** |
| RTC crystal starts / VBAT retention | none | **Unverified** |
| Any pixel appeared on a panel | none | **Unverified** |

Explicitly **not** claimed: ±2 bpm accuracy, medical-grade signal quality, any
diagnostic capability, or that the analog front-end is correct.

## 6. Known weaknesses I already see

1. **Partial batches are never flushed.** `send_ecg_batch()` fires only at 20
   samples, so `STOP_STREAM` mid-block can leave up to 19 samples unsent. The
   sample-index gap will show it; nothing recovers them.
2. **Blocking UART and blocking I2C in the main loop.** No priority analysis
   beyond the arithmetic in §3(3).
3. **`ui_fonts.c` is one 5×7 ASCII array aliased into three font slots.** Fine for
   a 128×64 demo, and `tools/gen_oled_fonts.py` exists to make real ones, but
   there is no CJK glyph coverage and no larger body face.
4. **The 3 s `ACQUIRING` window is a firmware constant mirrored by the demo
   device.** If the real settling behaviour differs, the demo lies about it.
5. **`describe_epoch()` / `looks_like_demo()` are heuristics**, and the latter can
   fire on a real board running firmware 0.0.0. It is only ever allowed to *add* a
   warning, never to remove one.
6. **XLSX charts were read back by `openpyxl`, never opened in Excel.**
7. **No `.ioc` regeneration has been run since the RTC fix** — §2(a) is my
   prediction of what it will do, not an observation.

## 7. Third-party code

| | |
| --- | --- |
| KK_UI | commit `582c3442ecbc539c1c82a342676b5b2eda69eee0`, MIT — vendored at `ThirdParty/kk_ui/` |
| KK_OLED | commit `f01831d63b1d426b629921edaba644732aa29223`, MIT — vendored at `ThirdParty/kk_oled/` |
| Modified files | **four total.** KK_UI: `include/kk_ui_config.h` → blocking refresh. KK_OLED: `driver/kk_oled_driver.{c,h}` → address, column offset and init table now come from `oled_bus`. Full diff and rationale: [`UPSTREAM.md`](UPSTREAM.md) |
| Fonts | Upstream's glyph *service* output is not covered by MIT, so the font arrays are authored in-repo by `tools/gen_oled_fonts.py`. No upstream glyph bytes are distributed. |

Licences were read **before** vendoring; both permit this redistribution.

## 8. How to reproduce the verification

```
# 1. firmware (Windows + Keil; the log must end "0 Error(s), 0 Warning(s)")
"C:\Keil_v5\UV4\UV4.exe" -j0 -b MDK-ARM/"Human Heart and Body Temperature Monitor".uvprojx -o build.log

# 2. Python environment -- no Keil licence needed for anything below this line
cd pc_monitor
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt

# 3. host C algorithms (run from the repo root; 1004 assertions, 6 binaries)
cd .. && pc_monitor/.venv/Scripts/python.exe tools/run_host_tests.py

# 4. golden frames: regenerates tests/host/protocol_vectors.json from the real C.
#    A committed-vs-regenerated diff is asserted by
#    pc_monitor/tests/test_protocol_vectors.py, which recompiles this itself.
pc_monitor/.venv/Scripts/python.exe tools/gen_protocol_vectors.py

# 5. PC tool, including the headless GUI group (251 cases)
QT_QPA_PLATFORM=offscreen pc_monitor/.venv/Scripts/python.exe -m pytest pc_monitor/tests -q
```

`tools/add_keil_sources.py` is **idempotent** and must be re-run after any CubeMX
regeneration: CubeMX owns the `.uvprojx` and drops hand-added groups.

Scratch note: `tools/_restamp_golden.py` exists only so the hand-written golden
hex in `pc_monitor/protocol.py` is reproducible rather than mysterious. It was run
once, when `PROTOCOL_VERSION` went 1 → 2.

## 9. What would make this review most useful

Attacked in this order, please:

1. **The integer signal chain in `ecg_signal.c`** — stall and rounding-bias bug
   classes are invisible to compilation and were each missed once already.
2. **Anything where a size is written down twice** — the `ECGP_TAIL` class of bug.
   Check `TEMPP_SIZE 8U /* 7 used */` and `RTCP_CAL_SIZE + RTC_RESPONSE_EXTRA` too.
3. **Whether the 1 kHz / blocking-I/O budget survives a full repaint**, since the
   measurement record and the display compete for the same loop.
4. **`rtc_service_preserve/restore` ordering** against a real `HAL_RTC_Init()`.
5. **Anything that could make a false clinical-looking statement reach a screen or
   a spreadsheet.** Treat an invented number as a defect even if the arithmetic is
   right.

Do not spend time on code style, missing features from the plan, or the fact that
hardware is untested — those are known and itemised in
[`HARDWARE_TEST_PLAN.md`](HARDWARE_TEST_PLAN.md).

---

## 10. Constraints honoured

No force push. `v0.1-baseline` not moved, no history deleted. No merge to `main`.
No GitHub token, Keil licence file or build log committed (`.gitignore` excludes
`*.log`, `*.htm` and the whole Keil output directory, which is where the Arm
licence serial lives). Keil memory regions and part number unchanged; no warnings
globally suppressed. Every `.ioc`-visible change has a matching `.ioc` edit.
Committed identity is the GitHub noreply address, not a personal email, because
this repository is public.

---

PHASE 1 READY FOR GPT CODE REVIEW
