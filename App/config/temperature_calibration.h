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
    TEMP_MODEL_LINEAR_MV         /**< centi = (pin_mV - intercept) * 100 / slope */
} temp_sensor_model_t;

/**
 * Which model temperature.c will run. Nothing here is measured yet, so the
 * shipping default is UNCALIBRATED.
 *
 * It is #ifndef-guarded for one reason: tests/host/test_temperature.c overrides
 * it so the linear and table branches are actually compiled and executed rather
 * than left as untested dead code. The firmware build never defines it, and so
 * always gets UNCALIBRATED.
 */
#ifndef TEMP_SENSOR_MODEL
#define TEMP_SENSOR_MODEL           TEMP_MODEL_UNCALIBRATED
#endif

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

/*
 * Probe confirmation is counted in AVERAGED UPDATES, not in raw samples: the
 * streaks in temperature.c only advance when a TEMP_AVERAGE_WINDOW average
 * completes, so one step is TEMP_UPDATE_PERIOD_MS long, not 1 ms. The previous
 * comment claimed "125 ms at 1 kHz" for a value that takes 31.25 s to elapse;
 * the counts themselves are kept unchanged so no timing that was never measured
 * on hardware is being re-tuned under cover of a documentation fix.
 */
#define TEMP_PROBE_FAULT_CONFIRM    125U   /* x 250 ms = 31.25 s to latch a fault */

/** Consecutive in-window updates before a latched fault is cleared. */
#define TEMP_PROBE_OK_CONFIRM       250U   /* x 250 ms = 62.50 s to clear */

/* ---------------------------------------------------- plausible body range */

/** Acceptance window for a computed body temperature, centi-degC. */
#define TEMP_CENTI_MIN_VALID        2500   /* 25.00 C */
#define TEMP_CENTI_MAX_VALID        4500   /* 45.00 C */

/** Alarm bands, editable at runtime via the settings page / protocol. */
#define TEMP_CENTI_LOW_DEFAULT      3500   /* 35.00 C */
#define TEMP_CENTI_HIGH_DEFAULT     3750   /* 37.50 C */

/* --------------------------------------------------------- model parameters */

/**
 * TEMP_MODEL_LINEAR_MV: a sensor whose output is a straight line in pin mV,
 *   centi = (pin_mV - TEMP_LINEAR_INTERCEPT_MV) * 100000 / slope_nv_per_centi
 *
 * Both numbers are UNVERIFIED and describe no real part. They are chosen so the
 * branch is exercisable at all: with these values an ADC code near mid-scale
 * lands inside TEMP_CENTI_MIN..MAX_VALID, which lets tests/host/
 * test_temperature_calibrated.c actually reach a successful conversion instead
 * of only ever exercising the rejection path. A placeholder of
 * "1 mV per 0.01 C" would have mapped mid-scale to 11 C, silently turning every
 * calibrated test into a rejection test.
 */
#define TEMP_LINEAR_SLOPE_NV_PER_CENTI   30000L  /* UNVERIFIED placeholder */
#define TEMP_LINEAR_INTERCEPT_MV             500L  /* UNVERIFIED placeholder */

/* ------------------------------------------------------------- smoothing */

/**
 * Decimation of the 1 kHz channel into the application-level reading. The ADC
 * keeps sampling at 1 kHz for the ECG path; temperature is averaged over this
 * many samples, which is also the fault-confirm time base.
 */
#define TEMP_AVERAGE_WINDOW         250U   /* 250 ms, comfortably <= 500 ms */

/**
 * Wall-clock length of one averaged update, and therefore of one step of the
 * probe-confirmation streaks above. Derived rather than restated so the two
 * cannot drift apart: TEMP_AVERAGE_WINDOW or the sample rate changes, and this
 * follows. temperature.c asserts at compile time that it divides exactly.
 *
 * The course requirement is an application-level temperature reading at least
 * every 500 ms; 250 ms leaves a 2x margin.
 */
#define TEMP_UPDATE_PERIOD_MS \
    ((uint32_t)TEMP_AVERAGE_WINDOW * 1000U / (uint32_t)ADC_SAMPLE_RATE_HZ)

/** Integer rounded division helper shared with temperature.c. */
#define TEMP_DIV_ROUND(nom, den)    (((nom) + ((den) / 2)) / (den))

#endif /* TEMPERATURE_CALIBRATION_H */
