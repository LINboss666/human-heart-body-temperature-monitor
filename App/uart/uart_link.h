/**
 * @file    uart_link.h
 * @brief   USART1 byte transport: polled non-blocking RX, blocking short TX.
 *
 * The frozen CubeMX configuration enables no USART1 interrupt and no USART1 DMA,
 * and adding either would be a regeneration whose only purpose was a transport
 * detail. So RX is drained one byte at a time with a zero-timeout HAL call from
 * the main loop, and TX is a short blocking write also issued only from the main
 * loop. At 230400 baud a 70-byte frame takes about 3 ms, which the acquisition
 * path absorbs because its buffering is 128 ms deep and hardware-driven.
 *
 * What is deliberately avoided: HAL_UART_Receive with a real timeout. That blocks
 * for the whole period, which would stall the sample consumer and the display.
 */
#ifndef UART_LINK_H
#define UART_LINK_H

#include <stdbool.h>
#include <stdint.h>

#include "app_config.h"

#ifdef __cplusplus
extern "C" {
#endif

/** Reset the ring and counters. The peripheral itself is set up by CubeMX. */
void uart_link_init(void);

/**
 * Drain whatever the peripheral has received into the ring.
 * Call often - at least once per APP_TICK_PERIOD_MS - or bytes overrun.
 */
void uart_link_rx_pump(void);

/** Pop one received byte. */
bool uart_link_read_byte(uint8_t *out);

/** Number of bytes currently buffered for the protocol layer. */
uint16_t uart_link_rx_count(void);

/** Blocking transmit of a whole frame. Call only from the main loop. */
bool uart_link_write(const uint8_t *data, uint16_t len);

uint32_t uart_link_tx_bytes(void);
uint32_t uart_link_rx_bytes(void);
uint32_t uart_link_rx_overruns(void);
uint32_t uart_link_tx_failures(void);

#ifdef __cplusplus
}
#endif

#endif /* UART_LINK_H */
