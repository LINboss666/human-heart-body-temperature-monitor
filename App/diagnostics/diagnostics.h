/**
 * @file    diagnostics.h
 * @brief   One place that knows how healthy the system is, readable by the OLED
 *          status page and by the PC over the STATUS packet.
 *
 * Deliberately a plain struct of counters rather than a logging framework: the
 * same numbers must be visible on the 128x64 panel and on the wire without either
 * consumer having to know about the other, and the STATUS payload is defined by
 * offset in protocol.h.
 */
#ifndef DIAGNOSTICS_H
#define DIAGNOSTICS_H

#include <stdbool.h>
#include <stdint.h>

#include "protocol/protocol.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    bool     adc_running;
    bool     adc_calibrated;
    uint32_t dma_blocks;
    uint32_t dma_dropped;
    uint32_t ecg_samples;
    uint8_t  hr_bpm;
    uint8_t  hr_state;          /**< hr_state_t */
    bool     hr_valid;
    int16_t  temp_centi;
    uint16_t temp_raw;          /**< averaged ADC code; valid even uncalibrated */
    uint8_t  temp_state;        /**< temp_state_t */
    bool     temp_valid;
    bool     temp_uncalibrated;
    uint8_t  lead_state;        /**< lead_state_t, from signal quality only */
    uint8_t  notch;             /**< ecg_notch_t currently applied */
    bool     oled_present;
    uint8_t  oled_address;      /**< 7-bit, 0 when nothing answered */
    bool     rtc_valid;
    uint32_t uart_tx_packets;
    uint32_t uart_rx_packets;
    uint32_t uart_crc_errors;
    uint32_t uart_rx_overruns;
    uint32_t protocol_errors;
    bool     streaming;
    bool     recording;
    uint32_t session_seconds;
    uint32_t uptime_seconds;
    uint8_t  last_error_code;
} diagnostics_t;

void diagnostics_init(void);

/** The single mutable instance. Modules update their own fields directly. */
diagnostics_t *diagnostics(void);

/**
 * Boot-time failures that leave the device running but something inside it not
 * working. Each one also increments protocol_errors, which does travel in the
 * STATUS packet, while the code itself stays in last_error_code for a debugger
 * or a later UI: no screen renders it in this build.
 *
 * 200-series rather than KK_UI's error codes, so the two cannot collide.
 */
enum {
    DIAG_ERR_NONE             = 0U,
    DIAG_ERR_ACQUISITION_START = 200U,  /**< ADC calibration or DMA arming failed */
    DIAG_ERR_RTC_SYNC         = 201U    /**< RTC register sync never completed;
                                             the clock is reported unset because the
                                             elapsed interval cannot be known */
};

/** Record a protocol-level failure and keep the reason for the STATUS page. */
void diagnostics_note_error(uint8_t code);

/**
 * Fill STP_* fields of a STATUS payload. Returns the payload length.
 * Kept here so the wire layout and the display layout are produced from the same
 * struct and cannot disagree about what "OK" meant.
 */
uint16_t diagnostics_fill_status_payload(uint8_t *payload, uint16_t capacity);

#ifdef __cplusplus
}
#endif

#endif /* DIAGNOSTICS_H */
