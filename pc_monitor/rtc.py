"""Calendar <-> epoch helpers for the ``SET_RTC`` / ``RTC_RESPONSE`` exchange.

Proleptic Gregorian throughout, with no dependency on the platform C library's
``mktime`` (which is undefined for pre-1970 dates on Windows and silently
applies DST).  The conversion is the days-from-civil algorithm of Howard
Hinnant, integer only, so a round trip is exact.

WIRE NOTE -- one genuine ambiguity, deliberately not hidden.
``SET_RTC`` carries ``year, month, day, hour, minute, second`` with no zone and
no UTC-offset field, while ``RTC_RESPONSE`` appends ``epoch:u32``.  Only one of
those can be pinned down by the contract: if the calendar is *local* wall-clock
time (what a human reads, and what an on-device RTC display shows), then the
epoch is the local time reinterpreted as UTC and disagrees with the true UTC
instant by the PC's offset; if the calendar is *UTC*, the epoch is exact but the
device clock no longer matches the wall clock the student is looking at.

``pc_monitor`` sends **local wall-clock time** by default, because the course
requirement is "the device shows the right time", and exposes
:func:`pc_calendar` with ``as_utc=`` for the alternative.  :func:`epoch_from_calendar`
always interprets its arguments as UTC, so the two conventions differ by exactly
``time.timezone`` and :func:`calendar_from_epoch` inverts it cleanly.
"""

from __future__ import annotations

import datetime as _dt
from typing import Tuple

from . import protocol

__all__ = [
    "EPOCH",
    "is_leap_year",
    "days_in_month",
    "days_from_civil",
    "civil_from_days",
    "epoch_from_calendar",
    "calendar_from_epoch",
    "validate_calendar",
    "pc_calendar",
    "to_payload",
    "from_payload",
    "u32_epoch_limits",
    "RTC_MIN_YEAR",
    "RTC_MAX_YEAR",
]

#: Unix epoch as a day number in the civil calendar.
EPOCH = _dt.date(1970, 1, 1)

# A u32 epoch would reach 2106-02-07, but the firmware does not: `RTC_EPOCH_MIN_YEAR
# .. RTC_EPOCH_MAX_YEAR` in App/rtc_service/rtc_calendar.h is 1970..2099 because the
# F1 RTC carries a two-digit year.  The host mirrors the narrower limit so a date
# the device would NACK is refused locally instead of round-tripping.
_EPOCH_U32_MAX = 0xFFFFFFFF

RTC_MIN_YEAR = 1970
RTC_MAX_YEAR = 2099

_DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def is_leap_year(year: int) -> bool:
    """Gregorian rule: divisible by 4, not by 100, unless by 400."""
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def days_in_month(year: int, month: int) -> int:
    if not 1 <= month <= 12:
        raise ValueError("month out of range: %r" % (month,))
    if month == 2 and is_leap_year(year):
        return 29
    return _DAYS_IN_MONTH[month - 1]


def days_from_civil(year: int, month: int, day: int) -> int:
    """Days since 1970-01-01 for a proleptic-Gregorian date (may be negative).

    Howard Hinnant's ``days_from_civil``, in Python's floor-division form: the
    ``era`` is a plain ``// 400`` because floor division already gives the
    mathematical floor that the C version has to arrange for.
    """
    y = year - (1 if month <= 2 else 0)
    era = y // 400
    yoe = y - era * 400  # [0, 399]
    doy = (153 * (month + (-3 if month > 2 else 9)) + 2) // 5 + day - 1  # [0, 365]
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy  # [0, 146096]
    return era * 146097 + doe - 719468


def civil_from_days(days: int) -> Tuple[int, int, int]:
    """Exact inverse of :func:`days_from_civil`."""
    z = days + 719468
    era = z // 146097
    doe = z - era * 146097  # [0, 146096]
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365  # [0, 399]
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)  # [0, 365]
    mp = (5 * doy + 2) // 153  # [0, 11]
    day = doy - (153 * mp + 2) // 5 + 1  # [1, 31]
    month = mp + (3 if mp < 10 else -9)  # [1, 12]
    return (y + (1 if month <= 2 else 0), month, day)


def epoch_from_calendar(year: int, month: int, day: int, hour: int, minute: int, second: int) -> int:
    """Seconds since 1970-01-01T00:00:00Z for the given fields, read as UTC."""
    validate_calendar(year, month, day, hour, minute, second)
    return (
        days_from_civil(year, month, day) * 86400 + hour * 3600 + minute * 60 + second
    )


def calendar_from_epoch(seconds: int) -> Tuple[int, int, int, int, int, int]:
    """Inverse of :func:`epoch_from_calendar`; accepts negative (pre-1970) values."""
    days, rem = divmod(int(seconds), 86400)
    year, month, day = civil_from_days(days)
    hour, rem = divmod(rem, 3600)
    minute, second = divmod(rem, 60)
    return year, month, day, hour, minute, second


def validate_calendar(year: int, month: int, day: int, hour: int, minute: int, second: int) -> None:
    """Reject what the firmware is specified to reject with ``NACK_BAD_VALUE``.

    Field ranges plus a real month length, which is where a leap-year bug would
    hide (29 February).
    """
    if not RTC_MIN_YEAR <= year <= RTC_MAX_YEAR:
        raise ValueError("year %d outside %d..%d (the firmware's range)"
                         % (year, RTC_MIN_YEAR, RTC_MAX_YEAR))
    if not 1 <= month <= 12:
        raise ValueError("month %d outside 1..12" % (month,))
    max_day = days_in_month(year, month)
    if not 1 <= day <= max_day:
        raise ValueError("%04d-%02d has %d days, not %d" % (year, month, max_day, day))
    if not 0 <= hour <= 23:
        raise ValueError("hour %d outside 0..23" % (hour,))
    if not 0 <= minute <= 59:
        raise ValueError("minute %d outside 0..59" % (minute,))
    if not 0 <= second <= 59:
        raise ValueError("second %d outside 0..59 (no leap second on the wire)" % (second,))


def pc_calendar(*, as_utc: bool = False, when: _dt.datetime | None = None) -> protocol.RtcCalendar:
    """The PC clock as an :class:`~pc_monitor.protocol.RtcCalendar`.

    Default is *local wall-clock* time; pass ``as_utc=True`` for UTC.  The naive
    fields are taken straight from the chosen instant, which is exactly what the
    7-byte payload can express.
    """
    moment = when if when is not None else _dt.datetime.now(_dt.timezone.utc)
    if not as_utc:
        moment = moment.astimezone()
    return protocol.RtcCalendar(
        year=moment.year,
        month=moment.month,
        day=moment.day,
        hour=moment.hour,
        minute=moment.minute,
        second=moment.second,
    )


def to_payload(cal: protocol.RtcCalendar, *, include_epoch: bool = False) -> bytes:
    """Encode a calendar, optionally as an ``RTC_RESPONSE`` (calendar + epoch)."""
    validate_calendar(cal.year, cal.month, cal.day, cal.hour, cal.minute, cal.second)
    epoch = None
    if include_epoch:
        epoch = epoch_from_calendar(cal.year, cal.month, cal.day, cal.hour, cal.minute, cal.second)
        if epoch < 0 or epoch > _EPOCH_U32_MAX:
            raise ValueError("epoch %d does not fit the u32 field" % epoch)
    return protocol.RtcCalendar.encode(
        cal.year, cal.month, cal.day, cal.hour, cal.minute, cal.second, epoch=epoch
    )


def from_payload(payload: bytes) -> protocol.RtcCalendar:
    return protocol.RtcCalendar.decode(payload)


def to_datetime(cal: protocol.RtcCalendar) -> _dt.datetime:
    """A naive datetime with the same fields; handy for a date picker."""
    return _dt.datetime(cal.year, cal.month, cal.day, cal.hour, cal.minute, cal.second)


def u32_epoch_limits() -> Tuple[int, int]:
    """The raw range of the ``epoch:u32`` wire field, in seconds since 1970.

    Wider than what the firmware accepts -- see ``RTC_MAX_YEAR``.  Kept because
    the difference between the two is the reason the host validates at all.
    """
    return (0, _EPOCH_U32_MAX)


def describe_epoch(seconds: int) -> str:
    year, month, day, hour, minute, second = calendar_from_epoch(seconds)
    return "%04d-%02d-%02dT%02d:%02d:%02dZ" % (year, month, day, hour, minute, second)
