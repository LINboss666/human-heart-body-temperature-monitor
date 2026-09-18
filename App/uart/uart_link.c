#include "uart_link.h"

#include <string.h>

#include "stm32f1xx_hal.h"

#include "usart.h"

/*
 * Power-of-two ring so the head/tail wrap is a mask rather than a modulo, and a
 * single producer (the pump, in main-loop context) plus a single consumer (the
 * protocol parser, same context) means no locking is needed. Both sides actually
 * run from App_Loop, so there is no ISR/main race to protect against here at all
 * - the reason ring buffers are usually lock-free is not needed, but the layout
 * costs nothing and stays correct if RX ever moves to an interrupt.
 */
#define RING_MASK (UART_RX_RING_SIZE - 1U)

static uint8_t  s_ring[UART_RX_RING_SIZE];
static volatile uint16_t s_head;
static volatile uint16_t s_tail;
static uint32_t s_tx_bytes;
static uint32_t s_rx_bytes;
static uint32_t s_overruns;
static uint32_t s_tx_failures;

/* Guarantees the mask arithmetic above is valid; fails the build if someone
 * changes UART_RX_RING_SIZE to something that is not a power of two. */
typedef char uart_rx_ring_size_must_be_power_of_two[
    ((UART_RX_RING_SIZE & RING_MASK) == 0U && UART_RX_RING_SIZE > 1U) ? 1 : -1];
static uart_rx_ring_size_must_be_power_of_two s_ring_size_check;

void uart_link_init(void)
{
    s_head = 0U;
    s_tail = 0U;
    s_tx_bytes = 0U;
    s_rx_bytes = 0U;
    s_overruns = 0U;
    s_tx_failures = 0U;
    (void)s_ring_size_check;
}

void uart_link_rx_pump(void)
{
    uint8_t b;
    uint16_t next;

    /* Bounded so a flooded line cannot starve the rest of the main loop. */
    for (uint8_t i = 0U; i < 32U; i++) {
        /* Timeout 0: returns HAL_TIMEOUT immediately when nothing has arrived.
         * This is the non-blocking read; a real timeout here would stall
         * acquisition for its whole duration. */
        if (HAL_UART_Receive(&huart1, &b, 1U, 0U) != HAL_OK) {
            return;
        }
        next = (uint16_t)((s_head + 1U) & RING_MASK);
        if (next == s_tail) {
            s_overruns++;          /* full: drop the byte and count it */
            continue;              /* never overwrite unread data */
        }
        s_ring[s_head] = b;
        s_head = next;
        s_rx_bytes++;
    }
}

bool uart_link_read_byte(uint8_t *out)
{
    if (out == NULL || s_tail == s_head) {
        return false;
    }
    *out = s_ring[s_tail];
    s_tail = (uint16_t)((s_tail + 1U) & RING_MASK);
    return true;
}

uint16_t uart_link_rx_count(void)
{
    return (uint16_t)((s_head - s_tail) & RING_MASK);
}

bool uart_link_write(const uint8_t *data, uint16_t len)
{
    if (data == NULL || len == 0U) {
        return false;
    }
    if (HAL_UART_Transmit(&huart1, (uint8_t *)data, len, UART_TX_TIMEOUT_MS) != HAL_OK) {
        s_tx_failures++;
        return false;
    }
    s_tx_bytes += len;
    return true;
}

uint32_t uart_link_tx_bytes(void)      { return s_tx_bytes; }
uint32_t uart_link_rx_bytes(void)      { return s_rx_bytes; }
uint32_t uart_link_rx_overruns(void)   { return s_overruns; }
uint32_t uart_link_tx_failures(void)   { return s_tx_failures; }
