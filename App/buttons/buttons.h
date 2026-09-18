/**
 * @file    buttons.h
 * @brief   Three-button input on PB12/PB13/PB14, non-blocking and debounced.
 *
 * Two views are exposed deliberately, because they have different consumers.
 *
 *   buttons_raw_mask()   the current pressed/not-pressed bitmask, with this
 *                        module's own 10 ms contact bounce removed. KK_UI does
 *                        its debounce, auto-repeat and long-press handling
 *                        internally (KK_UI_KEY_DEBOUNCE_MS /
 *                        KK_UI_KEY_REPEAT_DELAY_MS) and its documented porting
 *                        contract is to be handed key state from one execution
 *                        context, which is what this is.
 *
 *   buttons_take_event() PRESS / RELEASE / SHORT / LONG, debounced here, for the
 *                        application's own actions such as starting a recording.
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

typedef enum {
    BTN_EVT_PRESS = 0,
    BTN_EVT_RELEASE,
    BTN_EVT_SHORT,     /* emitted on release, before a long press was reached */
    BTN_EVT_LONG       /* emitted once, while still held */
} button_event_kind_t;

typedef struct {
    button_id_t       id;
    button_event_kind_t kind;
} button_event_t;

/** Call after MX_GPIO_Init; configures nothing, only resets state. */
void buttons_init(void);

/**
 * Sample and age the inputs. Call at least every 10 ms from one context.
 * @param now_ms a monotonic millisecond clock, normally HAL_GetTick().
 */
void buttons_scan(uint32_t now_ms);

/** Pop one queued event, oldest first. */
bool buttons_take_event(button_event_t *out);

/** Bitmask in KK_UI_KEY_UP / KK_UI_KEY_DOWN / KK_UI_KEY_OK order, raw. */
uint8_t buttons_raw_mask(void);

/** True while the named button is electrically read as pressed. */
bool buttons_is_down(button_id_t id);

void buttons_set_long_press_ms(uint16_t ms);

#ifdef __cplusplus
}
#endif

#endif /* BUTTONS_H */
