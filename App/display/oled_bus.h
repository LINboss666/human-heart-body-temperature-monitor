/**
 * @file    oled_bus.h
 * @brief   Controller profile and bus address for the 0.96" panel, kept out of
 *          the vendored KK_OLED driver so the profile is one editable table.
 *
 * WHAT IS AND IS NOT KNOWN. The panel is not built or bought yet, so neither its
 * controller (SSD1306 or SH1106 or the CH1116 the vendor driver was written
 * against) nor its 7-bit address (0x3C or 0x3D) nor its column offset is
 * established. The upstream kk-oled-port instructions are explicit that an I2C
 * scan can only ever prove that something answers at an address - it cannot
 * identify the controller, the addressing form, the resolution or the column
 * offset - and that guessing those values into a hardware driver is forbidden.
 *
 * So this file provides three complete, genuinely different profiles, all marked
 * UNVERIFIED, and nothing in the project claims any of them is correct.
 * Selecting one is a single compile-time define; HARDWARE_TEST_PLAN.md stage D
 * says how to tell them apart on a bench.
 */
#ifndef OLED_BUS_H
#define OLED_BUS_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    OLED_CTRL_SSD1306 = 0,   /* 128 columns, no column offset, 0x8D/0x14 pump */
    OLED_CTRL_SH1106  = 1,   /* 132 columns, offset 2, 0xAF/0xA3 dynamic pump */
    OLED_CTRL_CH1116  = 2    /* what the vendored driver shipped with */
} oled_controller_t;

/** Compile-time choice. Not verified against any real module. */
#ifndef OLED_CONTROLLER_SELECTED
#define OLED_CONTROLLER_SELECTED OLED_CTRL_SSD1306
#endif

/** Candidate 7-bit addresses, in the order they are probed. */
#define OLED_ADDR_A  0x3CU
#define OLED_ADDR_B  0x3DU

/**
 * Values the vendored driver reads instead of its own hard-coded constants.
 * Exposed so a debugger and the STATUS packet can report what was actually
 * chosen rather than what was assumed.
 */
extern volatile uint16_t oled_bus_address_8bit;   /**< HAL form: 7-bit shifted. */
extern volatile uint8_t  oled_bus_column_offset;
extern volatile uint8_t  oled_bus_controller;     /**< oled_controller_t */
extern volatile bool     oled_bus_scanned;

/** Apply the selected controller profile: offset and command table. */
void oled_bus_apply_profile(oled_controller_t controller);

/**
 * Probe the two candidate addresses. Returns true and latches the address when
 * something acknowledges; leaves oled_bus_address_8bit at the default otherwise
 * so a later init attempt still has something to try.
 */
bool oled_bus_scan(void);

/** The selected controller's initialisation command block. */
const uint8_t *oled_bus_init_sequence(uint16_t *len_out);

/** 7-bit form of whatever address is currently latched, for diagnostics. */
uint8_t oled_bus_address_7bit(void);

#ifdef __cplusplus
}
#endif

#endif /* OLED_BUS_H */
