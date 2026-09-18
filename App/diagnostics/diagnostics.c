#include "diagnostics.h"

#include <string.h>

#include "app_config.h"
#include "protocol/protocol.h"

static diagnostics_t s_diag;

void diagnostics_init(void)
{
    memset(&s_diag, 0, sizeof(s_diag));
}

diagnostics_t *diagnostics(void)
{
    return &s_diag;
}

void diagnostics_note_error(uint8_t code)
{
    s_diag.protocol_errors++;
    s_diag.last_error_code = code;
}

uint16_t diagnostics_fill_status_payload(uint8_t *payload, uint16_t capacity)
{
    uint16_t flags = 0U;

    if (payload == NULL || capacity < STP_SIZE) {
        return 0U;
    }

    flags = SFLAG_SET(flags, SFLAG_LEAD_SHIFT, SFLAG_LEAD_MASK,
                      (uint16_t)s_diag.lead_state);
    flags = SFLAG_SET(flags, SFLAG_TEMP_SHIFT, SFLAG_TEMP_MASK,
                      (uint16_t)s_diag.temp_state);
    if (s_diag.hr_valid)          { flags |= SFLAG_HR_VALID; }
    if (s_diag.recording)         { flags |= SFLAG_RECORDING; }
    if (s_diag.oled_present)      { flags |= SFLAG_OLED; }
    if (s_diag.adc_running)       { flags |= SFLAG_ADC_RUNNING; }
    if (s_diag.rtc_valid)         { flags |= SFLAG_RTC_VALID; }
    if (s_diag.dma_dropped != 0U) { flags |= SFLAG_DMA_DROPPED; }
    flags = SFLAG_SET(flags, SFLAG_NOTCH_SHIFT, SFLAG_NOTCH_MASK,
                      (uint16_t)s_diag.notch);
    if (s_diag.temp_uncalibrated) { flags |= SFLAG_TEMP_UNCALIB; }

    payload[STP_ADC_RUNNING] = s_diag.adc_running ? 1U : 0U;
    pkt_put_u32(&payload[STP_DMA_BLOCKS], s_diag.dma_blocks);
    pkt_put_u32(&payload[STP_DMA_DROPPED], s_diag.dma_dropped);
    pkt_put_u32(&payload[STP_ECG_SAMPLES], s_diag.ecg_samples);
    payload[STP_TEMP_VALID] = s_diag.temp_valid ? 1U : 0U;
    pkt_put_i16(&payload[STP_TEMP_CENTI], s_diag.temp_centi);
    payload[STP_HR_BPM] = s_diag.hr_bpm;
    payload[STP_HR_STATE] = (uint8_t)s_diag.hr_state;
    payload[STP_OLED_PRESENT] = s_diag.oled_present ? 1U : 0U;
    payload[STP_OLED_ADDR] = s_diag.oled_address;
    payload[STP_RTC_VALID] = s_diag.rtc_valid ? 1U : 0U;
    pkt_put_u32(&payload[STP_UART_TX], s_diag.uart_tx_packets);
    pkt_put_u32(&payload[STP_UART_RX], s_diag.uart_rx_packets);
    pkt_put_u32(&payload[STP_UART_CRC_ERR], s_diag.uart_crc_errors);
    pkt_put_u32(&payload[STP_PROTO_ERR], s_diag.protocol_errors);
    pkt_put_u16(&payload[STP_FLAGS], flags);
    pkt_put_u32(&payload[STP_UPTIME_S], s_diag.uptime_seconds);

    return STP_SIZE;
}
