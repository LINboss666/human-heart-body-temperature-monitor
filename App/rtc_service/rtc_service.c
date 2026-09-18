#include "rtc_service.h"

#include "stm32f1xx_hal.h"

#include "rtc.h"

/*
 * Preservation strategy, and why it is not "read the RTC registers and write
 * them back".
 *
 * rtc_service_preserve() runs from USER CODE BEGIN RTC_Init 0, before the
 * generated code has even assigned hrtc.Instance, so any HAL_RTC_GetTime() there
 * would dereference a null instance. The backup registers are used instead, as a
 * mirror of the software epoch refreshed once a second: after a reset that
 * clears them (no VBAT, or a genuinely first boot) the service reports "never
 * set" rather than inventing a time, and after one that does not, the clock
 * resumes where it stopped even though CubeMX has just forced it to
 * 2000-01-01.
 *
 * Two facts about this silicon shape the code. F1 backup registers are 16 bits
 * wide - HAL_RTCEx_BKUPWrite masks the value with BKP_DR1_D - so a 32-bit epoch
 * needs two of them. And BKP writes require the PWR and BKP clocks plus
 * HAL_PWR_EnableBkUpAccess(), which the generated HAL_RTC_MspInit does but which
 * has not run at preserve time, so it is done here.
 *
 * Note also that STM32F1's RTC_TimeTypeDef carries only Hours/Minutes/Seconds:
 * SubSeconds and the daylight-saving fields exist on other families and are
 * absent here, so they are not touched.
 */
#define BKP_MAGIC_VALUE   0x2B1CU
#define BKP_REG_MAGIC     RTC_BKP_DR1
#define BKP_REG_EPOCH_LO  RTC_BKP_DR2
#define BKP_REG_EPOCH_HI  RTC_BKP_DR3

/** Resync the software estimate against the hardware clock at this cadence. */
#define RTC_RESYNC_MS     1000U

static uint32_t s_epoch;
static uint32_t s_epoch_ticks;      /* HAL_GetTick() that s_epoch corresponds to */
static bool     s_valid;            /* deliberately set by user or PC at least once */
static bool     s_saved_available;
static uint32_t s_saved_epoch;
static uint32_t s_last_mirror_ms;
static uint32_t s_last_resync_ms;

static void bkp_unlock(void)
{
    __HAL_RCC_PWR_CLK_ENABLE();
    __HAL_RCC_BKP_CLK_ENABLE();
    HAL_PWR_EnableBkUpAccess();
}

static uint32_t bkp_read_epoch(void)
{
    uint32_t lo = HAL_RTCEx_BKUPRead(&hrtc, BKP_REG_EPOCH_LO);
    uint32_t hi = HAL_RTCEx_BKUPRead(&hrtc, BKP_REG_EPOCH_HI);
    return lo | (hi << 16);
}

static void bkp_write_epoch(uint32_t epoch)
{
    HAL_RTCEx_BKUPWrite(&hrtc, BKP_REG_EPOCH_LO, epoch & 0xFFFFU);
    HAL_RTCEx_BKUPWrite(&hrtc, BKP_REG_EPOCH_HI, epoch >> 16);
}

/** Push the software epoch into the hardware calendar registers. */
static bool hw_set(uint32_t epoch)
{
    rtc_datetime_t  dt;
    RTC_TimeTypeDef t;
    RTC_DateTypeDef d;

    rtc_from_epoch(epoch, &dt);
    if (!rtc_datetime_valid(&dt)) {
        return false;
    }

    t.Hours = dt.hour;
    t.Minutes = dt.minute;
    t.Seconds = dt.second;
    if (HAL_RTC_SetTime(&hrtc, &t, RTC_FORMAT_BIN) != HAL_OK) {
        return false;
    }

    d.WeekDay = dt.weekday;
    d.Month = dt.month;
    d.Date = dt.day;
    d.Year = (uint8_t)(dt.year % 100U);
    return HAL_RTC_SetDate(&hrtc, &d, RTC_FORMAT_BIN) == HAL_OK;
}

/** Pull the hardware calendar into a software epoch. False if unreadable. */
static bool hw_get(uint32_t *epoch_out)
{
    RTC_TimeTypeDef t;
    RTC_DateTypeDef d;
    rtc_datetime_t  dt;
    bool            ok = false;

    /* On F1 the time must be read before the date: the read locks the shadow
     * registers until both have been taken. */
    if (HAL_RTC_GetTime(&hrtc, &t, RTC_FORMAT_BIN) != HAL_OK) {
        return false;
    }
    if (HAL_RTC_GetDate(&hrtc, &d, RTC_FORMAT_BIN) != HAL_OK) {
        return false;
    }

    /* The hardware stores two digits; 19xx is outside the supported range, so
     * the 20xx reading is the only defensible one. */
    dt.year = (uint16_t)(2000U + (uint16_t)d.Year);
    dt.month = d.Month;
    dt.day = d.Date;
    dt.hour = t.Hours;
    dt.minute = t.Minutes;
    dt.second = t.Seconds;
    dt.weekday = d.WeekDay;

    if (dt.year < RTC_EPOCH_MIN_YEAR || dt.year > RTC_EPOCH_MAX_YEAR) {
        return false;
    }
    *epoch_out = rtc_to_epoch(&dt, &ok);
    return ok;
}

void rtc_service_preserve(void)
{
    bkp_unlock();
    s_saved_available = (HAL_RTCEx_BKUPRead(&hrtc, BKP_REG_MAGIC) == BKP_MAGIC_VALUE);
    s_saved_epoch = s_saved_available ? bkp_read_epoch() : 0U;
}

void rtc_service_restore(void)
{
    s_epoch_ticks = HAL_GetTick();

    if (!s_saved_available) {
        /* CubeMX has just written 2000-01-01 00:00:00. Leave it: the clock is
         * running, it is simply not set, and rtc_service_is_valid() says so. */
        s_epoch = 0U;
        return;
    }

    s_epoch = s_saved_epoch;
    if (!hw_set(s_epoch)) {
        /* The hardware refused the restore. Keep counting in software rather
         * than losing the time, and let the next resync discover the truth. */
        s_epoch_ticks = HAL_GetTick();
    }
}

void rtc_service_init(void)
{
    uint32_t hw_epoch;

    bkp_unlock();
    s_last_mirror_ms = HAL_GetTick();
    s_last_resync_ms = s_last_mirror_ms;

    if (hw_get(&hw_epoch)) {
        s_epoch = hw_epoch;
        s_epoch_ticks = HAL_GetTick();
    }
    s_valid = (HAL_RTCEx_BKUPRead(&hrtc, BKP_REG_MAGIC) == BKP_MAGIC_VALUE);
}

void rtc_service_poll(uint32_t now_ms)
{
    uint32_t elapsed_s;
    uint32_t hw_epoch;

    if ((uint32_t)(now_ms - s_last_resync_ms) < RTC_RESYNC_MS) {
        return;
    }
    s_last_resync_ms = now_ms;

    /* Advance the software epoch by the whole seconds that have gone by since it
     * was last sampled, so a busy main loop cannot make the clock run slow. Then
     * let the hardware correct any residual drift - but only forward. A clock
     * that browned out and came back at 2000-01-01 must not drag the displayed
     * time back four decades. */
    elapsed_s = (uint32_t)(now_ms - s_epoch_ticks) / 1000U;
    s_epoch += elapsed_s;
    s_epoch_ticks += elapsed_s * 1000U;

    if (hw_get(&hw_epoch)) {
        if (hw_epoch + 2U >= s_epoch) {
            s_epoch = hw_epoch;
            s_epoch_ticks = now_ms;
        } else if (s_valid) {
            /* Software is ahead of a clock that was reset: put it back. */
            (void)hw_set(s_epoch);
        }
    }

    if ((uint32_t)(now_ms - s_last_mirror_ms) >= RTC_RESYNC_MS) {
        s_last_mirror_ms = now_ms;
        bkp_write_epoch(s_epoch);
        HAL_RTCEx_BKUPWrite(&hrtc, BKP_REG_MAGIC,
                            s_valid ? (uint32_t)BKP_MAGIC_VALUE : 0U);
    }
}

uint32_t rtc_service_get_timestamp(void)
{
    uint32_t extra;

    extra = (uint32_t)(HAL_GetTick() - s_epoch_ticks) / 1000U;
    return s_epoch + extra;
}

void rtc_service_get_datetime(rtc_datetime_t *out)
{
    if (out != NULL) {
        rtc_from_epoch(rtc_service_get_timestamp(), out);
    }
}

bool rtc_service_set_datetime(const rtc_datetime_t *dt)
{
    bool ok = false;
    uint32_t epoch;

    if (!rtc_datetime_valid(dt)) {
        return false;
    }
    epoch = rtc_to_epoch(dt, &ok);
    if (!ok) {
        return false;
    }
    return rtc_service_set_timestamp(epoch);
}

bool rtc_service_set_timestamp(uint32_t epoch)
{
    rtc_datetime_t dt;

    rtc_from_epoch(epoch, &dt);
    if (!rtc_datetime_valid(&dt)) {
        return false;
    }
    if (!hw_set(epoch)) {
        return false;
    }

    s_epoch = epoch;
    s_epoch_ticks = HAL_GetTick();
    s_valid = true;

    bkp_unlock();
    bkp_write_epoch(epoch);
    HAL_RTCEx_BKUPWrite(&hrtc, BKP_REG_MAGIC, (uint32_t)BKP_MAGIC_VALUE);
    return true;
}

bool rtc_service_is_valid(void)
{
    return s_valid;
}
