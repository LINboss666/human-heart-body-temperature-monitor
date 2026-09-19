/**
 * @file    oled_bringup_test.h
 * @brief   Temporary one-shot OLED bring-up test for a real panel, bypassing
 *          KK_UI so a black screen can be attributed to a layer instead of to
 *          "the display doesn't work".
 *
 * WHY THIS EXISTS. The frozen Phase 1 firmware reports an I2C acknowledge at
 * 0x3C, which proves only that something answers on the bus. Everything after
 * that - initialisation accepted, frame written, pixels lit - has never been
 * compared against a panel. This test separates those, and leaves the answer in
 * a debugger-visible struct instead of on the screen it is testing.
 *
 * WHAT IT IS NOT. Not a feature, not a UI, and not a demo: it renders one
 * static image once and then does nothing more. Delete the branch, not the
 * firmware: with OLED_BRINGUP_TEST == 0 nothing here is compiled or called.
 */
#ifndef OLED_BRINGUP_TEST_H
#define OLED_BRINGUP_TEST_H

#include <stdint.h>

#include "app_config.h"

/**
 * Values of g_oled_test.stage. A number is deliberately distinguishable so one
 * glance says where the boot stopped: the error codes are not shared.
 */
enum {
    OLED_BRINGUP_STAGE_START      = 0,   /**< entered, profile applied */
    OLED_BRINGUP_STAGE_SCANNING   = 1,   /**< about to probe the two addresses */
    OLED_BRINGUP_STAGE_INIT       = 2,   /**< about to call OLED_Init() */
    OLED_BRINGUP_STAGE_PATTERN    = 3,   /**< drawing and pushing the image */
    OLED_BRINGUP_STAGE_DONE       = 10,  /**< every software step returned OK */
    OLED_BRINGUP_STAGE_UI_START   = 20,  /**< KK_UI variant: profile and scan first */
    OLED_BRINGUP_STAGE_UI_INIT    = 21,  /**< about to call the real ui_app_init() */
    OLED_BRINGUP_STAGE_UI_FRAMES  = 22,  /**< driving the real ui_app_update() */
    OLED_BRINGUP_STAGE_UI_PARKED  = 23,  /**< frame window closed, results frozen */
    OLED_BRINGUP_ERR_NO_I2C_ACK   = 80,  /**< nothing acknowledged either address */
    OLED_BRINGUP_ERR_INIT         = 81,  /**< OLED_Init() did not return OLED_OK */
    OLED_BRINGUP_ERR_UPDATE       = 82,  /**< OLED_Update() did not return OLED_OK */
    OLED_BRINGUP_ERR_KKUI_INIT    = 83,  /**< KK_UI_Init() rejected the tables */
    OLED_BRINGUP_ERR_KKUI_UPDATE  = 84   /**< the real update path never submitted */
};

/**
 * Every field is written before the stage advances, so the struct always
 * describes the furthest point reached. Kept in a status type widened to
 * uint8_t/uint32_t rather than the enum types so a debugger shows the raw
 * numbers the HAL and the library actually returned.
 */
typedef struct {
    uint8_t  stage;                /**< one of OLED_BRINGUP_STAGE_* / _ERR_* */
    uint8_t  scan_ok;              /**< 1 when an address acknowledged */
    uint8_t  address_7bit;         /**< oled_bus_address_7bit() after the scan */
    uint8_t  address_8bit;         /**< HAL form, oled_bus_address_8bit */
    uint8_t  controller;           /**< oled_bus_controller actually applied */
    uint8_t  init_seq_len;         /**< bytes in the selected init block */
    uint8_t  oled_init_status;     /**< OLED_Status from OLED_Init() */
    uint8_t  oled_update_status;   /**< OLED_Status from OLED_Update() */
    uint8_t  last_status;          /**< OLED_GetLastStatus() after the update */
    uint8_t  pattern;              /**< which OLED_BRINGUP_PATTERN_* was asked for */
    uint8_t  pattern_stage;        /**< 0 none, 1 drawn into the buffer, 2 pushed */
    uint8_t  pages_written;        /**< pages the update really sent, not cached */
    uint32_t i2c_error_code;       /**< hi2c1.ErrorCode at the last capture */
    uint32_t i2c_state;            /**< hi2c1.State at the last capture */
    uint32_t run_ticks;            /**< HAL_GetTick() when the run finished */

    /* ---- KK_UI variant: the real interface, mirrored as it happens ---- */

    uint8_t  ui_init_called;       /**< ui_app_init() was entered */
    uint8_t  kk_ui_init_status;    /**< KK_UI_Status from the real KK_UI_Init() */
    uint8_t  kk_ui_initialized;    /**< the library's own "tables accepted" flag */
    uint8_t  kk_ui_update_status;  /**< last KK_UI_Status from KK_UI_Update() */
    uint16_t ui_update_count;      /**< real ui_app_update() calls completed */
    uint32_t ui_run_ms;            /**< how long the frame window actually ran */

    uint8_t  kk_ui_error_valid;    /**< an error is still queued, unread */
    uint8_t  kk_ui_error_code;     /**< that error's KK_UI_Status code */
    uint16_t kk_ui_error_page;     /**< the page it names */
    uint16_t kk_ui_error_index;    /**< the index it names */
    uint16_t error_count;          /**< errors drained across the window */
    uint8_t  first_error_code;
    uint16_t first_error_page;
    uint16_t first_error_index;
    uint8_t  last_error_code;
    uint16_t last_error_page;
    uint16_t last_error_index;

    uint8_t  oled_present;         /**< diagnostics()->oled_present */
    uint8_t  display_fault;        /**< the library's own display-fault latch */
    uint8_t  kk_ui_dirty;          /**< a frame is waiting to be submitted */
    uint8_t  kk_ui_frame_ready;    /**< a submitted frame awaits its flush */
    uint8_t  transfer_active;      /**< a frame is on the wire right now */
    uint16_t kk_ui_current_page;   /**< page the library believes it is on */
    uint16_t kk_ui_selected;       /**< row the library believes is selected */
} oled_bringup_result_t;

/**
 * Deliberately not static and not const: an optimised-away static is what made
 * Keil answer "<cannot evaluate>" for the variables this test exists to show.
 */
extern volatile oled_bringup_result_t g_oled_test;

#if OLED_BRINGUP_TEST

/**
 * Run the selected variant once, from App_Init. PANEL leaves a static image on
 * the panel; KK_UI calls the real ui_app_init() and then drives the real
 * ui_app_update() for OLED_BRINGUP_UI_RUN_MS. Neither one retries, and neither
 * clears what it drew.
 */
void oled_bringup_test_run(void);

/**
 * Record a KK_UI_ErrorInfo that the application's own poll loop drained. Called
 * from ui_app.c so the code, page and index survive into a Watch window instead
 * of being squeezed into diagnostics' single, overwritten error byte.
 */
void oled_bringup_note_ui_error(uint8_t code, uint16_t page, uint16_t index);

#endif /* OLED_BRINGUP_TEST */

#endif /* OLED_BRINGUP_TEST_H */
