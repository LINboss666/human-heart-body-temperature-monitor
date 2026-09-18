"""Synthetic ECG / temperature source: the whole app, verifiable with no board.

=====================================================================
THIS PRODUCES FAKE DATA.  NOTHING HERE IS A MEASUREMENT.
=====================================================================

Three independent mechanisms keep it from being mistaken for a real record:

1. **A banner.**  :data:`DEMO_BANNER` is rendered full width at the top of the
   window, and :data:`DEMO_PORT_LABEL` is the "port" name in the connection
   panel, so every screenshot says DEMO.
2. **An out-of-band signature.**  The synthetic device reports firmware
   ``0.0.0`` and writes :data:`DEMO_SIGNATURE` into the two unnamed padding
   bytes at the end of the ``HELLO`` payload (``HELPP_SIZE`` is 12 but the named
   fields stop at byte 9).  :func:`looks_like_demo` checks it.  It is a *hint*,
   never the authority -- the authority is the explicit ``--demo`` flag, because
   those padding bytes are part of the section of the contract this module
   already flags as ambiguous.
3. **A per-row marker.**  Every recorded row carries
   ``origin == "DEMO (synthetic)"``, so a CSV/XLSX written from demo mode can
   never be re-read as if it came from hardware.

The waveform is a physiologically shaped P-QRS-T complex built from summed
Gaussians on a slowly varying beat clock (respiratory sinus arrhythmia), not a
sine wave: the QRS is ~60 ms wide and asymmetric, the T wave is broad and
follows it, and the P wave leads it.  A 1 Hz sine would teach nobody anything
about a QRS detector.

Like :mod:`pc_monitor.serial_worker`, this module imports no Qt: it is a byte
source with ``open/read/write/close``, which is exactly the interface the worker
thread drives.  That is deliberate -- the demo path shares the framing, the
parser and every counter with the hardware path, so "it works in demo" is real
evidence and not a private code path that can rot.
"""

from __future__ import annotations

import bisect
import math
import random
import time
from typing import Callable, Iterator

import numpy as np

from . import protocol
from . import rtc as rtc_module
from .protocol import Capability, HrState, LeadState, PacketType, TempState

__all__ = [
    "DEMO_BANNER",
    "DEMO_SHORT_BANNER",
    "DEMO_PORT_LABEL",
    "DEMO_SIGNATURE",
    "DEMO_ORIGIN",
    "SyntheticEcg",
    "DemoDevice",
    "DemoByteSource",
    "looks_like_demo",
]

DEMO_BANNER = "DEMO / SYNTHETIC DATA -- NOT FROM HARDWARE.  NOTHING ON SCREEN IS A MEASUREMENT."
DEMO_SHORT_BANNER = "DEMO / SYNTHETIC"
DEMO_PORT_LABEL = "DEMO (no hardware, synthetic ECG)"
DEMO_ORIGIN = "DEMO (synthetic)"
#: Written into HELLO payload bytes 10..11, which protocol.h leaves unnamed.
DEMO_SIGNATURE = 0xDE50

_STATUS_PERIOD_S = 0.5  # DIAG_STATUS_PERIOD_MS / TEMP_STATUS_PERIOD_MS in app_config.h
_ACQUIRING_SECONDS = 3.0  # the detector's settling window


# --------------------------------------------------------------- the waveform
class SyntheticEcg:
    """A morphologically shaped synthetic ECG, sampled at 1 kHz as ADC codes.

    Amplitudes are in *millivolts at the MCU pin*, because ``ecg_config.h``
    marks the front-end gain and offset UNVERIFIED: a synthetic "body-surface
    mV" would be a claim this project has not earned.  Mid-rail is 1650 mV,
    matching ``ECG_FRONTEND_OFFSET_MV``.

    ``seed`` fixes the noise, so a test can assert an exact waveform.
    """

    #: (offset from the R peak in seconds, Gaussian sigma, amplitude in mV)
    WAVES: tuple[tuple[float, float, float], ...] = (
        (-0.200, 0.0220, 0.045),  # P
        (-0.035, 0.0080, -0.090),  # Q
        (0.000, 0.0058, 0.720),  # R
        (0.045, 0.0092, -0.160),  # S
        (0.210, 0.0460, 0.150),  # T
        (0.420, 0.0650, 0.014),  # U, small and slow
    )
    #: The complex is only evaluated in this window around an R peak.
    WINDOW = (-0.45, 0.75)

    MID_RAIL_MV = 1650.0

    def __init__(
        self,
        *,
        sample_rate_hz: int = protocol.SAMPLE_RATE_HZ,
        target_hr: float = 72.0,
        rsa_amplitude_bpm: float = 4.5,
        noise_codes: float = 0.6,
        seed: int = 20260918,
    ) -> None:
        self.fs = int(sample_rate_hz)
        self.target_hr = float(target_hr)
        self.rsa_amplitude_bpm = float(rsa_amplitude_bpm)
        self.noise_codes = float(noise_codes)
        self.seed = int(seed)
        self._np_rng = np.random.default_rng(self.seed)
        self._beat_times: list[float] = []
        self._next_beat_t = 0.12  # the first R peak, 120 ms in
        self._generated_up_to = 0.0
        self._codes_written = 0

    # -- beat clock --------------------------------------------------------
    def instantaneous_hr(self, t: float) -> float:
        """Sinus arrhythmia plus a very slow drift: never a constant interval."""
        return (
            self.target_hr
            + self.rsa_amplitude_bpm * math.sin(2 * math.pi * 0.25 * t)
            + 2.0 * math.sin(2 * math.pi * 0.01 * t)
        )

    def _advance_beats(self, until: float) -> None:
        while self._next_beat_t <= until:
            self._beat_times.append(self._next_beat_t)
            hr = self.instantaneous_hr(self._next_beat_t)
            self._next_beat_t += 60.0 / max(hr, 20.0)
        # Drop beats that can no longer contribute (WINDOW spans ~1.2 s).
        horizon = until - 1.5
        if self._beat_times and self._beat_times[0] < horizon:
            keep_from = bisect.bisect_left(self._beat_times, horizon)
            if keep_from:
                del self._beat_times[:keep_from]

    # -- morphology --------------------------------------------------------
    @classmethod
    def complex_mv(cls, dt: np.ndarray) -> np.ndarray:
        """Pin millivolts of one beat family at offsets ``dt`` from an R peak."""
        lo, hi = cls.WINDOW
        inside = (dt >= lo) & (dt <= hi)
        if not inside.any():
            return np.zeros(dt.shape, dtype=np.float64)
        masked = np.where(inside, dt, 0.0)
        out = np.zeros(dt.shape, dtype=np.float64)
        for offset, sigma, amp in cls.WAVES:
            x = masked - offset
            out += np.where(inside, amp * np.exp(-0.5 * (x / sigma) ** 2), 0.0)
        return out

    def baseline_mv(self, t: np.ndarray) -> np.ndarray:
        """Slow wander and respiration -- the thing a baseline remover exists for."""
        return (
            0.030 * np.sin(2 * math.pi * 0.05 * t)
            + 0.012 * np.sin(2 * math.pi * 0.25 * t + 0.7)
            + 0.004 * np.sin(2 * math.pi * 1.7 * t)
        )

    # -- the entry point ---------------------------------------------------
    def codes(self, first_index: int, count: int) -> np.ndarray:
        """``count`` raw 12-bit ADC codes starting at absolute sample index."""
        if count <= 0:
            return np.empty(0, dtype=np.uint16)
        t = first_index / self.fs + np.arange(count, dtype=np.float64) / self.fs
        self._advance_beats(float(t[-1]))
        signal = np.full(count, self.MID_RAIL_MV, dtype=np.float64)
        for beat in self._beat_times:
            signal += self.complex_mv(t - beat)
        signal += self.baseline_mv(t)
        # A trace of 50 Hz mains: the notch choice is a device decision, and the
        # record is RAW and unfiltered by contract.
        signal += 0.008 * np.sin(2 * math.pi * 50.0 * t)
        codes = signal * protocol.ADC_FULL_SCALE_CODES / protocol.VDDA_MV
        codes = codes + self._np_rng.normal(0.0, self.noise_codes, size=count)
        codes = np.clip(np.rint(codes), 0, protocol.ADC_FULL_SCALE_CODES).astype(np.uint16)
        self._generated_up_to = float(t[-1])
        self._codes_written += count
        return codes

    @property
    def generated_codes(self) -> int:
        return self._codes_written

    def hr_bpm(self, t: float) -> int:
        return max(20, min(220, int(round(self.instantaneous_hr(t)))))

    def reset(self) -> None:
        self._beat_times.clear()
        self._next_beat_t = 0.12
        self._generated_up_to = 0.0
        self._codes_written = 0
        self._np_rng = np.random.default_rng(self.seed)


# ----------------------------------------------------------------- the device
class DemoDevice:
    """A fake monitor that speaks the frozen protocol.

    Cadence matches ``app_config.h``: 50 ``ECG_BATCH``/s of 20 samples, plus
    ``STATUS`` and ``TEMP_STATUS`` at 2 Hz.  :meth:`poll` returns whole encoded
    frames, so callers only ever deal with wire bytes.
    """

    BATCH_SAMPLES = protocol.ECG_BATCH_MAX_SAMPLES
    BATCH_PERIOD_S = BATCH_SAMPLES / protocol.SAMPLE_RATE_HZ  # 0.02 s

    def __init__(
        self,
        *,
        target_hr: float = 72.0,
        calibrated_temperature: bool = True,
        inject_dropped_batches: float = 0.002,
        inject_crc_errors: float = 0.001,
        seed: int = 20260918,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ecg = SyntheticEcg(target_hr=target_hr, seed=seed)
        self.fs = self.ecg.fs
        self.calibrated_temperature = bool(calibrated_temperature)
        self.gap_probability = float(inject_dropped_batches)
        self.crc_error_probability = float(inject_crc_errors)
        self._clock = clock
        self._rng = random.Random(seed ^ 0x5EED)
        self._rx_parser = protocol.StreamParser()
        self._uptime_start = self._clock()
        self._t0 = self._uptime_start
        self._sequence = 0
        self._next_batch_index = 0
        self._next_batch_at = 0.0
        self._next_status_at = _STATUS_PERIOD_S
        self._streaming = False
        self._hello_queued = True
        self.rtc_calendar: protocol.RtcCalendar | None = None
        self.rtc_error: str | None = None
        self.config: protocol.SetConfig | None = None
        self.commands_received = 0
        self.batches_sent = 0
        self.frames_corrupted = 0

    @property
    def is_demo(self) -> bool:
        return True

    @property
    def streaming(self) -> bool:
        return self._streaming

    # -- lifecycle ---------------------------------------------------------
    def connect(self) -> None:
        """(Re)start the device: boot-time HELLO, ADC already running."""
        self._t0 = self._clock()
        self._sequence = 0
        self._next_batch_index = 0
        self._next_batch_at = 0.0
        self._next_status_at = _STATUS_PERIOD_S
        self._streaming = False
        self._hello_queued = True
        self._rx_parser.reset()

    def _now(self) -> float:
        return max(0.0, self._clock() - self._t0)

    def _device_ts_ms(self) -> int:
        """``HAL_GetTick()``: uptime since boot, not since reconnect."""
        return int(max(0.0, self._clock() - self._uptime_start) * 1000) & 0xFFFFFFFF

    def _next_seq(self) -> int:
        seq = self._sequence
        self._sequence = (self._sequence + 1) & 0xFFFF
        return seq

    def _frame(self, type_: PacketType, payload: bytes) -> bytes:
        return protocol.build_frame(type_, self._next_seq(), self._device_ts_ms(), payload)

    def hello_frame(self) -> bytes:
        caps = Capability.OLED_PRESENT  # a display "answered the I2C scan"
        if self.calibrated_temperature:
            caps |= Capability.TEMP_CALIBRATED
        # Deliberately clear, exactly as docs/PROTOCOL.md describes the honest
        # default: CAP_LEAD_HW_DETECT, CAP_PROBE_HW_DETECT,
        # CAP_FRONTEND_VERIFIED, CAP_RTC_BATTERY_BACKED.
        payload = bytearray(
            protocol.Hello.encode(
                fw_major=0,
                fw_minor=0,
                fw_patch=0,  # 0.0.0 == "no board"
                proto_version=protocol.PROTOCOL_VERSION,
                sample_rate_hz=self.fs,
                batch_max_samples=self.BATCH_SAMPLES,
                adc_bits=12,
                caps=int(caps),
            )
        )
        if len(payload) >= 12:  # the unnamed padding, used as a DEMO signature
            payload[10] = DEMO_SIGNATURE & 0xFF
            payload[11] = (DEMO_SIGNATURE >> 8) & 0xFF
        return self._frame(PacketType.HELLO, bytes(payload))

    # -- cadence -----------------------------------------------------------
    def poll(self, now: float | None = None) -> list[bytes]:
        """Frames the device emits by device-uptime ``now`` (default: now)."""
        if now is None:
            now = self._now()
        out: list[bytes] = []
        if self._hello_queued:
            self._hello_queued = False
            out.append(self.hello_frame())
        guard = 0
        while self._streaming and now + 1e-9 >= self._next_batch_at and guard < 256:
            out.extend(self._emit_batch())
            self._next_batch_at += self.BATCH_PERIOD_S
            guard += 1
        while now + 1e-9 >= self._next_status_at and guard < 512:
            out.append(self._emit_status())
            out.append(self._emit_temp_status())
            self._next_status_at += _STATUS_PERIOD_S
            guard += 1
        return out

    def _flags(self) -> int:
        acquiring = self._next_batch_index < _ACQUIRING_SECONDS * self.fs
        return protocol.make_flags(
            lead=LeadState.UNKNOWN,  # the model has no lead-off hardware either
            temp=TempState.OK if self.calibrated_temperature else TempState.UNCALIBRATED,
            hr_valid=not acquiring,
            recording=self._streaming,
            oled_present=True,
            adc_running=True,
            rtc_valid=self.rtc_calendar is not None,
            dma_dropped=False,
            notch=protocol.NotchMode.HZ50,
            temp_uncalibrated=not self.calibrated_temperature,
        )

    def _emit_batch(self) -> list[bytes]:
        n = self.BATCH_SAMPLES
        # Fault injection 1: skip a whole batch, so first_sample_index -- the
        # loss measure docs/PROTOCOL.md calls authoritative -- shows a gap.
        if self.gap_probability and self._rng.random() < self.gap_probability:
            self._next_batch_index += n
            return []
        codes = self.ecg.codes(self._next_batch_index, n)
        mid_t = (self._next_batch_index + n / 2) / self.fs
        hr = self.ecg.hr_bpm(mid_t)
        acquiring = self._next_batch_index < _ACQUIRING_SECONDS * self.fs
        raw, centi = self.temperature(mid_t)
        payload = protocol.EcgBatch.encode(
            first_sample_index=self._next_batch_index,
            samples=[int(c) for c in codes],
            temp_raw=raw,
            temp_centi=centi,
            hr_bpm=0 if acquiring else hr,
            hr_state=HrState.ACQUIRING if acquiring else _hr_band(hr),
            flags=self._flags(),
        )
        self._next_batch_index += n
        self.batches_sent += 1
        frame = self._frame(PacketType.ECG_BATCH, payload)
        # Fault injection 2: damage a frame outright, exercising CRC error
        # accounting and the parser's resync, then carry on as a real link would.
        if self.crc_error_probability and self._rng.random() < self.crc_error_probability:
            frame = self._corrupt(frame)
            self.frames_corrupted += 1
        return [frame]

    @staticmethod
    def _corrupt(frame: bytes) -> bytes:
        broken = bytearray(frame)
        broken[protocol.HEADER_SIZE + 1] ^= 0x01
        return bytes(broken)

    # -- temperature -------------------------------------------------------
    def temperature(self, t: float) -> tuple[int, int]:
        """``(temp_raw code, temp_centi)`` with a plausible slow drift.

        ``raw`` maps monotonically from degC so the raw column and the degrees
        column tell the same story.  The real mapping lives in
        ``temperature_calibration.h`` and is UNVERIFIED; when calibration is
        switched off in the demo, ``temp_centi`` is reported as 0 and the state
        says UNCALIBRATED, which is exactly what the firmware contract requires.
        """
        celsius = (
            36.60
            + 0.15 * math.sin(2 * math.pi * t / 45.0)
            + 0.05 * math.sin(2 * math.pi * 0.25 * t)
        )
        centi = int(round(celsius * 100))
        raw = int(round(1500 + (centi - 3660) * 8))
        raw = max(0, min(protocol.ADC_FULL_SCALE_CODES, raw))
        if not self.calibrated_temperature:
            centi = 0
        return raw, centi

    # -- the independent 2 Hz views ---------------------------------------
    def _emit_status(self) -> bytes:
        t = self._now()
        _, centi = self.temperature(t)
        hr = self.ecg.hr_bpm(t)
        payload = protocol.StatusPacket.encode(
            adc_running=True,
            dma_blocks=int(t * 2),
            dma_dropped=0,
            ecg_samples=self._next_batch_index,
            temp_valid=self.calibrated_temperature,
            temp_centi=centi,
            hr_bpm=hr,
            hr_state=_hr_band(hr),
            oled_present=True,
            oled_addr=0x3C,
            rtc_valid=self.rtc_calendar is not None,
            uart_tx=self._next_batch_index * 3,
            uart_rx=0,
            uart_crc_err=0,
            proto_err=0,
            flags=self._flags(),
            uptime_s=int(t),
        )
        return self._frame(PacketType.STATUS, payload)

    def _emit_temp_status(self) -> bytes:
        t = self._now()
        raw, centi = self.temperature(t)
        state = TempState.OK if self.calibrated_temperature else TempState.UNCALIBRATED
        mv = int(round(raw * protocol.VDDA_MV / protocol.ADC_FULL_SCALE_CODES))
        return self._frame(PacketType.TEMP_STATUS, protocol.TempStatus.encode(raw, mv, centi, state))

    # -- host commands -----------------------------------------------------
    def handle_command(self, data: bytes) -> list[bytes]:
        """Consume host bytes and return the frames a device would answer with.

        The reason this exists in the demo is that the buttons on screen are
        wired to real commands: pressing START STREAM has to make a waveform
        appear, which only happens if something on the "device" side obeys it.
        """
        answers: list[bytes] = []
        for frame in self._rx_parser.feed(data):
            if not frame.is_host_command:
                continue  # a device ignores traffic that is not addressed to it
            self.commands_received += 1
            answers.extend(self._answer(frame))
        return answers

    def _answer(self, frame: protocol.Frame) -> list[bytes]:
        if frame.type == PacketType.START_STREAM:
            self._streaming = True
            self._next_batch_at = max(self._next_batch_at, self._now())
            return [self._ack(frame)]
        if frame.type == PacketType.STOP_STREAM:
            self._streaming = False
            return [self._ack(frame)]
        if frame.type == PacketType.GET_RTC:
            return [self._ack(frame), self._rtc_response()]
        if frame.type == PacketType.SET_RTC:
            return self._answer_set_rtc(frame)
        if frame.type == PacketType.SET_CONFIG:
            try:
                self.config = protocol.SetConfig.decode(frame.payload)
            except protocol.MalformedPayload as exc:
                self.rtc_error = str(exc)
                return [
                    self._frame(
                        PacketType.NACK,
                        protocol.Nack.encode(
                            frame.type, frame.sequence, protocol.NackReason.MALFORMED_LENGTH
                        ),
                    )
                ]
            return [self._ack(frame)]
        if frame.type == PacketType.PING:
            return [self._frame(PacketType.PONG, frame.payload)]
        return [
            self._frame(
                PacketType.NACK,
                protocol.Nack.encode(frame.type, frame.sequence, protocol.NackReason.UNSUPPORTED_TYPE),
            )
        ]

    def _answer_set_rtc(self, frame: protocol.Frame) -> list[bytes]:
        try:
            cal = protocol.RtcCalendar.decode(frame.payload)
            rtc_module.validate_calendar(
                cal.year, cal.month, cal.day, cal.hour, cal.minute, cal.second
            )
        except (protocol.MalformedPayload, ValueError) as exc:
            # "The device validates every field, including month length and leap
            # years, and answers NACK_BAD_VALUE without changing the clock."
            self.rtc_error = str(exc)
            return [
                self._frame(
                    PacketType.NACK,
                    protocol.Nack.encode(frame.type, frame.sequence, protocol.NackReason.BAD_VALUE),
                )
            ]
        self.rtc_calendar = cal
        self.rtc_error = None
        return [self._ack(frame), self._rtc_response()]

    def _rtc_response(self) -> bytes:
        cal = self.rtc_calendar or protocol.RtcCalendar(2026, 1, 1, 0, 0, 0)
        seconds = rtc_module.epoch_from_calendar(
            cal.year, cal.month, cal.day, cal.hour, cal.minute, cal.second
        )
        payload = protocol.RtcCalendar.encode(
            cal.year, cal.month, cal.day, cal.hour, cal.minute, cal.second, epoch=seconds
        )
        return self._frame(PacketType.RTC_RESPONSE, payload)

    def _ack(self, frame: protocol.Frame) -> bytes:
        return self._frame(PacketType.ACK, protocol.Ack.encode(frame.type, frame.sequence))


def _hr_band(hr: int) -> HrState:
    """``HR_BPM_LOW_DEFAULT`` / ``HR_BPM_HIGH_DEFAULT`` from app_config.h."""
    if hr < 60:
        return HrState.LOW
    if hr > 100:
        return HrState.HIGH
    return HrState.NORMAL


def looks_like_demo(hello: protocol.Hello, payload: bytes | None = None) -> bool:
    """Secondary, best-effort detection of the synthetic device.

    Never the authority -- ``--demo`` is.  A real board could coincidentally run
    firmware 0.0.0, so this can only ever *add* to the banner, never remove it.
    """
    if hello.fw_major == 0 and hello.fw_minor == 0 and hello.fw_patch == 0:
        return True
    if payload is not None and len(payload) >= 12:
        return protocol.get_u16(payload, 10) == DEMO_SIGNATURE
    return False


class DemoByteSource:
    """Adapts :class:`DemoDevice` to the ``open/read/write/close`` shape.

    ``read()`` hands back whatever the device has produced since the last read,
    chopped at a random byte count.  That detail is the whole point: a real
    serial read splits frames across calls, so the demo exercises the
    partial-frame and multi-frame paths of
    :class:`~pc_monitor.protocol.StreamParser` continuously, not only in the
    unit tests.
    """

    def __init__(self, device: DemoDevice | None = None, *, max_read: int = 256) -> None:
        self.device = device if device is not None else DemoDevice()
        self.name = DEMO_PORT_LABEL
        self.baudrate = protocol_uart_baud()
        self.timeout = 0.05
        self.is_open = False
        self._out = bytearray()
        self._max_read = max(16, int(max_read))
        self._rng = random.Random(0xD8E07D0)
        self._total_read = 0

    # -- the serial-compatible surface ------------------------------------
    def open(self) -> None:
        self.is_open = True
        self.device.connect()
        self._out += b"".join(self.device.poll())

    def write(self, data: bytes) -> int:
        if not self.is_open:
            raise OSError("demo source is not open")
        self._out += b"".join(self.device.handle_command(bytes(data)))
        return len(data)

    def read(self, size: int = 4096) -> bytes:
        """Return wire bytes, split the way a UART read would deliver them."""
        if not self.is_open:
            return b""
        self._out += b"".join(self.device.poll())
        available = len(self._out)
        if available == 0:
            time.sleep(self.timeout / 2)  # mirrors a serial read timeout, no busy spin
            return b""
        limit = min(int(size), self._max_read, available)
        take = self._rng.randint(max(1, limit // 3), limit)
        chunk = bytes(self._out[:take])
        del self._out[:take]
        self._total_read += take
        return chunk

    def close(self) -> None:
        self.is_open = False

    @property
    def in_waiting(self) -> int:
        return len(self._out)

    def reset_input_buffer(self) -> None:
        self._out.clear()

    @property
    def total_read(self) -> int:
        return self._total_read


def protocol_uart_baud() -> int:
    """UART_BAUD_RATE from app_config.h, for display consistency only."""
    return 230400


def iter_demo_frames(device: DemoDevice, seconds: float, step_s: float = 0.02) -> Iterator[bytes]:
    """Drain ``device`` for ``seconds`` of simulated time: a test/CLI helper."""
    start = device._now()
    while device._now() - start < seconds:
        for frame in device.poll():
            yield frame
        time.sleep(step_s)
