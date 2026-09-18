/*
 * Emits golden protocol frames built by the shipping C encoder, as `name|hex`
 * lines on stdout. tools/gen_protocol_vectors.py compiles and runs this and
 * writes tests/host/protocol_vectors.json; the PC tool's pytest suite then
 * replays every vector through pc_monitor/protocol.py.
 *
 * That direction matters: the bytes come out of the exact crc16.c and protocol.c
 * that are linked into the firmware, so a change on either side that alters the
 * wire fails a test instead of waiting for a reviewer to diff two files by eye.
 * This file is not a unit test and asserts nothing -- the assertions are the
 * Python side's job.
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "ecg_config.h"
#include "protocol/crc16.h"
#include "protocol/protocol.h"

static uint8_t  s_frame[PKT_MAX_FRAME];
static uint32_t s_lcg = 0x12345678UL;

/* Deterministic on every platform: only ever used to fill non-semantic bytes. */
static uint8_t next_byte(void)
{
    s_lcg = s_lcg * 1103515245UL + 12345UL;
    return (uint8_t)(s_lcg >> 16);
}

static void fill(uint8_t *p, uint16_t n)
{
    for (uint16_t i = 0U; i < n; i++) {
        p[i] = next_byte();
    }
}

static void emit(const char *name, uint16_t len)
{
    uint16_t i;

    printf("%s|", name);
    for (i = 0U; i < len; i++) {
        printf("%02X", s_frame[i]);
    }
    printf("\n");
}

/*
 * An ECG_BATCH assembled the way send_ecg_batch() assembles it, so the tail that
 * reaches the wire is the tail the firmware puts there -- including the flag word
 * whose high byte the old hand-counted ECGP_TAIL of 7 dropped.
 */
static void emit_ecg_batch(uint16_t n, uint16_t flags, const char *name)
{
    uint8_t  payload[PKT_MAX_PAYLOAD];
    uint16_t off;
    uint16_t made;

    memset(payload, 0, sizeof(payload));
    payload[ECGP_COUNT] = (uint8_t)n;
    pkt_put_u16(&payload[ECGP_PERIOD_US], (uint16_t)ADC_SAMPLE_PERIOD_US);
    pkt_put_u32(&payload[ECGP_FIRST_INDEX], 0x0001E240UL);
    fill(&payload[ECGP_SAMPLES], (uint16_t)(2U * n));
    off = ECGP_SIZE(n);
    pkt_put_u16(&payload[off + ECGT_TEMP_RAW], 1731U);
    pkt_put_i16(&payload[off + ECGT_TEMP_CENTI], 3667);
    payload[off + ECGT_HR_BPM] = 72U;
    payload[off + ECGT_HR_STATE] = (uint8_t)HR_NORMAL;
    pkt_put_u16(&payload[off + ECGT_FLAGS], flags);

    made = pkt_build(s_frame, sizeof(s_frame), PKT_ECG_BATCH, 4660U, 1234567UL,
                     payload, (uint16_t)(off + ECGP_TAIL));
    emit(name, made);
}

static void emit_simple(pkt_type_t type, uint16_t payload_len, uint16_t seq,
                        uint32_t ts, const char *name)
{
    uint8_t  payload[PKT_MAX_PAYLOAD];
    uint16_t made;

    memset(payload, 0, sizeof(payload));
    fill(payload, payload_len);
    made = pkt_build(s_frame, sizeof(s_frame), type, seq, ts, payload, payload_len);
    emit(name, made);
}

/*
 * The typed packets are filled with values the Python test can assert on
 * individually. Random bytes would still exercise the framing, which the
 * sized_* vectors already do; what is wanted here is proof that both sides agree
 * on where each field *starts*, and that needs distinguishable values.
 */
static void emit_hello(uint16_t caps)
{
    uint8_t  payload[HELPP_SIZE];
    uint16_t made;

    memset(payload, 0, sizeof(payload));
    payload[HELPP_FW_MAJOR] = FW_VERSION_MAJOR;
    payload[HELPP_FW_MINOR] = FW_VERSION_MINOR;
    payload[HELPP_FW_PATCH] = FW_VERSION_PATCH;
    payload[HELPP_PROTO_VER] = PROTOCOL_VERSION;
    pkt_put_u16(&payload[HELPP_SAMPLE_RATE], ADC_SAMPLE_RATE_HZ);
    payload[HELPP_BATCH_MAX] = ECG_BATCH_MAX_SAMPLES;
    payload[HELPP_ADC_BITS] = ADC_BITS;
    pkt_put_u16(&payload[HELPP_CAPS], caps);
    made = pkt_build(s_frame, sizeof(s_frame), PKT_HELLO, 1U, 250UL,
                     payload, sizeof(payload));
    emit("hello", made);
}

static void emit_status(void)
{
    uint8_t  payload[STP_SIZE];
    uint16_t made;
    uint16_t flags;

    memset(payload, 0, sizeof(payload));
    payload[STP_ADC_RUNNING] = 1U;
    pkt_put_u32(&payload[STP_DMA_BLOCKS], 4660UL);
    pkt_put_u32(&payload[STP_DMA_DROPPED], 0UL);
    pkt_put_u32(&payload[STP_ECG_SAMPLES], 100000UL);
    payload[STP_TEMP_VALID] = 0U;
    pkt_put_i16(&payload[STP_TEMP_CENTI], 3667);
    payload[STP_HR_BPM] = 72U;
    payload[STP_HR_STATE] = (uint8_t)HR_NORMAL;
    payload[STP_OLED_PRESENT] = 0U;
    payload[STP_OLED_ADDR] = 0U;
    payload[STP_RTC_VALID] = 1U;
    pkt_put_u32(&payload[STP_UART_TX], 500000UL);
    pkt_put_u32(&payload[STP_UART_RX], 1200UL);
    pkt_put_u32(&payload[STP_UART_CRC_ERR], 3UL);
    pkt_put_u32(&payload[STP_PROTO_ERR], 1UL);
    /* Every flag that lives in the high byte of status_flags_t, set at once:
     * the same combination ECG_BATCH dropped when ECGP_TAIL was 7. */
    flags = (uint16_t)(SFLAG_ADC_RUNNING | SFLAG_RTC_VALID | SFLAG_RECORDING |
                       SFLAG_OLED | ((uint16_t)ECG_NOTCH_60HZ << SFLAG_NOTCH_SHIFT) |
                       SFLAG_TEMP_UNCALIB |
                       ((uint16_t)LEAD_SIGNAL_POOR << SFLAG_LEAD_SHIFT));
    pkt_put_u16(&payload[STP_FLAGS], flags);
    pkt_put_u32(&payload[STP_UPTIME_S], 3600UL);
    made = pkt_build(s_frame, sizeof(s_frame), PKT_STATUS, 60210U, 8899UL,
                     payload, sizeof(payload));
    emit("status", made);
}

static void emit_temp_status(void)
{
    uint8_t  payload[TEMPP_SIZE];
    uint16_t made;

    memset(payload, 0, sizeof(payload));
    pkt_put_u16(&payload[TEMPP_RAW], 1234U);
    pkt_put_u16(&payload[TEMPP_MV], 1000U);
    pkt_put_i16(&payload[TEMPP_CENTI], 3657);
    payload[TEMPP_STATE] = (uint8_t)TEMP_UNCALIBRATED;
    made = pkt_build(s_frame, sizeof(s_frame), PKT_TEMP_STATUS, 7U, 6000UL,
                     payload, sizeof(payload));
    emit("temp_status", made);
}

int main(void)
{
    uint8_t  payload[PKT_MAX_PAYLOAD];
    uint16_t made;
    uint16_t n;

    puts("# generated by the firmware's own crc16.c and protocol.c");
    printf("protocol_version|%02X\n", (unsigned)PROTOCOL_VERSION);

    /* Every small payload length, so the Python parser is checked against the C
     * encoder's framing arithmetic rather than against a hand-written table. */
    for (n = 0U; n <= 8U; n++) {
        char name[24];

        memset(payload, 0, sizeof(payload));
        fill(payload, n);
        made = pkt_build(s_frame, sizeof(s_frame), PKT_PONG, (uint16_t)(100U + n),
                         0xF0F0F0F0UL, payload, n);
        sprintf(name, "sized_%u", (unsigned)n);
        emit(name, made);
    }

    emit_ecg_batch(ECG_BATCH_MAX_SAMPLES, 0xFFFFU, "ecg_batch_all_flags");
    /* lead_state_t occupies bits 0..2, so SIGNAL_POOR is what belongs here --
     * there is no lead-off hardware and hr_state travels in its own byte. */
    emit_ecg_batch(ECG_BATCH_MAX_SAMPLES,
                   (uint16_t)(SFLAG_ADC_RUNNING | SFLAG_RTC_VALID | SFLAG_HR_VALID |
                              ((uint16_t)LEAD_SIGNAL_POOR << SFLAG_LEAD_SHIFT)),
                   "ecg_batch_typical");
    emit_ecg_batch(1U, 0x0100U, "ecg_batch_single_sample");
    emit_ecg_batch(ECG_BATCH_MAX_SAMPLES, 0x00FFU, "ecg_batch_low_bytes");

    emit_hello(0U);
    emit_status();
    emit_temp_status();
    emit_simple(PKT_NACK, NACKP_SIZE, 5U, 5UL, "nack");

    memset(payload, 0, sizeof(payload));
    pkt_put_u16(&payload[RTCP_YEAR], 2031U);
    payload[RTCP_MONTH] = 11U;
    payload[RTCP_DAY] = 23U;
    payload[RTCP_HOUR] = 6U;
    payload[RTCP_MINUTE] = 45U;
    payload[RTCP_SECOND] = 12U;
    pkt_put_u32(&payload[RTCP_CAL_SIZE], 1953182712UL);
    made = pkt_build(s_frame, sizeof(s_frame), PKT_RTC_RESPONSE, 3U, 3UL,
                     payload, RTCP_CAL_SIZE + RTC_RESPONSE_EXTRA);
    emit("rtc_response", made);

    memset(payload, 0, sizeof(payload));
    payload[CFGP_NOTCH] = (uint8_t)ECG_NOTCH_50HZ;
    payload[CFGP_HR_LOW] = 45U;
    payload[CFGP_HR_HIGH] = 130U;
    pkt_put_i16(&payload[CFGP_TEMP_LOW], 3500);
    pkt_put_i16(&payload[CFGP_TEMP_HIGH], 3850);
    made = pkt_build(s_frame, sizeof(s_frame), PKT_SET_CONFIG, 11U, 11UL,
                     payload, CFGP_SIZE);
    emit("set_config", made);

    /* CRC-16/CCITT-FALSE's published check value, pinned through the C code. */
    printf("crc16_check|%04X\n", crc16_ccitt_buf((const uint8_t *)"123456789", 9U));
    return 0;
}
