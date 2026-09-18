/**
 * @file    temperature.h
 * @brief   Temperature channel service, split into raw and calibrated layers.
 *
 * The split is the whole point. The temperature front-end is another project
 * member's design and is not in this repository, so the calibration is not
 * merely unconfigured, it is unknown. Rather than invent a curve and print a
 * plausible 36.5, the calibrated layer reports TEMP_UNCALIBRATED and leaves the
 * raw code and its millivolts available - which is exactly what makes the
 * calibration possible later, since it needs the raw numbers.
 *
 * When the real sensor is characterised, only temperature_calibration.h and the
 * single function TemperatureConvert() change. Nothing downstream does.
 */
#ifndef TEMPERATURE_H
#define TEMPERATURE_H

#include <stdbool.h>
#include <stdint.h>

#include "temperature_calibration.h"
#include "protocol/protocol.h"   /* temp_state_t */

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint16_t      raw;        /**< averaged 12-bit ADC code, always available */
    uint16_t      mv;         /**< millivolts at the ADC pin */
    int16_t       centi_c;    /**< hundredths of a degree, only if valid */
    temp_state_t  state;
    bool          valid;      /**< centi_c may be shown */
    bool          calibrated; /**< false while the model is TEMP_MODEL_UNCALIBRATED */
    bool          probe_fault;
    uint32_t      updates;    /**< completed decimation windows */
} temperature_t;

/** Reset accumulators and state. */
void temperature_init(void);

/**
 * Feed one temperature ADC code. Call once per acquisition frame; the module
 * decimates internally so the 1 kHz sample rate is preserved for the ECG path
 * while the application-level reading updates on its own slower cadence.
 */
void temperature_feed(uint16_t adc_code);

/** Snapshot of the current reading. Never blocks. */
void temperature_get(temperature_t *out);

/** Runtime-editable alarm bands, in hundredths of a degree. */
void temperature_set_alarm_bands(int16_t low_centi, int16_t high_centi);
void temperature_get_alarm_bands(int16_t *low_centi, int16_t *high_centi);

#ifdef __cplusplus
}
#endif

#endif /* TEMPERATURE_H */
