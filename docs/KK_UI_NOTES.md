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
| Page descriptions, labels, fonts, icons and bound variables stay valid for the UI lifetime | **This is a borrow, not a copy: see [The descriptor lifetime contract](#the-descriptor-lifetime-contract-the-bug-that-made-a-real-panel-stay-black).** All of it is now `static`/`const` at file scope, including the `KK_UI_App` descriptor itself; live text is formatted into static buffers, never rebound to temporaries. Until 2026-09-19 the descriptor was a local in `ui_app_init()` and this row was false |
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

## The descriptor lifetime contract — the bug that made a real panel stay black

Found on hardware on 2026-09-19, on the first board ever flashed with this firmware.
It is recorded here because it is the one requirement in the table above that cannot be
satisfied by accident, and because every status word in the firmware said "fine" while
the screen was black.

**The contract.** `KK_UI_Init(const KK_UI_App *app)` stores the pointer — `kk_ui.app = app;`
in `ThirdParty/kk_ui/src/kk_ui.c` — and does not copy the struct. Every later frame reads
`routes`, `menu_pages`, `info_pages`, `int_bindings`, `bool_bindings`, `fonts` and `texts`
through it. So:

* the `KK_UI_App` object must outlive the entire KK_UI runtime;
* so must everything it points at: page tables, item labels, titles, row strings, fonts,
  binding descriptors — and the storage the bindings point into;
* `const` on the descriptor is correct and does not freeze state, because the binding
  members are pointers to mutable variables. A `const` descriptor may point at mutable data.

**What the code did instead.**

```c
void ui_app_init(void)
{
    KK_UI_App app;          /* a local */
    ...
    st = KK_UI_Init(&app);  /* KK_UI keeps &app */
}                         /* app dies here */
```

**Why nothing detected it.** `kk_ui_validate()` runs while the local is still on the live
stack, so `KK_UI_Init()` returned `KK_UI_OK`, `oled_present` was set true, and
`last_error_code` stayed 0. Later, `KK_UI_GetRoute()` reads `route_count` out of whatever
now occupies that stack region; when the answer is unusable, `KK_UI_DrawPage()` returns
early, the draw buffer stays identical to the stable frame, `OLED_Update()` finds an empty
difference and returns `OLED_OK` **without touching the bus**. Every layer tells the truth
about itself. The panel is black.

**How it was proven, and at what level.** A temporary debug branch brought up the panel in
stages, each one reported to a debugger: I2C scan `0x3C` OK, `OLED_Init()` OK, a direct
`OLED_Fill()` + `OLED_Update()` lit **every pixel of the real 128×64 module**. That put the
fault above the driver. Then the shipping `ui_app.c`, KK_UI and the KK_OLED graphics core
were compiled for the host against a shadow panel that records the page/column writes the
driver would have made: the real interface drew **0 pixels and sent 0 bytes**, while a
direct text render in the same harness drew 156. Changing that one local to file scope made
the same harness render the MAIN MENU — 2114 lit pixels across 7 of 8 page rows. That is
`tests/host/test_ui_frame.c`, now a permanent regression test.

Running many frames rather than one also walks off the dead memory: on the host it
segfaults after a couple of dozen frames. On the target the same reads are silent.

## Things upstream forbids guessing, which are therefore still open

`skills/kk-oled-port/SKILL.md` names this project's exact unknowns as things that must not
be filled in from a reference driver or common module defaults, including
CH1116-vs-SSD1306, `0x3C`-vs-`0x3D`, column offset 0-or-2, and "any generic init table",
and states that an I2C scan can prove only that some address acknowledges.

Accordingly `App/display/oled_bus.c` carries **three complete candidate profiles**, and
[HARDWARE_TEST_PLAN.md](HARDWARE_TEST_PLAN.md) stage D/E decides between them on a bench.

**Updated 2026-09-19, from the first real module.** Two of those unknowns are now closed by
observation rather than by a guess: the panel is **SSD1306** (identified on the module by the
user, and consistent with everything below) and it acknowledges at **7-bit `0x3C`** (HAL
`0x78`), which is what the address scan finds. A full-frame write lit every pixel of the
128×64 glass, so the selected profile's charge-pump and display-on bytes work.

**Still open, and not to be closed by inference.** The column offset and the COM pin layout.
An all-white frame cannot reveal either: every mapping shows white. A checker or border
pattern can, and that is the remaining part of stage E. The SH1106 and CH1116 profiles stay
in the tree as candidates for a different module, not as live options.

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

Everything else about the UI — layout, animation, focus, centring — was **compile verified
only** until 2026-09-19, when the first real panel made the question concrete. Two things
have changed since:

1. `tests/host/test_ui_frame.c` compiles the shipping `ui_app.c`, KK_UI and the KK_OLED
   graphics core unchanged, replaces only the I2C driver with a shadow panel that records
   the page/column writes the driver would have performed, and asserts that one real scene
   reaches the bus: the MAIN MENU, over 200 lit pixels minimum, several page rows, plus 60
   further frames that must neither crash nor corrupt the descriptor. It is the guard for
   the lifetime bug above, and it is what proved the fix by turning 0 pixels into 2114.
2. The panel itself has now answered some questions on hardware: I2C1 at 400 kHz on PB6/PB7,
   an ACK at 7-bit `0x3C`, the SSD1306 init sequence accepted, and a full-frame write
   lighting every pixel of the 128×64 module. See
   [HARDWARE_TEST_PLAN.md](HARDWARE_TEST_PLAN.md) stages D and E.

**Not yet seen on hardware:** the MAIN MENU rendered by the fixed firmware, button
navigation, animation, and whether the column mapping is right. The host render is
`HOST VERIFIED`, which is a different claim.
