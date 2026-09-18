# USART1 Binary Link Protocol — v1

Defined once in [`App/protocol/protocol.h`](../App/protocol/protocol.h). The PC
mirror is [`pc_monitor/protocol.py`](../pc_monitor/protocol.py) and
`pc_monitor/tests/test_protocol_vectors.py` fails if the two disagree, so this
document describes both.

Not a text protocol. There is no `printf("%d,%d\r\n")` anywhere on this link.

## Frame

All integers little-endian. No in-band escaping — the receiver resynchronises by
scanning for the magic pair, which is safe because the declared length is
validated before the CRC position is computed.

| Off | Size | Field | Notes |
| --- | --- | --- | --- |
| 0 | 1 | `magic0` | `0xA5` |
| 1 | 1 | `magic1` | `0x5A` |
| 2 | 1 | `version` | `0x02`; a mismatch is not a frame and the parser skips it |
| 3 | 1 | `type` | `pkt_type_t` |
| 4 | 2 | `sequence` | per-direction counter, wraps at 65535, starts at 0 |
| 6 | 2 | `length` | payload byte count, `0 … 64` |
| 8 | 4 | `device_ts_ms` | `HAL_GetTick()` at the moment the frame was built |
| 12 | N | `payload` | |
| 12+N | 2 | `crc16` | CRC-16/CCITT-FALSE over bytes `0 … 11+N` |

Frame size = `14 + N`. Maximum frame = 78 bytes.

### CRC

`width 16 · poly 0x1021 · init 0xFFFF · refin false · refout false · xorout 0x0000`
Check value for ASCII `123456789` is **`0x29B1`**. Implemented as a bitwise loop
rather than a table, deliberately: ~3.6 kB/s needs no lookup table to pay for
512 bytes of flash.

### Receiver states

`pkt_parse()` returns `PKT_OK`, `PKT_NEED_MORE`, `PKT_ERR_CRC`,
`PKT_ERR_VERSION`, `PKT_ERR_LENGTH`, and reports `consumed` — the number of
input bytes the caller may drop before retrying.

* A bad length or unknown version causes an **internal rescan** past the false
  magic; the caller sees `PKT_NEED_MORE` or a later valid frame, never a wedge.
* A CRC failure consumes exactly the 2 magic bytes, guaranteeing forward progress.
* A single trailing `0xA5` is **kept**, because it may be half of a magic that has
  not finished arriving. Verified by `test_protocol.c`.

## Packet types

### Device → host

| Type | Id | Purpose |
| --- | --- | --- |
| `PKT_HELLO` | `0x01` | identity and capabilities at boot |
| `PKT_STATUS` | `0x02` | diagnostics dump, 2 Hz |
| `PKT_ECG_BATCH` | `0x10` | the 1 kHz record, 20 samples per frame, 50 frames/s |
| `PKT_TEMP_STATUS` | `0x11` | temperature view, 2 Hz |
| `PKT_RTC_RESPONSE` | `0x20` | answer to `GET_RTC`, also sent after a successful `SET_RTC` |
| `PKT_PONG` | `0x31` | echoes the `PING` payload |
| `PKT_ACK` | `0x40` | accepted a host command |
| `PKT_NACK` | `0x41` | rejected a host command, carries a reason |

### Host → device

| Type | Id | Payload |
| --- | --- | --- |
| `PKT_START_STREAM` | `0x80` | none |
| `PKT_STOP_STREAM` | `0x81` | none |
| `PKT_SET_RTC` | `0x82` | 7-byte calendar |
| `PKT_GET_RTC` | `0x83` | none |
| `PKT_SET_CONFIG` | `0x84` | notch choice and alarm bands |
| `PKT_PING` | `0x90` | 4-byte opaque token, echoed |

`START_STREAM` / `STOP_STREAM` control **reporting**, not the ADC. The converter,
timer and DMA stay running from boot so that no hardware start/stop state machine
exists; see [ARCHITECTURE.md](ARCHITECTURE.md).

## Payload layouts

### `ECG_BATCH` (the record)

`n = sample_count`, `1 … 20`. Payload size = `7 + 2n + 8`.

| Off | Size | Field |
| --- | --- | --- |
| 0 | 1 | `sample_count` |
| 1 | 2 | `sample_period_us` = 1000 |
| 3 | 4 | `first_sample_index` — absolute index of this batch's first ECG sample |
| 7 | 2n | `ecg_raw[]` — **RAW 12-bit ADC codes**, unfiltered |
| 7+2n | 2 | `temp_raw` — latest temperature ADC code |
| 9+2n | 2 | `temp_centi` (i16) — only meaningful when the flags allow it |
| 11+2n | 1 | `hr_bpm`, 0 when not valid |
| 12+2n | 1 | `hr_state` |
| 13+2n | 2 | `flags` |

`first_sample_index` is what lets the PC rebuild an exact 1000 Hz time axis and
detect a gap: the next frame's index must be `first + n`. That is how a dropped
block is found from the record alone, rather than trusting a status counter.

With `n = 20`: payload 55, frame **69 bytes**.

`flags` is a full `u16` and all sixteen bits reach the host. It used to be one
byte short: `ECGP_TAIL` was the hand-counted literal `7U` while the field table
above needs `8`, and because `pkt_build()` computed the CRC over the same short
length the frame verified cleanly and `status_flags_t` bits 8…15 were simply
never transmitted. The constant is now derived (`ECGP_TAIL = ECGT_FLAGS + 2`) in
both `protocol.h` and `pc_monitor/protocol.py`, and
`tests/host/test_protocol.c::test_ecg_batch_tail` plus
`pc_monitor/tests/test_protocol_vectors.py` pin the 69-byte frame. That correction
is why the protocol version is `0x02`.

### `STATUS` (43 bytes payload, 57 byte frame)

Offsets are named `STP_*`: `adc_running`, `dma_blocks`, `dma_dropped`,
`ecg_samples`, `temp_valid`, `temp_centi`, `hr_bpm`, `hr_state`,
`oled_present`, `oled_addr`, `rtc_valid`, `uart_tx`, `uart_rx`,
`uart_crc_err`, `proto_err`, `flags`, `uptime_s`.

This is the same set of counters the OLED STATUS page shows, so a bench
observation and a PC observation cannot disagree.

### `HELLO` (12 bytes)

Firmware version triple, protocol version, `sample_rate_hz`,
`batch_max_samples`, `adc_bits`, capability bitmask:

| Bit | Name | Meaning when clear |
| --- | --- | --- |
| 0 | `CAP_TEMP_CALIBRATED` | temperature is `UNCALIBRATED`, degrees are not produced |
| 1 | `CAP_OLED_PRESENT` | no display answered the I2C scan |
| 2 | `CAP_LEAD_HW_DETECT` | **no lead-off hardware exists**, so `lead_state` stays `UNKNOWN` |
| 3 | `CAP_PROBE_HW_DETECT` | probe faults come from ADC range heuristics only |
| 4 | `CAP_FRONTEND_VERIFIED` | analog front-end gain/offset are assumptions |
| 5 | `CAP_RTC_BATTERY_BACKED` | VBAT retention has not been demonstrated |

The host must not render a capability it has not been given, and the defaults are
all clear until hardware says otherwise.

### `TEMP_STATUS` (8 bytes)

`temp_raw`, `temp_mv`, `temp_centi`, `temp_state`. Sent at 2 Hz independent of the
ECG stream, so a stalled ECG batch does not hide the temperature view.

### Calendar (`SET_RTC`, `RTC_RESPONSE`)

`year:u16, month, day, hour, minute, second` — 7 bytes, calendar fields rather
than an epoch so the human-readable intent is on the wire. `RTC_RESPONSE` appends
`epoch:u32`. The device validates every field, including month length and leap
years, and answers `NACK_BAD_VALUE` without changing the clock. Internally the
firmware converts to epoch seconds.

### `SET_CONFIG` (7 bytes)

`notch:u8` (0 = 50 Hz, 1 = 60 Hz, 2 = off), `hr_low`, `hr_high` (bpm),
`temp_low:16` and `temp_high:16` in centi-°C.

### `ACK` / `NACK`

`acked_type:u8`, `acked_sequence:u16`, and for `NACK` a `reason:u8` from
`nack_reason_t`.

## State flag word

| Bits | Name |
| --- | --- |
| 0–2 | `lead_state` — `UNKNOWN`/`CONNECTED`/`DISCONNECTED`/`SIGNAL_POOR` |
| 3–5 | `temp_state` — `OK`/`LOW`/`HIGH`/`PROBE_FAULT`/`UNCALIBRATED` |
| 6 | `hr_valid` |
| 7 | `recording` |
| 8 | `oled_present` |
| 9 | `adc_running` |
| 10 | `rtc_valid` |
| 11 | `dma_dropped` — sticky since last `STATUS` |
| 12–13 | active notch |
| 14 | `temp_uncalibrated` |

`lead_state` distinguishes `SIGNAL_POOR` from `LEAD_DISCONNECTED` on purpose.
Software that sees a flat line, a railed input or excessive noise reports
`SIGNAL_POOR`; it must never call that an electrode detachment, because without
injected-current or impedance hardware there is nothing measuring the electrode.
With `CAP_LEAD_HW_DETECT` clear, the honest default is `LEAD_UNKNOWN`.

## Bandwidth

At 230400 baud, 8N1 → 10 bits per byte → **23040 byte/s** of wire capacity.

| Packet | Frame | Rate | Byte/s |
| --- | --- | --- | --- |
| `ECG_BATCH` (n=20) | 69 | 50/s | 3450 |
| `STATUS` | 57 | 2/s | 114 |
| `TEMP_STATUS` | 22 | 2/s | 44 |
| **steady-state total** | | | **3608** |

**Utilisation 15.7 %** — a 6.4× margin. Worst case adds host commands and an
occasional `ACK`/`RTC_RESPONSE` burst, under 4 % more.

Per-second sample budget: 1000 ECG codes = 2000 byte/s of raw payload inside
3450 byte/s of `ECG_BATCH` frames, i.e. **1.45 bytes of overhead per sample**.

Blocking-time budget: a 69-byte frame takes `69 × 10 / 230400` = **3.0 ms**
inside `HAL_UART_Transmit`. At 50 frames/s the main loop spends ~15.0 % of its
time transmitting. The ADC path is unaffected because TIM3 triggers the ADC and
the DMA moves the results in hardware; the 512-byte double block gives 256 ms of
slack between completion interrupts, versus a worst-case multi-ms stall. Every
transmit is issued from `App_Loop()`, never from an ISR.

If 2.95 ms ever proves too coarse, the upgrade is TX via DMA1 (channel 6/7 for
USART1_TX), which is a CubeMX revision and deliberately not taken now.

## Sequence and loss

Sequence numbers are per-direction and per-type-independent; they exist so the PC
can say "packet 4711 of this stream never arrived", not for ordering — the link is
full-duplex point-to-point and arrives in order. The authoritative loss measure
is `first_sample_index` continuity in `ECG_BATCH`, because that also catches the
device dropping its own DMA blocks, which no sequence number would reveal.

## Versioning

`version` is a single byte for the framing, not for the payload schemas. Adding a
packet type is backwards compatible: a receiver that does not know a type skips it
by `length`. Changing a layout means bumping `PROTOCOL_VERSION` **and**
`pc_monitor/protocol.py`'s `PROTOCOL_VERSION`, and the vector test will fail if
only one side moves.
