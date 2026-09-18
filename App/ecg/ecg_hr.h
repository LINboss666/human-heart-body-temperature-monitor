/**
 * @file    ecg_hr.h
 * @brief   RR-interval history to a stable heart-rate reading and a status.
 *
 * Kept separate from ecg_signal.h so the detector can be tested against an
 * arbitrary sequence of beat times without running any filter, and so the
 * "acquiring before confident" rule cannot be accidentally bypassed.
 */
#ifndef ECG_HR_H
#define ECG_HR_H

#include <stdbool.h>
#include <stdint.h>

#include "ecg_config.h"
#include "protocol/protocol.h"   /* hr_state_t */

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint8_t     bpm;        /**< 0 unless state is a real rate */
    hr_state_t  state;
    uint16_t    rr_ms;      /**< the interval the reading came from */
    bool        valid;      /**< true when bpm may be shown to a user */
    uint32_t    beats;      /**< accepted R peaks since reset */
    uint32_t    rejected;   /**< intervals outside the physiological window */
} ecg_hr_t;

/** Alarm bands are passed in rather than baked in so the settings page and the
 *  PC link can change them at runtime. */
void ecg_hr_reset(uint16_t low_bpm, uint16_t high_bpm);
void ecg_hr_set_bands(uint16_t low_bpm, uint16_t high_bpm);

/**
 * Call once per sample with the detector's monotonic sample index, so the
 * reading can expire when beats stop arriving.
 */
void ecg_hr_tick(uint32_t sample_index, ecg_hr_t *out);

/** Call when ecg_signal reports an R peak, with that sample's index. */
void ecg_hr_notify_beat(uint32_t sample_index, ecg_hr_t *out);

const ecg_hr_t *ecg_hr_current(void);

#ifdef __cplusplus
}
#endif

#endif /* ECG_HR_H */
