/**
 * @file    ui_app.c
 * @brief   KK_UI integration: page table, custom ECG waveform page, refresh.
 *
 * Page inventory. HOME is switched off in kk_ui_config.h and the root is a menu
 * instead. That is a deliberate trade: the HOME template needs a 32x32 XBM per
 * entry, which is four more authored bitmaps plus their verification and their
 * licensing, for decoration on a 64 KB part. The menu template already gives a
 * highlighted, scrollable, key-driven list, which is what a three-button 128x64
 * interface needs in order to read as a product rather than a text dump.
 *
 * The ECG page is a KK_UI custom page because the library has no waveform widget
 * and kk-ui-extend's own instructions forbid adding one. The trace is drawn here
 * with KK_OLED primitives.
 */
#include "ui_app.h"

#include <stdio.h>
#include <string.h>

#include "app.h"
#include "app_config.h"
#include "buttons.h"
#include "diagnostics.h"
#include "ecg_config.h"
#include "ecg_hr.h"
#include "kk_oled.h"
#include "kk_ui.h"
#include "oled_bringup_test.h"
#include "oled_bus.h"
#include "protocol.h"
#include "protocol_service.h"
#include "rtc_calendar.h"
#include "rtc_service.h"
#include "temperature.h"
#include "ui_fonts.h"

/*
 * Page numbering follows KK_UI's rule that routes[0] is page ID 1 and ID 0 is
 * invalid, so this enum is the array order plus one rather than a free choice.
 * Being off by one here is the classic way to make every menu entry open its
 * neighbour.
 */
enum {
    PAGE_NONE = 0,        /* KK_UI page ids start at 1, so 0 means "not on a page" */
    PAGE_MAIN = 1,
    PAGE_ECG,
    PAGE_TEMP,
    PAGE_STATUS,
    PAGE_DATETIME,
    PAGE_SETTINGS,
    PAGE_ABOUT,
    PAGE_COUNT
};

enum { EVT_APPLY_RTC = 1, EVT_READ_RTC, EVT_STREAM_TOGGLE };

/* ------------------------------------------------------------- page tables */

static const KK_UI_PageRoute routes[] = {
    { KK_UI_PAGE_MENU,   0U },   /* 1 PAGE_MAIN     */
    { KK_UI_PAGE_CUSTOM, 0U },   /* 2 PAGE_ECG      */
    { KK_UI_PAGE_INFO,   0U },   /* 3 PAGE_TEMP     */
    { KK_UI_PAGE_INFO,   1U },   /* 4 PAGE_STATUS   */
    { KK_UI_PAGE_MENU,   1U },   /* 5 PAGE_DATETIME */
    { KK_UI_PAGE_MENU,   2U },   /* 6 PAGE_SETTINGS */
    { KK_UI_PAGE_INFO,   2U }    /* 7 PAGE_ABOUT    */
};

/*
 * Editable values. KK_UI_Init refuses to start if a bound value is already
 * outside its declared interval, so each one is seeded inside it and
 * seed_from_rtc() clamps whatever it reads back from the clock.
 */
static int32_t v_year = 2026, v_month = 1, v_day = 1;
static int32_t v_hour = 12, v_minute = 0, v_second = 0;
static int32_t v_notch = 0;                 /* 0 = 50 Hz, 1 = 60 Hz, 2 = off */
static int32_t v_hr_low = HR_BPM_LOW_DEFAULT;
static int32_t v_hr_high = HR_BPM_HIGH_DEFAULT;
/* Held in 0.1 degC so the editor step and its unit stay readable; converted to
 * the service's centi-degrees at the point of use. */
static int32_t v_temp_low_dc = TEMP_CENTI_LOW_DEFAULT / 10;
static int32_t v_temp_high_dc = TEMP_CENTI_HIGH_DEFAULT / 10;
static bool    v_stream = false;

/* Menu item state tables: two bits per item, (count + 3) / 4 bytes. */
static uint8_t main_states[(6U + 3U) / 4U];
static uint8_t rtc_states[(8U + 3U) / 4U];
static uint8_t set_states[(6U + 3U) / 4U];

static const KK_UI_MenuItem main_items[] = {
    { "ECG MONITOR", KK_UI_MENU_PAGE,    PAGE_ECG },
    { "BODY TEMP",   KK_UI_MENU_PAGE,    PAGE_TEMP },
    { "STATUS",      KK_UI_MENU_PAGE,    PAGE_STATUS },
    { "DATE & TIME", KK_UI_MENU_PAGE,    PAGE_DATETIME },
    { "SETTINGS",    KK_UI_MENU_PAGE,    PAGE_SETTINGS },
    { "ABOUT",       KK_UI_MENU_PAGE,    PAGE_ABOUT }
};

static const KK_UI_MenuItem rtc_items[] = {
    { "Year",       KK_UI_MENU_INT,    0U },
    { "Month",      KK_UI_MENU_INT,    1U },
    { "Day",        KK_UI_MENU_INT,    2U },
    { "Hour",       KK_UI_MENU_INT,    3U },
    { "Minute",     KK_UI_MENU_INT,    4U },
    { "Second",     KK_UI_MENU_INT,    5U },
    { "SET RTC",    KK_UI_MENU_ACTION, EVT_APPLY_RTC },
    { "READ RTC",   KK_UI_MENU_ACTION, EVT_READ_RTC }
};

static const KK_UI_MenuItem set_items[] = {
    { "Mains notch", KK_UI_MENU_INT,   6U },
    { "HR low",      KK_UI_MENU_INT,   7U },
    { "HR high",     KK_UI_MENU_INT,   8U },
    { "Temp low",    KK_UI_MENU_INT,   9U },
    { "Temp high",   KK_UI_MENU_INT,  10U },
    { "PC stream",   KK_UI_MENU_BOOL,  0U }
};

static const KK_UI_IntBinding int_bindings[] = {
    { "Year",   &v_year,        RTC_EPOCH_MIN_YEAR, RTC_EPOCH_MAX_YEAR, 1U, "",          0U },
    { "Month",  &v_month,       1U,   12U,  1U, "",          0U },
    { "Day",    &v_day,         1U,   31U,  1U, "",          0U },
    { "Hour",   &v_hour,        0U,   23U,  1U, "h",         0U },
    { "Minute", &v_minute,      0U,   59U,  1U, "m",         0U },
    { "Second", &v_second,      0U,   59U,  1U, "s",         0U },
    { "Notch",  &v_notch,       0U,   2U,   1U, "50/60/off", 0U },
    { "HR low", &v_hr_low,      HR_BPM_MIN_VALID, 120U, 1U, "bpm", 0U },
    { "HR high",&v_hr_high,     80U, HR_BPM_MAX_VALID, 1U, "bpm", 0U },
    { "T low",  &v_temp_low_dc, 250U, 380U, 5U, "x0.1C",     0U },
    { "T high", &v_temp_high_dc,350U, 450U, 5U, "x0.1C",     0U }
};

static const KK_UI_BoolBinding bool_bindings[] = {
    { "Stream to PC", &v_stream, EVT_STREAM_TOGGLE }
};

/* ------------------------------------------------------------- live text */

/*
 * KK_UI keeps these pointers for the life of the UI, so values are formatted
 * into static buffers rather than rebound. Handing the library a stack or
 * transient string is a guaranteed bug by its own porting rules.
 */
static char s_hr_text[12];
static char s_temp_text[12];
static char s_clock_text[20];
static char s_lead_text[14];
static char s_probe_text[14];
static char s_rec_text[14];
static char s_status_rows[8][14];
static char s_about_mcu[16];
static char s_about_fw[24];
static char s_about_proto[16];

static const KK_UI_InfoRow temp_rows[] = {
    { "Heart rate", s_hr_text },
    { "Lead",       s_lead_text },
    { "Body temp",  s_temp_text },
    { "Probe",      s_probe_text },
    { "Clock",      s_clock_text },
    { "Recording",  s_rec_text }
};

static const KK_UI_InfoRow status_rows[] = {
    { "OLED",    s_status_rows[0] },
    { "ADC",     s_status_rows[1] },
    { "RTC",     s_status_rows[2] },
    { "UART",    s_status_rows[3] },
    { "ECG",     s_status_rows[4] },
    { "TEMP",    s_status_rows[5] },
    { "DROP",    s_status_rows[6] },
    { "CRC ERR", s_status_rows[7] }
};

static const KK_UI_InfoRow about_rows[] = {
    { "Name",     (const char *)"HHBTM" },
    { "MCU",      s_about_mcu },
    { "Firmware", s_about_fw },
    { "Protocol", s_about_proto },
    { "Safety",   (const char *)"EDUCATIONAL, NOT MEDICAL" }
};

static const KK_UI_MenuPage menu_pages[] = {
    { "MAIN MENU",   main_items, main_states, 6U },
    { "DATE & TIME", rtc_items,  rtc_states,  8U },
    { "SETTINGS",    set_items,  set_states,  6U }
};

static const KK_UI_InfoPage info_pages[] = {
    { "MONITOR",     temp_rows,   (uint16_t)(sizeof(temp_rows) / sizeof(temp_rows[0])) },
    { "DIAGNOSTICS", status_rows, (uint16_t)(sizeof(status_rows) / sizeof(status_rows[0])) },
    { "ABOUT",       about_rows,  (uint16_t)(sizeof(about_rows) / sizeof(about_rows[0])) }
};

/* ------------------------------------------------------------ ECG trace */

/*
 * One column per eight samples, holding that group's min and max, so a QRS
 * narrower than the decimation window still paints instead of falling between
 * samples. Two int16 per column times 128 columns = 512 bytes.
 */
static int16_t  s_col_min[WAVE_COLUMNS];
static int16_t  s_col_max[WAVE_COLUMNS];
static uint16_t s_col_next;
static int16_t  s_run_min;
static int16_t  s_run_max;
static uint16_t s_run_count;
static uint32_t s_wave_samples;
static KK_UI_PageId s_current_page = PAGE_MAIN;
static bool     s_display_present;

static void waveform_reset(void)
{
    for (uint16_t i = 0U; i < WAVE_COLUMNS; i++) {
        s_col_min[i] = 0;
        s_col_max[i] = 0;
    }
    s_col_next = 0U;
    s_run_min = 0;
    s_run_max = 0;
    s_run_count = 0U;
    s_wave_samples = 0U;
}

void ui_app_push_waveform(int16_t display_value, uint32_t sample_index)
{
    (void)sample_index;

    if (s_run_count == 0U) {
        s_run_min = display_value;
        s_run_max = display_value;
    } else {
        if (display_value < s_run_min) { s_run_min = display_value; }
        if (display_value > s_run_max) { s_run_max = display_value; }
    }
    if (++s_run_count < WAVE_COLUMN_SAMPLES) {
        return;
    }
    s_run_count = 0U;

    s_col_min[s_col_next] = s_run_min;
    s_col_max[s_col_next] = s_run_max;
    s_col_next++;
    if (s_col_next >= WAVE_COLUMNS) {
        s_col_next = 0U;
    }
    s_wave_samples++;
}

bool ui_app_on_ecg_page(void)
{
    return s_current_page == PAGE_ECG;
}

void ui_app_goto_ecg(void)
{
    /* Navigation belongs to KK_UI. The application only asks for a repaint and
     * never clears or submits a frame itself, which is the rule that keeps the
     * double-buffered flush consistent with the library's own state machine. */
    KK_UI_Invalidate();
}

bool ui_app_display_present(void)
{
    return s_display_present;
}

/* ------------------------------------------------------------ text build */

static void format_monitor_lines(void)
{
    const diagnostics_t *d = diagnostics();
    rtc_datetime_t dt;

    if (d->hr_valid) {
        (void)snprintf(s_hr_text, sizeof(s_hr_text), "%u bpm", (unsigned)d->hr_bpm);
    } else {
        (void)snprintf(s_hr_text, sizeof(s_hr_text), "-- bpm");
    }

    if (d->temp_valid) {
        int32_t c = d->temp_centi;
        int32_t a = (c < 0) ? -c : c;
        (void)snprintf(s_temp_text, sizeof(s_temp_text), "%d.%d C",
                       (int)(c / 100), (int)((a / 10) % 10));
    } else if (d->temp_uncalibrated) {
        (void)snprintf(s_temp_text, sizeof(s_temp_text), "--.- C");
    } else {
        (void)snprintf(s_temp_text, sizeof(s_temp_text), "NO DATA");
    }

    /* UNKNOWN must not be dressed up as CONNECTED: there is no lead-off
     * hardware, so anything other than SIGNAL_POOR is genuinely unknown. */
    switch ((lead_state_t)d->lead_state) {
    case LEAD_SIGNAL_POOR:  (void)snprintf(s_lead_text, sizeof(s_lead_text), "SIGNAL POOR"); break;
    case LEAD_DISCONNECTED: (void)snprintf(s_lead_text, sizeof(s_lead_text), "DISCONNECTED"); break;
    case LEAD_CONNECTED:    (void)snprintf(s_lead_text, sizeof(s_lead_text), "CONNECTED"); break;
    case LEAD_UNKNOWN:
    default:                (void)snprintf(s_lead_text, sizeof(s_lead_text), "UNKNOWN"); break;
    }

    (void)snprintf(s_probe_text, sizeof(s_probe_text),
                   ((temp_state_t)d->temp_state == TEMP_PROBE_FAULT) ? "FAULT"
                   : (d->temp_uncalibrated ? "UNCALIB" : "OK"));

    rtc_service_get_datetime(&dt);
    (void)snprintf(s_clock_text, sizeof(s_clock_text),
                   "%04u-%02u-%02u %02u:%02u",
                   (unsigned)dt.year, (unsigned)dt.month, (unsigned)dt.day,
                   (unsigned)dt.hour, (unsigned)dt.minute);

    if (d->recording) {
        (void)snprintf(s_rec_text, sizeof(s_rec_text), "REC %02u:%02u",
                       (unsigned)(d->session_seconds / 60U),
                       (unsigned)(d->session_seconds % 60U));
    } else {
        (void)snprintf(s_rec_text, sizeof(s_rec_text), "STOPPED");
    }
}

static void format_status_lines(void)
{
    const diagnostics_t *d = diagnostics();

    if (d->oled_present) {
        (void)snprintf(s_status_rows[0], sizeof(s_status_rows[0]), "OK 0x%02X",
                       (unsigned)d->oled_address);
    } else {
        (void)snprintf(s_status_rows[0], sizeof(s_status_rows[0]), "ABSENT");
    }
    (void)snprintf(s_status_rows[1], sizeof(s_status_rows[1]),
                   d->adc_calibrated ? (d->adc_running ? "RUN" : "HALT") : "NO CAL");
    (void)snprintf(s_status_rows[2], sizeof(s_status_rows[2]),
                   d->rtc_valid ? "SET" : "UNSET");
    (void)snprintf(s_status_rows[3], sizeof(s_status_rows[3]),
                   "%lu/%lu", (unsigned long)d->uart_tx_packets,
                   (unsigned long)d->uart_rx_packets);
    (void)snprintf(s_status_rows[4], sizeof(s_status_rows[4]),
                   "%lu", (unsigned long)d->ecg_samples);
    (void)snprintf(s_status_rows[5], sizeof(s_status_rows[5]),
                   d->temp_valid ? "OK"
                   : (d->temp_uncalibrated ? "UNCAL" : "NONE"));
    (void)snprintf(s_status_rows[6], sizeof(s_status_rows[6]),
                   "%lu blk", (unsigned long)d->dma_dropped);
    (void)snprintf(s_status_rows[7], sizeof(s_status_rows[7]),
                   "%lu", (unsigned long)d->uart_crc_errors);

    (void)snprintf(s_about_mcu, sizeof(s_about_mcu), "STM32F103C8");
    (void)snprintf(s_about_fw, sizeof(s_about_fw), "%s", FW_VERSION_STRING);
    (void)snprintf(s_about_proto, sizeof(s_about_proto), "v%u",
                   (unsigned)PROTOCOL_VERSION);
}

/* --------------------------------------------------------- custom page */

void KK_UI_CustomOnEnter(KK_UI_PageId page)
{
    s_current_page = page;
    if (page == PAGE_ECG) {
        waveform_reset();
    }
}

void KK_UI_CustomOnLeave(KK_UI_PageId page)
{
    /* KK_UI only calls this when the page being left is a CUSTOM one, which for
     * this application means exactly PAGE_ECG. Without it s_current_page kept
     * claiming the ECG page after navigation to any menu or info screen, so
     * ui_app_on_ecg_page() stayed true forever and the waveform gate stayed open.
     */
    (void)page;
    s_current_page = PAGE_NONE;
}

void KK_UI_CustomOnInput(KK_UI_PageId page, KK_UI_InputEvent event)
{
    if (page != PAGE_ECG) {
        return;
    }
    /* KK_UI_DispatchInput() only routes input here while a CUSTOM page has focus,
     * so this is the authoritative place for the ECG page's own key action. Doing
     * it here rather than in App_Loop against a mirrored page id is what makes
     * "OK means start/stop only on this screen" true instead of nearly true. */
    if (event.action == KK_UI_INPUT_OK && event.source == KK_UI_INPUT_PRESS) {
        App_ToggleRecording();
    }
}

bool KK_UI_CustomOnTick(KK_UI_PageId page, uint32_t now_ms)
{
    (void)now_ms;
    /* Redraw the trace at the cadence KK_UI already uses for frames. Nothing
     * here may block: this runs on the same path as the display flush. */
    return (page == PAGE_ECG) && (s_wave_samples >= WAVE_COLUMN_SAMPLES);
}

void KK_UI_CustomOnDraw(KK_UI_PageId page, int16_t x_offset,
                        int16_t clip_x, uint16_t clip_width)
{
    const diagnostics_t *d = diagnostics();
    int16_t mid = (int16_t)((WAVE_TOP_ROW + WAVE_BOTTOM_ROW) / 2U);

    if (page != PAGE_ECG) {
        return;
    }

    (void)OLED_SetFont(ui_font_body);
    (void)OLED_SetDrawMode(OLED_DRAW_SET);
    OLED_SetClipWindow(clip_x, 0, clip_width, 64U);

    (void)OLED_DrawUTF8((int16_t)(4 + x_offset), 10, (const char *)"ECG");
    if (d->hr_valid) {
        char hr[12];
        (void)snprintf(hr, sizeof(hr), "HR %u", (unsigned)d->hr_bpm);
        (void)OLED_DrawUTF8((int16_t)(44 + x_offset), 10, hr);
    } else {
        (void)OLED_DrawUTF8((int16_t)(44 + x_offset), 10,
                            (d->lead_state == LEAD_SIGNAL_POOR) ? "SIGNAL" : "HR --");
    }

    /* One vertical segment per column: min/max decimation, so a narrow QRS
     * cannot be averaged away between columns. */
    for (uint16_t col = 0U; col < WAVE_COLUMNS; col++) {
        int16_t lo = s_col_min[col];
        int16_t hi = s_col_max[col];
        int16_t y0;
        int16_t y1;
        int16_t x;

        if (lo == 0 && hi == 0) {
            continue;
        }
        if (lo > hi) {
            int16_t t = lo; lo = hi; hi = t;
        }
        /* The display path is signed about a mid-rail baseline. Scale into the
         * trace box and clamp, so a saturating input cannot flood the panel. */
        y0 = (int16_t)(mid - ((hi * 3) >> 4));
        y1 = (int16_t)(mid - ((lo * 3) >> 4));
        if (y0 < (int16_t)(mid - WAVE_MAX_DEVIATION_ROWS)) {
            y0 = (int16_t)(mid - WAVE_MAX_DEVIATION_ROWS);
        }
        if (y1 > (int16_t)(mid + WAVE_MAX_DEVIATION_ROWS)) {
            y1 = (int16_t)(mid + WAVE_MAX_DEVIATION_ROWS);
        }
        if (y0 < WAVE_TOP_ROW) { y0 = WAVE_TOP_ROW; }
        if (y1 > WAVE_BOTTOM_ROW) { y1 = WAVE_BOTTOM_ROW; }
        if (y0 > y1) {
            int16_t t = y0; y0 = y1; y1 = t;
        }

        x = (int16_t)(col + x_offset);
        if (y0 == y1) {
            OLED_DrawPixel(x, y0);
        } else {
            OLED_DrawVLine(x, y0, (uint16_t)(y1 - y0 + 1));
        }
    }

    format_monitor_lines();
    (void)OLED_DrawUTF8((int16_t)(4 + x_offset), 57, s_rec_text);
    {
        const char *lead = (d->lead_state == LEAD_SIGNAL_POOR) ? "SIGNAL"
                         : ((d->lead_state == LEAD_UNKNOWN) ? "LEAD ?" : "LEAD");
        (void)OLED_DrawUTF8((int16_t)(76 + x_offset), 57, lead);
    }

    OLED_ResetClipWindow();
}

/* ---------------------------------------------------------------- init */

static void seed_from_rtc(void)
{
    rtc_datetime_t dt;

    rtc_service_get_datetime(&dt);
    v_year = (int32_t)dt.year;
    v_month = (int32_t)dt.month;
    v_day = (int32_t)dt.day;
    v_hour = (int32_t)dt.hour;
    v_minute = (int32_t)dt.minute;
    v_second = (int32_t)dt.second;

    /* KK_UI_Init fails if a bound value is outside its interval, so the seed is
     * clamped into the declared ranges rather than trusted from the clock. */
    if (v_year < (int32_t)RTC_EPOCH_MIN_YEAR || v_year > (int32_t)RTC_EPOCH_MAX_YEAR) {
        v_year = 2026;
    }
    if (v_month < 1 || v_month > 12) { v_month = 1; }
    if (v_day < 1 || v_day > 31) { v_day = 1; }
}

void ui_app_init(void)
{
    KK_UI_App app;
    KK_UI_Status st;
    OLED_Status ost;

    memset(main_states, 0, sizeof(main_states));
    memset(rtc_states, 0, sizeof(rtc_states));
    memset(set_states, 0, sizeof(set_states));
    waveform_reset();
    seed_from_rtc();
    (void)snprintf(s_hr_text, sizeof(s_hr_text), "-- bpm");
    (void)snprintf(s_temp_text, sizeof(s_temp_text), "--.- C");
    (void)snprintf(s_lead_text, sizeof(s_lead_text), "UNKNOWN");
    (void)snprintf(s_probe_text, sizeof(s_probe_text), "UNCALIB");
    (void)snprintf(s_clock_text, sizeof(s_clock_text), "--");
    (void)snprintf(s_rec_text, sizeof(s_rec_text), "STOPPED");
    format_status_lines();

    /* The application owns power-on display init; KK_UI never calls OLED_Init.
     * A panel that is not there must not stop anything else. */
    ost = OLED_Init();
    s_display_present = (ost == OLED_OK);
    diagnostics()->oled_present = s_display_present;
    diagnostics()->oled_address = oled_bus_address_7bit();
#if OLED_BRINGUP_TEST
    /* DEBUG MIRROR ONLY. The bring-up test needs the value this function already
     * has and then only records as a boolean. */
    g_oled_test.oled_init_status = (uint8_t)ost;
#endif

    memset(&app, 0, sizeof(app));
    app.root_page = PAGE_MAIN;
    app.routes = routes;
    app.route_count = (uint16_t)(sizeof(routes) / sizeof(routes[0]));
    app.menu_pages = menu_pages;
    app.menu_page_count = (uint16_t)(sizeof(menu_pages) / sizeof(menu_pages[0]));
    app.info_pages = info_pages;
    app.info_page_count = (uint16_t)(sizeof(info_pages) / sizeof(info_pages[0]));
    app.custom_page_count = 1U;
    app.int_bindings = int_bindings;
    app.int_binding_count = (uint16_t)(sizeof(int_bindings) / sizeof(int_bindings[0]));
    app.bool_bindings = bool_bindings;
    app.bool_binding_count = (uint16_t)(sizeof(bool_bindings) / sizeof(bool_bindings[0]));
    /* Three slots, one table: hierarchy comes from layout rather than from a
     * second typeface, because every extra font is real Flash on a 64 KB part. */
    app.fonts.home_font = ui_font_body;
    app.fonts.title_font = ui_font_body;
    app.fonts.body_font = ui_font_body;
    app.texts.return_text = "BACK";
    app.texts.cancel_text = "CANCEL";
    app.texts.confirm_text = "OK";
    app.texts.on_text = "ON";
    app.texts.off_text = "OFF";
    app.texts.message_title = "NOTICE";

    st = KK_UI_Init(&app);
#if OLED_BRINGUP_TEST
    /* DEBUG MIRROR ONLY. Below, a rejected description is reduced to one
     * diagnostics byte that later modules overwrite; the test needs the status
     * itself, and the error record KK_UI attached to it. */
    g_oled_test.kk_ui_init_status = (uint8_t)st;
    if (st != KK_UI_OK) {
        KK_UI_ErrorInfo init_err;
        while (KK_UI_PollError(&init_err)) {
            oled_bringup_note_ui_error((uint8_t)init_err.code, init_err.page,
                                       init_err.index);
        }
    }
#endif
    if (st != KK_UI_OK) {
        /* A rejected description means these tables disagree with the library.
         * Recorded rather than trapped: monitoring and the PC link both work with
         * no display, and bricking the device over a UI table would be worse. */
        s_display_present = false;
        diagnostics()->oled_present = false;
        diagnostics_note_error((uint8_t)st);
    }
}

/* ------------------------------------------------------------- per frame */

static void apply_event(KK_UI_EventId id)
{
    switch (id) {
    case EVT_APPLY_RTC: {
        rtc_datetime_t dt;

        memset(&dt, 0, sizeof(dt));
        dt.year = (uint16_t)v_year;
        dt.month = (uint8_t)v_month;
        dt.day = (uint8_t)v_day;
        dt.hour = (uint8_t)v_hour;
        dt.minute = (uint8_t)v_minute;
        dt.second = (uint8_t)v_second;
        if (!rtc_service_set_datetime(&dt)) {
            (void)KK_UI_ShowToast("INVALID DATE", 0U);
        } else {
            (void)KK_UI_ShowToast("RTC SET", 0U);
        }
        break;
    }
    case EVT_READ_RTC:
        seed_from_rtc();
        (void)KK_UI_ShowToast("READ FROM RTC", 0U);
        break;
    case EVT_STREAM_TOGGLE:
        /* By the time this event is polled, KK_UI has already written the new
         * value through the binding, so v_stream is the request, not the old
         * state. protocol_service owns streaming; this switch and the PC's
         * START/STOP commands are two front doors to the one boolean. */
        protocol_service_set_streaming(v_stream);
        (void)KK_UI_ShowToast(v_stream ? "STREAMING" : "STREAM OFF", 0U);
        break;
    default:
        break;
    }
}

void ui_app_update(uint32_t now_ms)
{
    KK_UI_Input in;
    KK_UI_EventId ev;
    KK_UI_ErrorInfo ei;
    static uint32_t last_text_ms;

    in.keys = buttons_raw_mask();
    in.encoder_delta = 0;

#if OLED_BRINGUP_TEST
    {
        /* DEBUG MIRROR ONLY. The production path discards this status; the test
         * cannot tell "the UI refreshed" from "the UI refused" without it. */
        KK_UI_Status us = KK_UI_Update(now_ms, in);
        g_oled_test.kk_ui_update_status = (uint8_t)us;
        if (g_oled_test.ui_update_count < 0xFFFFU) {
            g_oled_test.ui_update_count++;
        }
    }
#else
    (void)KK_UI_Update(now_ms, in);
#endif

    while (KK_UI_PollEvent(&ev)) {
        apply_event(ev);
    }
    while (KK_UI_PollError(&ei)) {
#if OLED_BRINGUP_TEST
        oled_bringup_note_ui_error((uint8_t)ei.code, ei.page, ei.index);
#endif
        diagnostics_note_error((uint8_t)ei.code);
    }

    /* The switch shows the protocol's state, not what was last clicked here, so a
     * PC START_STREAM or the ECG page's KEY_OK is reflected rather than fought. */
    if (v_stream != protocol_service_streaming()) {
        v_stream = protocol_service_streaming();
        KK_UI_Invalidate();
    }

    if ((uint32_t)(now_ms - last_text_ms) >= 500U) {
        last_text_ms = now_ms;
        format_monitor_lines();
        format_status_lines();
        KK_UI_Invalidate();
    }
}
