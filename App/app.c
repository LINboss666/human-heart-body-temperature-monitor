#include "app.h"

#include "stm32f1xx_hal.h"

#include "acquisition/acquisition.h"
#include "app_config.h"
#include "buttons/buttons.h"
#include "diagnostics/diagnostics.h"
#include "display/oled_bus.h"
#include "ecg/ecg_hr.h"
#include "ecg/ecg_signal.h"
#include "protocol/protocol_service.h"
#include "rtc_service/rtc_service.h"
#include "temperature/temperature.h"
#include "uart/uart_link.h"
#include "ui/ui_app.h"

/*
 * Cooperative super-loop, no RTOS, no heap.
 *
 * The ordering is the design: hardware produces samples continuously and the
 * loop drains as much as is pending each pass, so UI work or a blocking UART
 * frame delays processing but never loses samples while the 128 ms block budget
 * holds. That budget is what makes skipping a cooperative scheduler defensible
 * here rather than lazy.
 */

static bool     s_recording;
static uint32_t s_recording_start_ms;
static uint32_t s_session_seconds;
static bool     s_app_started;

static void consume_blocks(uint32_t now_ms)
{
    const uint16_t *words;
    uint16_t frames;
    uint32_t first;
    uint8_t guard;

    /* Bounded so a stalled consumer cannot livelock the loop away from the UI. */
    for (guard = 0U; guard < ACQ_BLOCK_COUNT; guard++) {
        ecg_sample_t es;
        ecg_hr_t hr;
        temperature_t temp;

        if (!acquisition_take_block(&words, &frames, &first)) {
            return;
        }

        for (uint16_t i = 0U; i < frames; i++) {
            /* Rank 1 is channel 0 (ECG) and rank 2 is channel 1 (temperature),
             * so index 2i is ECG and 2i+1 is temperature. This interleave is the
             * reason the DMA length is an even number of frames. */
            uint16_t ecg_code = words[(i * 2U)];
            uint16_t temp_code = words[(i * 2U) + 1U];

            ecg_signal_process(ecg_code, &es);

            hr = *ecg_hr_current();
            if (es.r_peak) {
                ecg_hr_notify_beat(es.sample_index, &hr);
            }
            ecg_hr_tick(es.sample_index, &hr);

            temperature_feed(temp_code);

            if (ui_app_on_ecg_page()) {
                ui_app_push_waveform(es.display, es.sample_index);
            }
            protocol_service_push_sample(&es, &hr);
        }

        /* One refresh of the slower services per block, not per sample. */
        temperature_get(&temp);
        {
            diagnostics_t *d = diagnostics();
            const ecg_hr_t *h = ecg_hr_current();

            /* Frames produced, not beats detected: one frame is one ECG sample. */
            d->ecg_samples = acquisition_frames_produced();
            d->hr_bpm = h->valid ? h->bpm : 0U;
            d->hr_valid = h->valid;
            d->hr_state = (uint8_t)h->state;
            d->lead_state = (uint8_t)ecg_signal_lead_state();
            d->temp_centi = temp.centi_c;
            d->temp_raw = temp.raw;
            d->temp_valid = temp.valid;
            d->temp_state = (uint8_t)temp.state;
            d->temp_uncalibrated = !temp.calibrated;
            d->adc_running = acquisition_is_running();
            d->adc_calibrated = acquisition_calibrated();
            d->dma_blocks = acquisition_frames_produced() / ACQ_FRAMES_PER_BLOCK;
            d->dma_dropped = acquisition_dropped_blocks();
            d->oled_address = oled_bus_address_7bit();
            d->rtc_valid = rtc_service_is_valid();
            d->notch = (uint8_t)ecg_signal_get_notch();
        }
        (void)now_ms;
    }
}

void App_Init(void)
{
    diagnostics_init();

    /* Controller profile and address probe must happen before OLED_Init, which
     * reads both. A panel that does not answer is recorded, not fatal. */
    oled_bus_apply_profile((oled_controller_t)OLED_CONTROLLER_SELECTED);
    (void)oled_bus_scan();

    buttons_init();
    ecg_signal_reset();
    ecg_hr_reset(HR_BPM_LOW_DEFAULT, HR_BPM_HIGH_DEFAULT);
    temperature_init();
    rtc_service_init();
    if (rtc_service_sync_failed()) {
        /* RTC register synchronisation never completed, so this boot could not
         * read the counter and the clock is reported unset. Everything else can
         * still run. The count reaches the PC inside STATUS (protocol_errors) and
         * the code is held in last_error_code, which no screen renders yet -- this
         * is a bench-visible breadcrumb, not a user-facing message. Noted here
         * rather than in restore() because diagnostics_init() has not run at
         * MX_RTC_Init() time and would erase it. */
        diagnostics_note_error(DIAG_ERR_RTC_SYNC);
    }
    protocol_service_init();
    ui_app_init();

    if (!acquisition_start()) {
        /* Calibration or DMA arming failed. Everything that does not need the ADC
         * still runs; adc_running stays clear on the STATUS page and in the
         * STATUS packet, and the reason is held in last_error_code, rather than
         * the device trapping in Error_Handler() with nothing on screen. */
        diagnostics_note_error(DIAG_ERR_ACQUISITION_START);
    }

    s_recording = false;
    s_app_started = true;
    s_recording_start_ms = HAL_GetTick();
}

void App_SetRecording(bool on)
{
    if (on == s_recording) {
        return;
    }
    s_recording = on;
    s_recording_start_ms = HAL_GetTick();
    s_session_seconds = 0U;
    diagnostics()->recording = on;
}

bool App_Recording(void)
{
    return s_recording;
}

void App_ToggleRecording(void)
{
    App_SetRecording(!s_recording);
    protocol_service_set_streaming(s_recording);
}

uint32_t App_SessionSeconds(void)
{
    return s_session_seconds;
}

void App_Loop(void)
{
    uint32_t now_ms = HAL_GetTick();

    if (!s_app_started) {
        return;
    }

    buttons_scan(now_ms);
    uart_link_rx_pump();
    consume_blocks(now_ms);
    rtc_service_poll(now_ms);
    protocol_service_poll(now_ms);
    ui_app_update(now_ms);

    if (s_recording) {
        s_session_seconds = (uint32_t)(now_ms - s_recording_start_ms) / 1000U;
        diagnostics()->session_seconds = s_session_seconds;
    }
    diagnostics()->uptime_seconds = now_ms / 1000U;
    diagnostics()->streaming = protocol_service_streaming();
    diagnostics()->uart_rx_overruns = uart_link_rx_overruns();
}
