/*
 * Host stand-ins for what ui_app.c touches but a host test cannot: the I2C
 * driver, and a few service entry points that need a peripheral.
 *
 * The driver is replaced by a shadow panel - the same page/column addressing the
 * real one performs, into RAM the test can inspect - so a host run answers "what
 * would be on the glass" without glass. Everything above it is the shipping
 * code: ui_app.c, KK_UI, KK_OLED's graphics core, the real page tables.
 *
 * Only the SSD1306 profile is modelled, so the column offset is 0 and a logical
 * column is a physical column. The real driver adds oled_bus_column_offset, which
 * is what a SH1106 would need and what this file deliberately does not test.
 */
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "kk_oled.h"
#include "kk_oled_driver.h"
#include "kk_oled_internal.h"

#include "app.h"
#include "buttons/buttons.h"
#include "oled_bus.h"
#include "protocol/protocol_service.h"
#include "rtc_service/rtc_calendar.h"
#include "rtc_service/rtc_service.h"

/* --------------------------------------------------------------- shadow panel */

uint8_t ui_host_shadow[OLED_PHYSICAL_PAGES][OLED_PHYSICAL_WIDTH];
uint32_t ui_host_bytes_written;      /**< pixel bytes the driver was told to send */
uint32_t ui_host_transactions;       /**< driver writes, init included */

OLED_Status OLED_DriverInit(void)
{
    memset(ui_host_shadow, 0, sizeof(ui_host_shadow));
    ui_host_transactions++;
    return OLED_OK;
}

OLED_Status OLED_DriverWriteBlocking(void)
{
    const uint8_t *buffer = OLED_InternalGetTransferBuffer();
    uint8_t page;

    for (page = 0U; page < OLED_PHYSICAL_PAGES; page++) {
        uint8_t min_x = OLED_InternalGetTransferMinX(page);
        uint8_t max_x = OLED_InternalGetTransferMaxX(page);
        uint8_t x;

        if (min_x >= OLED_PHYSICAL_WIDTH) {
            continue;   /* clean page: the real driver sends nothing for it */
        }
        for (x = min_x; x <= max_x; x++) {
            ui_host_shadow[page][x] = buffer[(page * OLED_PHYSICAL_WIDTH) + x];
            ui_host_bytes_written++;
        }
    }
    ui_host_transactions++;
    return OLED_OK;
}

bool OLED_DriverIsBusy(void)
{
    return false;
}

/* KK_UI is configured for the blocking refresh, so these exist only to keep the
 * link complete; they behave like a synchronous write. */
OLED_Status OLED_DriverWriteIT(void)
{
    OLED_Status status = OLED_DriverWriteBlocking();

    OLED_InternalTransferFinished(status);
    return status;
}

OLED_Status OLED_DriverWriteDMA(void)
{
    return OLED_DriverWriteIT();
}

OLED_Status OLED_DriverSetContrast(uint8_t value)
{
    (void)value;
    return OLED_OK;
}

OLED_Status OLED_DriverSetPowerSave(bool enable)
{
    (void)enable;
    return OLED_OK;
}

void OLED_DriverHandleMemTxComplete(void)
{
}

void OLED_DriverHandleError(void)
{
}

/* ------------------------------------------------- services ui_app.c calls */

/* No key is ever pressed: the test judges the interface on what it draws by
 * itself, not on navigation. */
uint8_t buttons_raw_mask(void)
{
    return 0U;
}

static bool s_streaming;

void protocol_service_set_streaming(bool on)
{
    s_streaming = on;
}

bool protocol_service_streaming(void)
{
    return s_streaming;
}

void App_ToggleRecording(void)
{
}

/* The clock is unset on a board that has never been told the time, which is the
 * state the frozen firmware boots in. Reproduce that rather than a friendly one:
 * CubeMX leaves it at 2000-01-01, and ui_app.c seeds its editors from it. */
void rtc_service_get_datetime(rtc_datetime_t *out)
{
    if (out == NULL) {
        return;
    }
    out->year = 2000U;
    out->month = 1U;
    out->day = 1U;
    out->hour = 0U;
    out->minute = 0U;
    out->second = 0U;
    out->weekday = 0U;
}

bool rtc_service_set_datetime(const rtc_datetime_t *dt)
{
    (void)dt;
    return true;
}

/* oled_bus.c needs hi2c1; only the 7-bit form is asked for here. */
uint8_t oled_bus_address_7bit(void)
{
    return 0x3CU;
}
