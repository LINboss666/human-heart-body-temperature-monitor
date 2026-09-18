/**
 * @file    temperature_calibration.h
 * @brief   Temperature transfer function, isolated so that only this file
 *          changes when the analog front-end is finally built.
 *
 * STATUS: UNCALIBRATED. The temperature front-end is another member's design
 * and is not in this repository, so no claim about degrees Celsius is made here.
 *
 * The contract that stays fixed regardless of the eventual sensor is:
 *   temperature_raw    -> always available (ADC code, and code as pin mV)
 *   temperature_centi_c -> only valid when state == TEMP_OK
 *
 * When calibration is not filled in, state stays TEMP_UNCALIBRATED and the UI
 * must print "--.-" rather than a plausible-looking number.
 */
#ifndef TEMPERATURE_CALIBRATION_H
#define TEMPERATURE_CALIBRATION_H

#include "app_config.h"

/* ------------------------------------------------------------- model switch */

typedef enum {
    TEMP_MODEL_UNCALIBRATED = 0, /**< default; produces TEMP_UNCALIBRATED */
    TEMP_MODEL_LINEAR_MV,        /**< centi = (mv - intercept) * 100 / slope_uV */
    TEMP_MODEL_NTC_TABLE         /**< piecewise-linear ADC-code table */
} temp_sensor_model_t;

/** Which model temperature.c will run. Nothing here is measured yet. */
#define TEMP_SENSOR_MODEL           TEMP_MODEL_UNCALIBRATED

/* --------------------------------------------- raw code sanity / probe fault */

/**
 * Open and short thresholds for the current baseline assumption, i.e. a
 * resistive divider driven into the ADC. These are placeholder numbers derived
 * from "near the rails is not a real temperature", NOT from a measured circuit,
 * and must be revised against the final divider before being trusted.
 *
 * An open input on an unloaded ADC pin drifts, so a code near full scale may or
 * may not indicate a broken probe; the only honest default is to report the
 * uncertainty rather than to invent a fault.
 */
#define TEMP_ADC_OPEN_THRESHOLD     (ADC_FULL_SCALE_CODES - 64U)  /* UNVERIFIED */
#define TEMP_ADC_SHORT_THRESHOLD    64U                           /* UNVERIFIED */

/** Consecutive out-of-window samples before a probe fault is latched. */
#define TEMP_PROBE_FAULT_CONFIRM    125U   /* 125 ms at 1 kHz, decimated input */

/** Consecutive in-window samples before the fault is cleared. */
#define TEMP_PROBE_OK_CONFIRM       250U

/* ---------------------------------------------------- plausible body range */

/** Acceptance window for a computed body temperature, centi-degC. */
#define TEMP_CENTI_MIN_VALID        2500   /* 25.00 C */
#define TEMP_CENTI_MAX_VALID        4500   /* 45.00 C */

/** Alarm bands, editable at runtime via the settings page / protocol. */
#define TEMP_CENTI_LOW_DEFAULT      3500   /* 35.00 C */
#define TEMP_CENTI_HIGH_DEFAULT     3750   /* 37.50 C */

/* --------------------------------------------------------- model parameters */

/**
 * TEMP_MODEL_LINEAR_MV: a sensor whose output is a straight line in pin mV.
 * slope_nanovolt_per_centi is nanovolts per 0.01 C, so 10000 uV/C = 100000.
 * Both are UNVERIFIED placeholders.
 */
#define TEMP_LINEAR_SLOPE_NV_PER_CENTI   100000L  /* UNVERIFIED */
#define TEMP_LINEAR_INTERCEPT_MV               500L  /* UNVERIFIED */

/**
 * TEMP_MODEL_NTC_TABLE: piecewise linear over ADC codes. Table entries are
 * (code, centi) pairs sorted by ascending code; interpolation between points is
 * integer. Empty by construction: filling it in is the hardware task.
 */
#define TEMP_TABLE_POINT_COUNT      0U

typedef struct {
    uint16_t adc_code;
    int16_t  centi_c;
} temp_table_point_t;

/** Explicitly empty rather than absent, so the file compiles either way. */
#define TEMP_TABLE_EMPTY_INIT \
    { { 0U, 0 } }

/* ------------------------------------------------------------- smoothing */

/**
 * Decimation of the 1 kHz channel into the application-level reading. The ADC
 * keeps sampling at 1 kHz for the ECG path; temperature is averaged over this
 * many samples, which is also the fault-confirm time base.
 */
#define TEMP_AVERAGE_WINDOW         250U   /* 250 ms, comfortably <= 500 ms */

/** Integer rounded division helper shared with temperature.c. */
#define TEMP_DIV_ROUND(nom, den)    (((nom) + ((den) / 2)) / (den))

#endif /* TEMPERATURE_CALIBRATION_H */
