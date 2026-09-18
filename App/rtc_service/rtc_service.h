/**
 * @file    rtc_service.h
 * @brief   Wall-clock service over the LSE-backed RTC, with epoch as the
 *          internal representation.
 *
 * Why preserve/restore hooks exist. The CubeMX regeneration that fixed the RTC
 * output (see docs/UPSTREAM.md and the first commit on this branch) also made
 * MX_RTC_Init() call HAL_RTC_SetTime(00:00:00) and HAL_RTC_SetDate(2000-01-01)
 * unconditionally on every boot. CubeMX 6.17 emits that block with no .ioc
 * switch that turns it off, so rather than hand-editing generated code - which
 * the next regeneration would silently revert - the service snapshots the clock
 * into a backup register immediately before the reset and writes it back
 * immediately after. The .ioc and the generated code stay mutually consistent,
 * which is the property the Phase 1 brief asks to preserve.
 *
 * Continuity across a power cycle still depends on VBAT, which is hardware this
 * project has not confirmed; see HARDWARE_TEST_PLAN.md.
 */
#ifndef RTC_SERVICE_H
#define RTC_SERVICE_H

#include <stdbool.h>
#include <stdint.h>

#include "rtc_calendar.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Called from USER CODE BEGIN RTC_Init 0, i.e. before CubeMX's unconditional
 * SetTime/SetDate. Reads the running clock and remembers it if the backup
 * register says the clock was previously initialised.
 */
void rtc_service_preserve(void);

/**
 * Called from USER CODE BEGIN RTC_Init 2, after CubeMX has reset the clock.
 * Restores the saved time when there was one, otherwise installs the default
 * start-of-epoch date and marks the clock as never-set.
 */
void rtc_service_restore(void);

/** Establish the software epoch from the hardware clock. Call after MX_RTC_Init. */
void rtc_service_init(void);

/** Re-sync the software epoch from the hardware clock. Call periodically. */
void rtc_service_poll(uint32_t now_ms);

uint32_t rtc_service_get_timestamp(void);
void     rtc_service_get_datetime(rtc_datetime_t *out);

/**
 * Set both the hardware RTC and the software epoch. Returns false without
 * touching the clock when any field is impossible - 2026-02-30, hour 24, and so
 * on - so a bad packet from the PC cannot corrupt the time.
 */
bool rtc_service_set_datetime(const rtc_datetime_t *dt);
bool rtc_service_set_timestamp(uint32_t epoch);

/**
 * False until the user or the PC has set the clock for the first time on this
 * board. True does not mean accurate, only that it was deliberately set.
 */
bool rtc_service_is_valid(void);

#ifdef __cplusplus
}
#endif

#endif /* RTC_SERVICE_H */
