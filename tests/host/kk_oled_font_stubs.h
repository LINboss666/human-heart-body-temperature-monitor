/**
 * @file  kk_oled_font_stubs.h
 * @brief Canvas that stands in for the OLED frame buffer so the vendor font
 *        decoder can run on a host. See the .c file.
 */
#ifndef KK_OLED_FONT_STUBS_H
#define KK_OLED_FONT_STUBS_H

#include <stdbool.h>
#include <stdint.h>

void     stub_clear(void);
void     stub_set_origin(int16_t x, int16_t y);
uint32_t stub_lit_count(void);
bool     stub_pixel(uint32_t x, uint32_t y);

#endif /* KK_OLED_FONT_STUBS_H */
