"""Calendar arithmetic and the RTC wire forms.

``pc_monitor/rtc.py`` is a second, independent implementation of the same
conversion as ``App/rtc_service/rtc_calendar.c``.  ``tests/host/
test_rtc_calendar.c`` checks the C side against Python's ``datetime``; this checks
the Python side the same way, so the two firmware/host paths are pinned to the
same answer without either being trusted to agree with itself.
"""
from __future__ import annotations

import datetime as dt

import pytest

from pc_monitor import protocol as p
from pc_monitor import rtc


class TestCalendarArithmetic:
    @pytest.mark.parametrize("date", [
        (1970, 1, 1), (2000, 2, 29), (1900, 3, 1), (2100, 2, 28),
        (2024, 2, 29), (2023, 2, 28), (2038, 1, 19), (2038, 1, 20),
    ])
    def test_days_from_civil_matches_datetime(self, date):
        y, m, d = date
        want = (dt.date(y, m, d) - dt.date(1970, 1, 1)).days
        assert rtc.days_from_civil(y, m, d) == want

    def test_a_whole_year_round_trips_day_by_day(self):
        start = rtc.days_from_civil(2024, 1, 1)   # a leap year: 366 days
        for days in range(start, start + 366):
            y, m, d = rtc.civil_from_days(days)
            assert rtc.days_from_civil(y, m, d) == days
            assert dt.date(y, m, d).toordinal() - dt.date(1970, 1, 1).toordinal() == days

    def test_epoch_and_calendar_are_inverse_over_a_week(self):
        start = rtc.epoch_from_calendar(2026, 9, 18, 0, 0, 0)
        for seconds in range(0, 7 * 86400, 3607):
            cal = rtc.calendar_from_epoch(start + seconds)
            assert rtc.epoch_from_calendar(*cal) == start + seconds
            rtc.validate_calendar(*cal)  # every derived calendar must be legal

    def test_the_epoch_of_the_epoch_is_zero(self):
        assert rtc.epoch_from_calendar(1970, 1, 1, 0, 0, 0) == 0
        assert rtc.calendar_from_epoch(0) == (1970, 1, 1, 0, 0, 0)

    def test_leap_rules_including_the_century_exception(self):
        assert rtc.is_leap_year(2000) and rtc.is_leap_year(2024)
        assert not rtc.is_leap_year(1900) and not rtc.is_leap_year(2100)
        assert not rtc.is_leap_year(2023)

    @pytest.mark.parametrize("bad", [
        (2023, 2, 29, 0, 0, 0),   # no 29 Feb in a common year
        (2026, 13, 1, 0, 0, 0),
        (2026, 0, 1, 0, 0, 0),
        (2026, 1, 32, 0, 0, 0),
        (2026, 1, 1, 24, 0, 0),
        (2026, 1, 1, 0, 60, 0),
        (2026, 1, 1, 0, 0, 61),
        (1899, 1, 1, 0, 0, 0),    # below the supported window
    ])
    def test_impossible_instants_are_refused_not_wrapped(self, bad):
        with pytest.raises(ValueError):
            rtc.validate_calendar(*bad)

    def test_days_in_month_follows_the_leap_rule(self):
        assert rtc.days_in_month(2024, 2) == 29
        assert rtc.days_in_month(2023, 2) == 28
        assert all(rtc.days_in_month(2026, m) == d
                   for m, d in enumerate([31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31], 1))


class TestRtcWireForm:
    def test_payload_is_the_seven_calendar_bytes(self):
        cal = p.RtcCalendar(year=2026, month=9, day=18, hour=14, minute=30, second=0)
        payload = rtc.to_payload(cal)
        assert len(payload) == 7
        assert p.get_u16(payload, p.RTCP_YEAR) == 2026
        assert payload[p.RTCP_MONTH] == 9
        assert payload[p.RTCP_DAY] == 18
        assert payload[p.RTCP_HOUR] == 14
        assert payload[p.RTCP_MINUTE] == 30
        assert payload[p.RTCP_SECOND] == 0

    def test_the_optional_epoch_agrees_with_the_calendar_it_sits_next_to(self):
        cal = p.RtcCalendar(year=2031, month=11, day=23, hour=6, minute=45, second=12)
        payload = rtc.to_payload(cal, include_epoch=True)
        assert len(payload) == p.RTCP_CAL_SIZE + p.RTC_RESPONSE_EXTRA
        assert p.get_u32(payload, p.RTCP_CAL_SIZE) == rtc.epoch_from_calendar(
            2031, 11, 23, 6, 45, 12)

    def test_payload_round_trips(self):
        cal = p.RtcCalendar(year=2026, month=2, day=28, hour=1, minute=2, second=3)
        assert rtc.from_payload(rtc.to_payload(cal)) == cal

    def test_set_rtc_frame_is_the_size_the_firmware_requires(self):
        """``protocol_service.c`` rejects a SET_RTC shorter than RTCP_CAL_SIZE."""
        cal = rtc.pc_calendar()
        raw = p.encode_set_rtc(year=cal.year, month=cal.month, day=cal.day,
                               hour=cal.hour, minute=cal.minute, second=cal.second)
        _, frame, _ = p.parse_frame(raw)
        assert frame.type == int(p.PacketType.SET_RTC)
        assert frame.length == p.RTCP_CAL_SIZE
        back = rtc.from_payload(frame.payload)
        assert (back.year, back.month, back.day) == (cal.year, cal.month, cal.day)
        short = p.encode_frame(p.PacketType.SET_RTC, 2, 0, bytes(p.RTCP_CAL_SIZE - 1))
        _, frame, _ = p.parse_frame(short)
        with pytest.raises(p.MalformedPayload):
            rtc.from_payload(frame.payload)

    def test_the_host_refuses_what_the_device_would_nack(self):
        """The u32 epoch field reaches 2106; the firmware's calendar stops at 2099.

        Sending a year the device will NACK is a wasted round trip and a confusing
        error, so the host has to hold the narrower of the two limits.
        """
        low, high = rtc.u32_epoch_limits()
        assert low == 0 and high == 0xFFFFFFFF
        assert rtc.epoch_from_calendar(2099, 12, 31, 23, 59, 59) <= high
        assert rtc.RTC_MAX_YEAR == 2099
        assert rtc.RTC_MIN_YEAR == 1970
        for year in (2100, 2106, 3000):
            with pytest.raises(ValueError):
                rtc.validate_calendar(year, 1, 1, 0, 0, 0)
        with pytest.raises(ValueError):
            p.encode_set_rtc(year=2200, month=1, day=1)

    def test_pc_calendar_produces_something_the_device_will_accept(self):
        cal = rtc.pc_calendar()
        rtc.validate_calendar(cal.year, cal.month, cal.day, cal.hour,
                              cal.minute, cal.second)
        assert rtc.to_datetime(cal).year >= 2020
