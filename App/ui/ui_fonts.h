/**
 * @file    ui_fonts.h
 * @brief   Font data for the OLED UI. See ui_fonts.c for provenance.
 */
#ifndef UI_FONTS_H
#define UI_FONTS_H

#include <stdint.h>

/* Glyph box and the advance the layout code needs without parsing the header. */
#define UI_FONT_WIDTH    5
#define UI_FONT_HEIGHT   7
#define UI_FONT_ADVANCE  6

#define UI_FONT_BYTES    1113

/* A single table. KK_UI's font struct holds three pointers, and ui_app.c gives
 * all three of them this same array: the UI creates hierarchy through layout and
 * inverted rows rather than through a second and third typeface, which on a
 * 64 KB part is the right trade. */
extern const uint8_t ui_font_body[UI_FONT_BYTES];

#endif /* UI_FONTS_H */
