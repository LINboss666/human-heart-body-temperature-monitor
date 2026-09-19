/**
 * @file    buttons.h
 * @brief   Three-button input on PB12/PB13/PB14, non-blocking and debounced.
 *
 * This module answers one question — which keys are pressed right now, with
 * contact bounce removed — and hands the answer to KK_UI, which owns everything
 * above it: edge detection, auto-repeat, long-press, and which screen a key
 * belongs to (KK_UI_KEY_DEBOUNCE_MS / KK_UI_KEY_REPEAT_DELAY_MS). Its documented
 * porting contract is to be given key state from one execution context, which is
 * what buttons_scan() + buttons_raw_mask() provide.
 *
 * It used to also expose a second view, buttons_take_event(), delivering
 * PRESS/RELEASE/SHORT/LONG for the application's own actions. That was removed:
 * BTN_EVT_SHORT was declared and compared against by the only consumer but never
 * emitted by any code path, so the action it gated could never fire; and routing
 * a key to "the screen that should act on it" duplicates KK_UI's own focus
 * decision, less accurately. Key actions now live in the KK_UI page callbacks.
 *
 * No HAL_Delay anywhere: a blocking debounce would stall the sample consumer.
 */
#ifndef BUTTONS_H
#define BUTTONS_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    BTN_UP = 0,
    BTN_DOWN = 1,
    BTN_OK = 2,
    BTN_COUNT
} button_id_t;

/** Call after MX_GPIO_Init; configures nothing, only resets state. */
void buttons_init(void);

/**
 * Sample and age the inputs. Call at least every 10 ms from one context.
 * @param now_ms a monotonic millisecond clock, normally HAL_GetTick().
 */
void buttons_scan(uint32_t now_ms);

/** Bitmask in KK_UI_KEY_UP / KK_UI_KEY_DOWN / KK_UI_KEY_OK order, raw. */
uint8_t buttons_raw_mask(void);

/** True while the named button is electrically read as pressed. */
bool buttons_is_down(button_id_t id);

#ifdef __cplusplus
}
#endif

#endif /* BUTTONS_H */
