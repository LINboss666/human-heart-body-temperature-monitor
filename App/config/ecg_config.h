/**
 * @file    ecg_config.h
 * @brief   Every number the ECG signal chain uses, in one file.
 *
 * Three independent data paths leave the filter stage, per the Phase 1 brief:
 *
 *   RAW      unfiltered ADC codes, 0..4095. Streamed to the PC and written to
 *            CSV/XLSX. No filter in this repository may touch it, because the
 *            course metric is a 0.05-150 Hz recording bandwidth and truncating
 *            the record to a heart-rate band would destroy the measurement.
 *   DISPLAY  baseline-removed and low-passed, for the OLED trace and the PC
 *            overview plot.
 *   QRS      narrow 5-15 Hz band, energy-integrated, feeds only the detector.
 *
 * Fixed point throughout: the Cortex-M3 has no FPU and the point is not speed
 * but that the host tests can execute the identical integer arithmetic and
 * prove the detector on synthetic beats.
 */
#ifndef ECG_CONFIG_H
#define ECG_CONFIG_H

#include "app_config.h"

/* ------------------------------------------------------------- sample rate */

/** The pipeline hard-depends on this; see ECG_FILTER_DESIGN_FS. */
#define ECG_FS_HZ                   ADC_SAMPLE_RATE_HZ   /* 1000 */

/** Convenience: milliseconds in N samples. */
#define ECG_MS_TO_SAMPLES(ms)       ((ms) * ECG_FS_HZ / 1000U)
#define ECG_SAMPLES_TO_MS(n)        ((n) * 1000U / ECG_FS_HZ)

/* ------------------------------------------------- RAW -> millivolts at pin */

/** adc_mv = raw * VDDA_MV / 4095, in integer milli-volts. */
#define ECG_RAW_TO_MV(raw)          ((int32_t)((int32_t)(raw) * VDDA_MV / ADC_FULL_SCALE_CODES))

/* ------------------------------------------- baseline removal (display/QRS) */
/*
 * Two cascaded 1st-order leaky integrators estimate the baseline, which is then
 * subtracted. Power-of-two coefficients keep this exact in int32, which is why
 * it is not the Q14 biquad that gen_ecg_filters.py rejected for 0.5 Hz.
 *
 *   pole period = 2**K samples  ->  fc = FS / (2*pi*2**K)
 *   K = 8  ->  256 ms, fc ~ 0.62 Hz
 *
 * The estimator works on x << ECG_BASELINE_Q so that the >> K step does not
 * stall inside a dead band a whole ADC LSB wide.
 */
#define ECG_BASELINE_SHIFT_K        8U
#define ECG_BASELINE_INPUT_Q        8U
/** Nominal -3 dB corner of one stage, for documentation and tests. */
#define ECG_BASELINE_CORNER_HZ      0.62f

/**
 * A saturated or near-rail input makes the baseline estimator useless, and a
 * mid-rail constant looks like a perfect flat line. Treat anything outside this
 * window as "not a signal" rather than as a beat-free interval.
 */
#define ECG_RAIL_LOW_CODE           24U
#define ECG_RAIL_HIGH_CODE          (ADC_FULL_SCALE_CODES - 24U)

/* --------------------------------------------------------------- notch band */

typedef enum {
    ECG_NOTCH_50HZ = 0,   /* default: China mains */
    ECG_NOTCH_60HZ = 1,
    ECG_NOTCH_OFF  = 2
} ecg_notch_t;

/** Compile-time default; runtime selection lives in app_settings. */
#define ECG_NOTCH_DEFAULT           ECG_NOTCH_50HZ

/* ------------------------------------------------------------- QRS pathway */

/** Derivative (2x + x[-1] - x[-2]) >> 3, following Pan & Tompkins. */
#define ECG_DERIV_SHIFT             3U

/** Energy = y*y >> ECG_SQUARE_SHIFT, keeps the int32 accumulator in range. */
#define ECG_SQUARE_SHIFT            10U

/**
 * Moving-window integration length. 120 ms covers a normal QRS complex
 * (60-120 ms) without smearing two adjacent beats together at the top of the
 * heart-rate range; at 220 bpm the RR interval is still 273 ms.
 */
#define ECG_MWI_WINDOW_SAMPLES      ECG_MS_TO_SAMPLES(120U)

/** Absolute floor below which the integrated signal is never a beat. */
#define ECG_THRESH_ABS_MIN          64L

/** Adaptive threshold: 0.35 * running max of the last 8 beats' peak. */
#define ECG_THRESH_SIG_RATIO_Q      15U   /* 0.35 ~ 11536/32768 */
#define ECG_THRESH_SIG_RATIO        11536L
/** Threshold falls this slowly so a transient cannot kill detection for long. */
#define ECG_THRESH_DECAY_SHIFT      6U
/** Threshold rises this fast to chase a bigger signal. */
#define ECG_THRESH_RISE_SHIFT       2U

/** Refractory period: no second R within this, i.e. caps detection at 240 bpm. */
#define ECG_REFRACTORY_SAMPLES      ECG_MS_TO_SAMPLES(250U)

/* --------------------------------------------------------- heart-rate logic */

/** RR samples corresponding to the acceptance window in app_config.h. */
#define ECG_RR_MIN_SAMPLES          ((uint32_t)(ECG_MS_TO_SAMPLES(60000U / HR_BPM_MAX_VALID)))
#define ECG_RR_MAX_SAMPLES          ((uint32_t)(ECG_MS_TO_SAMPLES(60000U / HR_BPM_MIN_VALID)))

/** Beats needed before the displayed value stops being HR_ACQUIRING. */
#define HR_ACQUIRING_MIN_BEATS      3U

/**
 * A single missed or extra beat moves one element of the median window, which is
 * why the median of HR_MEDIAN_WINDOW beats is reported rather than the raw RR.
 * Odd so the median is a real observation, not an average of two outliers.
 */
#define HR_MEDIAN_WINDOW_ODD        ((HR_MEDIAN_WINDOW | 1U))

/* ------------------------------------------------- display / OLED waveform */

/**
 * One OLED column stands for this many samples. 8 -> 128 columns = 1024 samples
 * ~ 1.02 s of trace across the screen.
 */
#define WAVE_COLUMN_SAMPLES         8U
#define WAVE_COLUMNS                128U
#define WAVE_RING_SAMPLES           (WAVE_COLUMN_SAMPLES * WAVE_COLUMNS)

/** Trace area inside the 128x64 panel, leaving a header row and a footer row. */
#define WAVE_TOP_ROW                16
#define WAVE_BOTTOM_ROW             56
#define WAVE_HEIGHT_ROWS            (WAVE_BOTTOM_ROW - WAVE_TOP_ROW + 1U)

/** QRS amplitudes in the display path are scaled by this before centring. */
#define WAVE_GAIN_Q4_SHIFT          4U
/** Clamp the trace to +/- this many rows so a saturating input cannot flood it. */
#define WAVE_MAX_DEVIATION_ROWS     20

#endif /* ECG_CONFIG_H */
