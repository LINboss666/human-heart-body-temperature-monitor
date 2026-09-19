/**
 * @file    ui_app.h
 * @brief   KK_UI integration: page table, custom ECG waveform page, refresh.
 *
 * Page inventory. HOME is switched off in kk_ui_config.h and the root is a menu
 * instead. That is a deliberate trade: the HOME template needs a 32x32 XBM per
 * entry, which is four more authored bitmaps plus their verification and their
 * licensing, for decoration on a 64 KB part. The menu template already gives a
 * highlighted, scrollable, keyboard-driven list, which is what a three-button
 * 128x64 interface needs to feel like a product rather than a text dump.
 *
 * The ECG page is a KK_UI custom page because the library has no waveform
 * widget, and kk-ui-extend's own instructions forbid adding one. The trace is
 * drawn here with KK_OLED primitives.
 */
#ifndef UI_APP_H
#define UI_APP_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/** Build the KK_UI application description and initialise the panel. Safe when
 *  no display answers: oled_present is then false and everything still runs. */
void ui_app_init(void);

/**
 * Drive one UI frame. Call at least every 10 ms from the main loop; the function
 * rate-limits its own rendering to KK_UI_FRAME_INTERVAL_MS and never blocks the
 * sample path beyond a single partial flush.
 */
void ui_app_update(uint32_t now_ms);

/** Push one processed sample into the scrolling trace. Never blocks. */
void ui_app_push_waveform(int16_t display_value, uint32_t sample_index);

/**
 * True while the ECG page is on screen, so the sample loop can skip building a
 * trace nobody is looking at.
 *
 * This is a convenience mirror, cleared by KK_UI_CustomOnLeave, and KK_UI defers
 * that callback until the slide animation finishes -- so it can stay true for one
 * transition. That is harmless for a redraw decision. It is deliberately NOT what
 * the KEY_OK recording action tests: that runs inside KK_UI's own dispatch, where
 * focus is known exactly rather than within a frame or two.
 */
bool ui_app_on_ecg_page(void);

/** Ask the UI to move to the ECG page (KEY_OK from HOME, or a host command). */
void ui_app_goto_ecg(void);

/** Display presence, for the STATUS page and HELLO capabilities. */
bool ui_app_display_present(void);

#ifdef __cplusplus
}
#endif

#endif /* UI_APP_H */
