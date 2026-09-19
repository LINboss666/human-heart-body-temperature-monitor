#include "rtc_service.h"

#include "stm32f1xx_hal.h"

#include "rtc.h"

/*
 * Three separate things have to be true before elapsed time can be reconstructed,
 * and each has its own failure mode:
 *
 *  1. The RTC core. A 32-bit seconds counter in the backup domain, still running
 *     through NRST and through VDD removal with VBAT applied. Nothing here can
 *     make it survive; it either is powered or it is not.
 *  2. The APB interface that presents that counter as CNTH/CNTL. Its registers
 *     are synchronised copies, so after a reset they must be re-acquired (RSF)
 *     before they can be trusted. rtc_sync_before_read() does that.
 *  3. The anchor in backup registers, mapping counter readings to Unix seconds.
 *     Written as a transaction: blank commit, payload, valid commit last.
 *
 * rtc_service_preserve() runs from USER CODE BEGIN RTC_Init 0, before the
 * generated code has assigned hrtc.Instance, so HAL_RTC_* calls cannot be used
 * there; HAL_RTCEx_BKUPRead/Write are the exception because they ignore the
 * handle. Reading RTC registers additionally needs the PWR and BKP clocks and the
 * DBP access bit, which the generated HAL_RTC_MspInit sets up only later, so
 * bkp_unlock() does it here first. The RTC interface clock (RCC_BDCR RTCEN) is
 * already on, enabled by HAL_RCCEx_PeriphCLKConfig in SystemClock_Config, so the
 * register access cannot hang the bus.
 *
 * F1 backup registers are 16 bits wide, so each 32-bit half of the anchor needs
 * two of them; STM32F103C8 has ten in total and five are used.
 *
 * STM32F1's RTC_TimeTypeDef carries only Hours/Minutes/Seconds; SubSeconds and the
 * daylight-saving fields exist on other families and are not touched here.
 */

/** Backup registers holding the anchor, indexed by RTC_ANCHOR_W_* . */
static const uint32_t s_bkp_reg[RTC_ANCHOR_WORDS] = {
    RTC_BKP_DR1, RTC_BKP_DR2, RTC_BKP_DR3, RTC_BKP_DR4, RTC_BKP_DR5
};

/** Resync the software estimate against the hardware clock at this cadence. */
#define RTC_RESYNC_MS     1000U

static uint32_t s_epoch;
static uint32_t s_epoch_ticks;      /* HAL_GetTick() that s_epoch corresponds to */
static bool     s_valid;            /* deliberately set by user or PC at least once */
static bool     s_anchor_available;
static bool     s_counter_valid;    /* CNTH/CNTL was readable through a done sync */
static bool     s_sync_failed;      /* sticky, for diagnostics after App_Init */
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
 * Bring the APB-visible RTC registers in step with the RTC core, and report
 * whether that completed.
 *
 * The RTC counter lives in the RTC core, which is powered from the backup domain
 * and keeps running straight through an NRST, a POR with VBAT, and Stop/Standby
 * wake-up. What reset does break is the AHB-to-APB bridge that presents CNTH and
 * CNTL to the bus: those are synchronised copies, and after a reset the first
 * read can return the value latched before it. Reading the elapsed counter
 * without re-acquiring RSF can therefore return a stale count, which turns a
 * "how long was the supply off" delta into an arbitrary number -- including zero.
 *
 * This is the same clear-then-poll the HAL performs in HAL_RTC_Init() via
 * HAL_RTC_WaitForSynchro(), reproduced on the registers rather than the handle:
 * at RTC_Init 0 hrtc.Instance has not been assigned yet, so neither that function
 * nor any other HAL_RTC_* call can be used this early. HAL_RTCEx_BKUPRead/Write
 * are the exception, and take the handle only to ignore it (UNUSED(hrtc)).
 *
 * Bounded by the HAL's own RTC_TIMEOUT_VALUE. If LSE is not running the wait
 * expires rather than hanging the boot; nothing before MX_RTC_Init() can have
 * failed for want of a clock here, because HAL_RCC_OscConfig() has already
 * returned with LSE ready or taken Error_Handler().
 */
static bool rtc_sync_before_read(void)
{
    uint32_t started = HAL_GetTick();

    CLEAR_BIT(RTC->CRL, RTC_FLAG_RSF);

    while ((RTC->CRL & RTC_FLAG_RSF) == 0U) {
        if ((uint32_t)(HAL_GetTick() - started) > RTC_TIMEOUT_VALUE) {
            return false;
        }
    }
    return true;
}

/**
 * Read the 32-bit RTC counter without a HAL handle.
 *
 * Only meaningful once rtc_sync_before_read() has said the mirror is fresh. Same
 * high/low/high re-read the HAL itself uses, because the upper half can tick over
 * between the two accesses.
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

static bool anchor_read(rtc_anchor_t *a)
{
    uint16_t words[RTC_ANCHOR_WORDS];
    uint8_t  i;

    for (i = 0U; i < RTC_ANCHOR_WORDS; i++) {
        words[i] = bkp_read(s_bkp_reg[i]);
    }
    return rtc_anchor_decode_words(words, a);
}

/**
 * Publish an anchor so that an interruption can only lose it, never corrupt it.
 *
 * The commit word is blanked first and written last, so any prefix of the update
 * leaves the pair undecodable; the fields are never read as a mixture of old and
 * new. A read-back catches a register that did not take, and demotes the anchor
 * back to blank rather than committing a value that is not there.
 *
 * What this buys over not blanking first: with magic left valid across the write,
 * VDD loss mid-update would hand the next boot a new epoch against an old counter
 * and reconstruct a plausible, wrong time. Reporting the clock unset instead is
 * strictly better.
 */
static void anchor_write(const rtc_anchor_t *a)
{
    rtc_anchor_write_t steps[RTC_ANCHOR_WRITE_STEPS];
    uint8_t n;
    uint8_t i;
    rtc_anchor_t readback;

    n = rtc_anchor_write(steps, a);

    for (i = 0U; i < n; i++) {
        bkp_write(s_bkp_reg[steps[i].slot], steps[i].value);
    }

    /* Verified into a local: a.out param here would overwrite the caller's
     * anchor, which is const precisely because it is the value being published. */
    if (!anchor_read(&readback)
        || readback.epoch != a->epoch || readback.counter != a->counter) {
        /* Something did not land, or did not come back as written. Blank the
         * commit word so the next boot reports "never set" rather than trusting a
         * half-written pair. */
        bkp_write(s_bkp_reg[RTC_ANCHOR_W_COMMIT], (uint32_t)RTC_ANCHOR_COMMIT_BLANK);
    }
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

    /* Order matters: unlock the backup interface, re-acquire the APB shadow, and
     * only then read the counter and the anchor. CNTH/CNTL are synchronised
     * copies of the RTC core; reading them before RSF has been re-acquired after
     * this reset can return the value latched before it, which would make the
     * elapsed delta wrong in either direction -- zero included. */
    s_counter_valid = rtc_sync_before_read();
    s_sync_failed = !s_counter_valid;

    /* The one reading that cannot be taken later. Everything after this point in
     * MX_RTC_Init overwrites the counter with seconds-of-day, so the elapsed
     * VBAT time is unrecoverable if it is not sampled here. */
    s_counter_at_boot = s_counter_valid ? hw_counter_raw() : 0U;

    s_anchor_available = anchor_read(&s_saved_anchor);
    if (!s_anchor_available) {
        s_saved_anchor.epoch = 0U;
        s_saved_anchor.counter = 0U;
    }
}

bool rtc_service_sync_failed(void)
{
    return s_sync_failed;
}

void rtc_service_restore(void)
{
    uint32_t epoch;

    s_epoch_ticks = HAL_GetTick();

    if (!s_anchor_available || !s_counter_valid
        || !rtc_anchor_restore(&s_saved_anchor, s_counter_at_boot, &epoch)) {
        /* Three distinct ways to have no honest time: genuinely never set (or an
         * anchor caught mid-update, which now decodes as unset rather than as a
         * mixture); the APB sync failing, which leaves the elapsed interval
         * unknown and the stored epoch merely hours stale; or an anchor and a
         * counter that disagree too badly to derive anything. In every case
         * CubeMX's 2000-01-01 is left running and rtc_service_is_valid() says
         * "not set", rather than presenting a constructed timestamp. */
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
    /* Trust the stored clock only when this boot actually read the counter; a
     * valid anchor with an unreadable elapsed time is not a valid clock. See
     * rtc_service_restore() for the same rule. */
    s_valid = s_anchor_available && s_counter_valid;
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
        if (s_valid && anchored) {
            /* Only ever store a pair that was observed together. If the hardware
             * could not be read this cycle the previous anchor is kept: it still
             * describes the counter, which kept advancing, so the next boot
             * reconstructs the true elapsed time from it. */
            anchor_now();
        } else if (!s_valid) {
            /* The clock was never deliberately set, so no anchor may exist: an old
             * one must not resurrect a time nobody chose. Blank the commit word
             * rather than write a payload and then invalidate it. */
            bkp_write(s_bkp_reg[RTC_ANCHOR_W_COMMIT],
                      (uint32_t)RTC_ANCHOR_COMMIT_BLANK);
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
