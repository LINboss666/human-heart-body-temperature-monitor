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
    OLED_BRINGUP_ERR_NO_I2C_ACK   = 80,  /**< nothing acknowledged either address */
    OLED_BRINGUP_ERR_INIT         = 81,  /**< OLED_Init() did not return OLED_OK */
    OLED_BRINGUP_ERR_UPDATE       = 82   /**< OLED_Update() did not return OLED_OK */
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
} oled_bringup_result_t;

/**
 * Deliberately not static and not const: an optimised-away static is what made
 * Keil answer "<cannot evaluate>" for the variables this test exists to show.
 */
extern volatile oled_bringup_result_t g_oled_test;

#if OLED_BRINGUP_TEST

/**
 * Run the whole sequence once, from App_Init. Returns either after leaving a
 * static image on the panel or at the first stage that failed; it never loops,
 * never retries, and never clears what it drew.
 */
void oled_bringup_test_run(void);

#endif /* OLED_BRINGUP_TEST */

#endif /* OLED_BRINGUP_TEST_H */
