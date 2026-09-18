#include "oled_bus.h"

#include <stddef.h>

#include "stm32f1xx_hal.h"

#include "i2c.h"

/*
 * Every command block below is a real, documented controller initialisation, but
 * none of them is confirmed against hardware that exists yet. They are candidates
 * that differ in the three ways that actually break a panel: the charge-pump
 * enable bytes, the column offset, and the COM pin layout. Picking one is a
 * bench measurement, not a code decision - see HARDWARE_TEST_PLAN.md stage D.
 */

/* SSD1306: internal pump via 0x8D/0x14, 128-column RAM so offset 0. */
static const uint8_t SEQ_SSD1306[] = {
    0xAEU,                /* display off */
    0xD5U, 0x80U,         /* clock divide */
    0xA8U, 0x3FU,         /* multiplex = 64 */
    0xD3U, 0x00U,         /* display offset 0 */
    0x40U,                /* start line 0 */
    0x8DU, 0x14U,         /* charge pump ON */
    0x20U, 0x02U,         /* page addressing mode */
    0xA1U,                /* segment remap */
    0xC8U,                /* COM scan decrement */
    0xDAU, 0x12U,         /* COM pins: alternative, matches the common 0.96" wiring */
    0x81U, 0xCFU,         /* contrast */
    0xD9U, 0xF1U,         /* pre-charge period */
    0xDBU, 0x40U,         /* VCOMH deselect */
    0xA4U,                /* resume RAM content */
    0xA6U,                /* normal (not inverted) */
    0xAFU                 /* display ON */
};

/* SH1106: 132-column RAM, so the visible window starts at column 2; pump is
 * enabled by a different pair of bytes and 0xAF/0xA3 selects the dynamic one. */
static const uint8_t SEQ_SH1106[] = {
    0xAEU,
    0x88U, 0x06U,         /* panel blue / row frequency, typical SH1106 module */
    0xB0U, 0x00U,         /* CLK and charge pump: 0x00 = external, 0x02 = internal */
    0x9CU, 0xA5U,         /* icon off, all pixels on off */
    0x1FU, 0x0DU,         /* escape sequence present on SH1106 only */
    0xA2U,                /* software reset */
    0xA8U, 0x3FU,         /* multiplex = 64 */
    0xD3U, 0x00U,         /* display offset */
    0x40U,                /* start line */
    0xA1U,                /* segment remap */
    0xC0U,                /* COM scan decrement */
    0xD5U, 0xF0U,         /* clock divide */
    0xD9U, 0x05U,         /* display cycle length */
    0xDBU, 0x30U,         /* VCOMH deselect level */
    0x81U, 0x80U,         /* contrast */
    0xA6U,                /* normal */
    0xAFU                 /* display ON */
};

/* CH1116: exactly what the vendored driver shipped with, kept so the original
 * reference behaviour is still reachable from one define. */
static const uint8_t SEQ_CH1116[] = {
    0xAEU,
    0x02U, 0x10U,
    0x40U,
    0xB0U,
    0x81U, 0xCFU,
    0xA1U,
    0xA6U,
    0xA8U, 0x3FU,
    0xADU, 0x8BU,
    0x33U,
    0xC8U,
    0xD3U, 0x00U,
    0xD5U, 0xC0U,
    0xD9U, 0x1FU,
    0xDAU, 0x12U,
    0xDBU, 0x40U
};

volatile uint16_t oled_bus_address_8bit = (uint16_t)(OLED_ADDR_A << 1U);
volatile uint8_t  oled_bus_column_offset = 0U;
volatile uint8_t  oled_bus_controller = (uint8_t)OLED_CTRL_SSD1306;
volatile bool     oled_bus_scanned;

const uint8_t *oled_bus_init_sequence(uint16_t *len_out)
{
    const uint8_t *p;
    uint16_t n;

    switch (oled_bus_controller) {
    case OLED_CTRL_SH1106:
        p = SEQ_SH1106;  n = (uint16_t)sizeof(SEQ_SH1106);  break;
    case OLED_CTRL_CH1116:
        p = SEQ_CH1116;  n = (uint16_t)sizeof(SEQ_CH1116); break;
    case OLED_CTRL_SSD1306:
    default:
        p = SEQ_SSD1306; n = (uint16_t)sizeof(SEQ_SSD1306); break;
    }
    if (len_out != NULL) {
        *len_out = n;
    }
    return p;
}

void oled_bus_apply_profile(oled_controller_t controller)
{
    oled_bus_controller = (uint8_t)controller;
    switch (controller) {
    case OLED_CTRL_SH1106:
        oled_bus_column_offset = 2U;    /* 132-column RAM, window centred */
        break;
    case OLED_CTRL_CH1116:
        oled_bus_column_offset = 2U;    /* as shipped by the vendor driver */
        break;
    case OLED_CTRL_SSD1306:
    default:
        oled_bus_column_offset = 0U;    /* 128-column RAM, no offset */
        break;
    }
}

bool oled_bus_scan(void)
{
    /* HAL_I2C_IsDeviceReady probes until it sees an ACK or burns the trials.
     * Two candidate addresses, short timeouts, no retries beyond that: a panel
     * that is absent must not add measurable boot latency, because the firmware
     * has to keep working with no display at all. */
    static const uint8_t candidates[2] = { OLED_ADDR_A, OLED_ADDR_B };
    uint8_t i;

    for (i = 0U; i < 2U; i++) {
        if (HAL_I2C_IsDeviceReady(&hi2c1, (uint16_t)(candidates[i] << 1U),
                                  2U, 20U) == HAL_OK) {
            oled_bus_address_8bit = (uint16_t)(candidates[i] << 1U);
            oled_bus_scanned = true;
            return true;
        }
    }
    oled_bus_scanned = false;
    return false;
}

uint8_t oled_bus_address_7bit(void)
{
    return (uint8_t)(oled_bus_address_8bit >> 1U);
}
