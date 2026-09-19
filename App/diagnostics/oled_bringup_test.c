#include "oled_bringup_test.h"

#if OLED_BRINGUP_TEST

#include <stddef.h>

#include "stm32f1xx_hal.h"

#include "i2c.h"
#include "kk_oled_internal.h"
#include "oled_bus.h"

/*
 * Four questions, answered separately, in this order:
 *
 *   1. does anything acknowledge on I2C1 at 0x3C / 0x3D?       (scan_ok)
 *   2. does the vendored driver accept its initialisation?      (oled_init_status)
 *   3. does a full frame reach the bus at all?                  (pages_written)
 *   4. does the panel light up?                                 (the user's eyes)
 *
 * Only 4 needs hardware to answer, which is the entire point: 1 to 3 are
 * reported even when the answer to 4 is "black". The profile is forced to
 * SSD1306 here because that is the controller the user identified on the module
 * itself - not because software ever proved it.
 *
 * Nothing here loops or retries. Each stage runs at most once, and the image is
 * never cleared afterwards, so what the panel shows when the debugger is
 * attached is what the firmware last wrote.
 */

/** Non-static so Keil's Watch can evaluate it under -O3. */
volatile oled_bringup_result_t g_oled_test;

/** Latch what the I2C peripheral thinks happened, at the point of a result. */
static void capture_bus(void)
{
    g_oled_test.i2c_error_code = hi2c1.ErrorCode;
    g_oled_test.i2c_state = (uint32_t)hi2c1.State;
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

void oled_bringup_test_run(void)
{
    uint16_t len = 0U;

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

#endif /* OLED_BRINGUP_TEST */
