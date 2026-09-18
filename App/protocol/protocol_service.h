/**
 * @file    protocol_service.h
 * @brief   Turns processed samples into ECG_BATCH frames and host packets into
 *          actions. Sits above uart_link (bytes) and below app (decisions).
 *
 * Ownership rule that keeps the transport honest: this module is the only thing
 * that calls uart_link_write, and it is only ever called from the main loop. A
 * second writer would interleave two half-frames on the wire, and a call from an
 * ISR would put a multi-millisecond blocking transmit inside the interrupt that
 * is refilling the sample buffer.
 */
#ifndef PROTOCOL_SERVICE_H
#define PROTOCOL_SERVICE_H

#include <stdbool.h>
#include <stdint.h>

#include "ecg/ecg_hr.h"
#include "ecg/ecg_signal.h"
#include "temperature/temperature.h"

#ifdef __cplusplus
extern "C" {
#endif

void protocol_service_init(void);

/**
 * Offer one processed sample. The RAW code is what goes on the wire; the
 * derived values ride along in the frame trailer so a PC-side record is
 * self-describing without a second channel.
 */
void protocol_service_push_sample(const ecg_sample_t *ecg,
                                  const ecg_hr_t *hr,
                                  const temperature_t *temp);

/** Drain RX, answer commands, emit anything whose cadence is due. */
void protocol_service_poll(uint32_t now_ms);

/** Reporting control. Neither touches the ADC, which runs from boot to power-off. */
void protocol_service_set_streaming(bool on);
bool protocol_service_streaming(void);

/** True once a host has connected (any valid frame received). */
bool protocol_service_host_present(void);

/** Latest configuration the host asked for, for the settings page to display. */
void protocol_service_get_config(uint8_t *notch, uint8_t *hr_low, uint8_t *hr_high,
                                 int16_t *temp_low, int16_t *temp_high);

#ifdef __cplusplus
}
#endif

#endif /* PROTOCOL_SERVICE_H */
