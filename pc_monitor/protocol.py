"""Byte-for-byte Python mirror of ``App/protocol/protocol.h``.

The C header is the single definition of the wire format (see
``docs/PROTOCOL.md``); this module is its PC-side mirror.  Every offset constant
here keeps the ``ECGP_*`` / ``ECGT_*`` / ``STP_*`` / ``HELPP_*`` / ``RTCP_*`` /
``CFGP_*`` / ``ACKP_*`` / ``NACKP_*`` / ``SFLAG_*`` spelling of the C side on
purpose, so a reviewer can diff the two files field by field.

Frame (all multi-byte fields little-endian)::

    off  size  field
    0    1     magic0   0xA5
    1    1     magic1   0x5A
    2    1     version  PROTOCOL_VERSION
    3    1     type     PacketType
    4    2     sequence
    6    2     payload length N   (0 .. MAX_PAYLOAD)
    8    4     device_ts_ms       HAL_GetTick() when the frame was built
    12   N     payload
    12+N 2     CRC-16/CCITT-FALSE over bytes 0 .. 11+N

Total frame size ``14 + N``; there is no in-band escape, so the receiver resyncs
by scanning for the magic pair.

==============================================================================
PROTOCOL REVISION 2 -- the ECG_BATCH tail
==============================================================================
Writing this mirror surfaced a real firmware defect.  ``ECG_BATCH``'s fixed tail
is ``temp_raw:u16, temp_centi:i16, hr_bpm:u8, hr_state:u8, flags:u16`` at tail
offsets 0, 2, 4, 5 and 6, so it is **8** bytes wide -- but ``ECGP_TAIL`` was the
hand-counted literal ``7U``.  ``send_ecg_batch()`` computed its CRC over the same
short length, so every frame verified cleanly while the flag word's high byte
(``SFLAG_RECORDING``, ``SFLAG_OLED``, ``SFLAG_ADC_RUNNING``, ``SFLAG_RTC_VALID``,
``SFLAG_DMA_DROPPED``, ``SFLAG_NOTCH_*``, ``SFLAG_TEMP_UNCALIB``) was never
transmitted.  Fixed in ``App/protocol/protocol.h`` by deriving the constant from
the field table::

    #define ECGP_TAIL  (ECGT_FLAGS + 2U)

Consequences of the correction, all reflected here:

* ``ECG_BATCH`` payload is ``15 + 2n``; at n = 20 that is payload 55, frame **69**.
* Bandwidth is 50*69 + 2*57 + 2*22 = 3608 byte/s of a 23040 byte/s line = 15.7 %.
* There is exactly one tail width, so this module has no lenient/strict decode
  split, no ``flags_complete`` flag, and no need to recover flag bits from
  ``STATUS`` -- each batch now carries its own complete word.
* ``tests/test_protocol.py::TestEcgBatchTail`` pins the 69-byte frame, and the
  mirrored C check is ``tests/host/test_protocol.c::test_ecg_batch_tail``.
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass, field, replace
from enum import IntEnum, IntFlag
from typing import Iterable, Iterator, Sequence

__all__ = [
    "PROTOCOL_VERSION",
    "MAGIC0",
    "MAGIC1",
    "HEADER_SIZE",
    "CRC_SIZE",
    "OVERHEAD",
    "MAX_PAYLOAD",
    "MAX_FRAME",
    "CRC16_INIT",
    "CRC16_POLY",
    "CRC16_CHECK",
    "crc16_ccitt",
    "crc16_ccitt_buf",
    "encode_frame",
    "build_frame",
    "parse_frame",
    "ParseResult",
    "Frame",
    "IncrementalParser",
    "StreamParser",
    "PacketType",
    "LeadState",
    "HrState",
    "TempState",
    "NackReason",
    "NotchMode",
    "Capability",
    "Flags",
    "split_flags",
    "make_flags",
    "hr_state_name",
    "lead_state_name",
    "temp_state_name",
    "packet_type_name",
    "Hello",
    "StatusPacket",
    "EcgBatch",
    "TempStatus",
    "RtcCalendar",
    "SetConfig",
    "Ack",
    "Nack",
    "Pong",
    "MalformedPayload",
    "StreamTracker",
    "TrackEvent",
    "TrackKind",
    "decode_frame",
    "decode_hello",
    "decode_status",
    "decode_temp_status",
    "decode_ecg_batch",
    "decode_rtc_response",
    "decode_ack",
    "decode_nack",
    "decode_pong",
    "SequenceCounter",
    "encode_start_stream",
    "encode_stop_stream",
    "encode_set_rtc",
    "encode_get_rtc",
    "encode_set_config",
    "encode_ping",
    "GOLDEN_FRAMES",
    "golden_by_name",
    "iter_golden",
    "SAMPLE_RATE_HZ",
    "SAMPLE_PERIOD_US",
    "VDDA_MV",
    "ADC_FULL_SCALE_CODES",
    "ECG_BATCH_MAX_SAMPLES",
    "ECG_BATCH_TAIL_SIZE",
    "ECG_BATCH_PAYLOAD_BASE",
    "ecg_batch_payload_len",
    "ecg_raw_to_mv",
    "centi_to_c",
]

# --------------------------------------------------------------- frame limits
# protocol.h / app_config.h: PROTOCOL_HEADER_SIZE, PROTOCOL_CRC_SIZE,
# PROTOCOL_MAX_PAYLOAD, PROTOCOL_MAX_FRAME.

PROTOCOL_VERSION = 0x02

MAGIC0 = 0xA5
MAGIC1 = 0x5A

HEADER_SIZE = 12  # PKT_HEADER_SIZE
CRC_SIZE = 2  # PKT_CRC_SIZE
OVERHEAD = HEADER_SIZE + CRC_SIZE  # PKT_OVERHEAD == 14
MAX_PAYLOAD = 64  # PKT_MAX_PAYLOAD
MAX_FRAME = HEADER_SIZE + MAX_PAYLOAD + CRC_SIZE  # 78

# ------------------------------------------------------------- sample domain
# app_config.h: ADC_SAMPLE_RATE_HZ, ADC_SAMPLE_PERIOD_US, VDDA_MV,
# ADC_FULL_SCALE_CODES, ECG_BATCH_MAX_SAMPLES.

SAMPLE_RATE_HZ = 1000
SAMPLE_PERIOD_US = 1000
VDDA_MV = 3300
ADC_FULL_SCALE_CODES = 4095
ECG_BATCH_MAX_SAMPLES = 20


def ecg_raw_to_mv(raw: int) -> float:
    """ADC code -> millivolts *at the MCU pin*: ``raw * 3300 / 4095``.

    NOT a body-surface potential.  ``ecg_config.h`` marks the front-end gain and
    offset UNVERIFIED, so pin millivolts are the most honest physical unit the
    wire carries.
    """
    return raw * VDDA_MV / ADC_FULL_SCALE_CODES


def centi_to_c(centi: int) -> float:
    """centi-degC -> degC (``3657`` -> ``36.57``)."""
    return centi / 100.0


# ----------------------------------------------------------------------- CRC16
# crc16.c: width 16, poly 0x1021, init 0xFFFF, refin/refout false,
# xorout 0x0000.  Check vector ASCII "123456789" == 0x29B1.

CRC16_INIT = 0xFFFF
CRC16_POLY = 0x1021
CRC16_CHECK = 0x29B1


def crc16_ccitt(data: bytes | bytearray | memoryview, crc: int = CRC16_INIT) -> int:
    """CRC-16/CCITT-FALSE, mirroring ``crc16_ccitt()`` statement for statement.

    Bitwise rather than table driven, exactly like the firmware: identical
    arithmetic, so a disagreement between the two sides is a real bug and not an
    accident of implementation.
    """
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ CRC16_POLY) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc & 0xFFFF


def crc16_ccitt_buf(data: bytes | bytearray | memoryview) -> int:
    """``crc16_ccitt_buf()``: CRC over a whole buffer from the initial value."""
    return crc16_ccitt(data, CRC16_INIT)


# ------------------------------------------------------------ field primitives
# protocol.c: pkt_put_u16 / pkt_put_u32 / pkt_put_i16 / pkt_get_u16 / ...

_LE = struct.Struct("<")


def get_u8(buf: Sequence[int], at: int) -> int:
    return buf[at]


def get_u16(buf: Sequence[int], at: int) -> int:
    """``pkt_get_u16`` -- little-endian, two bytes."""
    return buf[at] | (buf[at + 1] << 8)


def get_u32(buf: Sequence[int], at: int) -> int:
    """``pkt_get_u32`` -- little-endian, four bytes."""
    return buf[at] | (buf[at + 1] << 8) | (buf[at + 2] << 16) | (buf[at + 3] << 24)


def get_i16(buf: Sequence[int], at: int) -> int:
    """``pkt_get_i16`` -- signed little-endian 16-bit."""
    value = get_u16(buf, at)
    return value - 0x10000 if value & 0x8000 else value


def put_u16(value: int) -> bytes:
    return bytes((value & 0xFF, (value >> 8) & 0xFF))


def put_u32(value: int) -> bytes:
    return bytes((value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF, (value >> 24) & 0xFF))


def put_i16(value: int) -> bytes:
    return put_u16(value & 0xFFFF)


# ------------------------------------------------------------- packet types
# protocol.h: pkt_type_t.

class PacketType(IntEnum):
    # device -> host
    HELLO = 0x01
    STATUS = 0x02
    ECG_BATCH = 0x10
    TEMP_STATUS = 0x11
    RTC_RESPONSE = 0x20
    PONG = 0x31
    ACK = 0x40
    NACK = 0x41
    # host -> device
    START_STREAM = 0x80
    STOP_STREAM = 0x81
    SET_RTC = 0x82
    GET_RTC = 0x83
    SET_CONFIG = 0x84
    PING = 0x90

    @property
    def is_from_device(self) -> bool:
        return int(self) < 0x80

    @property
    def label(self) -> str:
        return self.name


UNKNOWN_PACKET = PacketType.PONG  # placeholder, never used for dispatch


def packet_type_name(value: int) -> str:
    """Name for a type id, tolerating types this build does not know.

    ``docs/PROTOCOL.md``: "Adding a packet type is backwards compatible: a
    receiver that does not know a type skips it by length."
    """
    try:
        return PacketType(value).name
    except ValueError:
        return "UNKNOWN(0x%02X)" % value


# --------------------------------------------------------------- enumerations

class LeadState(IntEnum):
    """``lead_state_t``.

    ``LEAD_UNKNOWN`` is the honest default: with ``CAP_LEAD_HW_DETECT`` clear
    there is no lead-off hardware, so the host must not turn it into an
    electrode verdict -- and must not render it the same as ``DISCONNECTED``.
    """

    UNKNOWN = 0
    CONNECTED = 1
    DISCONNECTED = 2
    SIGNAL_POOR = 3  # software judgement, NOT an electrode verdict

    @property
    def text(self) -> str:
        return {
            LeadState.UNKNOWN: "UNKNOWN",
            LeadState.CONNECTED: "CONNECTED",
            LeadState.DISCONNECTED: "DISCONNECTED",
            LeadState.SIGNAL_POOR: "SIGNAL POOR",
        }[self]


class HrState(IntEnum):
    """``hr_state_t``."""

    INVALID = 0
    ACQUIRING = 1
    NORMAL = 2
    LOW = 3
    HIGH = 4

    @property
    def text(self) -> str:
        return self.name


class TempState(IntEnum):
    """``temp_state_t``."""

    OK = 0
    LOW = 1
    HIGH = 2
    PROBE_FAULT = 3
    UNCALIBRATED = 4

    @property
    def text(self) -> str:
        return "NO PROBE" if self is TempState.PROBE_FAULT else self.name


class NackReason(IntEnum):
    """``nack_reason_t``."""

    NONE = 0
    BAD_CRC = 1
    UNSUPPORTED_TYPE = 2
    MALFORMED_LENGTH = 3
    BAD_VALUE = 4
    BUSY = 5

    @property
    def text(self) -> str:
        return self.name.replace("_", " ").title()


class NotchMode(IntEnum):
    """``ecg_notch_t`` as carried by ``SET_CONFIG`` and ``SFLAG_NOTCH``."""

    HZ50 = 0
    HZ60 = 1
    OFF = 2

    @property
    def text(self) -> str:
        return {NotchMode.HZ50: "50 Hz", NotchMode.HZ60: "60 Hz", NotchMode.OFF: "off"}[self]


class Capability(IntFlag):
    """``CAP_*`` bits of the ``HELLO`` capability word.

    Every default is clear until hardware says otherwise, and the host must not
    render a capability it has not been given.
    """

    NONE = 0
    TEMP_CALIBRATED = 1 << 0
    OLED_PRESENT = 1 << 1
    LEAD_HW_DETECT = 1 << 2
    PROBE_HW_DETECT = 1 << 3
    FRONTEND_VERIFIED = 1 << 4
    RTC_BATTERY_BACKED = 1 << 5


class TrackKind(IntEnum):
    """``nack_reason_t``-adjacent host-side accounting events."""

    SEQUENCE_GAP = 1
    SAMPLE_INDEX_GAP = 2
    SEQUENCE_RESTART = 3
    UNKNOWN_TYPE = 4
    MALFORMED = 5


@dataclass(frozen=True)
class TrackEvent:
    kind: TrackKind
    detail: str
    missing: int = 0


# -------------------------------------------------------------- status flags
# protocol.h: SFLAG_*.

SFLAG_LEAD_SHIFT = 0
SFLAG_LEAD_MASK = 0x7 << SFLAG_LEAD_SHIFT
SFLAG_TEMP_SHIFT = 3
SFLAG_TEMP_MASK = 0x7 << SFLAG_TEMP_SHIFT
SFLAG_HR_VALID = 1 << 6
SFLAG_RECORDING = 1 << 7
SFLAG_OLED = 1 << 8
SFLAG_ADC_RUNNING = 1 << 9
SFLAG_RTC_VALID = 1 << 10
SFLAG_DMA_DROPPED = 1 << 11
SFLAG_NOTCH_SHIFT = 12
SFLAG_NOTCH_MASK = 0x3 << SFLAG_NOTCH_SHIFT
SFLAG_TEMP_UNCALIB = 1 << 14


@dataclass(frozen=True)
class Flags:
    """Decoded ``status_flags_t`` word."""

    lead: LeadState = LeadState.UNKNOWN
    temp: TempState = TempState.UNCALIBRATED
    hr_valid: bool = False
    recording: bool = False
    oled_present: bool = False
    adc_running: bool = False
    rtc_valid: bool = False
    dma_dropped: bool = False
    notch: NotchMode = NotchMode.HZ50
    temp_uncalibrated: bool = True
    #: The word this was decoded from, verbatim.  Deliberately stored rather than
    #: rebuilt from the fields: an out-of-range enum value falls back to a legal
    #: one, so a rebuilt word would silently rewrite what the device sent.
    raw_word: int = 0

    @property
    def raw(self) -> int:
        return self.raw_word

    # Named accessors spelled the way ``docs/PROTOCOL.md`` names the bit fields,
    # so a caller never has to remember which of two names this dataclass uses.
    @property
    def lead_state(self) -> LeadState:
        """Bits 0-2.  ``UNKNOWN`` is the honest default without lead-off hardware."""
        return self.lead

    @property
    def temp_state(self) -> TempState:
        """Bits 3-5."""
        return self.temp

    @property
    def notch_mode(self) -> NotchMode:
        """Bits 12-13."""
        return self.notch


def split_flags(word: int) -> Flags:
    """Decode a ``status_flags_t`` word into named parts."""
    lead_raw = (word & SFLAG_LEAD_MASK) >> SFLAG_LEAD_SHIFT
    temp_raw = (word & SFLAG_TEMP_MASK) >> SFLAG_TEMP_SHIFT
    notch_raw = (word & SFLAG_NOTCH_MASK) >> SFLAG_NOTCH_SHIFT
    return Flags(
        lead=_enum_or(LeadState, lead_raw, LeadState.UNKNOWN),
        temp=_enum_or(TempState, temp_raw, TempState.UNCALIBRATED),
        hr_valid=bool(word & SFLAG_HR_VALID),
        recording=bool(word & SFLAG_RECORDING),
        oled_present=bool(word & SFLAG_OLED),
        adc_running=bool(word & SFLAG_ADC_RUNNING),
        rtc_valid=bool(word & SFLAG_RTC_VALID),
        dma_dropped=bool(word & SFLAG_DMA_DROPPED),
        notch=_enum_or(NotchMode, notch_raw, NotchMode.HZ50),
        temp_uncalibrated=bool(word & SFLAG_TEMP_UNCALIB),
        raw_word=word & 0xFFFF,
    )


def _enum_or(enum_cls, value: int, fallback):
    try:
        return enum_cls(value)
    except ValueError:
        return fallback


def hr_state_name(value: int) -> str:
    """``hr_state_t`` text that also copes with a value out of range."""
    return _enum_or(HrState, value, HrState.INVALID).name.replace("_", " ").title()


def lead_state_name(value: int) -> str:
    return _enum_or(LeadState, value, LeadState.UNKNOWN).text


def temp_state_name(value: int) -> str:
    return _enum_or(TempState, value, TempState.UNCALIBRATED).text


def make_flags(
    *,
    lead: LeadState | int = LeadState.UNKNOWN,
    temp: TempState | int = TempState.UNCALIBRATED,
    hr_valid: bool = False,
    recording: bool = False,
    oled_present: bool = False,
    adc_running: bool = False,
    rtc_valid: bool = False,
    dma_dropped: bool = False,
    notch: NotchMode | int = NotchMode.HZ50,
    temp_uncalibrated: bool = True,
) -> int:
    """Build a ``status_flags_t`` word; the inverse of :func:`split_flags`.

    Mirrors ``SFLAG_SET(v, shift, mask, val)`` from ``protocol.h``.
    """
    word = 0
    for value, shift, mask in (
        (int(lead), SFLAG_LEAD_SHIFT, SFLAG_LEAD_MASK),
        (int(temp), SFLAG_TEMP_SHIFT, SFLAG_TEMP_MASK),
        (int(notch), SFLAG_NOTCH_SHIFT, SFLAG_NOTCH_MASK),
    ):
        word = (word & ~mask) | ((value << shift) & mask)
    for flag, bit in (
        (hr_valid, SFLAG_HR_VALID),
        (recording, SFLAG_RECORDING),
        (oled_present, SFLAG_OLED),
        (adc_running, SFLAG_ADC_RUNNING),
        (rtc_valid, SFLAG_RTC_VALID),
        (dma_dropped, SFLAG_DMA_DROPPED),
        (temp_uncalibrated, SFLAG_TEMP_UNCALIB),
    ):
        if flag:
            word |= bit
    return word & 0xFFFF


# ---------------------------------------------------------- ECG_BATCH offsets
# protocol.h's ECGP_* / ECGT_* #defines, verbatim spelling and verbatim values,
# with n = sample_count.

ECGP_COUNT = 0  # u8
ECGP_PERIOD_US = 1  # u16 == ADC_SAMPLE_PERIOD_US
ECGP_FIRST_INDEX = 3  # u32
ECGP_SAMPLES = 7  # u16[n]

ECGT_TEMP_RAW = 0  # u16
ECGT_TEMP_CENTI = 2  # i16
ECGT_HR_BPM = 4  # u8, 0 when invalid
ECGT_HR_STATE = 5  # u8 hr_state_t
ECGT_FLAGS = 6  # u16 status_flags_t
# Derived, not hand-counted -- mirrors the identical derivation in protocol.h so
# that a future field appended to the tail cannot be written but not transmitted.
ECGP_TAIL = ECGT_FLAGS + 2  # 8

#: The tail width, period.  One value, because there is one wire format.
ECG_BATCH_TAIL_SIZE = ECGP_TAIL  # 8

#: Payload bytes before the samples: 7 + 8.
ECG_BATCH_PAYLOAD_BASE = ECGP_SAMPLES + ECG_BATCH_TAIL_SIZE  # 15


# ------------------------------------------------------------ frame build/parse

@dataclass(frozen=True)
class Frame:
    """``pkt_frame_t`` -- a whole, CRC-verified frame.

    ``payload`` is a copy rather than the C "pointer into the caller's buffer",
    because the Python buffer is consumed incrementally.
    """

    version: int
    type: int
    sequence: int
    length: int
    device_ts_ms: int
    payload: bytes = b""

    def __post_init__(self) -> None:
        if self.length != len(self.payload):
            raise ValueError(
                "Frame.length %d disagrees with payload size %d" % (self.length, len(self.payload))
            )

    @property
    def type_name(self) -> str:
        return packet_type_name(self.type)

    @property
    def is_host_command(self) -> bool:
        """``type >= 0x80`` is host -> device in ``pkt_type_t``."""
        return self.type >= 0x80

    @property
    def is_device_report(self) -> bool:
        return self.type < 0x80

    @property
    def total_size(self) -> int:
        return OVERHEAD + self.length

    @property
    def flags(self) -> int:
        """The ``status_flags_t`` word this packet type carries, or ``-1``.

        ``ECG_BATCH`` keeps it in its tail, ``STATUS`` in ``STP_FLAGS``;
        ``TEMP_STATUS`` has no flag word at all, which is why ``temp_state``
        arrives twice by two routes and the host must reconcile them.
        """
        if self.type == PacketType.ECG_BATCH:
            try:
                return EcgBatch.decode(self.payload).flags
            except MalformedPayload:
                return -1
        if self.type == PacketType.STATUS:
            try:
                return StatusPacket.decode(self.payload).flags
            except MalformedPayload:
                return -1
        return -1

    @property
    def flags_decoded(self) -> Flags | None:
        """The flag word split into named parts, or ``None`` without a flag word."""
        word = self.flags
        return None if word < 0 else split_flags(word)

    def decode(self) -> object:
        """This frame's payload as its typed object; see :func:`decode_frame`."""
        if self.type == PacketType.ECG_BATCH:
            return decode_ecg_batch(self)
        return decode_frame(self)


class ParseResult(IntEnum):
    """``pkt_result_t``."""

    NEED_MORE = 0
    OK = 1
    ERR_CRC = -1
    ERR_VERSION = -2
    ERR_LENGTH = -3


def encode_frame(
    type_: PacketType | int,
    sequence: int,
    device_ts_ms: int,
    payload: bytes = b"",
    *,
    version: int = PROTOCOL_VERSION,
) -> bytes:
    """``pkt_build()`` -> the finished frame bytes.

    Raises :class:`ValueError` where the C function returns 0 (payload too big),
    because the caller here is never racing a fixed-size destination buffer.
    """
    payload = bytes(payload)
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload %d bytes exceeds MAX_PAYLOAD %d" % (len(payload), MAX_PAYLOAD))
    if not 0 <= int(sequence) <= 0xFFFF:
        raise ValueError("sequence out of u16 range: %r" % (sequence,))
    if not 0 <= int(device_ts_ms) <= 0xFFFFFFFF:
        raise ValueError("device_ts_ms out of u32 range: %r" % (device_ts_ms,))
    header = bytearray()
    header += bytes((MAGIC0, MAGIC1, version & 0xFF, int(type_) & 0xFF))
    header += put_u16(int(sequence))
    header += put_u16(len(payload))
    header += put_u32(int(device_ts_ms))
    body = bytes(header) + payload
    return body + put_u16(crc16_ccitt_buf(body))


#: ``pkt_build()`` under its C-ish name; identical object, kept because the
#: worker and the demo device were written against it.
build_frame = encode_frame


def parse_frame(buf: bytes | bytearray | memoryview) -> tuple[ParseResult, Frame | None, int]:
    """``pkt_parse()`` for a whole buffer.

    Returns ``(result, frame, consumed)`` where ``consumed`` is the number of
    leading input bytes the caller may drop before retrying.  The resync rules
    are the C ones, byte for byte:

    * no magic pair present -> ``NEED_MORE``, ``consumed = len - 1``: a lone
      trailing ``0xA5`` is **kept**, because it may be half a magic that has not
      finished arriving (``test_protocol.c`` asserts this);
    * declared length > ``MAX_PAYLOAD``, or version mismatch -> step two bytes
      past the false magic and rescan internally, so a corrupted stream never
      wedges (the caller still just sees ``NEED_MORE`` or a later good frame);
    * CRC failure -> ``ERR_CRC`` with ``consumed = 2``, consuming exactly the
      magic so forward progress is guaranteed.
    """
    return _parse_at(buf, 0)


def _find_magic(buf, start: int, length: int) -> int:
    """``find_magic()``: index of the next ``A5 5A`` pair, or ``-1``.

    The scan stops one byte short of the end, which is what leaves a trailing
    ``0xA5`` in play.
    """
    at = start
    limit = length - 1
    while at < limit:
        if buf[at] == MAGIC0 and buf[at + 1] == MAGIC1:
            return at
        at += 1
    return -1


def _parse_at(buf, origin: int) -> tuple[ParseResult, Frame | None, int]:
    at = origin
    length = len(buf)
    while True:
        magic = _find_magic(buf, at, length)
        if magic < 0:
            return ParseResult.NEED_MORE, None, max(length - 1, 0)
        at = magic

        if length - at < HEADER_SIZE:
            return ParseResult.NEED_MORE, None, at

        payload_len = get_u16(buf, at + 6)
        if payload_len > MAX_PAYLOAD:
            at = at + 2  # not a frame we can trust; rescan past the false magic
            continue
        if buf[at + 2] != PROTOCOL_VERSION:
            at = at + 2
            continue

        total = OVERHEAD + payload_len
        want = HEADER_SIZE + payload_len
        if length - at < total:
            return ParseResult.NEED_MORE, None, at

        calc = crc16_ccitt_buf(memoryview(bytes(buf[at : at + want])))
        held = get_u16(buf, at + want)
        if calc != held:
            return ParseResult.ERR_CRC, None, at + 2

        frame = Frame(
            version=buf[at + 2],
            type=buf[at + 3],
            sequence=get_u16(buf, at + 4),
            length=payload_len,
            device_ts_ms=get_u32(buf, at + 8),
            payload=bytes(buf[at + HEADER_SIZE : at + HEADER_SIZE + payload_len]),
        )
        return ParseResult.OK, frame, at + total


class MalformedPayload(Exception):
    """A CRC-valid frame whose payload does not match its declared layout.

    Distinct from a link error: the bytes arrived intact, so this is a schema
    disagreement, and it is counted separately from CRC errors on purpose.
    """


class IncrementalParser:
    """Incremental framing over a byte stream: feed arbitrary chunks, get frames.

    ``feed()`` takes whatever the OS handed back (a partial frame, three frames,
    half a frame plus junk) and returns the complete frames it can.  All framing
    state lives here, in the worker thread; the GUI never touches it.

    Guarantees, all of them exercised by ``tests/test_protocol.py``:

    * a frame split over two (or twenty) feeds is reassembled;
    * several frames in one feed all come out, in order;
    * junk before the magic is discarded and counted, not fatal;
    * a bad CRC costs one frame, increments :attr:`crc_errors`, and the parser
      resynchronises on the next magic pair instead of wedging;
    * a declared length above ``MAX_PAYLOAD`` or an unknown version is rescanned
      past the false magic, exactly like ``pkt_parse()``.
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self.crc_errors = 0
        self.bytes_discarded = 0
        self.resyncs = 0
        self.frames_parsed = 0
        self.empty_feeds = 0
        self.type_counts: dict[int, int] = {}

    # -- introspection used by the status bar ------------------------------
    @property
    def pending(self) -> int:
        """Bytes held back waiting for the rest of a frame."""
        return len(self._buf)

    def reset(self) -> None:
        self._buf.clear()

    # -- the hot path ------------------------------------------------------
    def feed(self, chunk: bytes | bytearray) -> list[Frame]:
        """Append ``chunk`` and return every frame that is now complete."""
        if not chunk:
            self.empty_feeds += 1
            return []
        self._buf += chunk
        out: list[Frame] = []
        while True:
            result, frame, consumed = _parse_at(self._buf, 0)
            if result is ParseResult.NEED_MORE:
                # ``consumed`` is everything up to the magic we can safely drop.
                if consumed:
                    del self._buf[:consumed]
                    self.bytes_discarded += consumed
                return out
            if result is ParseResult.ERR_CRC:
                self.crc_errors += 1
                self.resyncs += 1
                del self._buf[:consumed]
                self.bytes_discarded += consumed
                continue
            # OK: drop the frame and keep scanning the rest of this read.
            assert frame is not None
            del self._buf[:consumed]
            self.frames_parsed += 1
            self.type_counts[frame.type] = self.type_counts.get(frame.type, 0) + 1
            out.append(frame)

    def feed_iter(self, chunk: bytes | bytearray) -> Iterator[Frame]:
        return iter(self.feed(chunk))

    def counters(self) -> dict[str, int]:
        """The link-health numbers a recording needs to state its own caveats."""
        return {
            "crc_errors": self.crc_errors,
            "discarded": self.bytes_discarded,
            "resyncs": self.resyncs,
            "frames": self.frames_parsed,
            "pending_bytes": self.pending,
        }


#: The historical name for :class:`IncrementalParser`; the framing is unchanged,
#: only the spelling of the class follows the role it plays.
StreamParser = IncrementalParser


# --------------------------------------------------------------- seq / loss
@dataclass
class StreamTracker:
    """Sequence and ``first_sample_index`` accounting.

    ``docs/PROTOCOL.md``: sequence numbers "exist so the PC can say 'packet 4711
    of this stream never arrived', not for ordering", and the *authoritative*
    loss measure is ``first_sample_index`` continuity, because that also catches
    the device dropping its own DMA blocks.  Both are tracked, separately.
    """

    packets: int = 0
    by_type: dict[int, int] = field(default_factory=dict)
    sequence_gaps: int = 0
    sequence_missing: int = 0
    index_gaps: int = 0
    samples_missing: int = 0
    batches: int = 0
    malformed_batches: int = 0
    unknown_types: int = 0
    _last_sequence: int | None = field(default=None, repr=False)
    _next_index: int | None = field(default=None, repr=False)
    _total_samples: int = 0
    #: ``docs/PROTOCOL.md`` says sequence is per-direction, so one cursor is
    #: correct.  Set True only if a device is ever found counting per type.
    per_type_sequence: bool = False
    _type_cursors: dict[int, int] = field(default_factory=dict, repr=False)

    @property
    def total_samples(self) -> int:
        return self._total_samples

    @property
    def samples_expected(self) -> int:
        """``first_sample_index`` continuity window this host has observed."""
        return self._total_samples + self.samples_missing

    def observe(self, frame: Frame) -> list[TrackEvent]:
        """Update the counters and report what looks missing."""
        events: list[TrackEvent] = []
        self.packets += 1
        self.by_type[frame.type] = self.by_type.get(frame.type, 0) + 1

        # Sequence is per-direction, so one global cursor is the contract-true
        # check: any single dropped packet, whatever its type, moves it.
        if self.per_type_sequence:
            last = self._type_cursors.get(frame.type)
            self._type_cursors[frame.type] = frame.sequence
        else:
            last = self._last_sequence
            self._last_sequence = frame.sequence
        if last is not None:
            expected = (last + 1) & 0xFFFF
            if frame.sequence != expected:
                # 16-bit wrap makes "missing" ambiguous for a large jump; report
                # it, and treat a bare restart (0 after 0xFFFF) as not a loss.
                if frame.sequence == 0 and last == 0xFFFF:
                    events.append(
                        TrackEvent(TrackKind.SEQUENCE_RESTART, "sequence wrapped at 65535", 0)
                    )
                else:
                    missing = (frame.sequence - expected) & 0xFFFF
                    self.sequence_gaps += 1
                    self.sequence_missing += missing
                    events.append(
                        TrackEvent(
                            TrackKind.SEQUENCE_GAP,
                            "%s seq %d -> %d (%d missing)"
                            % (frame.type_name, last, frame.sequence, missing),
                            missing,
                        )
                    )

        if frame.type == PacketType.ECG_BATCH:
            self.batches += 1
            self.track_ecg_batch(frame.payload, events)
        elif frame.type not in {int(t) for t in PacketType}:
            self.unknown_types += 1
            events.append(TrackEvent(TrackKind.UNKNOWN_TYPE, frame.type_name, 0))
        return events

    def track_ecg_batch(self, payload: bytes, events: list[TrackEvent] | None = None) -> list[TrackEvent]:
        """Check ``first_sample_index`` continuity: next must be ``first + n``."""
        events = events if events is not None else []
        try:
            batch = EcgBatch.decode(payload)
        except MalformedPayload as exc:
            self.malformed_batches += 1
            events.append(TrackEvent(TrackKind.MALFORMED, str(exc), 0))
            return events
        if self._next_index is None:
            self._next_index = batch.first_sample_index + batch.count
            self._total_samples += batch.count
            return events
        if batch.first_sample_index != self._next_index:
            missing = (batch.first_sample_index - self._next_index) & 0xFFFFFFFF
            if missing > MAX_PAYLOAD * SAMPLE_RATE_HZ:
                # A backwards or absurd jump is a reset, not a loss: re-anchor.
                events.append(
                    TrackEvent(
                        TrackKind.SAMPLE_INDEX_GAP,
                        "sample index reset %d -> %d" % (self._next_index, batch.first_sample_index),
                        0,
                    )
                )
            else:
                self.index_gaps += 1
                self.samples_missing += missing
                events.append(
                    TrackEvent(
                        TrackKind.SAMPLE_INDEX_GAP,
                        "%d ECG samples missing before index %d" % (missing, batch.first_sample_index),
                        missing,
                    )
                )
            self._next_index = batch.first_sample_index + batch.count
            self._total_samples += batch.count
            return events
        self._next_index += batch.count
        self._total_samples += batch.count
        return events

    def note_samples(self, count: int) -> None:
        self._total_samples += count

    def reset(self) -> None:
        """Zero the counters, keeping the configured accounting mode."""
        mode = self.per_type_sequence
        self.__init__()
        self.per_type_sequence = mode


# ------------------------------------------------------------- ECG_BATCH body
# The ECGP_* / ECGT_* offsets and the tail width live above, next to the
# status-flag section they are entangled with (see "ECG_BATCH offsets").


def ecg_batch_payload_len(sample_count: int) -> int:
    """Payload size for ``n`` samples: 7 + 2n + 8."""
    return ECG_BATCH_PAYLOAD_BASE + 2 * sample_count


@dataclass(frozen=True)
class EcgBatch:
    """``ECG_BATCH`` (0x10): the 1 kHz record, batched.

    ``samples`` are RAW 12-bit ADC codes.  The record must stay unfiltered --
    ``ecg_config.h`` is explicit that the course metric is a 0.05-150 Hz
    recording bandwidth, so the host must not quietly plot something else.
    """

    count: int
    sample_period_us: int
    first_sample_index: int
    samples: tuple[int, ...]
    temp_raw: int
    temp_centi: int
    hr_bpm: int
    hr_state: int
    flags: int

    # -- decode ------------------------------------------------------------
    @classmethod
    def decode(cls, payload: bytes) -> "EcgBatch":
        """Decode an ``ECG_BATCH`` payload, or raise :class:`MalformedPayload`."""
        n = payload[ECGP_COUNT] if len(payload) > ECGP_COUNT else 0
        if not 1 <= n <= ECG_BATCH_MAX_SAMPLES:
            raise MalformedPayload(
                "ECG_BATCH sample_count %d outside 1..%d" % (n, ECG_BATCH_MAX_SAMPLES)
            )
        expected = ecg_batch_payload_len(n)
        if len(payload) != expected:
            raise MalformedPayload(
                "ECG_BATCH payload is %d bytes, %d expected for n=%d (7 + 2n + %d). "
                "See docs/PROTOCOL.md."
                % (len(payload), expected, n, ECG_BATCH_TAIL_SIZE)
            )
        samples_at = ECGP_SAMPLES
        tail_at = samples_at + 2 * n
        return cls(
            count=n,
            sample_period_us=get_u16(payload, ECGP_PERIOD_US),
            first_sample_index=get_u32(payload, ECGP_FIRST_INDEX),
            samples=struct.unpack_from("<%dH" % n, payload, samples_at),
            temp_raw=get_u16(payload, tail_at + ECGT_TEMP_RAW),
            temp_centi=get_i16(payload, tail_at + ECGT_TEMP_CENTI),
            hr_bpm=payload[tail_at + ECGT_HR_BPM],
            hr_state=payload[tail_at + ECGT_HR_STATE],
            flags=get_u16(payload, tail_at + ECGT_FLAGS),
        )

    # -- encode ------------------------------------------------------------
    @classmethod
    def encode(
        cls,
        *,
        first_sample_index: int,
        samples: Iterable[int],
        temp_raw: int = 0,
        temp_centi: int = 0,
        hr_bpm: int = 0,
        hr_state: HrState | int = HrState.INVALID,
        flags: int = 0,
        sample_period_us: int = SAMPLE_PERIOD_US,
    ) -> bytes:
        """Build an ``ECG_BATCH`` payload byte-for-byte as the device sends it."""
        data = list(samples)
        if not 1 <= len(data) <= ECG_BATCH_MAX_SAMPLES:
            raise MalformedPayload("cannot batch %d samples into 1..%d" % (len(data), ECG_BATCH_MAX_SAMPLES))
        p = bytearray()
        p.append(len(data))
        p += put_u16(sample_period_us)
        p += put_u32(first_sample_index)
        p += struct.pack("<%dH" % len(data), *data)
        p += put_u16(temp_raw)
        p += put_i16(temp_centi)
        p.append(hr_bpm & 0xFF)
        p.append(int(hr_state) & 0xFF)
        p += put_u16(flags)
        return bytes(p)

    # -- conveniences ------------------------------------------------------
    @property
    def flags_decoded(self) -> Flags:
        return split_flags(self.flags)

    @property
    def sample_rate_hz(self) -> float:
        return 1e6 / self.sample_period_us if self.sample_period_us else float("nan")

    @property
    def last_sample_index(self) -> int:
        return self.first_sample_index + self.count - 1


# --------------------------------------------------------- TEMP_STATUS body

TEMPP_RAW = 0  # u16
TEMPP_MV = 2  # u16 pin millivolts
TEMPP_CENTI = 4  # i16
TEMPP_STATE = 6  # u8 temp_state_t
TEMPP_SIZE = 8  # 7 used, padded to even


@dataclass(frozen=True)
class TempStatus:
    """``TEMP_STATUS`` (0x11): the temperature view at its own 2 Hz cadence.

    Independent of the ECG stream so a stalled batch cannot hide it.
    """

    temp_raw: int
    temp_mv: int
    temp_centi: int
    temp_state: int

    @classmethod
    def decode(cls, payload: bytes) -> "TempStatus":
        if len(payload) < TEMPP_SIZE - 1:
            raise MalformedPayload("TEMP_STATUS payload %d bytes, need %d" % (len(payload), TEMPP_SIZE - 1))
        return cls(
            temp_raw=get_u16(payload, TEMPP_RAW),
            temp_mv=get_u16(payload, TEMPP_MV),
            temp_centi=get_i16(payload, TEMPP_CENTI),
            temp_state=payload[TEMPP_STATE],
        )

    @staticmethod
    def encode(temp_raw: int, temp_mv: int, temp_centi: int, temp_state: TempState | int) -> bytes:
        p = bytearray()
        p += put_u16(temp_raw)
        p += put_u16(temp_mv)
        p += put_i16(temp_centi)
        p.append(int(temp_state) & 0xFF)
        p.append(0)  # padding byte, per "7 used, padded to even"
        return bytes(p)

    @property
    def state(self) -> TempState:
        return _enum_or(TempState, self.temp_state, TempState.UNCALIBRATED)

    @property
    def is_valid(self) -> bool:
        """Degrees only exist for ``TEMP_OK``/``TEMP_LOW``/``TEMP_HIGH``.

        ``temperature_calibration.h`` is explicit: while the model is
        uncalibrated the state stays ``TEMP_UNCALIBRATED`` and the UI must print
        ``--.-`` rather than a plausible-looking number.
        """
        return self.state in (TempState.OK, TempState.LOW, TempState.HIGH)

    @property
    def temp_c(self) -> float | None:
        return centi_to_c(self.temp_centi) if self.is_valid else None


# ------------------------------------------------------------- STATUS body

STP_ADC_RUNNING = 0  # u8
STP_DMA_BLOCKS = 1  # u32
STP_DMA_DROPPED = 5  # u32
STP_ECG_SAMPLES = 9  # u32
STP_TEMP_VALID = 13  # u8
STP_TEMP_CENTI = 14  # i16
STP_HR_BPM = 16  # u8
STP_HR_STATE = 17  # u8
STP_OLED_PRESENT = 18  # u8
STP_OLED_ADDR = 19  # u8, 7-bit, 0 if none
STP_RTC_VALID = 20  # u8
STP_UART_TX = 21  # u32
STP_UART_RX = 25  # u32
STP_UART_CRC_ERR = 29  # u32
STP_PROTO_ERR = 33  # u32
STP_FLAGS = 37  # u16
STP_UPTIME_S = 39  # u32
STP_SIZE = 43


@dataclass(frozen=True)
class StatusPacket:
    """``STATUS`` (0x02): the same counters the OLED STATUS page shows, so a
    bench observation and a PC observation cannot disagree."""

    adc_running: bool
    dma_blocks: int
    dma_dropped: int
    ecg_samples: int
    temp_valid: bool
    temp_centi: int
    hr_bpm: int
    hr_state: int
    oled_present: bool
    oled_addr: int
    rtc_valid: bool
    uart_tx: int
    uart_rx: int
    uart_crc_err: int
    proto_err: int
    flags: int
    uptime_s: int

    @classmethod
    def decode(cls, payload: bytes) -> "StatusPacket":
        if len(payload) < STP_SIZE:
            raise MalformedPayload("STATUS payload %d bytes, need %d" % (len(payload), STP_SIZE))
        return cls(
            adc_running=bool(payload[STP_ADC_RUNNING]),
            dma_blocks=get_u32(payload, STP_DMA_BLOCKS),
            dma_dropped=get_u32(payload, STP_DMA_DROPPED),
            ecg_samples=get_u32(payload, STP_ECG_SAMPLES),
            temp_valid=bool(payload[STP_TEMP_VALID]),
            temp_centi=get_i16(payload, STP_TEMP_CENTI),
            hr_bpm=payload[STP_HR_BPM],
            hr_state=payload[STP_HR_STATE],
            oled_present=bool(payload[STP_OLED_PRESENT]),
            oled_addr=payload[STP_OLED_ADDR],
            rtc_valid=bool(payload[STP_RTC_VALID]),
            uart_tx=get_u32(payload, STP_UART_TX),
            uart_rx=get_u32(payload, STP_UART_RX),
            uart_crc_err=get_u32(payload, STP_UART_CRC_ERR),
            proto_err=get_u32(payload, STP_PROTO_ERR),
            flags=get_u16(payload, STP_FLAGS),
            uptime_s=get_u32(payload, STP_UPTIME_S),
        )

    @staticmethod
    def encode(**kw: object) -> bytes:
        p = bytearray(STP_SIZE)

        def u32(at: int, value: int) -> None:
            p[at : at + 4] = put_u32(int(value))  # type: ignore[arg-type]

        p[STP_ADC_RUNNING] = int(bool(kw.get("adc_running", False)))
        u32(STP_DMA_BLOCKS, kw.get("dma_blocks", 0))  # type: ignore[arg-type]
        u32(STP_DMA_DROPPED, kw.get("dma_dropped", 0))  # type: ignore[arg-type]
        u32(STP_ECG_SAMPLES, kw.get("ecg_samples", 0))  # type: ignore[arg-type]
        p[STP_TEMP_VALID] = int(bool(kw.get("temp_valid", False)))
        p[STP_TEMP_CENTI : STP_TEMP_CENTI + 2] = put_i16(int(kw.get("temp_centi", 0)))  # type: ignore[arg-type]
        p[STP_HR_BPM] = int(kw.get("hr_bpm", 0)) & 0xFF  # type: ignore[arg-type]
        p[STP_HR_STATE] = int(kw.get("hr_state", 0)) & 0xFF  # type: ignore[arg-type]
        p[STP_OLED_PRESENT] = int(bool(kw.get("oled_present", False)))
        p[STP_OLED_ADDR] = int(kw.get("oled_addr", 0)) & 0xFF  # type: ignore[arg-type]
        p[STP_RTC_VALID] = int(bool(kw.get("rtc_valid", False)))
        u32(STP_UART_TX, kw.get("uart_tx", 0))  # type: ignore[arg-type]
        u32(STP_UART_RX, kw.get("uart_rx", 0))  # type: ignore[arg-type]
        u32(STP_UART_CRC_ERR, kw.get("uart_crc_err", 0))  # type: ignore[arg-type]
        u32(STP_PROTO_ERR, kw.get("proto_err", 0))  # type: ignore[arg-type]
        p[STP_FLAGS : STP_FLAGS + 2] = put_u16(int(kw.get("flags", 0)))  # type: ignore[arg-type]
        u32(STP_UPTIME_S, kw.get("uptime_s", 0))  # type: ignore[arg-type]
        return bytes(p)

    @property
    def flags_decoded(self) -> Flags:
        return split_flags(self.flags)


# --------------------------------------------------------------- HELLO body

HELPP_FW_MAJOR = 0  # u8
HELPP_FW_MINOR = 1  # u8
HELPP_FW_PATCH = 2  # u8
HELPP_PROTO_VER = 3  # u8
HELPP_SAMPLE_RATE = 4  # u16
HELPP_BATCH_MAX = 6  # u8
HELPP_ADC_BITS = 7  # u8
HELPP_CAPS = 8  # u16
HELLO_SIZE = 12  # bytes 10..11 are unnamed padding; see README "ambiguity"


@dataclass(frozen=True)
class Hello:
    """``HELLO`` (0x01): identity plus the capability mask.

    The host must not render a capability it has not been given, and the
    defaults are all clear until hardware says otherwise.
    """

    fw_major: int
    fw_minor: int
    fw_patch: int
    proto_version: int
    sample_rate_hz: int
    batch_max_samples: int
    adc_bits: int
    caps: int

    @property
    def fw_version(self) -> str:
        return "%d.%d.%d" % (self.fw_major, self.fw_minor, self.fw_patch)

    @property
    def capabilities(self) -> Capability:
        return Capability(self.caps)

    def has(self, cap: Capability) -> bool:
        return bool(self.caps & cap)

    @classmethod
    def decode(cls, payload: bytes) -> "Hello":
        if len(payload) < HELPP_CAPS + 2:
            raise MalformedPayload("HELLO payload %d bytes, need %d" % (len(payload), HELPP_CAPS + 2))
        return cls(
            fw_major=payload[HELPP_FW_MAJOR],
            fw_minor=payload[HELPP_FW_MINOR],
            fw_patch=payload[HELPP_FW_PATCH],
            proto_version=payload[HELPP_PROTO_VER],
            sample_rate_hz=get_u16(payload, HELPP_SAMPLE_RATE),
            batch_max_samples=payload[HELPP_BATCH_MAX],
            adc_bits=payload[HELPP_ADC_BITS],
            caps=get_u16(payload, HELPP_CAPS),
        )

    @staticmethod
    def encode(
        *,
        fw_major: int = 1,
        fw_minor: int = 0,
        fw_patch: int = 0,
        proto_version: int = PROTOCOL_VERSION,
        sample_rate_hz: int = SAMPLE_RATE_HZ,
        batch_max_samples: int = ECG_BATCH_MAX_SAMPLES,
        adc_bits: int = 12,
        caps: int = 0,
    ) -> bytes:
        p = bytearray(HELLO_SIZE)
        p[HELPP_FW_MAJOR] = fw_major & 0xFF
        p[HELPP_FW_MINOR] = fw_minor & 0xFF
        p[HELPP_FW_PATCH] = fw_patch & 0xFF
        p[HELPP_PROTO_VER] = proto_version & 0xFF
        p[HELPP_SAMPLE_RATE : HELPP_SAMPLE_RATE + 2] = put_u16(sample_rate_hz)
        p[HELPP_BATCH_MAX] = batch_max_samples & 0xFF
        p[HELPP_ADC_BITS] = adc_bits & 0xFF
        p[HELPP_CAPS : HELPP_CAPS + 2] = put_u16(caps)
        return bytes(p)


# ------------------------------------------------------------ RTC payloads

RTCP_YEAR = 0  # u16
RTCP_MONTH = 2  # u8
RTCP_DAY = 3  # u8
RTCP_HOUR = 4  # u8
RTCP_MINUTE = 5  # u8
RTCP_SECOND = 6  # u8
RTCP_CAL_SIZE = 7
RTC_RESPONSE_EXTRA = 4  # trailing u32 epoch -> 11 bytes total


@dataclass(frozen=True)
class RtcCalendar:
    """``SET_RTC`` / ``RTC_RESPONSE`` calendar.

    Calendar fields, not an epoch, "so the human-readable intent is on the wire"
    (``docs/PROTOCOL.md``).  ``epoch`` is only present in ``RTC_RESPONSE``.
    """

    year: int
    month: int
    day: int
    hour: int
    minute: int
    second: int
    epoch: int | None = None

    def decode_payload_only(self) -> "RtcCalendar":
        return self

    @classmethod
    def decode(cls, payload: bytes) -> "RtcCalendar":
        if len(payload) < RTCP_CAL_SIZE:
            raise MalformedPayload("RTC payload %d bytes, need %d" % (len(payload), RTCP_CAL_SIZE))
        epoch = get_u32(payload, RTCP_CAL_SIZE) if len(payload) >= RTCP_CAL_SIZE + RTC_RESPONSE_EXTRA else None
        return cls(
            year=get_u16(payload, RTCP_YEAR),
            month=payload[RTCP_MONTH],
            day=payload[RTCP_DAY],
            hour=payload[RTCP_HOUR],
            minute=payload[RTCP_MINUTE],
            second=payload[RTCP_SECOND],
            epoch=epoch,
        )

    @staticmethod
    def encode(
        year: int,
        month: int,
        day: int,
        hour: int,
        minute: int,
        second: int,
        *,
        epoch: int | None = None,
    ) -> bytes:
        p = bytearray()
        p += put_u16(year)
        p += bytes((month & 0xFF, day & 0xFF, hour & 0xFF, minute & 0xFF, second & 0xFF))
        if epoch is not None:
            p += put_u32(epoch)
        return bytes(p)

    @property
    def text(self) -> str:
        return "%04d-%02d-%02d %02d:%02d:%02d" % (
            self.year,
            self.month,
            self.day,
            self.hour,
            self.minute,
            self.second,
        )


# ------------------------------------------------------- SET_CONFIG / ACK

CFGP_NOTCH = 0  # u8 ecg_notch_t
CFGP_HR_LOW = 1  # u8
CFGP_HR_HIGH = 2  # u8
CFGP_TEMP_LOW = 3  # i16 centi
CFGP_TEMP_HIGH = 5  # i16 centi
CFGP_SIZE = 7


@dataclass(frozen=True)
class SetConfig:
    """``SET_CONFIG`` (0x84): notch choice and the alarm bands."""

    notch: int
    hr_low: int
    hr_high: int
    temp_low_centi: int
    temp_high_centi: int

    @classmethod
    def decode(cls, payload: bytes) -> "SetConfig":
        if len(payload) < CFGP_SIZE:
            raise MalformedPayload("SET_CONFIG payload %d bytes, need %d" % (len(payload), CFGP_SIZE))
        return cls(
            notch=payload[CFGP_NOTCH],
            hr_low=payload[CFGP_HR_LOW],
            hr_high=payload[CFGP_HR_HIGH],
            temp_low_centi=get_i16(payload, CFGP_TEMP_LOW),
            temp_high_centi=get_i16(payload, CFGP_TEMP_HIGH),
        )

    @staticmethod
    def encode(
        notch: NotchMode | int = NotchMode.HZ50,
        hr_low: int = 60,
        hr_high: int = 100,
        temp_low_centi: int = 3400,
        temp_high_centi: int = 3800,
    ) -> bytes:
        p = bytearray()
        p.append(int(notch) & 0xFF)
        p.append(hr_low & 0xFF)
        p.append(hr_high & 0xFF)
        p += put_i16(temp_low_centi)
        p += put_i16(temp_high_centi)
        return bytes(p)


ACKP_TYPE = 0  # u8  packet type being answered
ACKP_SEQ = 1  # u16 sequence of the packet being answered
NACKP_REASON = 3  # u8 nack_reason_t
ACKP_SIZE = 3
NACKP_SIZE = 4


@dataclass(frozen=True)
class Ack:
    acked_type: int
    acked_sequence: int

    @property
    def acked_name(self) -> str:
        return packet_type_name(self.acked_type)

    @classmethod
    def decode(cls, payload: bytes) -> "Ack":
        if len(payload) < ACKP_SIZE:
            raise MalformedPayload("ACK payload %d bytes, need %d" % (len(payload), ACKP_SIZE))
        return cls(acked_type=payload[ACKP_TYPE], acked_sequence=get_u16(payload, ACKP_SEQ))

    @staticmethod
    def encode(acked_type: PacketType | int, acked_sequence: int) -> bytes:
        return bytes((int(acked_type) & 0xFF,)) + put_u16(acked_sequence)


@dataclass(frozen=True)
class Nack:
    acked_type: int
    acked_sequence: int
    reason: int

    @property
    def acked_name(self) -> str:
        return packet_type_name(self.acked_type)

    @property
    def reason_name(self) -> str:
        return _enum_or(NackReason, self.reason, NackReason.BUSY).text

    @classmethod
    def decode(cls, payload: bytes) -> "Nack":
        if len(payload) < NACKP_SIZE:
            raise MalformedPayload("NACK payload %d bytes, need %d" % (len(payload), NACKP_SIZE))
        return cls(
            acked_type=payload[ACKP_TYPE],
            acked_sequence=get_u16(payload, ACKP_SEQ),
            reason=payload[NACKP_REASON],
        )

    @staticmethod
    def encode(acked_type: PacketType | int, acked_sequence: int, reason: NackReason | int) -> bytes:
        return Ack.encode(acked_type, acked_sequence) + bytes((int(reason) & 0xFF,))


def replace_frame(frame: Frame, **changes) -> Frame:
    """Small helper so callers do not import ``dataclasses.replace`` blindly."""
    return replace(frame, **changes)


# ------------------------------------------------------ typed payload decoders
# One call per packet type, all of them total for a CRC-valid frame: an
# unrecognised or short payload raises MalformedPayload, which the caller counts
# separately from link errors on purpose.

@dataclass(frozen=True)
class Pong:
    """``PONG`` (0x31): the echo of a ``PING`` token.

    ``protocol_service.c`` zero-pads or truncates the echo to exactly four bytes,
    so ``token`` is four bytes against this firmware even if the host sent more.
    Round-trip time is a host-side measurement (the frame's ``device_ts_ms`` is
    the *device's* boot clock), which is why it is not a field here.
    """

    token: bytes

    @classmethod
    def decode(cls, payload: bytes) -> "Pong":
        return cls(token=bytes(payload))

    @staticmethod
    def encode(token: bytes = b"\x00\x00\x00\x00") -> bytes:
        return bytes(token)

    @property
    def token_hex(self) -> str:
        return self.token.hex()


def decode_hello(frame: Frame | bytes) -> Hello:
    return Hello.decode(_payload_of(frame, PacketType.HELLO))


def decode_status(frame: Frame | bytes) -> StatusPacket:
    return StatusPacket.decode(_payload_of(frame, PacketType.STATUS))


def decode_temp_status(frame: Frame | bytes) -> TempStatus:
    return TempStatus.decode(_payload_of(frame, PacketType.TEMP_STATUS))


def decode_ecg_batch(frame: Frame | bytes) -> EcgBatch:
    """Decode an ``ECG_BATCH``."""
    return EcgBatch.decode(_payload_of(frame, PacketType.ECG_BATCH))


def decode_rtc_response(frame: Frame | bytes) -> RtcCalendar:
    """``RTC_RESPONSE``: the 7-byte calendar plus the trailing ``epoch:u32``."""
    payload = _payload_of(frame, PacketType.RTC_RESPONSE)
    if len(payload) < RTCP_CAL_SIZE + RTC_RESPONSE_EXTRA:
        raise MalformedPayload(
            "RTC_RESPONSE payload %d bytes, need %d (calendar + epoch)"
            % (len(payload), RTCP_CAL_SIZE + RTC_RESPONSE_EXTRA)
        )
    return RtcCalendar.decode(payload)


def decode_ack(frame: Frame | bytes) -> Ack:
    return Ack.decode(_payload_of(frame, PacketType.ACK))


def decode_nack(frame: Frame | bytes) -> Nack:
    return Nack.decode(_payload_of(frame, PacketType.NACK))


def decode_pong(frame: Frame | bytes) -> Pong:
    return Pong.decode(_payload_of(frame, PacketType.PONG))


def _payload_of(frame: Frame | bytes, expect: PacketType | int) -> bytes:
    """Accept either a :class:`Frame` (type-checked) or a raw payload."""
    if isinstance(frame, Frame):
        if frame.type != int(expect):
            raise MalformedPayload(
                "expected %s payload, got %s frame" % (packet_type_name(int(expect)), frame.type_name)
            )
        return frame.payload
    return bytes(frame)


_DECODERS = {
    int(PacketType.HELLO): decode_hello,
    int(PacketType.STATUS): decode_status,
    int(PacketType.TEMP_STATUS): decode_temp_status,
    int(PacketType.ECG_BATCH): decode_ecg_batch,
    int(PacketType.RTC_RESPONSE): decode_rtc_response,
    int(PacketType.ACK): decode_ack,
    int(PacketType.NACK): decode_nack,
    int(PacketType.PONG): decode_pong,
}


def decode_frame(frame: Frame) -> object:
    """Dispatch a frame's payload to its type's decoder.

    Raises :class:`MalformedPayload` for a payload that does not fit, and for a
    type this build does not know -- ``docs/PROTOCOL.md`` permits a device to add
    types, and the receiver is meant to skip them by length rather than crash, so
    callers that log rather than fail should catch the exception.
    """
    try:
        decoder = _DECODERS[frame.type]
    except KeyError:
        raise MalformedPayload("no decoder for packet type 0x%02X" % frame.type) from None
    return decoder(frame)


# --------------------------------------------------------- host -> device frames
# These are the only frames the PC is allowed to send, and they are the frames
# ``handle_frame()`` in protocol_service.c dispatches on.  Every one of them
# validates against the rules the firmware NACKs for, so a rejected command shows
# up as a local exception instead of a mystery NACK on the wire.
#
# The u32 header field is named ``device_ts_ms`` and is the device's boot clock.
# ``protocol_service.c`` reads only ``type``, ``sequence`` and ``payload`` from a
# host frame, so what the PC writes there is unconstrained by the contract; this
# module defaults it to the host's monotonic millisecond clock, clamped to u32,
# and says so instead of inventing a meaning.  Flagged as ambiguity #3.

def _host_ts_ms(device_ts_ms: int | None) -> int:
    """The u32 header stamp for a host frame; see the note above the encoders."""
    if device_ts_ms is None:
        return int(time.monotonic() * 1000.0) & 0xFFFFFFFF
    return int(device_ts_ms) & 0xFFFFFFFF


class SequenceCounter:
    """The per-direction ``sequence`` counter: starts at 0, wraps at 0xFFFF.

    ``docs/PROTOCOL.md``: "per-direction counter, wraps at 65535, starts at 0".
    The device checks it "for observation, not for rejection", so a skipped value
    is worth logging rather than retrying.
    """

    __slots__ = ("_next", "_issued")

    def __init__(self, start: int = 0) -> None:
        if not 0 <= int(start) <= 0xFFFF:
            raise ValueError("sequence start out of u16 range: %r" % (start,))
        self._next = int(start)
        self._issued = 0

    def next(self) -> int:
        """The value to put in the frame about to be sent, then advance."""
        value = self._next
        self._next = (self._next + 1) & 0xFFFF
        self._issued += 1
        return value

    @property
    def current(self) -> int:
        """The value :meth:`next` will return, without consuming it."""
        return self._next

    @property
    def issued(self) -> int:
        """Commands sent through this counter since construction/reset."""
        return self._issued

    def reset(self, start: int = 0) -> None:
        self.__init__(start)

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return "SequenceCounter(next=%d, issued=%d)" % (self._next, self._issued)


def encode_start_stream(sequence: int = 0, *, device_ts_ms: int | None = None) -> bytes:
    """``START_STREAM`` (0x80): begin *reporting*; the ADC never stops (docs)."""
    return encode_frame(PacketType.START_STREAM, sequence, _host_ts_ms(device_ts_ms))


def encode_stop_stream(sequence: int = 0, *, device_ts_ms: int | None = None) -> bytes:
    """``STOP_STREAM`` (0x81): stop reporting.  Symmetric with ``START_STREAM``."""
    return encode_frame(PacketType.STOP_STREAM, sequence, _host_ts_ms(device_ts_ms))


def encode_get_rtc(sequence: int = 0, *, device_ts_ms: int | None = None) -> bytes:
    """``GET_RTC`` (0x83): the device answers ``ACK`` then ``RTC_RESPONSE``."""
    return encode_frame(PacketType.GET_RTC, sequence, _host_ts_ms(device_ts_ms))


def _calendar_fields(
    calendar: object | None,
    year: int | None,
    month: int | None,
    day: int | None,
    hour: int,
    minute: int,
    second: int,
) -> tuple[int, int, int, int, int, int]:
    """Normalise the four accepted spellings of a calendar time into six ints.

    Accepts an :class:`RtcCalendar`, a 6-sequence, any object exposing
    ``year/month/day/hour/minute/second`` (a ``datetime`` does), or the fields as
    keywords.  Only the fields actually supplied override one another, so
    ``encode_set_rtc(datetime(2026, 9, 18, 14, 30, 0))`` needs no keywords.
    """
    if calendar is None:
        missing = [n for n, v in (("year", year), ("month", month), ("day", day)) if v is None]
        if missing:
            raise ValueError("encode_set_rtc needs a calendar or these fields: %s" % ", ".join(missing))
        return (int(year), int(month), int(day), int(hour), int(minute), int(second))
    if isinstance(calendar, RtcCalendar):
        return (calendar.year, calendar.month, calendar.day, calendar.hour, calendar.minute, calendar.second)
    if isinstance(calendar, (tuple, list)):
        if len(calendar) != 6:
            raise ValueError("a calendar sequence needs exactly 6 fields, got %d" % len(calendar))
        return tuple(int(v) for v in calendar)  # type: ignore[return-value]
    try:
        return (
            int(calendar.year),  # type: ignore[attr-defined]
            int(calendar.month),  # type: ignore[attr-defined]
            int(calendar.day),  # type: ignore[attr-defined]
            int(getattr(calendar, "hour", hour)),
            int(getattr(calendar, "minute", minute)),
            int(getattr(calendar, "second", second)),
        )
    except AttributeError:
        raise ValueError("cannot read a calendar out of %r" % (calendar,)) from None


def _validate_calendar_fields(fields: tuple[int, int, int, int, int, int]) -> None:
    """The device's own validation, applied before the bytes leave the PC.

    Delegated to :mod:`pc_monitor.rtc` -- the single implementation of
    proleptic-Gregorian field validation in this package -- through a deferred
    import, because ``rtc`` imports ``protocol`` and the reverse must not happen
    at module load time.
    """
    from . import rtc as _rtc

    _rtc.validate_calendar(*fields)


def encode_set_rtc(
    calendar: object = None,
    *,
    year: int | None = None,
    month: int | None = None,
    day: int | None = None,
    hour: int = 0,
    minute: int = 0,
    second: int = 0,
    sequence: int = 0,
    device_ts_ms: int | None = None,
    validate: bool = True,
) -> bytes:
    """``SET_RTC`` (0x82): the 7-byte calendar, no epoch on this direction.

    Accepts an :class:`RtcCalendar`, a 6-tuple, a ``datetime``/``date``, or the
    six fields as keywords.  ``validate`` mirrors the device's own rule -- an
    impossible calendar time earns ``NACK_BAD_VALUE`` and does not move the
    clock -- so validating here turns a wire rejection into a local
    :class:`ValueError` naming the field.
    """
    fields = _calendar_fields(calendar, year, month, day, hour, minute, second)
    if validate:
        _validate_calendar_fields(fields)
    payload = RtcCalendar.encode(*fields)
    if len(payload) != RTCP_CAL_SIZE:
        raise MalformedPayload("SET_RTC payload must be %d bytes, built %d" % (RTCP_CAL_SIZE, len(payload)))
    return encode_frame(PacketType.SET_RTC, sequence, _host_ts_ms(device_ts_ms), payload)


def encode_set_config(
    *,
    notch: NotchMode | int = NotchMode.HZ50,
    hr_low: int = 60,
    hr_high: int = 100,
    temp_low_centi: int = 3400,
    temp_high_centi: int = 3800,
    sequence: int = 0,
    device_ts_ms: int | None = None,
) -> bytes:
    """``SET_CONFIG`` (0x84): notch choice plus the three alarm bands.

    Rejected locally under the same conditions the firmware answers with
    ``NACK_BAD_VALUE``: ``notch > 2`` (``ECG_NOTCH_OFF``) or ``hr_low >= hr_high``
    (``handle_set_config`` compares them with ``>=``).
    """
    notch_value = int(notch)
    if notch_value not in (int(NotchMode.HZ50), int(NotchMode.HZ60), int(NotchMode.OFF)):
        raise ValueError("notch %d is not 0 (50 Hz), 1 (60 Hz) or 2 (off)" % notch_value)
    for name, value in (("hr_low", hr_low), ("hr_high", hr_high)):
        if not 0 <= int(value) <= 0xFF:
            raise ValueError("%s %r is not a u8 bpm" % (name, value))
    if int(hr_low) >= int(hr_high):
        raise ValueError("firmware requires hr_low < hr_high, got %d >= %d" % (hr_low, hr_high))
    for name, value in (("temp_low_centi", temp_low_centi), ("temp_high_centi", temp_high_centi)):
        if not -0x8000 <= int(value) <= 0x7FFF:
            raise ValueError("%s %r does not fit i16 centi-degC" % (name, value))
    if int(temp_low_centi) >= int(temp_high_centi):
        raise ValueError(
            "temp band is empty: %d >= %d centi-degC" % (temp_low_centi, temp_high_centi)
        )
    payload = SetConfig.encode(notch_value, hr_low, hr_high, temp_low_centi, temp_high_centi)
    return encode_frame(PacketType.SET_CONFIG, sequence, _host_ts_ms(device_ts_ms), payload)


#: ``handle_frame()`` echoes ``min(length, 4)`` bytes and zero-pads to four.
PING_TOKEN_SIZE = 4


def encode_ping(
    token: bytes | int = 0x01020304,
    *,
    sequence: int = 0,
    device_ts_ms: int | None = None,
) -> bytes:
    """``PING`` (0x90): an opaque token the device returns as a ``PONG``.

    A 4-byte int or 4 bytes of any content; longer or shorter payloads are legal
    on the link but the firmware truncates/pads the echo to four bytes, so the
    round trip stops proving the payload -- warned about rather than silently
    rewritten.
    """
    if isinstance(token, int):
        raw = put_u32(token & 0xFFFFFFFF)
    else:
        raw = bytes(token)
        if len(raw) > MAX_PAYLOAD:
            raise ValueError("PING token %d bytes exceeds MAX_PAYLOAD" % len(raw))
    return encode_frame(PacketType.PING, sequence, _host_ts_ms(device_ts_ms), raw)



# ----------------------------------------------------------- golden vectors
@dataclass(frozen=True)
class GoldenVector:
    """A whole frame as literal hex, generated by an *independent* CRC
    implementation so that a regression in :func:`crc16_ccitt` cannot hide by
    agreeing with itself.

    Each ``fields`` note spells out the byte breakdown, which is what lets a
    human verify the vector without running anything.
    """

    name: str
    hex_bytes: str
    type: int
    sequence: int
    device_ts_ms: int
    payload_len: int
    note: str

    @property
    def bytes(self) -> bytes:
        return bytes.fromhex(self.hex_bytes)


GOLDEN_FRAMES: tuple[GoldenVector, ...] = (
    GoldenVector(
        "empty_payload_frame",
        "A55A02800000000000000000B43C",
        0x80,
        0,
        0,
        0,
        "START_STREAM, zero-length payload: CRC 0x3CB4 stored little-endian as B4 3C",
    ),
    GoldenVector(
        "start_stream_seq1",
        "A55A028001000000E80300001E23",
        0x80,
        1,
        1000,
        0,
        "seq=0001 ts=0x000003E8(1000) len=0 crc=0x231E",
    ),
    GoldenVector(
        "stop_stream_seq2",
        "A55A028102000000D0070000A275",
        0x81,
        2,
        2000,
        0,
        "seq=0002 ts=0x000007D0(2000) len=0 crc=0x75A2",
    ),
    GoldenVector(
        "get_rtc_seq3",
        "A55A0283030000000000000085D9",
        0x83,
        3,
        0,
        0,
        "GET_RTC takes no payload",
    ),
    GoldenVector(
        "ping_token",
        "A55A029007000400D2040000112233449857",
        0x90,
        7,
        1234,
        4,
        "PING carries a 4-byte opaque token 11 22 33 44, echoed by PONG",
    ),
    GoldenVector(
        "set_rtc_2026_09_18_14_30_00",
        "A55A02820400070039300000EA0709120E1E0001"
                "89",
        0x82,
        4,
        12345,
        7,
        "SET_RTC calendar payload EA07=year 2026, 09 month, 12=18 day, 0E=14 h, 1E=30 min, 00 s",
    ),
    GoldenVector(
        "set_config_50hz",
        "A55A0284050007002B020000003C64480DD80E00"
                "47",
        0x84,
        5,
        555,
        7,
        "SET_CONFIG notch=0(50Hz) hr_low=60 hr_high=100 temp_low=3400 temp_high=3800 centi",
    ),
    GoldenVector(
        "ack_of_set_rtc",
        "A55A02400800030078030000820400F4DA",
        0x40,
        8,
        888,
        3,
        "ACK payload 82 0400: accepted SET_RTC whose sequence was 4",
    ),
    GoldenVector(
        "nack_bad_value",
        "A55A024109000400E703000082040004A89D",
        0x41,
        9,
        999,
        4,
        "NACK payload 82 0400 04: rejected SET_RTC, reason NACK_BAD_VALUE",
    ),
    GoldenVector(
        "hello_caps_none",
        "A55A020100000C00FA00000001000002E803140C"
                "0000000022DE",
        0x01,
        0,
        250,
        12,
        "HELLO fw 1.0.0 proto 2 rate 1000 batch_max 20 bits 12 caps 0x0000 + 2 pad bytes",
    ),
    GoldenVector(
        "temp_status_uncalibrated",
        "A55A02110600080070170000D204E803490E0400"
                "D113",
        0x11,
        6,
        6000,
        8,
        "TEMP_STATUS raw=1234 mv=1000 centi=3657 state=4(UNCALIBRATED) pad 00 -- degrees must not be shown",
    ),
    GoldenVector(
        "status_dump",
        "A55A020207002B00581B00000139300000070000"
                "00CD81010001490E4802013C0187D61200B1CB74"
                "000300000001000000E043E110000045B0",
        0x02,
        7,
        7000,
        43,
        "STATUS 43-byte payload per STP_* offsets, flags word 0x43E0",
    ),
    GoldenVector(
        "ecg_batch_n1_index0",
        "A55A0210000011000000000001E8030000000000"
                "08D204490E4802E043B742",
        0x10,
        0,
        0,
        17,
        "ECG_BATCH n=1 index=0 sample=2048 tail raw=1234 centi=3657 hr=72 hr_state=2 flags=0x43E0",
    ),
    GoldenVector(
        "ecg_batch_n3_index1000",
        "A55A0210050015008813000003E803E803000000"
                "080308FF07D204490E4802E0430E3A",
        0x10,
        5,
        5000,
        21,
        "ECG_BATCH n=3 index=1000 samples 2048,2051,2047 (0800/0803/07FF little-endian)",
    ),
    GoldenVector(
        "rtc_response_with_epoch",
        "A55A02200A000B00F2030000EA0709120E1E0040"
                "F9A16AFEE2",
        0x20,
        10,
        1010,
        11,
        "RTC_RESPONSE: 7-byte calendar + epoch 0x6AA1F940 = 1789000000",
    ),
    GoldenVector(
        "ecg_batch_n20_full",
        "A55A0210300037009001000014E803C0030000D0"
                "07D107D207D307D407D507D607D707D807D907DA"
                "07DB07DC07DD07DE07DF07E007E107E207E307D2"
                "04490E4802E043A917",
        0x10,
        48,
        400,
        55,
        "ECG_BATCH n=20 index=960 samples 2000..2019: payload 55, frame 69 under the ECGT_* offsets",
    ),
    GoldenVector(
        "max_payload_pong_64",
        "A55A02313F004000E703000001080F161D242B32"
                "3940474E555C636A71787F868D949BA2A9B0B7BE"
                "C5CCD3DAE1E8EFF6FD040B121920272E353C434A"
                "51585F666D747B828990979EA5ACB3BABAFE",
        0x31,
        63,
        999,
        64,
        "PONG with a 64-byte opaque payload: the largest legal frame, 78 bytes total",
    ),
)


def golden_by_name(name: str) -> GoldenVector:
    for vector in GOLDEN_FRAMES:
        if vector.name == name:
            return vector
    raise KeyError(name)


def iter_golden() -> Iterable[GoldenVector]:
    return iter(GOLDEN_FRAMES)
