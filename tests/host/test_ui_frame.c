/*
 * Does the shipping interface draw anything at all?
 *
 * The panel is proven - the same module lit every pixel when the driver was
 * called directly - so a black screen under the real UI has to come from above
 * the driver. This runs the unmodified ui_app.c against the unmodified KK_UI and
 * KK_OLED graphics, with the I2C driver replaced by a shadow panel, and reports
 * what the glass would have received.
 *
 * AS WRITTEN THIS TEST FAILS, and it is meant to: it is the reproducer for the
 * defect found on 2026-09-19 by bringing a real panel up. ui_app_init() hands
 * KK_UI_Init() the address of a stack-local KK_UI_App; KK_UI_Init() stores that
 * pointer (kk_ui.c:242) and reads the tables through it on every later draw, so
 * from the moment ui_app_init() returns the library is describing the UI out of a
 * dead stack frame. Everything still returns OK, because validation happens while
 * the struct is alive. The observable difference is here: the shadow panel ends up
 * empty. Making that one local `static` fills the panel with the main menu (2114
 * lit pixels, verified in the same harness), which is what pins the cause.
 *
 * The frame loop is deliberately not run long: past a handful of frames the
 * dangling reads segfault on the host, which is the same corruption that keeps the
 * panel black on the target.
 */
#include <stdio.h>
#include <string.h>

#include "ctest.h"

#include "diagnostics/diagnostics.h"
#include "kk_oled.h"
#include "kk_ui.h"
#include "kk_ui_internal.h"
#include "ui/ui_app.h"

#define UI_HOST_PAGES 8U
#define UI_HOST_COLS  128U

extern uint8_t ui_host_shadow[UI_HOST_PAGES][UI_HOST_COLS];
extern uint32_t ui_host_bytes_written;
extern uint32_t ui_host_transactions;

static uint32_t lit_pixels(void)
{
    uint32_t n = 0U;
    uint8_t page;
    uint8_t x;
    uint8_t bit;

    for (page = 0U; page < UI_HOST_PAGES; page++) {
        for (x = 0U; x < UI_HOST_COLS; x++) {
            for (bit = 0U; bit < 8U; bit++) {
                if ((ui_host_shadow[page][x] >> bit) & 1U) {
                    n++;
                }
            }
        }
    }
    return n;
}

static void dump_panel(void)
{
    uint8_t row;

    printf("    shadow panel, 16 blocks of 8 columns per page row\n");
    printf("    '.' = empty   '+' = 1-31 lit   '#' = 32-64 lit\n");
    for (row = 0U; row < UI_HOST_PAGES; row++) {
        uint8_t block;
        char line[32];
        int at = 0;

        for (block = 0U; block < 16U; block++) {
            uint32_t lit = 0U;
            uint8_t x;
            uint8_t bit;

            for (x = (uint8_t)(block * 8U); x < (uint8_t)(block * 8U + 8U); x++) {
                for (bit = 0U; bit < 8U; bit++) {
                    if ((ui_host_shadow[row][x] >> bit) & 1U) {
                        lit++;
                    }
                }
            }
            line[at++] = (lit == 0U) ? ' ' : (lit <= 31U ? '+' : '#');
        }
        line[at] = '\0';
        printf("    |%s|\n", line);
    }
    printf("    lit pixels: %lu of 8192, driver writes: %lu, bytes sent: %lu\n",
           (unsigned long)lit_pixels(), (unsigned long)ui_host_transactions,
           (unsigned long)ui_host_bytes_written);
}

static void test_ui_frame(void)
{
    CTEST_CASE("ui_app_init() accepts the real tables - and that is the trap");
    memset(ui_host_shadow, 0, sizeof(ui_host_shadow));
    ui_host_bytes_written = 0U;
    ui_host_transactions = 0U;
    diagnostics_init();
    ui_app_init();

    printf("    after ui_app_init: oled_present=%d last_error_code=%u "
           "kk_ui.initialized=%u display_fault=%u\n",
           diagnostics()->oled_present ? 1 : 0,
           (unsigned)diagnostics()->last_error_code,
           (unsigned)kk_ui.initialized, (unsigned)kk_ui.display_fault);
    CHECK(kk_ui.initialized == 1U);
    CHECK(diagnostics()->oled_present);

    CTEST_CASE("the description KK_UI keeps must still be alive after init returns");
    /* Read the way every later draw reads it: through the stored pointer. A dead
     * stack frame answers with a route that is not a page the library can draw. */
    {
        const KK_UI_PageRoute *route = KK_UI_GetRoute(kk_ui.current.page);

        printf("    KK_UI_GetRoute(page %u) -> %s\n", (unsigned)kk_ui.current.page,
               (route != NULL) ? "a route" : "NULL");
        CHECK(route != NULL);
    }

    CTEST_CASE("one real scene, one real update: the panel must receive pixels");
    KK_UI_DrawScene(1000U);
    (void)OLED_Update();
    dump_panel();
    printf("    expected: the MAIN MENU, six rows. Got %lu lit pixels.\n",
           (unsigned long)lit_pixels());
    CHECK(lit_pixels() > 0U);
}

CTEST_MAIN("ui frame (host shadow panel) - reproducer, see file header")
{
    test_ui_frame();
}
