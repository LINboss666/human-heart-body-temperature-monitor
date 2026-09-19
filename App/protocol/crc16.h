/**
 * @file    crc16.h
 * @brief   CRC-16/CCITT-FALSE as used by the USART1 framing.
 *
 * Parameters, so the PC side and any third-party tool can reproduce it exactly:
 *   width 16, poly 0x1021, init 0xFFFF, refin false, refout false, xorout 0x0000
 *   check value (ASCII "123456789") = 0x29B1
 *
 * Deliberately a bitwise loop rather than a 512-byte lookup table: the whole
 * link carries about 3.6 kB/s, so ~16 shifts per byte costs far less Flash than
 * the table would.
 */
#ifndef CRC16_H
#define CRC16_H

#include <stddef.h>
#include <stdint.h>

#define CRC16_INIT   0xFFFFU
#define CRC16_POLY   0x1021U

/** CRC of the "123456789" check vector; used by the test suite. */
#define CRC16_CHECK  0x29B1U

uint16_t crc16_ccitt(uint16_t crc, const uint8_t *data, uint16_t len);

/** Convenience: CRC over a whole buffer starting from the initial value. */
uint16_t crc16_ccitt_buf(const uint8_t *data, uint16_t len);

#endif /* CRC16_H */
