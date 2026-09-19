#include "rtc_calendar.h"

#include <stddef.h>

/*
 * Days-since-civil-epoch arithmetic after Howard Hinnant's algorithm: exact,
 * table-free, branch-light and identical on every compiler because it is pure
 * integer arithmetic on values that never go negative in the supported range.
 *
 * The month rotation (March = month 1) is what makes the leap day fall at the
 * end of the adjusted year, so the 365.2425 correction factors work without a
 * per-month lookup table.
 */

static int32_t days_from_civil(uint16_t y, uint8_t m, uint8_t d)
{
    int32_t year = (int32_t)y;
    int32_t era;
    int32_t yoe;
    int32_t doy;
    int32_t doe;

    if (m <= 2U) {
        year -= 1;
    }
    era = (year >= 0 ? year : year - 399) / 400;
    yoe = year - era * 400;                              /* [0, 399] */

    {
        int32_t mp = (m > 2U) ? ((int32_t)m - 3) : ((int32_t)m + 9);
        doy = (153 * mp + 2) / 5 + (int32_t)d - 1;        /* [0, 365] */
    }
    doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;          /* [0, 146096] */
    return era * 146097 + doe - 719468;                   /* days since 1970-01-01 */
}

static void civil_from_days(int32_t z, uint16_t *y, uint8_t *m, uint8_t *d)
{
    int32_t era;
    int32_t doe;
    int32_t yoe;
    int32_t year;
    int32_t doy;
    int32_t mp;

    z += 719468;
    era = (z >= 0 ? z : z - 146096) / 146097;
    doe = z - era * 146097;                               /* [0, 146096] */
    yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;  /* [0, 399] */
    year = yoe + era * 400;
    doy = doe - (365 * yoe + yoe / 4 - yoe / 100);        /* [0, 365] */
    mp = (5 * doy + 2) / 153;                             /* [0, 11], March-based */
    *d = (uint8_t)(doy - (153 * mp + 2) / 5 + 1);         /* [1, 31] */
    *m = (uint8_t)(mp + (mp < 10 ? 3 : -9));              /* [1, 12] */
    if (*m <= 2U) {
        year += 1;
    }
    *y = (uint16_t)year;
}

bool rtc_is_leap(uint16_t year)
{
    return ((year % 4U) == 0U) && (((year % 100U) != 0U) || ((year % 400U) == 0U));
}

uint8_t rtc_days_in_month(uint16_t year, uint8_t month)
{
    static const uint8_t lengths[12] = {
        31U, 28U, 31U, 30U, 31U, 30U, 31U, 31U, 30U, 31U, 30U, 31U
    };

    if (month < 1U || month > 12U) {
        return 0U;
    }
    if (month == 2U && rtc_is_leap(year)) {
        return 29U;
    }
    return lengths[month - 1U];
}

bool rtc_datetime_valid(const rtc_datetime_t *dt)
{
    if (dt == NULL) {
        return false;
    }
    if (dt->year < RTC_EPOCH_MIN_YEAR || dt->year > RTC_EPOCH_MAX_YEAR) {
        return false;
    }
    if (dt->month < 1U || dt->month > 12U) {
        return false;
    }
    if (dt->day < 1U || dt->day > rtc_days_in_month(dt->year, dt->month)) {
        return false;
    }
    if (dt->hour > 23U || dt->minute > 59U || dt->second > 59U) {
        return false;
    }
    return true;
}

uint32_t rtc_to_epoch(const rtc_datetime_t *dt, bool *ok)
{
    int32_t days;
    uint32_t secs;

    if (!rtc_datetime_valid(dt)) {
        if (ok != NULL) {
            *ok = false;
        }
        return 0U;
    }
    days = days_from_civil(dt->year, dt->month, dt->day);
    /* days is >= 0 across the supported range; the multiply is done in 32 bits
     * because the maximum here is 474838400, well inside uint32. */
    secs = (uint32_t)days * 86400U
         + (uint32_t)dt->hour * 3600U
         + (uint32_t)dt->minute * 60U
         + (uint32_t)dt->second;
    if (ok != NULL) {
        *ok = true;
    }
    return secs;
}

void rtc_from_epoch(uint32_t epoch, rtc_datetime_t *dt)
{
    int32_t days;
    uint32_t rem;

    if (dt == NULL) {
        return;
    }
    days = (int32_t)(epoch / 86400U);
    rem = epoch % 86400U;

    civil_from_days(days, &dt->year, &dt->month, &dt->day);
    dt->hour = (uint8_t)(rem / 3600U);
    rem %= 3600U;
    dt->minute = (uint8_t)(rem / 60U);
    dt->second = (uint8_t)(rem % 60U);
    dt->weekday = rtc_weekday(dt->year, dt->month, dt->day);
}

uint8_t rtc_weekday(uint16_t year, uint8_t month, uint8_t day)
{
    int32_t days = days_from_civil(year, month, day);
    /* 1970-01-01 was a Thursday, so offset 4 puts Sunday at 0. */
    int32_t w = (days + 4) % 7;
    if (w < 0) {
        w += 7;
    }
    return (uint8_t)w;
}

uint32_t rtc_counter_elapsed(uint32_t counter_ref, uint32_t counter_now)
{
    return (uint32_t)(counter_now - counter_ref);
}

bool rtc_anchor_restore(const rtc_anchor_t *anchor, uint32_t counter_now,
                        uint32_t *epoch_out)
{
    uint64_t sum;

    if (anchor == NULL || epoch_out == NULL) {
        return false;
    }
    if (anchor->epoch > (uint32_t)RTC_EPOCH_MAX_SECOND) {
        return false;
    }

    sum = (uint64_t)anchor->epoch
        + (uint64_t)rtc_counter_elapsed(anchor->counter, counter_now);

    /* A counter that was reset while the backup domain kept its anchor produces a
     * wrapped, enormous delta here. Refusing is correct: the alternative is
     * confidently reporting a date in the next century. */
    if (sum > (uint64_t)RTC_EPOCH_MAX_SECOND) {
        return false;
    }
    *epoch_out = (uint32_t)sum;
    return true;
}
