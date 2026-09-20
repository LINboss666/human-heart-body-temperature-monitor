# Phase 1 final fix handoff

**English** · [中文](PHASE1_FINAL_HANDOFF.zh-CN.md)

The last software correction pass before hardware bring-up. Three subjects only: RTC
register synchronisation before any counter read, the backup-register anchor's behaviour
under a torn write, and the temperature probe fault/recovery response time. No feature
work, no general cleanup, no redesign of anything that already worked.

| | |
| --- | --- |
| Branch | `phase1/final-fixes` → **merged into `main`** once the GPT review of this branch was accepted (see the end of §7); no release tag |
| Starting commit | `282713b` (tip of `phase1/review-fixes`; that branch is unchanged) |
| Predecessors | `phase1/full-system` → `phase1/review-fixes` → this branch |
| Frozen baseline | tag `v0.1-baseline` → `eb8a795` (untouched) |
| Files changed | 19 — 11 of code and tests (8 under `App/`, 3 under `tests/host/`) plus 8 documentation files |
| Firmware build | `0 Error(s), 0 Warning(s)` (full `UV4 -j0 -r` rebuild, ARMCC V5.06 u5, `-O3`, warning level 2, nothing suppressed) |
| Footprint | `Code=38392 RO-data=3096 RW-data=380 ZI-data=7524` → flash 41868/65536 = **63.9 %**, RAM 7904/20480 = **38.6 %** |
| Host C tests | 6 binaries, **1050 assertions, 0 failures** (was 1004) |
| Python tests | **256 cases, 0 failures**, incl. headless GUI |
| Golden vectors | regenerated with **no diff** → wire format unchanged → `PROTOCOL_VERSION` stays **2** |
| CubeMX | `.ioc` unmodified, no regeneration, no manual edit outside an existing `USER CODE` region |
| Hardware verified | **None.** Unchanged by this pass; §7 says what that costs |

---

## 1. RTC pre-read synchronisation

**The claim, and whether it held.** The review said the code reads `RTC_CNTH`/`RTC_CNTL`
after a reset without re-acquiring the synchronisation flag, so it can read the shadow
register of the previous session. **True.** Root cause is structural, not a typo: the only
place that can read the counter before CubeMX destroys it is `USER CODE BEGIN RTC_Init 0`
(`Core/Src/rtc.c:33`), which runs *before* `MX_RTC_Init()` assigns `hrtc.Instance`, so
`HAL_RTC_*` — including `HAL_RTC_WaitForSynchro()` — cannot be used there at all. The one
place that does perform the wait is `HAL_RTC_Init()`, i.e. after the reset has already
happened.

**What STM32F1 actually needs.** The counter lives in the RTC core in the backup domain and
keeps running through NRST and through VDD removal with VBAT applied. What reset breaks is
the AHB-to-APB presentation of `CNTH`/`CNTL`: those are synchronised copies, and until `RSF`
is set again in `RTC_CRL` a read may return the value latched before the reset. A stale
counter makes the "how long was the supply off" delta wrong in either direction, **including
to zero**, which is the failure that looks like success.

**Fix.** `rtc_sync_before_read()` in `App/rtc_service/rtc_service.c` mirrors what
`HAL_RTC_WaitForSynchro()` does, on raw registers:

```c
CLEAR_BIT(RTC->CRL, RTC_FLAG_RSF);
while ((RTC->CRL & RTC_FLAG_RSF) == 0U) {
    if ((uint32_t)(HAL_GetTick() - started) > RTC_TIMEOUT_VALUE) return false;
}
```

* **Bounded.** `RTC_TIMEOUT_VALUE` is the HAL's own 1000 ms
  (`Drivers/STM32F1xx_HAL_Driver/Inc/stm32f1xx_hal_rtc.h:67`). Worst case, boot is one
  second later and the clock is reported unset. There is no unbounded spin on this path.
* **Explicit, not implied.** The result is carried in `bool s_counter_valid`; counter `0`
  is never treated as "valid, and also zero elapsed", because `s_counter_at_boot` is only
  sampled when the sync completed:
  `s_counter_at_boot = s_counter_valid ? hw_counter_raw() : 0U;`
* **Ordering enforced.** unlock backup interface → re-acquire `RSF` → read counter → read
  anchor. `preserve()` documents why the middle step cannot move.
* **Not swallowed.** The failure is sticky (`s_sync_failed`) and surfaces as
  `rtc_service_sync_failed()`, which `App_Init()` reports as `DIAG_ERR_RTC_SYNC` (201) —
  after `diagnostics_init()`, because `MX_RTC_Init()` runs earlier and would memset a note
  made before it.

**Consequence, and it is the intended one.** With no synchronised counter the elapsed
interval is unknown, so a stored epoch is at best hours stale and at worst unrelated to
this boot. `rtc_service_restore()` therefore rejects the reconstruction and leaves the clock
**invalid**; CubeMX's 2000-01-01 keeps running, the UI shows `UNSET`, and nothing is
presented as a timestamp. `rtc_service_init()` applies the same rule
(`s_valid = s_anchor_available && s_counter_valid`) so a valid anchor with an unreadable
counter cannot resurrect a clock either.

**No hardware claim.** Whether LSE starts, whether VBAT holds the counter, and whether this
wait ever actually completes on this board are all unmeasured. Stage O of
[`HARDWARE_TEST_PLAN.md`](HARDWARE_TEST_PLAN.md) is written for exactly that.

**Cold-start follow-up, added after the GPT review of this branch.** The review found that
the wait above can still never succeed on the *first* backup-domain power-up, and it is
right: `__HAL_RCC_RTC_CONFIG()` is `MODIFY_REG(RCC->BDCR, RCC_BDCR_RTCSEL, ...)`
(`stm32f1xx_hal_rcc.h:985`), so `HAL_RCCEx_PeriphCLKConfig()` selects a source without
enabling the peripheral. `RTCEN` is raised by `__HAL_RCC_RTC_ENABLE()`
(`stm32f1xx_hal_rcc.h:999`, a bit-band write to `RCC_BDCR_RTCEN_BB`), which the generated
code calls only from `HAL_RTC_MspInit()` — after `MX_RTC_Init()`, i.e. after this reading
would have had to happen. On a warm boot the bit is already set from the previous session
because it lives in the backup domain, which is why the defect is invisible until a genuine
cold start; there it costs a 1 s timeout and a `DIAG_ERR_RTC_SYNC` with a healthy crystal.
`rtc_service.c` previously asserted the opposite in its header comment, and that comment is
what the review caught.

The fix is sequencing only, in `rtc_clock_prepare()`: after `bkp_unlock()` (DBP is what
makes the BDCR write land at all), read `__HAL_RCC_GET_RTC_SOURCE()` — the HAL documents
`__HAL_RCC_RTC_ENABLE()` as usable "only after the RTC clock source was selected", so a zero
`RTCSEL` returns false and **no RTC register is touched**, not even the `RSF` clear — then
`__HAL_RCC_RTC_ENABLE()`. The write is idempotent, so the warm path is unaffected. `RTCSEL`
is not modified, the backup domain is not reset, and `s_counter_valid` / `s_sync_failed` /
the anchor semantics are unchanged: `s_counter_valid = rtc_clock_prepare() && rtc_sync_before_read()`.
No host test was added for this: the decision is two register bits behind a HAL macro, and
mocking `RCC` to assert `x != 0` would be a test that cannot fail. It is `STATIC REVIEWED`
against the macros above and `BUILD VERIFIED`, and Stage O (d) now tests the cold start on
silicon.

## 2. RTC backup-register anchor: torn writes and power loss

**Register budget, verified rather than assumed.** The part is `STM32F103xB`
(`MDK-ARM/*.uvprojx:337`) → `RTC_BKP_NUMBER 10`
(`Drivers/CMSIS/Device/ST/STM32F1xx/Include/stm32f103xb.h:860`), so `IS_RTC_BKP` resolves to
the `(BKP) <= RTC_BKP_NUMBER` branch and the `DR11…DR42` half of that macro is dead code for
this device. Ten 16-bit registers exist; **five** are used, four for payload and one for the
commit marker:

| Slot | Register | Contents |
| --- | --- | --- |
| `RTC_ANCHOR_W_COMMIT` | DR1 | `RTC_ANCHOR_COMMIT_VALID` (`0x2B1C`) or `…_BLANK` (`0x0000`) |
| `RTC_ANCHOR_W_EPOCH_LO/HI` | DR2, DR3 | epoch seconds, two 16-bit halves |
| `RTC_ANCHOR_W_COUNT_LO/HI` | DR4, DR5 | RTC counter at that instant, two 16-bit halves |

F1 backup registers are 16 bits wide, which is why each 32-bit field costs two of them.

**The claim, and whether it held.** The review said a write interrupted by power loss can
leave a new epoch against an old counter, which decodes as a *plausible, wrong* time rather
than as damage. **True, and worse than it sounds**: `rtc_anchor_restore()` cannot detect it,
because the mixture is arithmetically self-consistent — new epoch with an older counter
yields a small forward elapsed value and passes the plausibility check. A host test asserts
this explicitly (`test_anchor_update_is_transactional`: *"a payload written without touching
the commit word is a mixture"* — the image decodes, and `rtc_anchor_restore()` accepts it both
unchanged and with the counter ten seconds on) so nobody later "fixes" it by trusting the
delta. Detection is impossible after the fact; the only correct response is to make the
partial state undecodable, which is what the commit word is for.

**Fix — publish as a transaction.** `rtc_anchor_write()` (pure, in `rtc_calendar.c`) emits
six ordered steps: **blank the commit word, write the four payload words, write the commit
word valid last.** Any prefix of that sequence leaves the pair undecodable, so the worst an
interruption can cause is "no anchor", which is reported as *never set* — the same answer as
losing the backup domain entirely, and far better than a fabricated clock.
`anchor_write()` replays those steps and then **reads the pair back**; if it does not come
back as written, it blanks the commit word again rather than leaving a value that is not
really there. The read-back goes into a local: writing through the `const rtc_anchor_t *`
parameter would have overwritten the caller's anchor (ARMCC flagged that as error #167).

**Where the anchor is written, and what is never stored.** `rtc_service_poll()` only calls
`anchor_now()` when the software epoch and a hardware reading were *observed in the same
cycle* (`s_valid && anchored`). If the hardware could not be read that second, the previous
anchor is kept — it still describes the counter, which kept advancing, so the next boot
reconstructs the true elapsed time from it. When the clock was never deliberately set, the
commit word is blanked rather than a payload written and then invalidated: an old anchor
must not resurrect a time nobody chose.

**Dual-slot rejected, deliberately.** A/B slot rotation would let an interrupted write fall
back to the previous anchor, i.e. buy one second of accuracy at the cost of doubling the
register count, adding a slot selector that is itself torn-write-exposed, and re-introducing
the same window during the slot flip. The priority here was stated as *never accept a torn
anchor as valid*, and blank-before-payload-valid-last achieves that with one word of state.
The remaining exposure is documented rather than hidden: **losing the anchor loses the
clock**, so an outage that outlives the anchor yields `UNSET`, never a guess.

**Reconstruction rules** (`rtc_anchor_restore`, host-tested): reject NULL; reject an epoch
outside `RTC_EPOCH_MIN_SECOND…RTC_EPOCH_MAX_SECOND`; `elapsed = (uint32_t)(counter_now -
counter_ref)`, which is wrap-safe because unsigned modulo 2³² matches the hardware's own
wrap; reject any sum that would pass `RTC_EPOCH_MAX_SECOND` — that is the check which
catches a counter that reset while the backup domain survived, since the delta becomes
~4.29e9 s.

## 3. Temperature probe fault and recovery response

**The claim.** The review-fix pass had left the confirmation counters at one window each,
which is correct but slow to explain and, at the time, mislabelled as milliseconds. The
final pass retunes the counts, which the spec explicitly authorised, and names the unit
honestly. **ADC thresholds were not touched**: `TEMP_ADC_OPEN_THRESHOLD` and
`TEMP_ADC_SHORT_THRESHOLD` still carry their `/* UNVERIFIED */` markers, because nothing
about the analog front-end exists yet.

**Names are in windows, not fake milliseconds** (`App/config/temperature_calibration.h`):

```c
#define TEMP_PROBE_FAULT_CONFIRM_WINDOWS  2U
#define TEMP_PROBE_OK_CONFIRM_WINDOWS     4U
#define TEMP_UPDATE_PERIOD_MS  ((uint32_t)TEMP_AVERAGE_WINDOW * 1000U / (uint32_t)ADC_SAMPLE_RATE_HZ)
```

`TEMP_UPDATE_PERIOD_MS` is derived, not typed: 250 samples at 1 kHz = 250 ms.

| Response | Count | Time |
| --- | --- | --- |
| Open or shorted probe → `PROBE FAULT` latched | 2 consecutive fault windows | ≈ **500 ms** |
| Probe returns → fault cleared | 4 consecutive healthy windows | ≈ **1000 ms** |
| One isolated outlier window | 1 | changes nothing, either direction |

Asymmetric on purpose: an alarm must not fire on a single bad average, and clearing an alarm
should need more evidence than raising one. Two compile-time assertions remain — that the
update period is a whole number of milliseconds, and that it meets the ≤ 500 ms refresh
requirement; the third assertion became tautological once the windows were the definition and
was removed rather than left as decoration.

`tests/host/test_temperature.c:143` `test_probe_confirm_is_window_by_window` walks the
feed window by window and asserts: one fault window does not latch, the second does,
recovery needs all four, an alternating input never reaches either count, and each streak
resets on the opposite input. The previous test only asserted *eventually*, which is why it
passed against the wrong units.

## 4. Verification

Every number below was produced by the commands shown, on this branch, after the last edit.

| Category | Meaning | Applies to |
| --- | --- | --- |
| `HOST VERIFIED` | compiled and executed on a host compiler (clang via the `ziglang` wheel) | the pure anchor model in `rtc_calendar.c`, the temperature state machine, the protocol, the calendar |
| `BUILD VERIFIED` | compiles clean for the target, never executed | `rtc_service.c`, `app.c`, `diagnostics.c`, `temperature.c`'s HAL-facing glue |
| `STATIC REVIEWED` | read against the vendored HAL/register map, nothing ran | register counts, `RSF` semantics, call ordering |
| `HARDWARE VERIFIED` | measured on silicon | **nothing** |

```
# 1. firmware, from-scratch rebuild
MDK-ARM> /c/Keil_v5/UV4/UV4.exe -j0 -r "Human Heart and Body Temperature Monitor.uvprojx" -o final_rebuild.log
   → 0 Error(s), 0 Warning(s)   Code=38392 RO-data=3096 RW-data=380 ZI-data=7524

# 2. host C algorithms (1050 assertions)
pc_monitor/.venv/Scripts/python.exe tools/run_host_tests.py
   ecg pipeline 97 · oled font format 78 · protocol+crc16 624 · rtc calendar 189 ·
   temperature (uncalibrated) 50 · temperature (linear model) 12

# 3. PC side (256 cases, GUI included)
cd pc_monitor && QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest -q
   → 256 passed in 44.61s

# 4. golden vectors must not move
pc_monitor/.venv/Scripts/python.exe tools/gen_protocol_vectors.py
   → wrote tests/host/protocol_vectors.json (21 vectors, ECG_BATCH frame 69 bytes)
   → git diff on that file: EMPTY, so PROTOCOL_VERSION stays 2
```

`.ioc` is not in the diff; no vendored HAL or CMSIS file is in the diff; no generated `Core/`
file is in the diff. The only change touching the generated side of the project remains the
pre-existing `USER CODE BEGIN RTC_Init 0` / `RTC_Init 2` call sites.

## 5. Where the review prompts were wrong, incomplete, or right

* **The GPT review of this branch was right about the cold start**, and it was a real defect
  in what this pass had just written: the RSF wait was correctly bounded but placed before
  anything had clocked the RTC interface. §1 carries the follow-up. Its instruction to
  *verify the RTC clock source is selected* before enabling was the part worth keeping: the
  no-source case is not a timeout to wait out, so it now returns early without touching an
  RTC register at all.
* *"Verify exactly how many backup registers this device exposes"* was worth doing: the
  `IS_RTC_BKP` macro *looks* like it allows DR42, and on this part it does not. Five
  registers of ten is comfortably inside the limit, and DR6–DR10 remain free.
* A torn anchor **is** silently accepted if the commit word happens to survive as valid —
  detection after the fact is impossible, which is the whole reason the blank write goes
  first. My first version of the regression test asserted the opposite and had to be
  inverted to document the real hazard.
* *"Treat counter 0 as suspicious"* would have been wrong: 0 is a legitimate counter value
  at the very start of an epoch. The implemented rule is the opposite — an **unsynchronised
  read** is what makes the counter unusable, whatever it says.
* Speeding up probe confirmation below one average window is not achievable in this design
  without changing the decimation, which would break the ≤ 500 ms refresh arithmetic. The
  500 ms/1000 ms pair is the fastest that keeps the asymmetry.

## 6. Documentation updated in this pass

`docs/HARDWARE_TEST_PLAN.md` (Stage K timings corrected — they claimed ~125 ms/~250 ms from
a constant name that no longer exists; new **Stage O** covers RTC continuity through reset
and power loss and separates the three failure layers), `docs/COURSE_REQUIREMENTS.md` (probe
row, three-layer RTC rows, the new refuse-to-guess row, calendar count, footprint),
`docs/ARCHITECTURE.md` (module-map wording), `README.md` and `README.zh-CN.md` (footprint,
test counts, stage range, branch/chain, this document), and forward-pointing notes added to
`docs/PHASE1_REVIEW_HANDOFF.md` and `docs/PHASE1_REVIEW_FIX_HANDOFF.md` — both left as the
historical records they are rather than rewritten.

## 7. Software freeze

Phase 1 application software is frozen here, at the tip of `phase1/final-fixes`.

Still unknown after this pass, and not claimable:

1. **No hardware behaviour of any kind.** No LSE start, no VBAT retention, no panel pixel,
   no ADC input range, no human-body measurement, no real 1 kHz sustained-with-UI timing.
   This pass made the RTC's failure modes honest; it did not make any of them observed.
2. **`rtc_service.c` has never executed.** Its ordering is `STATIC REVIEWED` against
   `HAL_RTC_Init()`, `HAL_RTC_WaitForSynchro()`, `RTC_ReadTimeCounter()`,
   `__HAL_RCC_RTC_CONFIG()`, `__HAL_RCC_RTC_ENABLE()` and
   `HAL_RCCEx_PeriphCLKConfig()` — including the findings that a normal LSE boot does *not*
   reset the backup domain, since `HAL_RCCEx_PeriphCLKConfig` only triggers `BDRST` when the
   clock source actually changes, and that `RTCEN` is not raised anywhere before
   `HAL_RTC_MspInit()`. Stage A/B/O decide it; Stage O (d) is the cold start this pass fixed.
3. **Temperature is uncalibrated by design** and the open/short thresholds are guesses with
   a `UNVERIFIED` marker. The probe response times in §3 are correct for whatever thresholds
   Stage J ends up with.
4. **`last_error_code` is not on any screen.** Only `protocol_errors` crosses the wire, so
   the RTC sync failure is visible as a flag plus an incremented counter over UART, and as a
   symbol under a debugger.
5. **KK_UI's KEY_OK page-focus change from the previous pass is still unexecuted** — Stage C
   of the hardware plan tests both directions.

This pass was told to stop at a push: no merge, no moving `v0.1-baseline`, no Phase 2. The
merge was the reviewer's call to make, and after the cold-start defect above was confirmed
it was made — `phase1/final-fixes` at `8d5c3c9` is merged into `main` as a merge commit, with
the three review branches left in place as the record. `v0.1-baseline` still points at
Phase 0, and no release tag exists for Phase 1: what is on `main` is software that has never
met the hardware it is written for.

The next piece of work is hardware bring-up against
[`HARDWARE_TEST_PLAN.md`](HARDWARE_TEST_PLAN.md), starting at Stage A.
