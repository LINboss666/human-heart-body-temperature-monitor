/**
 * @file    ecg_signal.h
 * @brief   Fixed-point ECG conditioning and R-peak detection.
 *
 * Pure C: no HAL, no board headers, no dynamic memory. That is deliberate,
 * because this file plus ecg_hr.c are the parts whose behaviour can genuinely
 * be proven without a patient, using the synthetic beats in
 * tests/host/test_ecg_pipeline.c. The same integer arithmetic runs on both the
 * host and the target, so a test that passes here describes the firmware.
 */
#ifndef ECG_SIGNAL_H
#define ECG_SIGNAL_H

#include <stdbool.h>
#include <stdint.h>

#include "ecg_config.h"
#include "protocol/protocol.h"   /* lead_state_t */

#ifdef __cplusplus
extern "C" {
#endif

/** One processed sample on each of the three paths. */
typedef struct {
    uint16_t raw;          /**< never filtered: the record and the calibration hook */
    int16_t  mv;           /**< millivolts at the ADC pin, not at the electrode */
    int16_t  display;      /**< baseline-removed, notched, 40 Hz low-passed */
    uint32_t energy;       /**< moving-window-integrated QRS energy */
    bool     r_peak;       /**< this sample closes a detection */
    uint32_t sample_index; /**< monotonic, one per call to ecg_signal_process */
} ecg_sample_t;

/** Reset all filter state and detection history. Call before first sample. */
void ecg_signal_reset(void);

/** Feed one 12-bit ADC code. Produces exactly one output sample. */
void ecg_signal_process(uint16_t raw_adc_code, ecg_sample_t *out);

/** Runtime mains-notch selection; takes effect from the next sample. */
void        ecg_signal_set_notch(ecg_notch_t notch);
ecg_notch_t ecg_signal_get_notch(void);

/**
 * Software signal-quality assessment.
 *
 * Returns at most LEAD_SIGNAL_POOR. It never returns CONNECTED or
 * DISCONNECTED, because nothing in this firmware measures the electrode:
 * there is no injected-current or impedance front-end, so a flat line is a
 * flat line and not a proven detachment. The distinction is the difference
 * between an honest warning and an invented diagnosis.
 */
lead_state_t ecg_signal_lead_state(void);

/** True while the input is pinned against a rail, i.e. not a usable signal. */
bool ecg_signal_saturated(void);

/** Peak-to-peak of the display path over the last quality window, in codes. */
uint16_t ecg_signal_peak_to_peak(void);

/** Number of R peaks accepted since reset. */
uint32_t ecg_signal_beat_count(void);

#ifdef __cplusplus
}
#endif

#endif /* ECG_SIGNAL_H */
