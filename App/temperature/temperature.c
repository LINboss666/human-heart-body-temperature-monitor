#include "temperature.h"

#include <stddef.h>

#include "app_config.h"

/*
 * Decimation of the temperature channel out of the 1 kHz stream. A plain
 * integer accumulator over TEMP_AVERAGE_WINDOW samples is enough: 250 x 4095
 * fits comfortably in 32 bits, and averaging is what a slow-moving body
 * temperature wants. No median filter, because nothing here is impulsive - and
 * if the eventual front-end is, that belongs in temperature_calibration.h too.
 */
static uint32_t      s_sum;
static uint16_t      s_count;
static temperature_t s_out;
static uint16_t      s_open_streak;
static uint16_t      s_short_streak;
static uint16_t      s_ok_streak;
static int16_t       s_low_centi  = TEMP_CENTI_LOW_DEFAULT;
static int16_t       s_high_centi = TEMP_CENTI_HIGH_DEFAULT;

/*
 * Convert an averaged code into hundredths of a degree.
 *
 * Every model here is a placeholder for the circuit that does not exist yet.
 * The uncalibrated case is the shipping default and returns "no value", which
 * is the only honest answer available.
 */
static bool TemperatureConvert(uint16_t code, int16_t *centi_out)
{
    switch (TEMP_SENSOR_MODEL) {
    case TEMP_MODEL_LINEAR_MV: {
        /* centi = (pin_mV - intercept_mV) * 100 / (slope_nV_per_cent / 1000) */
        int32_t mv = ADC_RAW_TO_MV(code);
        int32_t centi = ((mv - TEMP_LINEAR_INTERCEPT_MV) * 100000L)
                        / TEMP_LINEAR_SLOPE_NV_PER_CENTI;
        if (centi < TEMP_CENTI_MIN_VALID || centi > TEMP_CENTI_MAX_VALID) {
            return false;
        }
        *centi_out = (int16_t)centi;
        return true;
    }

    case TEMP_MODEL_UNCALIBRATED:
    default:
        return false;
    }
}

void temperature_init(void)
{
    s_sum = 0U;
    s_count = 0U;
    s_open_streak = 0U;
    s_short_streak = 0U;
    s_ok_streak = 0U;

    s_out.raw = 0U;
    s_out.mv = 0U;
    s_out.centi_c = 0;
    s_out.state = TEMP_UNCALIBRATED;
    s_out.valid = false;
    s_out.calibrated = (TEMP_SENSOR_MODEL != TEMP_MODEL_UNCALIBRATED);
    s_out.probe_fault = false;
    s_out.updates = 0U;
}

void temperature_feed(uint16_t adc_code)
{
    uint16_t avg;
    int16_t  centi = 0;
    bool     converted;

    s_sum += adc_code;
    if (++s_count < TEMP_AVERAGE_WINDOW) {
        return;
    }
    avg = (uint16_t)(s_sum / TEMP_AVERAGE_WINDOW);
    s_sum = 0U;
    s_count = 0U;

    s_out.raw = avg;
    s_out.mv = (uint16_t)ADC_RAW_TO_MV(avg);
    s_out.updates++;

    /* Probe presence. These thresholds are properties of a divider that has not
     * been built, so they are range tests, not a diagnosis, and the fault has to
     * persist for a full window before it is latched. */
    if (avg >= TEMP_ADC_OPEN_THRESHOLD) {
        s_open_streak++;
        s_short_streak = 0U;
        s_ok_streak = 0U;
    } else if (avg <= TEMP_ADC_SHORT_THRESHOLD) {
        s_short_streak++;
        s_open_streak = 0U;
        s_ok_streak = 0U;
    } else {
        s_ok_streak++;
        s_open_streak = 0U;
        s_short_streak = 0U;
    }

    if (s_open_streak >= TEMP_PROBE_FAULT_CONFIRM
        || s_short_streak >= TEMP_PROBE_FAULT_CONFIRM) {
        s_out.probe_fault = true;
    } else if (s_ok_streak >= TEMP_PROBE_OK_CONFIRM) {
        s_out.probe_fault = false;
    }

    converted = TemperatureConvert(avg, &centi);
    s_out.calibrated = (TEMP_SENSOR_MODEL != TEMP_MODEL_UNCALIBRATED);

    if (!s_out.calibrated) {
        /* No invented number: the UI shows --.- and the PC sees valid = false. */
        s_out.centi_c = 0;
        s_out.valid = false;
        s_out.state = TEMP_UNCALIBRATED;
        return;
    }

    if (s_out.probe_fault) {
        s_out.centi_c = 0;
        s_out.valid = false;
        s_out.state = TEMP_PROBE_FAULT;
        return;
    }

    if (!converted) {
        s_out.centi_c = 0;
        s_out.valid = false;
        s_out.state = TEMP_PROBE_FAULT;
        return;
    }

    s_out.centi_c = centi;
    s_out.valid = true;
    if (centi < s_low_centi) {
        s_out.state = TEMP_LOW;
    } else if (centi > s_high_centi) {
        s_out.state = TEMP_HIGH;
    } else {
        s_out.state = TEMP_OK;
    }
}

void temperature_get(temperature_t *out)
{
    if (out == NULL) {
        return;
    }
    *out = s_out;
}

void temperature_set_alarm_bands(int16_t low_centi, int16_t high_centi)
{
    if (low_centi >= high_centi) {
        return;                       /* a reversed band would flag everything */
    }
    if (low_centi < TEMP_CENTI_MIN_VALID || high_centi > TEMP_CENTI_MAX_VALID) {
        return;
    }
    s_low_centi = low_centi;
    s_high_centi = high_centi;
}

void temperature_get_alarm_bands(int16_t *low_centi, int16_t *high_centi)
{
    if (low_centi != NULL) { *low_centi = s_low_centi; }
    if (high_centi != NULL) { *high_centi = s_high_centi; }
}
