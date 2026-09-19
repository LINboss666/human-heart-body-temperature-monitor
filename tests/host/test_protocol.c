/*
 * Host tests for the CRC-16 and the USART1 framing. These are the two pieces
 * the PC tool also implements, so a disagreement here is a wire-format bug that
 * would otherwise surface as unexplained data loss on a bench.
 */
#include <stdint.h>
#include <string.h>

#include "ctest.h"

#include "protocol/crc16.h"
#include "protocol/protocol.h"

/* app_config.h is pulled in by protocol.h and wants HAL-free constants only. */

static void test_crc16(void)
{
    const uint8_t check_vector[] = "123456789";

    CTEST_CASE("crc16 check vector");
    CHECK_EQ(crc16_ccitt_buf(check_vector, 9U), CRC16_CHECK);
    CHECK_EQ(CRC16_CHECK, 0x29B1U);

    CTEST_CASE("crc16 empty and incremental equality");
    CHECK_EQ(crc16_ccitt(CRC16_INIT, NULL, 0U), CRC16_INIT);
    {
        const uint8_t all[] = "12345";
        uint16_t split = crc16_ccitt(crc16_ccitt(CRC16_INIT, all, 3U), all + 3U, 2U);
        CHECK_EQ(split, crc16_ccitt_buf(all, 5U));
    }

    CTEST_CASE("crc16 distinguishes a single flipped bit");
    {
        uint8_t a[4] = { 0x00, 0x11, 0x22, 0x33 };
        uint8_t b[4] = { 0x00, 0x12, 0x22, 0x33 };
        CHECK(crc16_ccitt_buf(a, 4U) != crc16_ccitt_buf(b, 4U));
    }
}

static void test_build_parse_roundtrip(void)
{
    uint8_t frame[PKT_MAX_FRAME];
    uint8_t payload[PKT_MAX_PAYLOAD];
    pkt_frame_t got;
    uint16_t consumed = 0U;
    uint16_t n;
    uint16_t i;

    CTEST_CASE("build then parse round-trips every legal payload size");
    for (i = 0U; i <= PKT_MAX_PAYLOAD; i++) {
        payload[i] = (uint8_t)(i * 7U + 1U);
    }
    for (n = 0U; n <= PKT_MAX_PAYLOAD; n++) {
        uint16_t made = pkt_build(frame, sizeof(frame), PKT_ECG_BATCH,
                                  (uint16_t)(n + 1000U), 0xDEADBEEFUL, payload, n);
        CHECK_EQ(made, (uint16_t)(PKT_OVERHEAD + n));
        if (made == 0U) {
            continue;
        }
        CHECK_EQ(pkt_parse(frame, made, &got, &consumed), PKT_OK);
        if (got.length != n) {
            CHECK_EQ(got.length, n);
            break;
        }
        CHECK_EQ(got.type, PKT_ECG_BATCH);
        CHECK_EQ(got.sequence, (uint16_t)(n + 1000U));
        CHECK_EQ(got.device_ts_ms, 0xDEADBEEFUL);
        CHECK_EQ(consumed, made);
        CHECK(memcmp(got.payload, payload, n) == 0);
    }
}

static void test_build_rejects(void)
{
    uint8_t frame[16];
    uint8_t payload[4] = { 1, 2, 3, 4 };

    CTEST_CASE("build refuses an undersized destination instead of overrunning");
    CHECK_EQ(pkt_build(frame, 10U, PKT_PING, 1U, 0U, NULL, 0U), 0U);
    CHECK_EQ(pkt_build(frame, sizeof(frame), PKT_PING, 1U, 0U, payload, 4U), 0U);
    CHECK_EQ(pkt_build(NULL, sizeof(frame), PKT_PING, 1U, 0U, NULL, 0U), 0U);
    {
        uint8_t big[8];
        CHECK_EQ(pkt_build(big, sizeof(big), PKT_PING, 1U, 0U, payload,
                           PKT_MAX_PAYLOAD), 0U);
    }
    /* 14-byte overhead with no payload must fit exactly. */
    CHECK_EQ(pkt_build(frame, PKT_OVERHEAD, PKT_PING, 9U, 1U, NULL, 0U), PKT_OVERHEAD);
}

static void test_truncated_stream(void)
{
    uint8_t frame[PKT_MAX_FRAME];
    uint8_t payload[20];
    pkt_frame_t got;
    uint16_t consumed;
    uint16_t made;
    uint16_t i;
    uint16_t n = 20U;

    for (i = 0U; i < n; i++) {
        payload[i] = (uint8_t)i;
    }
    made = pkt_build(frame, sizeof(frame), PKT_ECG_BATCH, 7U, 999U, payload, n);

    CTEST_CASE("a frame arriving byte by byte asks for more each time");
    for (i = 0U; i < made; i++) {
        pkt_result_t r = pkt_parse(frame, i, &got, &consumed);
        CHECK_EQ(r, PKT_NEED_MORE);
        /* Never claim to have eaten bytes that cannot be safely discarded. */
        CHECK_EQ(consumed, 0U);
    }
    CHECK_EQ(pkt_parse(frame, made, &got, &consumed), PKT_OK);
}

static void test_leading_garbage(void)
{
    uint8_t stream[PKT_MAX_FRAME + 16];
    uint8_t payload[6] = { 1, 2, 3, 4, 5, 6 };
    pkt_frame_t got;
    uint16_t consumed;
    uint16_t made;
    uint16_t i;

    CTEST_CASE("random junk before the magic is skipped");
    for (i = 0U; i < 9U; i++) {
        stream[i] = (uint8_t)(0x30U + i);
    }
    made = pkt_build(stream + 9U, sizeof(stream) - 9U, PKT_PING, 3U, 42U, payload, 6U);
    CHECK_EQ(pkt_parse(stream, (uint16_t)(9U + made), &got, &consumed), PKT_OK);
    CHECK_EQ(consumed, (uint16_t)(9U + made));
    CHECK_EQ(got.type, PKT_PING);
    CHECK_EQ(got.length, 6U);

    CTEST_CASE("a lone trailing 0xA5 is retained, not discarded");
    {
        uint8_t half[5] = { 0x00, PKT_MAGIC0, 0x11, 0x22, PKT_MAGIC0 };
        pkt_result_t r = pkt_parse(half, sizeof(half), &got, &consumed);
        CHECK_EQ(r, PKT_NEED_MORE);
        CHECK_EQ(consumed, 4U); /* last byte could start a real magic */
    }

    CTEST_CASE("a false magic inside junk does not wedge the parser");
    {
        uint8_t tricky[PKT_MAX_FRAME + 8];
        uint16_t k;
        for (k = 0U; k < 4U; k++) {
            tricky[k] = PKT_MAGIC0;
            tricky[k + 4U] = 0x00U; /* magic1 mismatch */
        }
        made = pkt_build(tricky + 8U, sizeof(tricky) - 8U, PKT_PONG, 1U, 0U, NULL, 0U);
        CHECK_EQ(pkt_parse(tricky, (uint16_t)(8U + made), &got, &consumed), PKT_OK);
        CHECK_EQ(got.type, PKT_PONG);
    }
}

static void test_back_to_back(void)
{
    uint8_t stream[PKT_MAX_FRAME * 3];
    uint8_t payload[10];
    pkt_frame_t got;
    uint16_t consumed;
    uint16_t off = 0U;
    uint16_t next = 0U;
    uint16_t made;
    uint16_t i;

    for (i = 0U; i < 10U; i++) {
        payload[i] = (uint8_t)(i + 1U);
    }

    CTEST_CASE("three frames in one read are returned one at a time");
    made = pkt_build(stream, sizeof(stream), PKT_PING, 1U, 10U, payload, 10U);
    off = made;
    made = pkt_build(stream + off, sizeof(stream) - off, PKT_PONG, 2U, 20U, payload, 10U);
    off = (uint16_t)(off + made);
    made = pkt_build(stream + off, sizeof(stream) - off, PKT_ACK, 3U, 30U, NULL, 0U);
    off = (uint16_t)(off + made);

    /* `next` is the absolute cursor into stream; `consumed` is relative to the
     * slice handed to pkt_parse(), so the two must not be conflated. */
    next = 0U;
    CHECK_EQ(pkt_parse(stream + next, (uint16_t)(off - next), &got, &consumed), PKT_OK);
    CHECK_EQ(got.type, PKT_PING);
    CHECK_EQ(got.sequence, 1U);
    next = (uint16_t)(next + consumed);

    CHECK_EQ(pkt_parse(stream + next, (uint16_t)(off - next), &got, &consumed), PKT_OK);
    CHECK_EQ(got.type, PKT_PONG);
    CHECK_EQ(got.sequence, 2U);
    next = (uint16_t)(next + consumed);

    CHECK_EQ(pkt_parse(stream + next, (uint16_t)(off - next), &got, &consumed), PKT_OK);
    CHECK_EQ(got.type, PKT_ACK);
    CHECK_EQ(got.sequence, 3U);
    CHECK_EQ(got.length, 0U);
    /* The third frame carried no payload, so exactly the overhead is consumed. */
    CHECK_EQ(consumed, (uint16_t)PKT_OVERHEAD);
    next = (uint16_t)(next + consumed);
    CHECK_EQ(next, off);
    /* Nothing left: an empty read must ask for more, not read out of bounds. */
    CHECK_EQ(pkt_parse(stream + next, 0U, &got, &consumed), PKT_NEED_MORE);
}

static void test_corruption(void)
{
    uint8_t frame[PKT_MAX_FRAME];
    uint8_t payload[8] = { 9, 8, 7, 6, 5, 4, 3, 2 };
    pkt_frame_t got;
    uint16_t consumed;
    uint16_t made;
    uint16_t i;

    CTEST_CASE("a single corrupted payload byte is caught by the CRC");
    for (i = 0U; i < 8U; i++) {
        made = pkt_build(frame, sizeof(frame), PKT_ECG_BATCH, 1U, 0U, payload, 8U);
        frame[PKT_HEADER_SIZE + i] ^= 0x01U;
        CHECK_EQ(pkt_parse(frame, made, &got, &consumed), PKT_ERR_CRC);
        /* The rejected magic must be consumed so the caller can make progress. */
        CHECK_EQ(consumed, 2U);
    }

    CTEST_CASE("a corrupted CRC itself is caught");
    made = pkt_build(frame, sizeof(frame), PKT_ECG_BATCH, 1U, 0U, payload, 8U);
    frame[made - 1U] ^= 0xFFU;
    CHECK_EQ(pkt_parse(frame, made, &got, &consumed), PKT_ERR_CRC);

    CTEST_CASE("an unknown protocol version is not accepted as a frame");
    made = pkt_build(frame, sizeof(frame), PKT_ECG_BATCH, 1U, 0U, payload, 8U);
    frame[2] = (uint8_t)(PROTOCOL_VERSION + 0x40U);
    CHECK(pkt_parse(frame, made, &got, &consumed) != PKT_OK);

    CTEST_CASE("an oversized declared length cannot read past the buffer");
    made = pkt_build(frame, sizeof(frame), PKT_ECG_BATCH, 1U, 0U, payload, 8U);
    pkt_put_u16(&frame[6], (uint16_t)(PKT_MAX_PAYLOAD + 100U));
    CHECK_EQ(pkt_parse(frame, made, &got, &consumed), PKT_NEED_MORE);
}

/*
 * The ECG_BATCH trailer.
 *
 * send_ecg_batch() is static in protocol_service.c and that file needs the HAL,
 * so what is proven here is the trailer itself: that the accessor pair every
 * builder and parser shares writes each field at the documented offset, keeps
 * the whole u16 flag word, and stays inside ECGP_TAIL bytes. Two defects lived
 * in this shape before: a hand-counted ECGP_TAIL of 7 that silently truncated
 * the flag word while the CRC still passed, and a nullable temperature argument
 * that left temp_raw/temp_centi at zero forever.
 */
static void test_ecg_batch_tail(void)
{
    uint8_t     frame[PKT_MAX_FRAME];
    uint8_t     payload[PKT_MAX_PAYLOAD];
    pkt_frame_t got;
    pkt_batch_tail_t in;
    pkt_batch_tail_t out;
    const uint8_t *t;
    uint16_t consumed = 0U;
    uint16_t made, off;
    uint16_t i;
    const uint16_t n = ECG_BATCH_MAX_SAMPLES;

    CTEST_CASE("ECGP_TAIL covers the widest tail field, by construction");
    CHECK_EQ(ECGP_TAIL, ECGT_FLAGS + 2U);
    CHECK_EQ(ECGP_SIZE(n) + ECGP_TAIL, (uint16_t)(ECGP_SAMPLES + 2U * n + ECGT_FLAGS + 2U));
    CHECK(ECGP_SIZE(n) + ECGP_TAIL <= PKT_MAX_PAYLOAD);

    memset(payload, 0, sizeof(payload));
    payload[ECGP_COUNT] = (uint8_t)n;
    pkt_put_u16(&payload[ECGP_PERIOD_US], (uint16_t)ADC_SAMPLE_PERIOD_US);
    pkt_put_u32(&payload[ECGP_FIRST_INDEX], 0x12345678UL);
    for (i = 0U; i < n; i++) {
        pkt_put_u16(&payload[ECGP_SAMPLES + i * 2U], (uint16_t)(0x800U + i));
    }
    off = ECGP_SIZE(n);

    /* Every field a different value, so a swapped pair cannot cancel out. */
    in.temp_raw = 1731U;
    in.temp_centi = 3667;
    in.hr_bpm = 72U;
    in.hr_state = (uint8_t)HR_NORMAL;
    in.flags = (uint16_t)(SFLAG_OLED | SFLAG_ADC_RUNNING | SFLAG_RTC_VALID |
                          SFLAG_DMA_DROPPED | SFLAG_TEMP_UNCALIB | SFLAG_HR_VALID |
                          SFLAG_RECORDING | SFLAG_LEAD_MASK | SFLAG_NOTCH_MASK);
    pkt_write_batch_tail(&payload[off], &in);

    made = pkt_build(frame, sizeof(frame), PKT_ECG_BATCH, 1U, 42U,
                     payload, (uint16_t)(off + ECGP_TAIL));

    CTEST_CASE("a full-rate ECG_BATCH frame is the documented 69 bytes");
    CHECK_EQ(made, 69U);
    CHECK_EQ(made, (uint16_t)(PKT_OVERHEAD + ECGP_SIZE(n) + ECGP_TAIL));

    CTEST_CASE("the tail reads back unchanged through the parser");
    CHECK_EQ(pkt_parse(frame, made, &got, &consumed), PKT_OK);
    CHECK_EQ(consumed, made);
    t = &got.payload[off];
    CHECK_EQ(pkt_get_u16(&t[ECGT_TEMP_RAW]), 1731U);
    CHECK_EQ(pkt_get_i16(&t[ECGT_TEMP_CENTI]), 3667);
    CHECK_EQ(t[ECGT_HR_BPM], 72U);
    CHECK_EQ(t[ECGT_HR_STATE], (uint8_t)HR_NORMAL);
    CHECK_EQ(pkt_get_u16(&t[ECGT_FLAGS]), in.flags);

    CTEST_CASE("status_flags_t bit 8 and above reach the far end of the frame");
    {
        uint16_t back = pkt_get_u16(&t[ECGT_FLAGS]);
        CHECK((back & SFLAG_OLED) != 0U);
        CHECK((back & SFLAG_ADC_RUNNING) != 0U);
        CHECK((back & SFLAG_RTC_VALID) != 0U);
        CHECK((back & SFLAG_DMA_DROPPED) != 0U);
        CHECK((back & SFLAG_TEMP_UNCALIB) != 0U);
        CHECK_EQ((back & SFLAG_NOTCH_MASK) >> SFLAG_NOTCH_SHIFT, 3U);
    }

    CTEST_CASE("the accessor pair round-trips every field, including negatives");
    {
        pkt_batch_tail_t probe;
        static const int16_t centi[] = { 0, 3667, -4321, 32767, -32768 };

        for (i = 0U; i < sizeof(centi) / sizeof(centi[0]); i++) {
            memset(&probe, 0, sizeof(probe));
            probe.temp_raw = (uint16_t)(0xF0F0U + i);
            probe.temp_centi = centi[i];
            probe.hr_bpm = (uint8_t)(70U + i);
            probe.hr_state = (uint8_t)(HR_INVALID + i);
            probe.flags = (uint16_t)((0x8000U >> i) | 0x00FFU);
            memset(payload, 0xA5, ECGP_TAIL);
            pkt_write_batch_tail(payload, &probe);
            pkt_read_batch_tail(payload, &out);
            CHECK_EQ(out.temp_raw, probe.temp_raw);
            CHECK_EQ(out.temp_centi, probe.temp_centi);
            CHECK_EQ(out.hr_bpm, probe.hr_bpm);
            CHECK_EQ(out.hr_state, probe.hr_state);
            CHECK_EQ(out.flags, probe.flags);
        }
    }

    CTEST_CASE("writing a tail never touches a byte outside ECGP_TAIL");
    {
        uint8_t buf[ECGP_TAIL + 4U];

        memset(buf, 0x5AU, sizeof(buf));
        memset(&in, 0, sizeof(in));
        in.temp_raw = 0xFFFFU;
        in.temp_centi = -1;
        in.hr_bpm = 0xFFU;
        in.hr_state = 0xFFU;
        in.flags = 0xFFFFU;
        pkt_write_batch_tail(buf, &in);
        for (i = ECGP_TAIL; i < sizeof(buf); i++) {
            CHECK_EQ(buf[i], 0x5AU);
        }
        /* And it really did fill the whole declared width. */
        CHECK_EQ(buf[ECGP_TAIL - 1U], 0xFFU);
    }

    CTEST_CASE("a negative centi value survives as two's complement bytes");
    {
        uint8_t buf[ECGP_TAIL];
        pkt_batch_tail_t neg;

        memset(&neg, 0, sizeof(neg));
        neg.temp_centi = -4321;
        pkt_write_batch_tail(buf, &neg);
        CHECK_EQ(pkt_get_i16(&buf[ECGT_TEMP_CENTI]), -4321);
        pkt_read_batch_tail(buf, &out);
        CHECK_EQ(out.temp_centi, -4321);
    }
}

/* CTEST_MAIN supplies main() and opens ctest_run_all(). */
CTEST_MAIN("protocol+crc16")
{
    test_crc16();
    test_build_parse_roundtrip();
    test_build_rejects();
    test_truncated_stream();
    test_leading_garbage();
    test_back_to_back();
    test_corruption();
    test_ecg_batch_tail();
}
