#!/usr/bin/env python3
"""
Hand-authored 5x7 ASCII bitmap set for the 128x64 OLED, packed into the u8g2
compressed font format that ThirdParty/kk_oled decodes.

Why this exists in-repo: kk_ui requires three font arrays and neither upstream
ships any glyph data. Their documented generator depends on an online third-party
service, and kk_oled's own README states plainly that MIT does not extend to the
resulting bitmaps. Committing a service's output into a public repository would
have been the one licence problem in this project that the vendoring of KK_UI
itself does not cover. Everything here is authored for this project, so there is
no third-party typeface licence in play. See docs/UPSTREAM.md.

The bitstream layout is not guessed. It mirrors graphics/kk_oled_font.c:

  header            23 bytes, [2..8] = field widths, [9..16] = maxima/offsets,
                    [17..22] = big-endian 16-bit jump offsets
  glyph records     [code][record_length][LSB-first fields][RLE pixels]
  field order       width, height, x_offset, y_offset, advance
  signed fields     stored biased: value = read - 2**(bits-1)
  RLE               repeat a (zero_run, one_run) pair while the 1-bit flag says so

tools/run_host_tests.py compiles the vendor decoder against these arrays, so a
mistake here fails a test instead of showing up as garbage on a bench.
"""
from __future__ import annotations

import sys
from pathlib import Path

FIRST, LAST = 0x20, 0x7E

# Each glyph is five columns, LSB = top row. Authored for legibility at 5x7 with
# no descenders below row 6, which keeps ascent/descent arithmetic simple.
FONT_5X7 = {
    " ": (0x00, 0x00, 0x00, 0x00, 0x00), "!": (0x00, 0x00, 0x5F, 0x00, 0x00),
    '"': (0x00, 0x07, 0x00, 0x07, 0x00), "#": (0x14, 0x7F, 0x14, 0x7F, 0x14),
    "$": (0x24, 0x2A, 0x7F, 0x2A, 0x12), "%": (0x23, 0x13, 0x08, 0x64, 0x62),
    "&": (0x36, 0x49, 0x55, 0x22, 0x50), "'": (0x00, 0x05, 0x03, 0x00, 0x00),
    "(": (0x00, 0x1C, 0x22, 0x41, 0x00), ")": (0x00, 0x41, 0x22, 0x1C, 0x00),
    "*": (0x14, 0x08, 0x3E, 0x08, 0x14), "+": (0x08, 0x08, 0x3E, 0x08, 0x08),
    ",": (0x00, 0x50, 0x30, 0x00, 0x00), "-": (0x08, 0x08, 0x08, 0x08, 0x08),
    ".": (0x00, 0x60, 0x60, 0x00, 0x00), "/": (0x20, 0x10, 0x08, 0x04, 0x02),
    "0": (0x3E, 0x51, 0x49, 0x45, 0x3E), "1": (0x00, 0x42, 0x7F, 0x40, 0x00),
    "2": (0x42, 0x61, 0x51, 0x49, 0x46), "3": (0x21, 0x41, 0x45, 0x4B, 0x31),
    "4": (0x18, 0x14, 0x12, 0x7F, 0x10), "5": (0x27, 0x45, 0x45, 0x45, 0x39),
    "6": (0x3C, 0x4A, 0x49, 0x49, 0x30), "7": (0x01, 0x71, 0x09, 0x05, 0x03),
    "8": (0x36, 0x49, 0x49, 0x49, 0x36), "9": (0x06, 0x49, 0x49, 0x29, 0x1E),
    ":": (0x00, 0x36, 0x36, 0x00, 0x00), ";": (0x00, 0x56, 0x36, 0x00, 0x00),
    "<": (0x08, 0x14, 0x22, 0x41, 0x00), "=": (0x14, 0x14, 0x14, 0x14, 0x14),
    ">": (0x00, 0x41, 0x22, 0x14, 0x08), "?": (0x02, 0x01, 0x51, 0x09, 0x06),
    "@": (0x32, 0x49, 0x79, 0x41, 0x3E), "A": (0x7E, 0x11, 0x11, 0x11, 0x7E),
    "B": (0x7F, 0x49, 0x49, 0x49, 0x36), "C": (0x3E, 0x41, 0x41, 0x41, 0x22),
    "D": (0x7F, 0x41, 0x41, 0x22, 0x1C), "E": (0x7F, 0x49, 0x49, 0x49, 0x41),
    "F": (0x7F, 0x09, 0x09, 0x09, 0x01), "G": (0x3E, 0x41, 0x49, 0x49, 0x7A),
    "H": (0x7F, 0x08, 0x08, 0x08, 0x7F), "I": (0x00, 0x41, 0x7F, 0x41, 0x00),
    "J": (0x20, 0x40, 0x41, 0x3F, 0x01), "K": (0x7F, 0x08, 0x14, 0x22, 0x41),
    "L": (0x7F, 0x40, 0x40, 0x40, 0x40), "M": (0x7F, 0x02, 0x0C, 0x02, 0x7F),
    "N": (0x7F, 0x04, 0x08, 0x10, 0x7F), "O": (0x3E, 0x41, 0x41, 0x41, 0x3E),
    "P": (0x7F, 0x09, 0x09, 0x09, 0x06), "Q": (0x3E, 0x41, 0x51, 0x21, 0x5E),
    "R": (0x7F, 0x09, 0x19, 0x29, 0x46), "S": (0x46, 0x49, 0x49, 0x49, 0x31),
    "T": (0x01, 0x01, 0x7F, 0x01, 0x01), "U": (0x3F, 0x40, 0x40, 0x40, 0x3F),
    "V": (0x1F, 0x20, 0x40, 0x20, 0x1F), "W": (0x3F, 0x40, 0x38, 0x40, 0x3F),
    "X": (0x63, 0x14, 0x08, 0x14, 0x63), "Y": (0x07, 0x08, 0x70, 0x08, 0x07),
    "Z": (0x61, 0x51, 0x49, 0x45, 0x43), "[": (0x00, 0x7F, 0x41, 0x41, 0x00),
    "\\": (0x02, 0x04, 0x08, 0x10, 0x20), "]": (0x00, 0x41, 0x41, 0x7F, 0x00),
    "^": (0x04, 0x02, 0x01, 0x02, 0x04), "_": (0x40, 0x40, 0x40, 0x40, 0x40),
    "`": (0x00, 0x01, 0x02, 0x04, 0x00), "a": (0x20, 0x54, 0x54, 0x54, 0x78),
    "b": (0x7F, 0x48, 0x4C, 0x54, 0x38), "c": (0x38, 0x44, 0x44, 0x44, 0x20),
    "d": (0x38, 0x4C, 0x54, 0x48, 0x7F), "e": (0x38, 0x54, 0x54, 0x54, 0x18),
    "f": (0x08, 0x7E, 0x09, 0x01, 0x02), "g": (0x0C, 0x52, 0x52, 0x52, 0x3E),
    "h": (0x7F, 0x08, 0x04, 0x04, 0x78), "i": (0x00, 0x44, 0x7D, 0x40, 0x00),
    "j": (0x20, 0x40, 0x44, 0x3D, 0x00), "k": (0x7F, 0x10, 0x28, 0x44, 0x00),
    "l": (0x00, 0x41, 0x7F, 0x40, 0x00), "m": (0x7C, 0x04, 0x18, 0x04, 0x78),
    "n": (0x7C, 0x08, 0x04, 0x04, 0x78), "o": (0x38, 0x44, 0x44, 0x44, 0x38),
    "p": (0x7C, 0x14, 0x14, 0x14, 0x08), "q": (0x08, 0x14, 0x14, 0x18, 0x7C),
    "r": (0x7C, 0x08, 0x04, 0x04, 0x08), "s": (0x48, 0x54, 0x54, 0x54, 0x20),
    "t": (0x04, 0x3F, 0x44, 0x40, 0x20), "u": (0x3C, 0x40, 0x40, 0x20, 0x7C),
    "v": (0x1C, 0x20, 0x40, 0x20, 0x1C), "w": (0x3C, 0x40, 0x30, 0x40, 0x3C),
    "x": (0x44, 0x28, 0x10, 0x28, 0x44), "y": (0x0C, 0x50, 0x50, 0x50, 0x3C),
    "z": (0x44, 0x64, 0x54, 0x4C, 0x44), "{": (0x00, 0x08, 0x36, 0x41, 0x00),
    "|": (0x00, 0x00, 0x7F, 0x00, 0x00), "}": (0x00, 0x41, 0x36, 0x08, 0x00),
    "~": (0x08, 0x04, 0x08, 0x10, 0x08),
}

GLYPH_W = 5
GLYPH_H = 7
ADVANCE = 6          # 5 pixels plus one inter-character gap
ASCENT = 7
DESCENT = 0

# Field widths. Runs of background pixels can span a whole blank row, so the
# zero-run field must hold up to 35 - but the encoder splits long runs, so 3
# bits (max 7) is enough and is what u8g2 uses at this size.
BPX0, BPX1 = 3, 3
BITS_W, BITS_H, BITS_X, BITS_Y, BITS_ADV = 3, 3, 1, 4, 4
MAX_RUN = (1 << BPX0) - 1


def rows_of(ch: str) -> list[int]:
    """Column-major 5 bytes -> one int per row, bit n = column n."""
    cols = FONT_5X7[ch]
    return [((cols[c] >> r) & 1) << c for c in range(GLYPH_W) for r in [0]][0:1] \
        if False else [sum(((cols[c] >> r) & 1) << c for c in range(GLYPH_W))
                       for r in range(GLYPH_H)]


class BitWriter:
    def __init__(self) -> None:
        self.bytes = bytearray()
        self.cur = 0
        self.nbits = 0

    def put(self, value: int, count: int) -> None:
        value &= (1 << count) - 1
        self.cur |= value << self.nbits
        self.nbits += count
        while self.nbits >= 8:
            self.bytes.append(self.cur & 0xFF)
            self.cur >>= 8
            self.nbits -= 8

    def put_signed(self, value: int, count: int) -> None:
        self.put(value + (1 << (count - 1)), count)

    def flush(self) -> None:
        if self.nbits:
            self.bytes.append(self.cur & 0xFF)
            self.cur = 0
            self.nbits = 0


def encode_glyph(rows: list[int]) -> bytes:
    """One glyph record body: metrics then the RLE pixel stream."""
    bw = BitWriter()
    bw.put(GLYPH_W, BITS_W)
    bw.put(GLYPH_H, BITS_H)
    bw.put_signed(0, BITS_X)
    bw.put_signed(-ASCENT, BITS_Y)
    bw.put_signed(ADVANCE, BITS_ADV)

    pixels = [(row >> c) & 1 for row in rows for c in range(GLYPH_W)]
    i = 0
    while i < len(pixels):
        z = 0
        while i < len(pixels) and pixels[i] == 0 and z < MAX_RUN:
            z += 1
            i += 1
        o = 0
        while i < len(pixels) and pixels[i] == 1 and o < MAX_RUN:
            o += 1
            i += 1
        bw.put(z, BPX0)
        bw.put(o, BPX1)
        # repeat flag 0: always read a fresh pair. Costs 1 bit per pair and keeps
        # the encoder obviously correct, which matters more here than 20 bytes.
        bw.put(0, 1)
        if i >= len(pixels):
            break
    bw.flush()
    return bytes(bw.bytes)


def build_font() -> tuple[bytes, dict]:
    glyphs = bytearray()
    start_upper = 0
    start_lower = 0
    for code in range(FIRST, LAST + 1):
        ch = chr(code)
        body = encode_glyph(rows_of(ch))
        rec_len = len(body) + 2
        if rec_len > 255:
            raise SystemExit(f"glyph {ch!r} record too long: {rec_len}")
        if code == ord("A"):
            start_upper = len(glyphs)
        if code == ord("a"):
            start_lower = len(glyphs)
        glyphs.append(code)
        glyphs.append(rec_len)
        glyphs += body

    # Terminator: cursor[1] == 0 stops the ASCII scan, and a >0xFF lookup lands
    # here too and gives up immediately.
    glyphs += bytes([0x00, 0x00])
    start_unicode = len(glyphs)

    header = bytearray(23)
    header[0] = 1
    header[1] = 0
    header[2], header[3] = BPX0, BPX1
    header[4], header[5] = BITS_W, BITS_H
    header[6], header[7], header[8] = BITS_X, BITS_Y, BITS_ADV
    header[9] = GLYPH_W
    header[10] = GLYPH_H
    header[11] = 0                       # x_offset of the whole font
    header[12] = (-ASCENT) & 0xFF        # y_offset
    header[13] = ASCENT                  # ascent_text
    header[14] = DESCENT                 # descent_text
    header[15] = ASCENT                  # ascent_extended
    header[16] = DESCENT                 # descent_extended
    for at, val in ((17, start_upper), (19, start_lower), (21, start_unicode)):
        header[at] = (val >> 8) & 0xFF
        header[at + 1] = val & 0xFF
    return bytes(header) + bytes(glyphs), {
        "chars": LAST - FIRST + 1, "bytes": len(header) + len(glyphs)}


def as_c_array(name: str, data: bytes, per_row: int = 12) -> str:
    lines = [f"const uint8_t {name}[{len(data)}] = {{"]
    for i in range(0, len(data), per_row):
        chunk = data[i:i + per_row]
        lines.append("    " + ", ".join(f"0x{b:02X}" for b in chunk) + ",")
    lines.append("};")
    return "\n".join(lines)


HEADER = """/**
 * @file    ui_fonts.c
 * @brief   Project-authored 5x7 ASCII glyphs in the compressed u8g2 layout the
 *          vendored KK_OLED decoder expects.
 *
 * Generated by tools/gen_oled_fonts.py. Do not edit by hand: change the bitmap
 * table there and regenerate. See docs/UPSTREAM.md for why the fonts are built
 * here rather than fetched from the third-party service kk_oled documents, whose
 * glyph output kk_oled itself says is not covered by its MIT licence.
 *
 * Three arrays exist because KK_UI's font validator requires home, title and body
 * to all be non-NULL. They are the same face, so one 5x7 design serves the whole
 * UI and no CJK table is carried - the brief caps this part at ASCII plus a few
 * icons for a reason.
 */

#include "ui_fonts.h"
"""

HEADER_H = """/**
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

#define UI_FONT_BYTES    %s

/* A single table. KK_UI's font struct holds three pointers, and ui_app.c gives
 * all three of them this same array: the UI creates hierarchy through layout and
 * inverted rows rather than through a second and third typeface, which on a
 * 64 KB part is the right trade. */
extern const uint8_t ui_font_body[UI_FONT_BYTES];

#endif /* UI_FONTS_H */
"""


def main() -> int:
    data, info = build_font()
    root = Path(__file__).resolve().parent.parent
    out_c = root / "App" / "ui" / "ui_fonts.c"
    out_h = root / "App" / "ui" / "ui_fonts.h"
    out_c.parent.mkdir(parents=True, exist_ok=True)

    # One table only. KK_UI_Fonts holds three pointers rather than three tables,
    # and ui_app.c points all three at this array: the UI builds hierarchy out of
    # layout and inverted rows instead of a second and third typeface, which on a
    # 64 KB part is the right trade.
    out_c.write_text(HEADER + "\n" + as_c_array("ui_font_body", data) + "\n",
                     encoding="utf-8", newline="\n")
    out_h.write_text(HEADER_H % info["bytes"], encoding="utf-8", newline="\n")

    print(f"font: {info['chars']} glyphs, {info['bytes']} bytes")
    print("wrote", out_h)
    print("wrote", out_c)
    return 0


if __name__ == "__main__":
    sys.exit(main())
