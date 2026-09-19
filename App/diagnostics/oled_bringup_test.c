#include "oled_bringup_test.h"

#if OLED_BRINGUP_TEST

#include <stddef.h>

#include "stm32f1xx_hal.h"

#include "diagnostics/diagnostics.h"
#include "i2c.h"
#include "kk_oled_internal.h"
#include "kk_ui_internal.h"
#include "oled_bus.h"
#include "ui/ui_app.h"

/*
 * Two variants, one question each.
 *
 * PANEL (already answered on the real module, kept for re-checking):
 *   1. does anything acknowledge on I2C1 at 0x3C / 0x3D?       (scan_ok)
 *   2. does the vendored driver accept its initialisation?      (oled_init_status)
 *   3. does a full frame reach the bus at all?                  (pages_written)
 *   4. does the panel light up?                                 (the user's eyes)
 *
 * KK_UI (the open question): the panel is proven, so a black screen under the
 * frozen firmware is something above the driver. This variant calls the real
 * ui_app_init() with the shipping tables, fonts and bindings - no hand-drawn
 * substitute - and then drives the real ui_app_update() for a fixed window, so
 * the first frame has every chance to be submitted and flushed. What KK_UI_Init
 * returned, what KK_UI_Update returns, whether the library considers itself
 * dirty, frame-ready or in display fault, and every error it raised, all end up
 * in g_oled_test rather than in diagnostics' single overwritten byte.
 *
 * The library's runtime is reached through its own internal header, read-only:
 * kk_ui is extern, not static, so no vendor file had to be touched to observe it.
 *
 * The profile is forced to SSD1306 because that is the controller the user
 * identified on the module itself - not because software ever proved it. Nothing
 * here clears what it drew.
 */

/** Non-static so Keil's Watch can evaluate it under -O3. */
volatile oled_bringup_result_t g_oled_test;

/** Latch what the I2C peripheral thinks happened, at the point of a result. */
static void capture_bus(void)
{
    g_oled_test.i2c_error_code = hi2c1.ErrorCode;
    g_oled_test.i2c_state = (uint32_t)hi2c1.State;
}

/** Start from a known image so a Watch reading cannot be from an earlier run. */
static void reset_result(void)
{
    g_oled_test.stage = OLED_BRINGUP_STAGE_START;
    g_oled_test.scan_ok = 0U;
    g_oled_test.address_7bit = 0U;
    g_oled_test.address_8bit = 0U;
    g_oled_test.controller = 0U;
    g_oled_test.init_seq_len = 0U;
    g_oled_test.oled_init_status = 0xFFU;
    g_oled_test.oled_update_status = 0xFFU;
    g_oled_test.last_status = 0xFFU;
    g_oled_test.pattern = (uint8_t)OLED_BRINGUP_PATTERN;
    g_oled_test.pattern_stage = 0U;
    g_oled_test.pages_written = 0U;
    g_oled_test.i2c_error_code = 0U;
    g_oled_test.i2c_state = 0U;
    g_oled_test.run_ticks = 0U;

    g_oled_test.ui_init_called = 0U;
    g_oled_test.kk_ui_init_status = 0xFFU;
    g_oled_test.kk_ui_initialized = 0U;
    g_oled_test.kk_ui_update_status = 0xFFU;
    g_oled_test.ui_update_count = 0U;
    g_oled_test.ui_run_ms = 0U;

    g_oled_test.kk_ui_error_valid = 0U;
    g_oled_test.kk_ui_error_code = 0U;
    g_oled_test.kk_ui_error_page = 0U;
    g_oled_test.kk_ui_error_index = 0U;
    g_oled_test.error_count = 0U;
    g_oled_test.first_error_code = 0U;
    g_oled_test.first_error_page = 0U;
    g_oled_test.first_error_index = 0U;
    g_oled_test.last_error_code = 0U;
    g_oled_test.last_error_page = 0U;
    g_oled_test.last_error_index = 0U;

    g_oled_test.oled_present = 0U;
    g_oled_test.display_fault = 0U;
    g_oled_test.kk_ui_dirty = 0U;
    g_oled_test.kk_ui_frame_ready = 0U;
    g_oled_test.transfer_active = 0U;
    g_oled_test.kk_ui_current_page = 0U;
    g_oled_test.kk_ui_selected = 0U;
}

void oled_bringup_note_ui_error(uint8_t code, uint16_t page, uint16_t index)
{
    if (g_oled_test.error_count == 0U) {
        g_oled_test.first_error_code = code;
        g_oled_test.first_error_page = page;
        g_oled_test.first_error_index = index;
    }
    g_oled_test.last_error_code = code;
    g_oled_test.last_error_page = page;
    g_oled_test.last_error_index = index;
    if (g_oled_test.error_count < 0xFFFFU) {
        g_oled_test.error_count++;
    }
}

/** Copy what the library and the application currently believe, verbatim. */
static void sample_ui_state(void)
{
    g_oled_test.kk_ui_initialized = kk_ui.initialized;
    g_oled_test.kk_ui_error_valid = kk_ui.error_valid;
    g_oled_test.kk_ui_error_code = (uint8_t)kk_ui.error.code;
    g_oled_test.kk_ui_error_page = kk_ui.error.page;
    g_oled_test.kk_ui_error_index = kk_ui.error.index;
    g_oled_test.display_fault = kk_ui.display_fault;
    g_oled_test.kk_ui_dirty = kk_ui.dirty;
    g_oled_test.kk_ui_frame_ready = kk_ui.frame_ready;
    g_oled_test.transfer_active = kk_ui.transfer_active;
    g_oled_test.kk_ui_current_page = kk_ui.current.page;
    g_oled_test.kk_ui_selected = kk_ui.current.selected;
    g_oled_test.oled_present = diagnostics()->oled_present ? 1U : 0U;
    g_oled_test.last_status = (uint8_t)OLED_GetLastStatus();
}

/**
 * One deterministic image, selected at compile time, using only the primitives
 * the shipping UI already uses. A checker tests column and page mapping at the
 * same time; a border tests whether anything is visible at all and where the
 * edges fall, which is what a wrong column offset looks like.
 */
static void draw_pattern(void)
{
    const int16_t width = (int16_t)OLED_GetWidth();
    const int16_t height = (int16_t)OLED_GetHeight();
    int16_t x;
    int16_t y;

    switch (OLED_BRINGUP_PATTERN) {
    case OLED_BRINGUP_PATTERN_CHECKER:
        for (y = 0; y < height; y++) {
            for (x = 0; x < width; x++) {
                if ((((uint16_t) x >> 3) + ((uint16_t) y >> 3)) & 1U) {
                    OLED_DrawPixel(x, y);
                }
            }
        }
        break;

    case OLED_BRINGUP_PATTERN_BORDER:
        OLED_DrawFrame(0, 0, (uint16_t)width, (uint16_t)height);
        OLED_DrawFrame(1, 1, (uint16_t)(width - 2), (uint16_t)(height - 2));
        break;

    case OLED_BRINGUP_PATTERN_FULL_WHITE:
    default:
        OLED_Fill();
        break;
    }
}

/** Pages the last update actually put on the wire, not pages it cached away. */
static uint8_t dirty_pages(void)
{
    uint8_t page;
    uint8_t n = 0U;

    for (page = 0U; page < OLED_PHYSICAL_PAGES; page++) {
        if (OLED_InternalGetTransferMinX(page) < OLED_PHYSICAL_WIDTH) {
            n++;
        }
    }
    return n;
}

/** The already-passed panel test: scan, init, one full frame, then nothing. */
static void run_panel(void)
{
    uint16_t len = 0U;

    reset_result();

    oled_bus_apply_profile((oled_controller_t)OLED_CTRL_SSD1306);
    g_oled_test.controller = oled_bus_controller;
    (void)oled_bus_init_sequence(&len);   /* the block OLED_Init() is about to send */
    g_oled_test.init_seq_len = (uint8_t)(len & 0xFFU);

    g_oled_test.stage = OLED_BRINGUP_STAGE_SCANNING;
    if (oled_bus_scan()) {
        g_oled_test.scan_ok = 1U;
    }
    g_oled_test.address_8bit = (uint8_t)oled_bus_address_8bit;
    g_oled_test.address_7bit = oled_bus_address_7bit();
    capture_bus();
    if (g_oled_test.scan_ok == 0U) {
        g_oled_test.stage = OLED_BRINGUP_ERR_NO_I2C_ACK;
        return;
    }

    /* OLED_Init() clears the panel and sends 0xAF; KK_UI_Init() is never called,
     * so nothing after this point has a page table to repaint over the result. */
    g_oled_test.stage = OLED_BRINGUP_STAGE_INIT;
    g_oled_test.oled_init_status = (uint8_t)OLED_Init();
    capture_bus();
    if (g_oled_test.oled_init_status != (uint8_t)OLED_OK) {
        g_oled_test.stage = OLED_BRINGUP_ERR_INIT;
        return;
    }

    g_oled_test.stage = OLED_BRINGUP_STAGE_PATTERN;
    draw_pattern();
    g_oled_test.pattern_stage = 1U;
    g_oled_test.oled_update_status = (uint8_t)OLED_Update();
    g_oled_test.last_status = (uint8_t)OLED_GetLastStatus();
    g_oled_test.pages_written = dirty_pages();
    capture_bus();
    if (g_oled_test.oled_update_status != (uint8_t)OLED_OK) {
        g_oled_test.stage = OLED_BRINGUP_ERR_UPDATE;
        return;
    }

    g_oled_test.pattern_stage = 2U;
    g_oled_test.stage = OLED_BRINGUP_STAGE_DONE;
    g_oled_test.run_ticks = HAL_GetTick();
}

/**
 * The real user interface, on a panel that is known to work.
 *
 * ui_app_init() is the shipping one: it applies nothing, substitutes nothing,
 * and calls OLED_Init() and KK_UI_Init() with the project's own routes, menus,
 * info pages, custom ECG page, bindings, fonts and texts. Its two results are
 * mirrored into g_oled_test from inside ui_app.c, which is where they would
 * otherwise be lost - the production path only keeps the last of them in
 * diagnostics' single error byte.
 */
static void run_kk_ui(void)
{
    uint32_t started;
    uint32_t now;
    uint32_t frames = 0U;

    reset_result();

    g_oled_test.stage = OLED_BRINGUP_STAGE_UI_START;
    oled_bus_apply_profile((oled_controller_t)OLED_CTRL_SSD1306);
    g_oled_test.controller = oled_bus_controller;
    if (oled_bus_scan()) {
        g_oled_test.scan_ok = 1U;
    }
    g_oled_test.address_8bit = (uint8_t)oled_bus_address_8bit;
    g_oled_test.address_7bit = oled_bus_address_7bit();
    capture_bus();
    if (g_oled_test.scan_ok == 0U) {
        /* Without an address the real path would fail for the wrong reason. */
        g_oled_test.stage = OLED_BRINGUP_ERR_NO_I2C_ACK;
        return;
    }

    g_oled_test.stage = OLED_BRINGUP_STAGE_UI_INIT;
    g_oled_test.ui_init_called = 1U;
    ui_app_init();
    sample_ui_state();
    capture_bus();
    if (g_oled_test.kk_ui_init_status != (uint8_t)KK_UI_OK) {
        g_oled_test.stage = OLED_BRINGUP_ERR_KKUI_INIT;
        return;
    }

    /* Every call is the production entry point, on the production tick source.
     * ui_update_count is incremented inside ui_app.c so it counts every real
     * call, including the ones the main loop keeps making after this returns;
     * `frames` here only bounds this window against a dead SysTick. */
    g_oled_test.stage = OLED_BRINGUP_STAGE_UI_FRAMES;
    started = HAL_GetTick();
    do {
        now = HAL_GetTick();
        ui_app_update(now);
        frames++;
        sample_ui_state();
    } while (((uint32_t)(now - started) < OLED_BRINGUP_UI_RUN_MS) &&
             (frames < OLED_BRINGUP_UI_MAX_FRAMES));

    g_oled_test.ui_run_ms = (uint32_t)(HAL_GetTick() - started);
    g_oled_test.stage = OLED_BRINGUP_STAGE_UI_PARKED;
    capture_bus();
    g_oled_test.run_ticks = HAL_GetTick();
    if (g_oled_test.kk_ui_update_status != (uint8_t)KK_UI_OK) {
        g_oled_test.stage = OLED_BRINGUP_ERR_KKUI_UPDATE;
    }
}

void oled_bringup_test_run(void)
{
    if (OLED_BRINGUP_TEST_WHICH == OLED_BRINGUP_TEST_KK_UI) {
        run_kk_ui();
    } else {
        run_panel();
    }
}

#endif /* OLED_BRINGUP_TEST */
