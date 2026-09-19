/**
 * @file    rtc_service.h
 * @brief   Wall-clock service over the LSE-backed RTC, with epoch as the
 *          internal representation.
 *
 * Why preserve/restore hooks exist. The CubeMX regeneration that fixed the RTC
 * output (see docs/UPSTREAM.md and commit 488521b) also made MX_RTC_Init() call
 * HAL_RTC_SetTime(00:00:00) and HAL_RTC_SetDate(2000-01-01) unconditionally on
 * every boot. CubeMX 6.17 emits that block with no .ioc switch that turns it off,
 * so rather than hand-editing generated code - which the next regeneration would
 * silently revert - the service captures the clock immediately before the reset
 * and reconstructs it immediately after. The .ioc and the generated code stay
 * mutually consistent, which is the property the Phase 1 brief asks to preserve.
 *
 * What is captured, and why an epoch alone is not enough. The STM32F1 RTC is a
 * 32-bit seconds counter in the backup domain with no calendar in hardware;
 * HAL_RTC_GetTime() folds elapsed days out of that counter into hrtc->DateToUpdate,
 * which is RAM. If only the epoch were saved, a VDD interruption with VBAT still
 * applied would resume at the stored value and silently discard every second the
 * RTC spent counting meanwhile. The service therefore stores an ANCHOR - the epoch
 * paired with the raw counter read at that same instant - and reconstructs
 *
 *     epoch_at_boot = anchor.epoch + (counter_at_boot - anchor.counter)
 *
 * reading the raw counter in preserve(), before CubeMX's SetTime overwrites it.
 * The arithmetic itself is rtc_anchor_restore() in rtc_calendar.c, which is
 * host-tested including the counter-wrap and implausible-delta cases.
 *
 * VBAT retention itself is hardware this project has NOT confirmed; see
 * HARDWARE_TEST_PLAN.md. This makes the elapsed interval recoverable if the
 * hardware turns out to hold; it does not claim that it does.
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
