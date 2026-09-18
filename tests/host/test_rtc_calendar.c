/*
 * Host tests for the calendar arithmetic the RTC service depends on.
 *
 * These are the cases the brief names explicitly - 2024 leap year, 2026 normal
 * year, Feb 28/29, Dec 31 -> Jan 1 - plus a full round-trip sweep, because a
 * date conversion that is right on the tested dates and wrong in October is the
 * kind of defect that would otherwise surface as a corrupt recording timestamp
 * months from now.
 */
#include <stdint.h>
#include <string.h>

#include "ctest.h"

#include "rtc_service/rtc_calendar.h"

/* Independently sourced anchor points, computed with Python's datetime in UTC
 * rather than by this code, and with weekday re-based to Sunday=0. */
static void test_known_anchors(void)
{
    struct { uint16_t y, mo, d; uint32_t epoch; uint8_t wd; } v[] = {
        { 1970U, 1U,  1U,             0U, 4U },  /* Thursday */
        { 2000U, 1U,  1U,   946684800U, 6U },  /* Saturday */
        { 2024U, 2U, 29U,  1709164800U, 4U },  /* Thursday, leap day */
        { 2026U, 9U, 18U,  1789689600U, 5U },  /* Friday */
        { 2038U, 1U, 19U,  2147472000U, 2U },  /* Tuesday, last day under 2^31 */
        { 2038U, 1U, 20U,  2147558400U, 3U },  /* Wednesday, wholly past 2^31 */
        { 2099U, 12U, 31U, 4102358400U, 4U }   /* top of the supported range */
    };
    size_t i;

    CTEST_CASE("epoch anchors agree with independently computed values");
    for (i = 0U; i < sizeof(v) / sizeof(v[0]); i++) {
        rtc_datetime_t dt;
        rtc_datetime_t back;
        bool ok = false;
        uint32_t got;

        memset(&dt, 0, sizeof(dt));
        dt.year = v[i].y; dt.month = v[i].mo; dt.day = v[i].d;
        got = rtc_to_epoch(&dt, &ok);
        CHECK(ok);
        if (got != v[i].epoch) {
            printf("      %04u-%02u-%02u -> %lu want %lu\n",
                   v[i].y, v[i].mo, v[i].d, (unsigned long)got,
                   (unsigned long)v[i].epoch);
        }
        CHECK_EQ(got, v[i].epoch);

        rtc_from_epoch(v[i].epoch, &back);
        CHECK_EQ(back.year, v[i].y);
        CHECK_EQ(back.month, v[i].mo);
        CHECK_EQ(back.day, v[i].d);
        CHECK_EQ(back.hour, 0U);
        CHECK_EQ(back.weekday, v[i].wd);
    }
}

static void test_leap_rules(void)
{
    CTEST_CASE("Gregorian leap rules including the century exception");
    CHECK(rtc_is_leap(2024U));      /* divisible by 4 */
    CHECK(!rtc_is_leap(2026U));     /* the project's current year */
    CHECK(rtc_is_leap(2000U));      /* century, but divisible by 400 */
    CHECK(!rtc_is_leap(1900U));     /* century, not divisible by 400 */
    CHECK(rtc_is_leap(2096U));
    CHECK(!rtc_is_leap(2100U));     /* out of range but the rule must hold */
    CHECK(!rtc_is_leap(1970U));

    CHECK_EQ(rtc_days_in_month(2024U, 2U), 29U);
    CHECK_EQ(rtc_days_in_month(2026U, 2U), 28U);
    CHECK_EQ(rtc_days_in_month(2000U, 2U), 29U);
    CHECK_EQ(rtc_days_in_month(1900U, 2U), 28U);
    CHECK_EQ(rtc_days_in_month(2026U, 1U), 31U);
    CHECK_EQ(rtc_days_in_month(2026U, 4U), 30U);
    CHECK_EQ(rtc_days_in_month(2026U, 12U), 31U);
    CHECK_EQ(rtc_days_in_month(2026U, 13U), 0U);
    CHECK_EQ(rtc_days_in_month(2026U, 0U), 0U);
}

static void test_feb_28_29_boundaries(void)
{
    CTEST_CASE("Feb 28/29 straddle exactly one day in a leap year");
    {
        rtc_datetime_t a = { 2024U, 2U, 28U, 0U, 0U, 0U, 0U };
        rtc_datetime_t b = { 2024U, 2U, 29U, 0U, 0U, 0U, 0U };
        rtc_datetime_t c = { 2024U, 3U,  1U, 0U, 0U, 0U, 0U };
        bool ok;
        uint32_t ea = rtc_to_epoch(&a, &ok);  CHECK(ok);
        uint32_t eb = rtc_to_epoch(&b, &ok);  CHECK(ok);
        uint32_t ec = rtc_to_epoch(&c, &ok);  CHECK(ok);
        CHECK_EQ(eb - ea, 86400U);
        CHECK_EQ(ec - eb, 86400U);
    }
    CTEST_CASE("Feb 29 does not exist in a normal year");
    {
        rtc_datetime_t bad = { 2026U, 2U, 29U, 0U, 0U, 0U, 0U };
        bool ok = true;
        CHECK(!rtc_datetime_valid(&bad));
        CHECK_EQ(rtc_to_epoch(&bad, &ok), 0U);
        CHECK(!ok);
    }
}

static void test_year_rollover(void)
{
    rtc_datetime_t from;
    rtc_datetime_t to;
    bool ok;

    CTEST_CASE("Dec 31 23:59:59 -> Jan 1 00:00:00 advances exactly one second");
    from.year = 2025U; from.month = 12U; from.day = 31U;
    from.hour = 23U; from.minute = 59U; from.second = 59U;
    ok = false;
    {
        uint32_t e = rtc_to_epoch(&from, &ok);
        CHECK(ok);
        rtc_from_epoch(e + 1U, &to);
        CHECK_EQ(to.year, 2026U);
        CHECK_EQ(to.month, 1U);
        CHECK_EQ(to.day, 1U);
        CHECK_EQ(to.hour, 0U);
        CHECK_EQ(to.minute, 0U);
        CHECK_EQ(to.second, 0U);
    }
    CTEST_CASE("a full leap year is 366 days long");
    from.year = 2024U; from.month = 1U; from.day = 1U;
    from.hour = from.minute = from.second = 0U;
    to.year = 2025U; to.month = 1U; to.day = 1U;
    to.hour = to.minute = to.second = 0U;
    {
        uint32_t a = rtc_to_epoch(&from, &ok);   CHECK(ok);
        uint32_t b = rtc_to_epoch(&to, &ok);     CHECK(ok);
        CHECK_EQ(b - a, 366U * 86400U);
    }
    CTEST_CASE("a normal year is 365 days long");
    from.year = 2026U;
    to.year = 2027U;
    {
        uint32_t a = rtc_to_epoch(&from, &ok);   CHECK(ok);
        uint32_t b = rtc_to_epoch(&to, &ok);     CHECK(ok);
        CHECK_EQ(b - a, 365U * 86400U);
    }
}

static void test_roundtrip_sweep(void)
{
    uint32_t e;
    uint32_t bad = 0U;

    CTEST_CASE("every hour from 1970 to 2099 round-trips through both directions");
    for (e = 0U; e < 4102444800U; e += 3600U) {
        rtc_datetime_t dt;
        bool ok = false;
        uint32_t back;
        rtc_from_epoch(e, &dt);
        if (!rtc_datetime_valid(&dt)) { bad++; continue; }
        back = rtc_to_epoch(&dt, &ok);
        if (!ok || back != e) { bad++; }
    }
    CHECK_EQ(bad, 0U);
}

static void test_validation_rejects(void)
{
    CTEST_CASE("impossible values are refused rather than wrapped");
    struct { uint16_t y; uint8_t mo, d, h, mi, s; } bad[] = {
        { 1969U, 12U, 31U, 0U, 0U, 0U },   /* below range */
        { 2100U,  1U,  1U, 0U, 0U, 0U },   /* above range */
        { 2026U,  0U, 15U, 0U, 0U, 0U },   /* month 0 */
        { 2026U, 13U, 15U, 0U, 0U, 0U },   /* month 13 */
        { 2026U,  4U, 31U, 0U, 0U, 0U },   /* April has 30 days */
        { 2026U,  2U, 30U, 0U, 0U, 0U },   /* February, any year */
        { 2026U,  1U,  0U, 0U, 0U, 0U },   /* day 0 */
        { 2026U,  1U, 15U, 24U, 0U, 0U },  /* hour 24 */
        { 2026U,  1U, 15U, 0U, 60U, 0U },  /* minute 60 */
        { 2026U,  1U, 15U, 0U, 0U, 60U }   /* second 60 */
    };
    size_t i;
    for (i = 0U; i < sizeof(bad) / sizeof(bad[0]); i++) {
        rtc_datetime_t dt;
        bool ok = true;
        memset(&dt, 0, sizeof(dt));
        dt.year = bad[i].y; dt.month = bad[i].mo; dt.day = bad[i].d;
        dt.hour = bad[i].h; dt.minute = bad[i].mi; dt.second = bad[i].s;
        CHECK(!rtc_datetime_valid(&dt));
        CHECK_EQ(rtc_to_epoch(&dt, &ok), 0U);
        CHECK(!ok);
    }
    CHECK(!rtc_datetime_valid(NULL));
}

static void test_weekday_continuity(void)
{
    uint32_t e;
    uint8_t prev = 0xFFU;
    uint32_t faults = 0U;

    CTEST_CASE("weekday advances by exactly one per day for two years");
    for (e = 1735689600U; e < 1800000000U; e += 86400U) {
        rtc_datetime_t dt;
        rtc_from_epoch(e, &dt);
        if (prev != 0xFFU && dt.weekday != (uint8_t)((prev + 1U) % 7U)) {
            faults++;
        }
        prev = dt.weekday;
    }
    CHECK_EQ(faults, 0U);
}

CTEST_MAIN("rtc calendar")
{
    test_known_anchors();
    test_leap_rules();
    test_feb_28_29_boundaries();
    test_year_rollover();
    test_roundtrip_sweep();
    test_validation_rejects();
    test_weekday_continuity();
}
