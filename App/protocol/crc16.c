#include "crc16.h"

uint16_t crc16_ccitt(uint16_t crc, const uint8_t *data, uint16_t len)
{
    uint16_t i;

    while (len-- != 0U) {
        crc ^= (uint16_t)((uint16_t)(*data++) << 8);
        for (i = 0U; i < 8U; i++) {
            if ((crc & 0x8000U) != 0U) {
                crc = (uint16_t)((uint16_t)(crc << 1) ^ CRC16_POLY);
            } else {
                crc = (uint16_t)(crc << 1);
            }
        }
    }
    return crc;
}

uint16_t crc16_ccitt_buf(const uint8_t *data, uint16_t len)
{
    return crc16_ccitt(CRC16_INIT, data, len);
}
