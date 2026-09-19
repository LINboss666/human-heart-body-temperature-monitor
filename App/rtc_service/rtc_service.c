#include "rtc_service.h"

#include "stm32f1xx_hal.h"

#include "rtc.h"

/*
 * Backup-register layout and why the raw counter is read without the HAL.
 *
 * rtc_service_preserve() runs from USER CODE BEGIN RTC_Init 0, before the
 * generated code has even assigned hrtc.Instance, so any HAL_RTC_* call there
 * would dereference a null instance. The RTC registers themselves are in the
 * backup domain and can be read directly once the PWR and BKP clocks and the
 * DBP access bit are on, which the generated HAL_RTC_MspInit does but which has
 * not run at preserve time - so bkp_unlock() does it here first.
 *
 * F1 backup registers are 16 bits wide (HAL_RTCEx_BKUPWrite masks the value with
 * BKP_DR1_D), so each 32-bit half of the anchor needs two of them.
 *
 * STM32F1's RTC_TimeTypeDef carries only Hours/Minutes/Seconds; SubSeconds and
 * the daylight-saving fields exist on other families and are not touched here.
 */
#define BKP_MAGIC_VALUE   0x2B1CU
#define BKP_REG_MAGIC     RTC_BKP_DR1
#define BKP_REG_EPOCH_LO  RTC_BKP_DR2
#define BKP_REG_EPOCH_HI  RTC_BKP_DR3
#define BKP_REG_CNT_LO    RTC_BKP_DR4
#define BKP_REG_CNT_HI    RTC_BKP_DR5

/** Resync the software estimate against the hardware clock at this cadence. */
#define RTC_RESYNC_MS     1000U

static uint32_t s_epoch;
static uint32_t s_epoch_ticks;      /* HAL_GetTick() that s_epoch corresponds to */
static bool     s_valid;            /* deliberately set by user or PC at least once */
static bool     s_anchor_available;
static rtc_anchor_t s_saved_anchor;
static uint32_t s_counter_at_boot;  /* raw RTC counter, read before CubeMX resets it */
static uint32_t s_last_mirror_ms;
static uint32_t s_last_resync_ms;

static void bkp_unlock(void)
{
    __HAL_RCC_PWR_CLK_ENABLE();
    __HAL_RCC_BKP_CLK_ENABLE();
    HAL_PWR_EnableBkUpAccess();
}

static uint16_t bkp_read(uint32_t reg)
{
    return (uint16_t)(HAL_RTCEx_BKUPRead(&hrtc, reg) & 0xFFFFU);
}

static void bkp_write(uint32_t reg, uint32_t value)
{
    HAL_RTCEx_BKUPWrite(&hrtc, reg, value & 0xFFFFU);
}

/**
 * Read the 32-bit RTC counter without a HAL handle.
 *
 * Same high/low/high re-read the HAL itself uses, because the upper half can
 * tick over between the two accesses.
 */
static uint32_t hw_counter_raw(void)
{
    uint32_t high1 = RTC->CNTH & RTC_CNTH_RTC_CNT;
    uint32_t low   = RTC->CNTL & RTC_CNTL_RTC_CNT;
    uint32_t high2 = RTC->CNTH & RTC_CNTH_RTC_CNT;

    if (high1 != high2) {
        return (high2 << 16) | (RTC->CNTL & RTC_CNTL_RTC_CNT);
    }
    return (high1 << 16) | low;
}

static void anchor_write(const rtc_anchor_t *a)
{
    bkp_write(BKP_REG_EPOCH_LO, a->epoch);
    bkp_write(BKP_REG_EPOCH_HI, a->epoch >> 16);
    bkp_write(BKP_REG_CNT_LO,   a->counter);
    bkp_write(BKP_REG_CNT_HI,   a->counter >> 16);
    bkp_write(BKP_REG_MAGIC,    BKP_MAGIC_VALUE);
}

/** Pair the current software epoch with the hardware counter as read now. */
static void anchor_now(void)
{
    rtc_anchor_t a;

    a.epoch = rtc_service_get_timestamp();
    a.counter = hw_counter_raw();
    anchor_write(&a);
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
    s_anchor_available = (bkp_read(BKP_REG_MAGIC) == BKP_MAGIC_VALUE);
    if (s_anchor_available) {
        s_saved_anchor.epoch   = (uint32_t)bkp_read(BKP_REG_EPOCH_LO)
                               | ((uint32_t)bkp_read(BKP_REG_EPOCH_HI) << 16);
        s_saved_anchor.counter = (uint32_t)bkp_read(BKP_REG_CNT_LO)
                               | ((uint32_t)bkp_read(BKP_REG_CNT_HI) << 16);
    } else {
        s_saved_anchor.epoch = 0U;
        s_saved_anchor.counter = 0U;
    }
    /* The one reading that cannot be taken later. Everything after this point in
     * MX_RTC_Init overwrites the counter with seconds-of-day, so the elapsed
     * VBAT time is unrecoverable if it is not sampled here. */
    s_counter_at_boot = hw_counter_raw();
}

void rtc_service_restore(void)
{
    uint32_t epoch;

    s_epoch_ticks = HAL_GetTick();

    if (!s_anchor_available || !rtc_anchor_restore(&s_saved_anchor,
                                                   s_counter_at_boot, &epoch)) {
        /* Either genuinely never set, or the anchor and the counter disagree so
         * badly that no honest time can be derived from them. CubeMX has just
         * written 2000-01-01; leave it running and let rtc_service_is_valid()
         * say "not set" rather than present an invented timestamp. */
        s_epoch = 0U;
        s_valid = false;
        return;
    }

    s_epoch = epoch;
    s_valid = true;
    if (!hw_set(s_epoch)) {
        /* The hardware refused the reconstruction. Keep counting in software from
         * the anchor anyway; that is strictly better than losing the clock, and
         * the next resync will discover the truth. */
        s_epoch_ticks = HAL_GetTick();
    }
    /* Re-anchor immediately: hw_set() normalised the counter to seconds-of-day,
     * so the stored pair must describe the counter as it now reads. */
    anchor_now();
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
    s_valid = (bkp_read(BKP_REG_MAGIC) == BKP_MAGIC_VALUE);
}

void rtc_service_poll(uint32_t now_ms)
{
    uint32_t elapsed_s;
    uint32_t hw_epoch;
    bool     anchored;

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

    anchored = false;
    if (hw_get(&hw_epoch)) {
        if (hw_epoch + 2U >= s_epoch) {
            s_epoch = hw_epoch;
            s_epoch_ticks = now_ms;
            anchored = true;      /* just read the hardware's own time */
        } else if (s_valid) {
            /* Software is ahead of a clock that was reset: put it back. */
            if (hw_set(s_epoch)) {
                anchored = true;  /* counter now means s_epoch */
            }
        }
    }

    if ((uint32_t)(now_ms - s_last_mirror_ms) >= RTC_RESYNC_MS) {
        s_last_mirror_ms = now_ms;
        /* Only ever store a pair that was observed together. If the hardware
         * could not be read this cycle, the previous anchor stays valid: it
         * still describes the counter, which kept advancing, so the next boot
         * reconstructs the true elapsed time from it. */
        if (anchored) {
            anchor_now();
        }
        if (!s_valid) {
            bkp_write(BKP_REG_MAGIC, 0U);
        }
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
    /* hw_set() above wrote seconds-of-day into the counter, so this is the moment
     * the two halves of the anchor are guaranteed to agree. */
    anchor_now();
    return true;
}

bool rtc_service_is_valid(void)
{
    return s_valid;
}
