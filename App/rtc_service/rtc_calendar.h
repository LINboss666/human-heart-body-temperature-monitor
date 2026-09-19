/**
 * @file    rtc_calendar.h
 * @brief   Calendar <-> epoch conversion for the RTC service, with no HAL and no
 *          dependency on the host or target C library's time implementation.
 *
 * Split out of rtc_service.c deliberately: this is the part of the RTC story
 * that can be proven correct on a PC, and the firmware stores epoch seconds as
 * its internal time representation while RTC/TR/DR registers stay BCD. Letting
 * the standard library do the conversion would make the result depend on
 * time_t width, on whether the platform has a real zone database, and on
 * whether 1970 is representable - none of which is worth a gamble in firmware.
 *
 * Range is 1970-01-01 through 2099-12-31, which brackets the year-2038 boundary
 * on purpose: an implementation that stores epoch seconds in a signed int32 will
 * survive this range only because everything here is unsigned.
 */
#ifndef RTC_CALENDAR_H
#define RTC_CALENDAR_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define RTC_EPOCH_MIN_YEAR   1970U
#define RTC_EPOCH_MAX_YEAR   2099U

typedef struct {
    uint16_t year;      /**< 1970..2099 */
    uint8_t  month;     /**< 1..12 */
    uint8_t  day;       /**< 1..31, validated against the month */
    uint8_t  hour;      /**< 0..23 */
    uint8_t  minute;    /**< 0..59 */
    uint8_t  second;    /**< 0..59 */
    uint8_t  weekday;   /**< 0=Sunday .. 6=Saturday, derived, not stored */
} rtc_datetime_t;

/** True for a Gregorian leap year. Also the century-rule case: 2000 is, 2100 is not. */
bool rtc_is_leap(uint16_t year);

/** Days in a given month of a given year, including the February case. */
uint8_t rtc_days_in_month(uint16_t year, uint8_t month);

/** Every field checked, including month length. Rejects 2026-02-30. */
bool rtc_datetime_valid(const rtc_datetime_t *dt);

/** Seconds since 1970-01-01T00:00:00, or 0 with *ok == false when out of range. */
uint32_t rtc_to_epoch(const rtc_datetime_t *dt, bool *ok);

/** Inverse of rtc_to_epoch. Saturates at the supported range. */
void rtc_from_epoch(uint32_t epoch, rtc_datetime_t *dt);

/** 0=Sunday..6=Saturday for a valid date, computed from the epoch day count. */
uint8_t rtc_weekday(uint16_t year, uint8_t month, uint8_t day);

/* --------------------------------------------------- power-loss continuity */

/**
 * The supported epoch window as a value, not just as a year range.
 * 4102444799 = 2099-12-31T23:59:59Z; asserted against rtc_to_epoch() by
 * tests/host/test_rtc_calendar.c rather than trusted as a literal.
 */
#define RTC_EPOCH_MIN_SECOND  0U
#define RTC_EPOCH_MAX_SECOND  4102444799UL

/**
 * An absolute time paired with the hardware counter reading taken at that same
 * instant. This is what makes elapsed time survivable across a power interruption.
 *
 * The STM32F1 RTC peripheral is a 32-bit seconds counter in the backup domain;
 * unlike newer parts it holds no calendar. HAL_RTC_GetTime() folds whole days out
 * of that counter into hrtc->DateToUpdate, which is RAM and is lost when power
 * goes. So after VDD removal with VBAT still applied, the counter is the only
 * evidence of how long the clock was running, and an epoch alone cannot say.
 */
typedef struct {
    uint32_t epoch;     /**< absolute seconds at the instant counter was read */
    uint32_t counter;   /**< raw RTC counter at that same instant */
} rtc_anchor_t;

/**
 * Seconds the hardware counter advanced between two readings.
 *
 * Subtraction on uint32_t is defined modulo 2^32, which is exactly the wrap the
 * counter itself performs (every 136 years), so no special case is needed.
 */
uint32_t rtc_counter_elapsed(uint32_t counter_ref, uint32_t counter_now);

/**
 * Reconstruct the absolute time at counter_now from a stored anchor.
 *
 * Returns false rather than invent a timestamp when the anchor itself is outside
 * the supported window, or when the implied elapsed time would push past
 * 2099-12-31 -- which is also how a counter that was reset underneath a surviving
 * backup domain is caught, since its delta wraps to a multi-decade jump.
 */
bool rtc_anchor_restore(const rtc_anchor_t *anchor, uint32_t counter_now,
                        uint32_t *epoch_out);

#ifdef __cplusplus
}
#endif

#endif /* RTC_CALENDAR_H */
