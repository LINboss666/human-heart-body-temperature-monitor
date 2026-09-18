# Architecture — Phase 1

No RTOS, no heap, cooperative super-loop, on STM32F103C8T6 (64 KB flash / 20 KB RAM).

## Data flow

```
TIM3 (72 MHz, PSC 71, ARR 999)
  │  update event → TRGO, 1000 Hz, entirely in hardware
  ▼
ADC1 scan: rank1 = CH0/PA0 (ECG), rank2 = CH1/PA1 (temperature)
  │  each result → DR
  ▼
DMA1_Channel1, circular, half-word, memory increment   [1024 B ring = 2 blocks]
  │  half-transfer IRQ ─┐        full-transfer IRQ ─┐
  ▼                     ▼                           ▼
  ready mask 0x02       ready mask 0x01      (set flag + frame index only;
  │                                          nothing else happens in ISR)
  ▼
App_Loop()  ── acquisition_take_block() ──► 128 frames per block
  │
  ├─► ecg_signal_process(raw)
  │      ├── RAW      unfiltered code            → protocol, CSV, calibration hook
  │      ├── DISPLAY  ~0.62 Hz baseline removal → 20-tap comb (50 Hz null)
  │      └── QRS      5 Hz HP → MA(20) → derivative → square → 120 ms MWI
  │                └──► detect_qrs(): floor EMA, envelope, self-calibrating threshold
  ├─► ecg_hr_tick / ecg_hr_notify_beat  → median of last 5 RR → bpm + state
  ├─► temperature_feed(temp code)       → 250-sample average → raw/centi + state
  ├─► ui_app_push_waveform(display)     → 128 columns × min/max
  ├─► protocol_service_push_sample()    → ECG_BATCH every 20 samples
  ├─► buttons_scan()                    → KK_UI key bitmask + app-level events
  ├─► rtc_service_poll()                → epoch advance + 1 s hardware resync
  ├─► protocol_service_poll()           → RX parse, commands, periodic packets
  └─► ui_app_update()                   → KK_UI_Update + partial-area OLED flush
```

## Module map

| Path | Depends on HAL? | Responsibility |
| --- | --- | --- |
| `App/app.c` | yes | Super-loop, ordering, recording state, diagnostics refresh |
| `App/acquisition/` | yes | Start sequence, DMA ring, block handoff, overrun counting |
| `App/ecg/ecg_signal.c` | **no** | Filters and R-peak detection, pure integer |
| `App/ecg/ecg_hr.c` | **no** | RR history, median, HR state machine |
| `App/temperature/` | **no** | Decimation, probe heuristics, calibration hook |
| `App/rtc_service/rtc_calendar.c` | **no** | Epoch ↔ calendar |
| `App/rtc_service/rtc_service.c` | yes | RTC access, backup-register epoch mirror |
| `App/buttons/` | yes | Debounce, events, raw mask |
| `App/protocol/protocol.c`, `crc16.c` | **no** | Framing, CRC, resync |
| `App/protocol/protocol_service.c` | yes (tick only) | Batch building, command dispatch |
| `App/uart/uart_link.c` | yes | Polled RX ring, blocking short TX |
| `App/display/oled_bus.c` | yes (I2C probe) | Controller profiles, address scan |
| `App/ui/` | yes | KK_UI page table, custom waveform page, font data |
| `ThirdParty/kk_ui`, `ThirdParty/kk_oled` | driver only | UI library and graphics core |

The four modules marked **no** in the HAL column plus `rtc_calendar` and `temperature`
are exactly the ones compiled and executed on a host by `tests/host/`, which is why the
algorithms have real test coverage instead of review-only assurance.

## Rules the code actually follows

1. **No `malloc`, `calloc`, `realloc` or `free` anywhere** — including in the vendored
   libraries, verified by grep across both.
2. **No floating point in the signal chain.** All filter, detector and temperature
   arithmetic is integer; the only decimals are fixed-point-scaled integers (centi-degC,
   Q14 coefficients expressed as power-of-two shifts).
3. **ISRs do nothing but flag.** No filtering, no UART, no OLED, no float.
4. **No `HAL_Delay` in application logic.** The one `HAL_Delay(20)` inside the vendored
   OLED driver runs at boot before the sample chain is started.
5. **Generated files are edited only inside `USER CODE` regions.** All eight added lines
   in `Core/Src/main.c` and `Core/Src/rtc.c` are inside them.
6. **`.ioc` is the source of truth for configuration.** No generated setting was changed
   by hand; the RTC output change and the calendar reset both came from CubeMX.
7. **No unbounded loop in the main loop.** Block draining is bounded by the block count,
   RX pumping by a byte budget, OLED flush by the dirty region.
8. **Nothing reports a capability it does not have.** Lead-off hardware, probe hardware
   and VBAT retention are all advertised as absent in `HELLO`.

## Why no RTOS

The brief required it and the budget supports it, but the honest reason is narrower:
there are exactly two periodic activities (1 kHz sample production, ~25 Hz UI) plus
on-demand I/O, and the hardware already does the 1 kHz part with DMA. A scheduler would
add RAM for task stacks and context state, and would still need the same buffer
handoff, in exchange for removing the one thing that makes this auditable — a single
visible execution context in which every callback also runs.

## Known architectural weaknesses

1. **`HAL_UART_Transmit` is blocking**, up to ~3 ms for a full batch frame. It is in the
   main loop, never in an ISR, and 128 ms of DMA buffering absorbs it — but headroom is
   asserted by arithmetic, not measured, and Stage G/M on real hardware decide whether it
   holds. The escape hatch is TX on DMA1 channel 6/7, which needs a CubeMX revision.
2. **`KK_UI_REFRESH_MODE` is BLOCKING**, costing a second framebuffer's worth of
   concurrency (not memory — KK_OLED's two 1 KB buffers are hard-coded and still
   allocated). Chosen to avoid a CubeMX change for a display-only benefit.
3. **`HAL_GetTick()` wraps** at ~49.7 days. Every interval comparison uses
   `(uint32_t)(now - then)`, which is wrap-safe, but `rtc_service_get_timestamp()`
   adds elapsed seconds to a stored epoch and would be wrong across a wrap if no
   resync had occurred in between; the 1 s resync closes that in practice.
4. **One execution context is assumed by design**, so moving RX to an interrupt later
   requires revisiting the ring's `volatile` discipline, which is currently belt-and-braces
   rather than strictly necessary.
5. **The Keil groups are not owned by CubeMX.** A regeneration drops them and
   `tools/add_keil_sources.py` must be re-run.
