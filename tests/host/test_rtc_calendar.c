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

/** Build a datetime and convert it, so the anchor tests below read as dates. */
static uint32_t rtc_to_epoch_dt(uint16_t y, uint8_t mo, uint8_t d,
                                uint8_t h, uint8_t mi, uint8_t s, bool *ok)
{
    rtc_datetime_t dt;

    memset(&dt, 0, sizeof(dt));
    dt.year = y;
    dt.month = mo;
    dt.day = d;
    dt.hour = h;
    dt.minute = mi;
    dt.second = s;
    return rtc_to_epoch(&dt, ok);
}

/*
 * The anchor arithmetic that makes elapsed VBAT time recoverable on STM32F1,
 * where the RTC peripheral is a bare seconds counter and the HAL keeps the
 * calendar in RAM. Everything CubeMX does at boot destroys the counter, so the
 * only thing that can prove "the clock ran for N seconds while the supply was
 * off" is the difference between two counter readings paired with an epoch.
 */
static void test_anchor_reconstruction(void)
{
    rtc_anchor_t  a;
    uint32_t      out;
    rtc_datetime_t dt;
    bool          ok;

    CTEST_CASE("the documented epoch ceiling is the one the code computes");
    {
        rtc_datetime_t top;
        bool           valid = false;

        memset(&top, 0, sizeof(top));
        top.year = RTC_EPOCH_MAX_YEAR;
        top.month = 12U;
        top.day = 31U;
        top.hour = 23U;
        top.minute = 59U;
        top.second = 59U;
        CHECK_EQ(rtc_to_epoch(&top, &valid), (uint32_t)RTC_EPOCH_MAX_SECOND);
        CHECK(valid);
        CHECK_EQ((uint32_t)RTC_EPOCH_MIN_SECOND, 0U);
    }

    a.epoch = rtc_to_epoch_dt(2026, 9, 18, 14, 30, 0, &ok);
    CHECK(ok);

    CTEST_CASE("no elapsed counter movement reproduces the anchor epoch exactly");
    a.counter = 500000U;
    CHECK(rtc_anchor_restore(&a, 500000U, &out));
    CHECK_EQ(out, a.epoch);

    CTEST_CASE("a software reset reconstructs the same second");
    /* NRST does not reset the backup domain, so the counter holds its value. */
    CHECK(rtc_anchor_restore(&a, a.counter, &out));
    CHECK_EQ(out, a.epoch);

    CTEST_CASE("an hour of VBAT-only running comes back as one hour");
    CHECK(rtc_anchor_restore(&a, a.counter + 3600U, &out));
    CHECK_EQ(out, a.epoch + 3600U);
    rtc_from_epoch(out, &dt);
    CHECK_EQ(dt.hour, 15U);
    CHECK_EQ(dt.day, 18U);

    CTEST_CASE("a 36-hour outage crosses midnight twice and is still exact");
    CHECK(rtc_anchor_restore(&a, a.counter + 36U * 3600U, &out));
    CHECK_EQ(out, a.epoch + 36U * 3600U);
    rtc_from_epoch(out, &dt);
    CHECK_EQ(dt.day, 20U);         /* 18th 14:30 + 36 h = 20th 02:30 */
    CHECK_EQ(dt.hour, 2U);
    CHECK_EQ(dt.minute, 30U);

    CTEST_CASE("the outage can cross a leap day");
    {
        rtc_datetime_t base;
        memset(&base, 0, sizeof(base));
        base.year = 2024; base.month = 2; base.day = 28;
        base.hour = 23; base.minute = 59; base.second = 59;
        a.epoch = rtc_to_epoch(&base, &ok);
        CHECK(ok);
        a.counter = 12345U;
        CHECK(rtc_anchor_restore(&a, a.counter + 1U, &out));
        rtc_from_epoch(out, &dt);
        CHECK_EQ(dt.month, 2U);
        CHECK_EQ(dt.day, 29U);      /* 2024 is a leap year */
    }

    CTEST_CASE("the outage can cross into a new year");
    {
        rtc_datetime_t base;
        memset(&base, 0, sizeof(base));
        base.year = 2025; base.month = 12; base.day = 31;
        base.hour = 23; base.minute = 59; base.second = 59;
        a.epoch = rtc_to_epoch(&base, &ok);
        CHECK(ok);
        a.counter = 0U;
        CHECK(rtc_anchor_restore(&a, 1U, &out));
        rtc_from_epoch(out, &dt);
        CHECK_EQ(dt.year, 2026U);
        CHECK_EQ(dt.month, 1U);
        CHECK_EQ(dt.day, 1U);
    }

    CTEST_CASE("counter wrap is handled by the modulo the hardware itself has");
    {
        uint32_t elapsed;

        a.epoch = rtc_to_epoch_dt(2026, 1, 1, 0, 0, 0, &ok);
        CHECK(ok);
        a.counter = 0xFFFFFFFFU - 10U;
        CHECK(rtc_anchor_restore(&a, 9U, &out));          /* wrapped past zero */
        CHECK_EQ(out, a.epoch + 20U);

        elapsed = rtc_counter_elapsed(0U, 0U);
        CHECK_EQ(elapsed, 0U);
        /* A counter reading below the anchor is not "negative time"; it is the
         * wrap the hardware performs at 2^32 seconds. */
        CHECK_EQ(rtc_counter_elapsed(10U, 5U), 0xFFFFFFFBUL);
        CHECK_EQ(rtc_counter_elapsed(0U, 0x80000000U), 0x80000000UL);
    }

    CTEST_CASE("an implausible delta is refused rather than turned into a future date");
    {
        /* Counter reset to zero while the anchor survived would otherwise ask for
         * 4 billion seconds of elapsed time. */
        a.epoch = rtc_to_epoch_dt(2026, 6, 1, 12, 0, 0, &ok);
        CHECK(ok);
        a.counter = 1000000U;
        CHECK(!rtc_anchor_restore(&a, 999999U, &out));
        CHECK(!rtc_anchor_restore(&a, 0U, &out));

        /* An anchor already past the supported window can never be repaired. */
        a.epoch = (uint32_t)RTC_EPOCH_MAX_SECOND;
        a.counter = 7U;
        CHECK(rtc_anchor_restore(&a, 7U, &out));          /* zero elapsed is fine */
        CHECK(!rtc_anchor_restore(&a, 8U, &out));         /* one second past the end */

        a.epoch = (uint32_t)RTC_EPOCH_MAX_SECOND + 1000U;
        a.counter = 7U;
        CHECK(!rtc_anchor_restore(&a, 7U, &out));
    }

    CTEST_CASE("NULL arguments are refused");
    CHECK(!rtc_anchor_restore(NULL, 0U, &out));
    a.epoch = 1000U;
    a.counter = 0U;
    CHECK(!rtc_anchor_restore(&a, 5U, NULL));
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
    test_anchor_reconstruction();
}
