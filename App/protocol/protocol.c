#include "protocol.h"

#include <string.h>

#include "crc16.h"

/* ------------------------------------------------------- field primitives */

void pkt_put_u16(uint8_t *p, uint16_t v)
{
    p[0] = (uint8_t)(v & 0xFFU);
    p[1] = (uint8_t)(v >> 8);
}

void pkt_put_u32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)(v & 0xFFU);
    p[1] = (uint8_t)((v >> 8) & 0xFFU);
    p[2] = (uint8_t)((v >> 16) & 0xFFU);
    p[3] = (uint8_t)(v >> 24);
}

void pkt_put_i16(uint8_t *p, int16_t v)
{
    pkt_put_u16(p, (uint16_t)v);
}

uint16_t pkt_get_u16(const uint8_t *p)
{
    return (uint16_t)((uint16_t)p[0] | ((uint16_t)p[1] << 8));
}

uint32_t pkt_get_u32(const uint8_t *p)
{
    return (uint32_t)p[0]
         | ((uint32_t)p[1] << 8)
         | ((uint32_t)p[2] << 16)
         | ((uint32_t)p[3] << 24);
}

int16_t pkt_get_i16(const uint8_t *p)
{
    return (int16_t)pkt_get_u16(p);
}

/* ---------------------------------------------------------------- builders */

uint16_t pkt_build(uint8_t *dst, uint16_t dst_len, uint8_t type,
                   uint16_t sequence, uint32_t device_ts_ms,
                   const uint8_t *payload, uint16_t payload_len)
{
    uint16_t total;
    uint16_t crc;

    if (dst == NULL) {
        return 0U;
    }
    if (payload_len > PKT_MAX_PAYLOAD) {
        return 0U;
    }
    total = (uint16_t)(PKT_OVERHEAD + payload_len);
    if (dst_len < total) {
        return 0U;
    }

    dst[0] = PKT_MAGIC0;
    dst[1] = PKT_MAGIC1;
    dst[2] = (uint8_t)PROTOCOL_VERSION;
    dst[3] = type;
    pkt_put_u16(&dst[4], sequence);
    pkt_put_u16(&dst[6], payload_len);
    pkt_put_u32(&dst[8], device_ts_ms);

    if (payload_len != 0U) {
        if (payload == NULL) {
            return 0U;
        }
        memcpy(&dst[PKT_HEADER_SIZE], payload, payload_len);
    }

    crc = crc16_ccitt_buf(dst, (uint16_t)(PKT_HEADER_SIZE + payload_len));
    pkt_put_u16(&dst[PKT_HEADER_SIZE + payload_len], crc);
    return total;
}

/* ----------------------------------------------------------------- parser */

/* Distance from `at` to the next magic pair, or PKT_NO_MAGIC. Leaves the final
 * byte in play because a lone trailing 0xA5 may be the first half of a magic
 * that has not finished arriving. */
#define PKT_NO_MAGIC 0xFFFFU

static uint16_t find_magic(const uint8_t *buf, uint16_t len, uint16_t at)
{
    while ((uint16_t)(at + 1U) < len) {
        if (buf[at] == PKT_MAGIC0 && buf[at + 1U] == PKT_MAGIC1) {
            return at;
        }
        at++;
    }
    return PKT_NO_MAGIC;
}

pkt_result_t pkt_parse(const uint8_t *buf, uint16_t len,
                       pkt_frame_t *out, uint16_t *consumed)
{
    uint16_t at = 0U;

    if (buf == NULL || consumed == NULL) {
        return PKT_ERR_LENGTH;
    }

    for (;;) {
        uint16_t magic;
        uint16_t payload_len;
        uint16_t total;
        uint16_t want;

        magic = find_magic(buf, len, at);
        if (magic == PKT_NO_MAGIC) {
            /* Keep a possible half magic at the very end of the buffer. */
            *consumed = (len > 0U) ? (uint16_t)(len - 1U) : 0U;
            return PKT_NEED_MORE;
        }
        at = magic;

        if ((uint16_t)(len - at) < PKT_HEADER_SIZE) {
            *consumed = at;
            return PKT_NEED_MORE;
        }

        payload_len = pkt_get_u16(&buf[at + 6]);
        if (payload_len > PKT_MAX_PAYLOAD) {
            /* Length is nonsense, so this is not a frame we can trust.
             * Step one byte past the magic and rescan. */
            at = (uint16_t)(at + 2U);
            continue;
        }
        if (buf[at + 2] != (uint8_t)PROTOCOL_VERSION) {
            at = (uint16_t)(at + 2U);
            continue;
        }

        total = (uint16_t)(PKT_OVERHEAD + payload_len);
        want = (uint16_t)(PKT_HEADER_SIZE + payload_len);
        if ((uint16_t)(len - at) < total) {
            *consumed = at;
            return PKT_NEED_MORE;
        }

        {
            uint16_t calc = crc16_ccitt_buf(&buf[at], want);
            uint16_t held = pkt_get_u16(&buf[at + want]);
            if (calc != held) {
                if (out != NULL) {
                    out->type = buf[at + 3];
                    out->sequence = pkt_get_u16(&buf[at + 4]);
                    out->length = payload_len;
                }
                *consumed = (uint16_t)(at + 2U);
                return PKT_ERR_CRC;
            }
        }

        if (out != NULL) {
            out->version = buf[at + 2];
            out->type = buf[at + 3];
            out->sequence = pkt_get_u16(&buf[at + 4]);
            out->length = payload_len;
            out->device_ts_ms = pkt_get_u32(&buf[at + 8]);
            out->payload = &buf[at + PKT_HEADER_SIZE];
        }
        *consumed = (uint16_t)(at + total);
        return PKT_OK;
    }
}
