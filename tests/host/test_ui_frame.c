/*
 * Does the shipping interface actually draw, and does what it drew reach the bus?
 *
 * This is the regression test for the defect that made a real OLED stay black
 * while every layer reported success: ui_app_init() used to hand KK_UI_Init() the
 * address of a stack-local KK_UI_App, and KK_UI stores that pointer and reads the
 * page tables through it on every later frame. The test compiles the shipping
 * ui_app.c, KK_UI and the KK_OLED graphics core unchanged, and replaces only the
 * I2C driver - with a shadow panel that records the exact page and column writes
 * the real driver would have performed - plus the handful of service calls
 * ui_app.c needs. Nothing is faked at the interface level: the tables under test
 * are the ones that ship.
 *
 * The thresholds are deliberately loose. The point is not the pixel count of the
 * main menu, it is that a frame is produced, submitted and different from blank -
 * all three of which were false with the dangling descriptor.
 */
#include <stdio.h>
#include <string.h>

#include "ctest.h"

#include "diagnostics/diagnostics.h"
#include "kk_oled.h"
#include "kk_ui.h"
#include "kk_ui_internal.h"
#include "ui/ui_app.h"

#define UI_HOST_PAGES   8U
#define UI_HOST_COLS    128U
#define UI_HOST_CELLS   (UI_HOST_PAGES * UI_HOST_COLS)

/* A menu with a header row and six rows cannot be under ~400 lit pixels; a blank
 * frame is exactly zero. Anything between proves the same thing. */
#define UI_MIN_LIT_PIXELS  200U
/* Enough frames that the old bug had to walk over reused stack a dozen times. */
#define UI_FRAME_COUNT     60U

extern uint8_t ui_host_shadow[UI_HOST_PAGES][UI_HOST_COLS];
extern uint32_t ui_host_bytes_written;
extern uint32_t ui_host_transactions;

static uint32_t lit_pixels(void)
{
    uint32_t n = 0U;
    uint32_t i;
    uint8_t bit;

    for (i = 0U; i < UI_HOST_CELLS; i++) {
        for (bit = 0U; bit < 8U; bit++) {
            if (((&ui_host_shadow[0][0])[i] >> bit) & 1U) {
                n++;
            }
        }
    }
    return n;
}

static uint8_t pages_with_content(void)
{
    uint8_t page;
    uint8_t n = 0U;

    for (page = 0U; page < UI_HOST_PAGES; page++) {
        uint8_t x;
        bool any = false;

        for (x = 0U; x < UI_HOST_COLS && !any; x++) {
            any = ui_host_shadow[page][x] != 0U;
        }
        if (any) {
            n++;
        }
    }
    return n;
}

static void test_interface_draws_a_frame(void)
{
    uint32_t bytes_before;
    uint32_t transactions_before;
    uint32_t lit_after_init;
    const KK_UI_PageRoute *route;
    unsigned frame;

    CTEST_CASE("ui_app_init() accepts the shipping tables");
    memset(ui_host_shadow, 0, sizeof(ui_host_shadow));
    ui_host_bytes_written = 0U;
    ui_host_transactions = 0U;
    diagnostics_init();
    ui_app_init();

    CHECK(kk_ui.initialized == 1U);
    CHECK(diagnostics()->oled_present);
    CHECK_EQ(diagnostics()->last_error_code, 0U);
    CHECK(kk_ui.display_fault == 0U);
    CHECK(kk_ui.error_valid == 0U);

    CTEST_CASE("the description is still readable after ui_app_init() returned");
    /* Every draw reads through this pointer, so this is the lifetime question in
     * one line: it is fine while the struct is in scope and garbage after. */
    route = KK_UI_GetRoute(kk_ui.current.page);
    CHECK(route != NULL);
    if (route != NULL) {
        CHECK_EQ(route->type, (int)KK_UI_PAGE_MENU);
        CHECK(kk_ui.app->menu_pages != NULL);
        CHECK(kk_ui.app->menu_pages[route->index].title != NULL);
        printf("    root page %u is a MENU titled \"%s\" with %u items\n",
               (unsigned)kk_ui.current.page,
               kk_ui.app->menu_pages[route->index].title,
               (unsigned)kk_ui.app->menu_pages[route->index].item_count);
    }

    CTEST_CASE("one real scene and one real update put pixels on the bus");
    transactions_before = ui_host_transactions;
    KK_UI_DrawScene(1000U);
    (void)OLED_Update();
    lit_after_init = lit_pixels();

    printf("    main menu: %lu lit pixels over %u of %u page rows, "
           "%lu bytes sent in %lu writes\n",
           (unsigned long)lit_after_init, (unsigned)pages_with_content(),
           UI_HOST_PAGES, (unsigned long)ui_host_bytes_written,
           (unsigned long)(ui_host_transactions - transactions_before));
    CHECK(ui_host_bytes_written > 0U);
    CHECK(lit_after_init > UI_MIN_LIT_PIXELS);
    /* A header plus menu rows cannot fit in one page row of the 64-pixel panel. */
    CHECK(pages_with_content() >= 3U);

    CTEST_CASE("the frame is submitted, not left pending forever");
    /* After a successful blocking update the stable frame is what was drawn, and
     * the draw buffer has been cleared for the next one. */
    CHECK(kk_ui.frame_ready == 0U);
    CHECK(kk_ui.transfer_active == 0U);

    CTEST_CASE("dozens of real frames neither crash nor corrupt the descriptor");
    bytes_before = ui_host_bytes_written;
    transactions_before = ui_host_transactions;
    for (frame = 0U; frame < UI_FRAME_COUNT; frame++) {
        ui_app_update(1000U + (frame * 20U));
    }

    printf("    %u frames: %lu lit pixels still standing, %lu more bytes sent, "
           "page %u row %u\n",
           frame, (unsigned long)lit_pixels(),
           (unsigned long)(ui_host_bytes_written - bytes_before),
           (unsigned)kk_ui.current.page, (unsigned)kk_ui.current.selected);
    CHECK(lit_pixels() > UI_MIN_LIT_PIXELS);
    CHECK(kk_ui.initialized == 1U);
    CHECK(kk_ui.display_fault == 0U);
    CHECK(kk_ui.error_valid == 0U);
    CHECK_EQ(kk_ui.current.page, 1U);
    /* The descriptor still says what it said at init: same menu, same title. */
    CHECK(kk_ui.app->menu_pages[0].title != NULL);
    CHECK(strcmp(kk_ui.app->menu_pages[0].title, "MAIN MENU") == 0);
    CHECK_EQ(kk_ui.app->route_count, 7U);
    /* Nothing new needed to be drawn every frame; a stable menu is a stable
     * frame. What must not happen is the interface going blank again. */
    CHECK(lit_pixels() == lit_after_init || lit_pixels() > UI_MIN_LIT_PIXELS);
    CHECK(ui_host_transactions >= transactions_before);
}

CTEST_MAIN("ui frame (real interface, host shadow panel)")
{
    test_interface_draws_a_frame();
}
