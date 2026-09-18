/*
 * Verifies the generated font data with the vendor's own decoder.
 *
 * tools/gen_oled_fonts.py hand-packs 95 ASCII glyphs into KK_OLED's compressed
 * u8g2 bit layout. Nothing about that layout can be confirmed by reading the C
 * array - a wrong field width or a mis-set jump offset yields bytes exactly as
 * plausible-looking as correct ones, and the failure would surface as garbage on
 * a panel that is not wired yet. So ThirdParty/kk_oled/graphics/kk_oled_font.c
 * is compiled here, unmodified, against the generated array, and the decoded
 * bitmaps are inspected.
 *
 * Glyphs are authored with no descenders, so with the baseline at y=0 the seven
 * rows land on canvas rows 0..6: the decoder computes
 * glyph_y = -height - y_offset + local_y and y_offset is stored as -ascent.
 * Every capture below depends on that. Getting it wrong is not a harmless
 * off-by-one: reading the wrong rows compares two all-zero grids, which makes the
 * symmetry assertions pass vacuously and reports every pair of glyphs identical.
 */
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "ctest.h"

#include "kk_oled_font_stubs.h"
#include "ui_fonts.h"

/* From ThirdParty/kk_oled/graphics/kk_oled_font.c, unmodified. */
void     OLED_SetFont(const uint8_t *font);
int16_t  OLED_DrawGlyph(int16_t x, int16_t y, uint16_t codepoint);
int16_t  OLED_GetGlyphAdvance(uint16_t codepoint);
uint16_t OLED_GetUTF8Width(const char *utf8);

#define GRID_W 5
#define GRID_H 7

typedef bool glyph_grid_t[GRID_H][GRID_W];

static void render_grid(uint16_t code, glyph_grid_t out)
{
    uint32_t row;
    uint32_t col;

    stub_clear();
    stub_set_origin(0, 0);
    (void)OLED_DrawGlyph(0, 0, code);
    for (row = 0U; row < GRID_H; row++) {
        for (col = 0U; col < GRID_W; col++) {
            out[row][col] = stub_pixel(col, row);
        }
    }
}

static uint32_t grid_lit(const glyph_grid_t g)
{
    uint32_t n = 0U;

    for (uint32_t row = 0U; row < GRID_H; row++) {
        for (uint32_t col = 0U; col < GRID_W; col++) {
            if (g[row][col]) { n++; }
        }
    }
    return n;
}

static void test_header_is_accepted(void)
{
    CTEST_CASE("the vendor decoder parses the generated header");
    OLED_SetFont(ui_font_body);
    /* A header the decoder cannot parse leaves the metrics at zero. */
    CHECK_EQ(OLED_GetGlyphAdvance('A'), UI_FONT_ADVANCE);
    CHECK_EQ(OLED_GetGlyphAdvance(' '), UI_FONT_ADVANCE);
    CHECK_EQ(OLED_GetUTF8Width(""), 0U);
    /* KK_OLED measures string width without the trailing glyph's right-hand gap:
     * it subtracts the last advance and adds that glyph's visible right edge,
     * width + x_offset. So n characters span (n-1) advances plus one glyph box,
     * not n advances. Checked here because the UI centres text with it and the
     * off-by-one is invisible until something is mis-centred on a real panel. */
    CHECK_EQ(OLED_GetUTF8Width("A"), GRID_W);
    CHECK_EQ(OLED_GetUTF8Width("AB"), UI_FONT_ADVANCE + GRID_W);
    CHECK_EQ(OLED_GetUTF8Width("Hello"), 4U * UI_FONT_ADVANCE + GRID_W);
}

static void test_every_printable_glyph_decodes(void)
{
    uint16_t c;
    uint16_t empty = 0U;
    glyph_grid_t g;

    CTEST_CASE("every printable codepoint except space produces a bitmap");
    OLED_SetFont(ui_font_body);
    for (c = 0x21U; c <= 0x7EU; c++) {
        render_grid(c, g);
        if (grid_lit(g) == 0U) {
            printf("      code %u ('%c') rendered nothing\n", c, (char)c);
            empty++;
        }
    }
    CHECK_EQ(empty, 0U);

    CTEST_CASE("space is genuinely empty");
    render_grid(' ', g);
    CHECK_EQ(grid_lit(g), 0U);
}

static void test_authored_shapes_survive_the_round_trip(void)
{
    glyph_grid_t g;

    CTEST_CASE("'O' is symmetric, hollow in the middle, and not a filled box");
    OLED_SetFont(ui_font_body);
    render_grid('O', g);
    for (uint32_t row = 0U; row < GRID_H; row++) {
        for (uint32_t col = 0U; col < GRID_W; col++) {
            CHECK_EQ(g[row][col], g[row][GRID_W - 1U - col]);
        }
    }
    CHECK(!g[3U][2U]);
    CHECK(grid_lit(g) >= 12U && grid_lit(g) <= 26U);

    CTEST_CASE("'E' lights the whole top row and the whole left column");
    render_grid('E', g);
    for (uint32_t col = 0U; col < GRID_W; col++) { CHECK(g[0U][col]); }
    for (uint32_t row = 0U; row < GRID_H; row++) { CHECK(g[row][0U]); }

    CTEST_CASE("'I' lights the full middle column");
    render_grid('I', g);
    for (uint32_t row = 0U; row < GRID_H; row++) { CHECK(g[row][2U]); }
}

static void test_distinct_glyphs_differ(void)
{
    static const struct { uint16_t a, b; } pairs[] = {
        { 'O', 'Q' }, { '0', 'O' }, { 'I', 'l' }, { '1', 'l' },
        { 'S', '5' }, { 'B', '8' }, { 'e', 'c' }, { ',', '.' },
        { '-', '_' }, { ':', ';' }, { 'O', 'o' }, { 'I', 'i' }
    };
    size_t i;

    CTEST_CASE("confusable pairs are not the same bitmap");
    OLED_SetFont(ui_font_body);
    for (i = 0U; i < sizeof(pairs) / sizeof(pairs[0]); i++) {
        glyph_grid_t a;
        glyph_grid_t b;
        bool same = true;

        render_grid(pairs[i].a, a);
        render_grid(pairs[i].b, b);
        for (uint32_t row = 0U; row < GRID_H && same; row++) {
            for (uint32_t col = 0U; col < GRID_W; col++) {
                if (a[row][col] != b[row][col]) {
                    same = false;
                    break;
                }
            }
        }
        if (same) {
            printf("      '%c' and '%c' are identical\n",
                   (char)pairs[i].a, (char)pairs[i].b);
        }
        CHECK(!same);
    }
}

static void test_missing_glyph_falls_back(void)
{
    glyph_grid_t g;

    CTEST_CASE("a codepoint outside the table yields no pixels, not garbage");
    OLED_SetFont(ui_font_body);
    render_grid(0x0001U, g);
    CHECK_EQ(grid_lit(g), 0U);

    CTEST_CASE("a codepoint above 0xFF takes the unicode path and terminates");
    render_grid(0x4E00U, g);
    CHECK_EQ(grid_lit(g), 0U);
}

CTEST_MAIN("oled font format")
{
    test_header_is_accepted();
    test_every_printable_glyph_decodes();
    test_authored_shapes_survive_the_round_trip();
    test_distinct_glyphs_differ();
    test_missing_glyph_falls_back();
}
