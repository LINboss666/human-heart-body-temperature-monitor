#include "buttons.h"

#include "stm32f1xx_hal.h"

#include "main.h"

/*
 * Buttons are active-low against GND with the internal pull-up enabled by the
 * CubeMX configuration, so a read of 0 means pressed. That polarity is an
 * assumption about hardware that is not in this repository; it is isolated in
 * read_raw() so a normally-open wiring is a one-line change rather than a hunt
 * through the state machine.
 */
#define DEBOUNCE_MS       10U

/* KK_UI's documented key bitmask, duplicated here so this module does not have
 * to include the UI library to describe raw hardware state. */
#define RAW_BIT_UP    1U
#define RAW_BIT_DOWN  2U
#define RAW_BIT_OK    4U

typedef struct {
    bool      stable;         /* debounced electrical state, true = pressed */
    bool      raw;            /* last sample, before debouncing */
    uint32_t  changed_ms;     /* when raw last differed from stable */
} button_state_t;

static button_state_t s_btn[BTN_COUNT];

static bool read_raw(button_id_t id)
{
    uint16_t pin;
    GPIO_TypeDef *port;

    switch (id) {
    case BTN_UP:   pin = GPIO_PIN_12; port = GPIOB; break;
    case BTN_DOWN: pin = GPIO_PIN_13; port = GPIOB; break;
    case BTN_OK:   pin = GPIO_PIN_14; port = GPIOB; break;
    default:       return false;
    }
    /* Active low: a pressed button pulls the pin to ground. */
    return HAL_GPIO_ReadPin(port, pin) == GPIO_PIN_RESET;
}

void buttons_init(void)
{
    for (uint8_t i = 0U; i < (uint8_t)BTN_COUNT; i++) {
        s_btn[i].stable = false;
        s_btn[i].raw = false;
        s_btn[i].changed_ms = 0U;
    }
}

void buttons_scan(uint32_t now_ms)
{
    for (uint8_t i = 0U; i < (uint8_t)BTN_COUNT; i++) {
        button_state_t *b = &s_btn[i];
        bool raw = read_raw((button_id_t)i);

        if (raw != b->raw) {
            b->raw = raw;
            b->changed_ms = now_ms;
        } else if (raw != b->stable) {
            if ((uint32_t)(now_ms - b->changed_ms) >= DEBOUNCE_MS) {
                b->stable = raw;
            }
        }
    }
}

uint8_t buttons_raw_mask(void)
{
    uint8_t mask = 0U;

    if (s_btn[BTN_UP].stable) { mask |= RAW_BIT_UP; }
    if (s_btn[BTN_DOWN].stable) { mask |= RAW_BIT_DOWN; }
    if (s_btn[BTN_OK].stable) { mask |= RAW_BIT_OK; }
    return mask;
}

bool buttons_is_down(button_id_t id)
{
    if (id >= BTN_COUNT) {
        return false;
    }
    return s_btn[id].stable;
}
