# KK_UI / KK_OLED Integration Notes

Records what was actually found in the upstream projects and how this firmware
conforms, so a reviewer does not have to re-derive it. Provenance and licences are
in [UPSTREAM.md](UPSTREAM.md).

## What the upstream actually is

`gitee.com/keysking/kk_ui` is not a repository of a standalone C UI library. It is an
**Agent Skills** distribution whose C runtime is carried as a *skill asset* at
`skills/kk-ui-port/assets/kk-ui-runtime/`. It cannot run on its own: every drawing and
flush call goes into `kk_oled`, a second mandatory repository, which owns the frame
buffer, the graphics core and the entire hardware porting layer.

Neither repository contains an example application, a demo, a Keil project, or any
SSD1306/SH1106 code. The only shipped display driver targets **CH1116**, on `hi2c1`,
at 8-bit address `0x3D << 1`, with a column offset of 2.

So the integration here follows upstream's own documented procedure
(`skills/kk-ui-port/SKILL.md`, nine steps, plus `references/porting-strategy.md` and
`references/verification-checklist.md`): copy the runtime into the project, treat the
copy as project-owned code, and adapt only the driver boundary.

## Conformance to the upstream contract

| Upstream requirement | How this project satisfies it |
| --- | --- |
| Application initialises the OLED, then KK_UI | `ui_app_init()` calls `OLED_Init()` before `KK_UI_Init()`; KK_UI never calls `OLED_Init()` |
| `KK_UI_Update(now_ms, input)` from one context, at least every 10 ms | Called once per `App_Loop()` iteration at ~5 ms; time is passed in, there is no tick hook |
| Interrupts only accumulate raw input state | Buttons are sampled in the main loop; the DMA ISRs touch no UI state |
| Page descriptions, labels, fonts, icons and bound variables stay valid for the UI lifetime | Everything is `static`/`const` at file scope; live text is formatted into static buffers, never rebound to temporaries |
| The application must not clear or submit a frame in parallel with KK_UI | No `OLED_Clear()` / `OLED_Update*()` call exists anywhere in `App/`; only `KK_UI_Invalidate()` |
| No dynamic memory, no widget tree, no runtime registration | Pages are static tables; grep confirms zero allocator calls in all three code bases |
| Missing hardware is reported, not guessed | `OLED_Init()` failure sets `oled_present=false` and the firmware continues |

## Deviations and their reasons

1. **`KK_UI_REFRESH_MODE` = `KK_UI_REFRESH_BLOCKING`** (upstream default is DMA).
   DMA flush needs I2C1 buffer DMA and its NVIC vector, which the frozen `.ioc` does not
   enable. Adding them would be a CubeMX revision for a display-only gain. Cost: a
   partial-area flush is synchronous, bounded by the dirty width, and the double buffer is
   still allocated even though nothing is being sent asynchronously.

2. **HOME page disabled, root is a MENU.**
   The HOME template requires a 32×32 XBM per entry (`icon_xbm_32x32` is validated
   non-NULL). Four authored icon bitmaps, plus their verification and licence review, for
   decoration on a 64 KB part, bought nothing the menu template does not already provide.

3. **One font table for all three slots.**
   `kk_ui_valid_fonts()` requires `home_font`, `title_font` and `body_font` non-NULL. All
   three point at the same 1113-byte array; visual hierarchy comes from layout and
   inverted rows. Duplicating the table would have cost 2.2 KB of flash.

4. **No waveform widget was added.**
   KK_UI has none. `skills/kk-ui-extend/SKILL.md` states extending the core is a last resort
   and that `实现页面` permission is not `扩展核心` permission, and the README's own guidance
   is to draw waveforms in a custom page. The ECG trace is therefore drawn by
   `KK_UI_CustomOnDraw()` using KK_OLED primitives, and `ThirdParty/kk_ui/src/` is
   byte-identical to upstream.

5. **Only `driver/kk_oled_*.{c,h}` in KK_OLED was edited**, replacing three literals with
   values read from `App/display/oled_bus.c`. The graphics core — pixel layout, clipping,
   double buffering, submit state machine — is untouched, because
   `references/driver-contract.md` marks those as protected boundaries and
   `kk-oled-port/SKILL.md` forbids guessing controller parameters into them.

## Things upstream forbids guessing, which are therefore still open

`skills/kk-oled-port/SKILL.md` names this project's exact unknowns as things that must not
be filled in from a reference driver or common module defaults, including
CH1116-vs-SSD1306, `0x3C`-vs-`0x3D`, column offset 0-or-2, and "any generic init table",
and states that an I2C scan can prove only that some address acknowledges.

Accordingly `App/display/oled_bus.c` carries **three complete candidate profiles**, all
marked unverified, and [HARDWARE_TEST_PLAN.md](HARDWARE_TEST_PLAN.md) stage D/E decides
between them on a bench. No claim is made anywhere that the default is correct.

## Font licensing, the one thing the MIT licences do not cover

Neither repository ships glyph data, and the documented generator uses an online service
whose output kk_oled explicitly says is governed by the chosen typeface's own licence
rather than by MIT. Committing such output to a public repository would have been the one
licence problem here that vendoring the libraries does not solve.

The 5×7 ASCII set in `App/ui/ui_fonts.c` was therefore authored for this project by
`tools/gen_oled_fonts.py`, and no third-party typeface is involved. No CJK glyphs are
carried at all, which also satisfies the brief's prohibition on large Chinese font tables.

## Verification actually performed

`tests/host/test_font_format.c` compiles `kk_oled_font.c` **unmodified** against the
generated arrays and decodes them: 78 assertions covering header parsing, every printable
codepoint, authored glyph shapes, twelve confusable pairs, and fallback for absent
codepoints. It found three real defects that inspection of the C array could not have
shown: an over-narrow `advance` field, an off-by-one that made the first version of the
test pass vacuously, and KK_OLED's no-trailing-gap width rule.

Everything else about the UI — layout, animation, focus, centring — is **compile
verified only**. No pixels have ever been rendered, because there is no panel. That is
recorded as pending in [COURSE_REQUIREMENTS.md](COURSE_REQUIREMENTS.md) rather than
assumed working.
