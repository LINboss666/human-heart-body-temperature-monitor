#include "protocol_service.h"

#include <string.h>

#include "stm32f1xx_hal.h"

#include "app_config.h"
#include "diagnostics/diagnostics.h"
#include "protocol/protocol.h"
#include "rtc_service/rtc_service.h"
#include "uart/uart_link.h"

/* Staging buffer for the parser. Two maximum-size frames so a complete frame is
 * always present after a single fill pass, and the parser's own resync can drop
 * bytes without the buffer needing to be re-scanned from scratch. */
#define RX_STAGE_SIZE (PKT_MAX_FRAME * 2U)

static uint8_t  s_rx[RX_STAGE_SIZE];
static uint16_t s_rx_len;

static uint8_t  s_frame[PKT_MAX_FRAME];
static uint8_t  s_payload[PKT_MAX_PAYLOAD];

/* Batch of raw codes awaiting an ECG_BATCH frame. */
static uint16_t s_batch[ECG_BATCH_MAX_SAMPLES];
static uint16_t s_batch_count;
static uint32_t s_batch_first_index;

/* A full-rate batch must fit the staging buffer it is built in, and a 20-sample
 * batch must fit ECG_BATCH_MAX_SAMPLES. Both are configuration-time facts that
 * would otherwise show up as memory corruption rather than a build failure. */
typedef char ecg_batch_fits_payload[
    (ECGP_SIZE(ECG_BATCH_MAX_SAMPLES) + ECGP_TAIL <= PKT_MAX_PAYLOAD) ? 1 : -1];

/* Latest derived values, copied in from the most recent pushed sample. */
static uint8_t  s_hr_bpm_latest;
static uint8_t  s_hr_state_latest;
static bool     s_hr_valid_latest;

static uint16_t s_tx_seq;
static uint16_t s_rx_seq_expect;
static bool     s_streaming;
static bool     s_host_present;
static uint32_t s_last_status_ms;
static uint32_t s_last_temp_ms;
static uint32_t s_last_hello_ms;
static uint32_t s_gap_count;

/* Host-configurable settings, mirrored here so the STATUS payload and the
 * settings page show what the PC last asked for. */
static uint8_t  s_notch = ECG_NOTCH_DEFAULT;
static uint8_t  s_hr_low = HR_BPM_LOW_DEFAULT;
static uint8_t  s_hr_high = HR_BPM_HIGH_DEFAULT;
static int16_t  s_temp_low = TEMP_CENTI_LOW_DEFAULT;
static int16_t  s_temp_high = TEMP_CENTI_HIGH_DEFAULT;

static uint32_t monotonic_ms(uint32_t now_ms)
{
    return now_ms;   /* HAL_GetTick wraps at ~49.7 days; see HARDWARE_TEST_PLAN */
}

static bool send_frame(uint8_t type, const uint8_t *payload, uint16_t len, uint32_t now_ms)
{
    uint16_t n;

    n = pkt_build(s_frame, sizeof(s_frame), type, s_tx_seq, monotonic_ms(now_ms),
                  payload, len);
    if (n == 0U) {
        diagnostics()->protocol_errors++;
        return false;
    }
    s_tx_seq++;
    if (!uart_link_write(s_frame, n)) {
        return false;
    }
    diagnostics()->uart_tx_packets++;
    return true;
}

static void send_ack(uint8_t acked_type, uint16_t acked_seq, uint32_t now_ms)
{
    uint8_t p[ACKP_SIZE];

    p[ACKP_TYPE] = acked_type;
    pkt_put_u16(&p[ACKP_SEQ], acked_seq);
    (void)send_frame(PKT_ACK, p, sizeof(p), now_ms);
}

static void send_nack(uint8_t acked_type, uint16_t acked_seq, uint8_t reason,
                      uint32_t now_ms)
{
    uint8_t p[NACKP_SIZE];

    p[ACKP_TYPE] = acked_type;
    pkt_put_u16(&p[ACKP_SEQ], acked_seq);
    p[NACKP_REASON] = reason;
    (void)send_frame(PKT_NACK, p, sizeof(p), now_ms);
    diagnostics()->protocol_errors++;
}

static void send_hello(uint32_t now_ms)
{
    uint8_t  p[HELPP_SIZE];
    uint16_t caps = 0U;
    const diagnostics_t *d = diagnostics();

    /* Only advertise what is actually true at this moment. Every capability that
     * depends on unbuilt hardware stays clear, so the PC cannot render a feature
     * the board does not have. */
    if (!d->temp_uncalibrated)   { caps |= CAP_TEMP_CALIBRATED; }
    if (d->oled_present)         { caps |= CAP_OLED_PRESENT; }
    if (ECG_FRONTEND_PARAMS_VERIFIED) { caps |= CAP_FRONTEND_VERIFIED; }
    /* CAP_LEAD_HW_DETECT, CAP_PROBE_HW_DETECT and CAP_RTC_BATTERY_BACKED are
     * never set from firmware: lead-off hardware does not exist here, probe
     * faults are range heuristics, and VBAT retention is unproven. */

    memset(p, 0, sizeof(p));
    p[HELPP_FW_MAJOR] = FW_VERSION_MAJOR;
    p[HELPP_FW_MINOR] = FW_VERSION_MINOR;
    p[HELPP_FW_PATCH] = FW_VERSION_PATCH;
    p[HELPP_PROTO_VER] = PROTOCOL_VERSION;
    pkt_put_u16(&p[HELPP_SAMPLE_RATE], ADC_SAMPLE_RATE_HZ);
    p[HELPP_BATCH_MAX] = ECG_BATCH_MAX_SAMPLES;
    p[HELPP_ADC_BITS] = ADC_BITS;
    pkt_put_u16(&p[HELPP_CAPS], caps);
    (void)send_frame(PKT_HELLO, p, sizeof(p), now_ms);
}

static void send_ecg_batch(uint32_t now_ms)
{
    uint8_t  *p = s_payload;
    uint16_t  n = s_batch_count;
    uint16_t  off;
    const diagnostics_t *d = diagnostics();

    if (n == 0U) {
        return;
    }

    p[ECGP_COUNT] = (uint8_t)n;
    pkt_put_u16(&p[ECGP_PERIOD_US], ADC_SAMPLE_PERIOD_US);
    pkt_put_u32(&p[ECGP_FIRST_INDEX], s_batch_first_index);
    for (uint16_t i = 0U; i < n; i++) {
        pkt_put_u16(&p[ECGP_SAMPLES + i * 2U], s_batch[i]);
    }

    off = ECGP_SIZE(n);
    {
        /* Temperature comes from the diagnostics snapshot rather than a per-sample
         * argument. It refreshes once per acquisition block, which is far coarser
         * than the 20 ms a batch spans and irrelevant for a signal decimated to
         * one update per 250 ms - and unlike the argument it replaced, it cannot
         * be left at zero by a caller that passed NULL. */
        pkt_batch_tail_t tail;

        tail.temp_raw   = d->temp_raw;
        tail.temp_centi = d->temp_centi;
        tail.hr_bpm     = s_hr_bpm_latest;
        tail.hr_state   = s_hr_state_latest;
        tail.flags = 0U;
        tail.flags = SFLAG_SET(tail.flags, SFLAG_LEAD_SHIFT, SFLAG_LEAD_MASK,
                               (uint16_t)d->lead_state);
        tail.flags = SFLAG_SET(tail.flags, SFLAG_TEMP_SHIFT, SFLAG_TEMP_MASK,
                               (uint16_t)d->temp_state);
        tail.flags = SFLAG_SET(tail.flags, SFLAG_NOTCH_SHIFT, SFLAG_NOTCH_MASK,
                               (uint16_t)s_notch);
        if (s_hr_valid_latest)      { tail.flags |= SFLAG_HR_VALID; }
        if (d->recording)           { tail.flags |= SFLAG_RECORDING; }
        if (d->oled_present)        { tail.flags |= SFLAG_OLED; }
        if (d->adc_running)         { tail.flags |= SFLAG_ADC_RUNNING; }
        if (d->rtc_valid)           { tail.flags |= SFLAG_RTC_VALID; }
        if (d->dma_dropped != 0U)   { tail.flags |= SFLAG_DMA_DROPPED; }
        if (d->temp_uncalibrated)   { tail.flags |= SFLAG_TEMP_UNCALIB; }
        pkt_write_batch_tail(&p[off], &tail);
    }

    (void)send_frame(PKT_ECG_BATCH, p, (uint16_t)(off + ECGP_TAIL), now_ms);
    s_batch_count = 0U;
}

static void send_temp_status(uint32_t now_ms)
{
    uint8_t p[TEMPP_SIZE];
    temperature_t t;

    temperature_get(&t);
    memset(p, 0, sizeof(p));
    pkt_put_u16(&p[TEMPP_RAW], t.raw);
    pkt_put_u16(&p[TEMPP_MV], t.mv);
    pkt_put_i16(&p[TEMPP_CENTI], t.centi_c);
    p[TEMPP_STATE] = (uint8_t)t.state;
    (void)send_frame(PKT_TEMP_STATUS, p, sizeof(p), now_ms);
}

static void send_status(uint32_t now_ms)
{
    uint16_t len;

    len = diagnostics_fill_status_payload(s_payload, sizeof(s_payload));
    if (len != 0U) {
        (void)send_frame(PKT_STATUS, s_payload, len, now_ms);
    }
}

static void send_rtc_response(uint32_t now_ms)
{
    uint8_t        p[RTCP_CAL_SIZE + RTC_RESPONSE_EXTRA];
    rtc_datetime_t dt;
    uint32_t       epoch;

    rtc_service_get_datetime(&dt);
    epoch = rtc_service_get_timestamp();

    pkt_put_u16(&p[RTCP_YEAR], dt.year);
    p[RTCP_MONTH] = dt.month;
    p[RTCP_DAY] = dt.day;
    p[RTCP_HOUR] = dt.hour;
    p[RTCP_MINUTE] = dt.minute;
    p[RTCP_SECOND] = dt.second;
    pkt_put_u32(&p[RTCP_CAL_SIZE], epoch);
    (void)send_frame(PKT_RTC_RESPONSE, p, sizeof(p), now_ms);
}

static void handle_set_rtc(const pkt_frame_t *f, uint32_t now_ms)
{
    rtc_datetime_t dt;

    if (f->length < RTCP_CAL_SIZE) {
        send_nack(PKT_SET_RTC, f->sequence, NACK_MALFORMED_LENGTH, now_ms);
        return;
    }
    memset(&dt, 0, sizeof(dt));
    dt.year = pkt_get_u16(&f->payload[RTCP_YEAR]);
    dt.month = f->payload[RTCP_MONTH];
    dt.day = f->payload[RTCP_DAY];
    dt.hour = f->payload[RTCP_HOUR];
    dt.minute = f->payload[RTCP_MINUTE];
    dt.second = f->payload[RTCP_SECOND];

    /* rtc_service_set_datetime validates month length and leap years, so
     * 2026-02-30 or 13:75 never reach the hardware. */
    if (!rtc_service_set_datetime(&dt)) {
        send_nack(PKT_SET_RTC, f->sequence, NACK_BAD_VALUE, now_ms);
        return;
    }
    send_ack(PKT_SET_RTC, f->sequence, now_ms);
    send_rtc_response(now_ms);
}

static void handle_set_config(const pkt_frame_t *f, uint32_t now_ms)
{
    if (f->length < CFGP_SIZE) {
        send_nack(PKT_SET_CONFIG, f->sequence, NACK_MALFORMED_LENGTH, now_ms);
        return;
    }
    if (f->payload[CFGP_NOTCH] > (uint8_t)ECG_NOTCH_OFF) {
        send_nack(PKT_SET_CONFIG, f->sequence, NACK_BAD_VALUE, now_ms);
        return;
    }
    if (f->payload[CFGP_HR_LOW] >= f->payload[CFGP_HR_HIGH]) {
        send_nack(PKT_SET_CONFIG, f->sequence, NACK_BAD_VALUE, now_ms);
        return;
    }

    s_notch = f->payload[CFGP_NOTCH];
    s_hr_low = f->payload[CFGP_HR_LOW];
    s_hr_high = f->payload[CFGP_HR_HIGH];
    s_temp_low = pkt_get_i16(&f->payload[CFGP_TEMP_LOW]);
    s_temp_high = pkt_get_i16(&f->payload[CFGP_TEMP_HIGH]);

    ecg_signal_set_notch((ecg_notch_t)s_notch);
    ecg_hr_set_bands(s_hr_low, s_hr_high);
    temperature_set_alarm_bands(s_temp_low, s_temp_high);
    diagnostics()->notch = s_notch;

    send_ack(PKT_SET_CONFIG, f->sequence, now_ms);
}

static void handle_frame(const pkt_frame_t *f, uint32_t now_ms)
{
    diagnostics()->uart_rx_packets++;

    /* Sequence is checked for observation, not for rejection: the link is a
     * point-to-point wire that cannot reorder, and dropping a valid command
     * because a counter wrapped somewhere would be worse than accepting it. */
    if (f->sequence != s_rx_seq_expect) {
        s_rx_seq_expect = (uint16_t)(f->sequence + 1U);
    } else {
        s_rx_seq_expect++;
    }

    switch (f->type) {
    case PKT_START_STREAM:
        protocol_service_set_streaming(true);
        send_ack(PKT_START_STREAM, f->sequence, now_ms);
        break;
    case PKT_STOP_STREAM:
        protocol_service_set_streaming(false);
        send_ack(PKT_STOP_STREAM, f->sequence, now_ms);
        break;
    case PKT_SET_RTC:
        handle_set_rtc(f, now_ms);
        break;
    case PKT_GET_RTC:
        send_ack(PKT_GET_RTC, f->sequence, now_ms);
        send_rtc_response(now_ms);
        break;
    case PKT_SET_CONFIG:
        handle_set_config(f, now_ms);
        break;
    case PKT_PING: {
        uint8_t echo[4] = { 0U, 0U, 0U, 0U };
        for (uint16_t i = 0U; i < 4U && i < f->length; i++) {
            echo[i] = f->payload[i];
        }
        (void)send_frame(PKT_PONG, echo, sizeof(echo), now_ms);
        break;
    }
    default:
        send_nack(f->type, f->sequence, NACK_UNSUPPORTED_TYPE, now_ms);
        break;
    }
}

void protocol_service_init(void)
{
    memset(s_rx, 0, sizeof(s_rx));
    s_rx_len = 0U;
    s_batch_count = 0U;
    s_tx_seq = 0U;
    s_rx_seq_expect = 0U;
    s_streaming = false;
    s_host_present = false;
    s_gap_count = 0U;
    s_last_status_ms = 0U;
    s_last_temp_ms = 0U;
    s_last_hello_ms = 0U;
    uart_link_init();
}

void protocol_service_push_sample(const ecg_sample_t *ecg, const ecg_hr_t *hr)
{
    if (ecg == NULL) {
        return;
    }

    if (s_batch_count == 0U) {
        s_batch_first_index = ecg->sample_index;
    } else if (s_streaming) {
        /* Continuity is checked against the previous sample so a dropped DMA
         * block shows up as a gap on the PC even though the wire itself looked
         * fine. */
        uint32_t expect = s_batch_first_index + s_batch_count;
        if (ecg->sample_index != expect) {
            s_gap_count++;
            diagnostics()->dma_dropped = s_gap_count;
        }
    }

    if (s_batch_count < ECG_BATCH_MAX_SAMPLES) {
        s_batch[s_batch_count++] = ecg->raw;
    }

    s_hr_bpm_latest = hr != NULL ? hr->bpm : 0U;
    s_hr_state_latest = hr != NULL ? (uint8_t)hr->state : (uint8_t)HR_INVALID;
    s_hr_valid_latest = (hr != NULL) && hr->valid;

    if (s_streaming && s_batch_count >= ECG_BATCH_MAX_SAMPLES) {
        send_ecg_batch((uint32_t)HAL_GetTick());
    } else if (!s_streaming) {
        s_batch_count = 0U;     /* not reporting: do not accumulate forever */
    }
}

void protocol_service_poll(uint32_t now_ms)
{
    uint16_t consumed = 0U;

    /* 1. Pull bytes into the staging buffer. */
    while (s_rx_len < RX_STAGE_SIZE && uart_link_rx_count() > 0U) {
        if (!uart_link_read_byte(&s_rx[s_rx_len])) {
            break;
        }
        s_rx_len++;
    }

    /* 2. Parse as many frames as the buffer holds. */
    while (s_rx_len > 0U) {
        pkt_frame_t  f;
        pkt_result_t r;
        uint16_t     avail = s_rx_len;

        r = pkt_parse(s_rx, avail, &f, &consumed);
        if (r == PKT_OK) {
            s_host_present = true;
            handle_frame(&f, now_ms);
        } else if (r == PKT_ERR_CRC) {
            diagnostics()->uart_crc_errors++;
        } else if (r == PKT_ERR_VERSION || r == PKT_ERR_LENGTH) {
            diagnostics()->protocol_errors++;
        }

        if (consumed == 0U) {
            break;                       /* NEED_MORE with nothing droppable */
        }
        if (consumed >= s_rx_len) {
            s_rx_len = 0U;
            break;
        }
        s_rx_len = (uint16_t)(s_rx_len - consumed);
        memmove(s_rx, &s_rx[consumed], s_rx_len);
        if (r == PKT_NEED_MORE) {
            break;                       /* only partial data, wait for more */
        }
    }

    /* 3. Periodic transmissions. */
    if (!s_host_present && (uint32_t)(now_ms - s_last_hello_ms) >= 5000U) {
        s_last_hello_ms = now_ms;
        send_hello(now_ms);
    }
    if (s_streaming && s_batch_count > 0U
        && (uint32_t)(now_ms - s_last_temp_ms) >= 40U) {
        /* Flush a part-full batch so a stopped or slow stream still shows live
         * samples rather than stalling until 20 more arrive. */
        send_ecg_batch(now_ms);
    }
    if ((uint32_t)(now_ms - s_last_status_ms) >= DIAG_STATUS_PERIOD_MS) {
        s_last_status_ms = now_ms;
        send_status(now_ms);
    }
    if ((uint32_t)(now_ms - s_last_temp_ms) >= TEMP_STATUS_PERIOD_MS) {
        s_last_temp_ms = now_ms;
        send_temp_status(now_ms);
    }
}

void protocol_service_set_streaming(bool on)
{
    s_streaming = on;
    diagnostics()->streaming = on;
    if (!on) {
        s_batch_count = 0U;
    }
}

bool protocol_service_streaming(void)
{
    return s_streaming;
}

bool protocol_service_host_present(void)
{
    return s_host_present;
}

void protocol_service_get_config(uint8_t *notch, uint8_t *hr_low, uint8_t *hr_high,
                                 int16_t *temp_low, int16_t *temp_high)
{
    if (notch != NULL)      { *notch = s_notch; }
    if (hr_low != NULL)     { *hr_low = s_hr_low; }
    if (hr_high != NULL)    { *hr_high = s_hr_high; }
    if (temp_low != NULL)   { *temp_low = s_temp_low; }
    if (temp_high != NULL)  { *temp_high = s_temp_high; }
}
