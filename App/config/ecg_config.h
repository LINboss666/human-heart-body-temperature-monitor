/**
 * @file    ecg_config.h
 * @brief   Every tunable number the ECG signal chain uses, in one file.
 *
 * Division of responsibility: the filter STRUCTURE (tap counts, shift amounts,
 * and their measured frequency response) is derived and documented by
 * tools/gen_ecg_filters.py in ecg_filter_coeff.h. This file holds the choices
 * that a human might reasonably retune: scales, thresholds and time constants
 * of the detector, plus the geometry of the waveform display.
 *
 * Three independent data paths leave the filter stage, per the Phase 1 brief:
 *
 *   RAW      unfiltered ADC codes, 0..4095. Streamed to the PC and written to
 *            CSV/XLSX. Nothing here may touch it: the course metric is a
 *            0.05-150 Hz recording bandwidth, and truncating the record to a
 *            heart-rate band would destroy the measurement being graded.
 *   DISPLAY  baseline-removed and comb-filtered, for the OLED trace.
 *   QRS      5-22 Hz band, energy-integrated, feeds only the detector.
 *
 * Fixed point throughout. The Cortex-M3 has no FPU, but the real reason is
 * verifiability: the host tests execute the identical integer arithmetic, so a
 * passing synthetic-beat test describes the shipping firmware rather than a
 * floating-point re-imagining of it.
 */
#ifndef ECG_CONFIG_H
#define ECG_CONFIG_H

#include "app_config.h"

/* ------------------------------------------------------------- sample rate */

/** The pipeline hard-depends on this; see ECG_FILTER_DESIGN_FS. */
#define ECG_FS_HZ                   ADC_SAMPLE_RATE_HZ   /* 1000 */

#define ECG_MS_TO_SAMPLES(ms)       ((ms) * ECG_FS_HZ / 1000U)
#define ECG_SAMPLES_TO_MS(n)        ((n) * 1000U / ECG_FS_HZ)

/* ------------------------------------------- RAW -> millivolts at the pin */

#define ECG_RAW_TO_MV(raw)          ((int32_t)((int32_t)(raw) * VDDA_MV / ADC_FULL_SCALE_CODES))

/* ---------------------------------------------------------------- rail gate */

/**
 * A code pinned near either supply is not a signal. The window is generous
 * because the true bias point of the analog front-end is UNVERIFIED; a mid-rail
 * biased input sits far inside it, and only a genuinely railed input trips it.
 */
#define ECG_RAIL_LOW_CODE           24U
#define ECG_RAIL_HIGH_CODE          (ADC_FULL_SCALE_CODES - 24U)

/* --------------------------------------------------------------- notch band */

typedef enum {
    ECG_NOTCH_50HZ = 0,   /* default: China mains */
    ECG_NOTCH_60HZ = 1,
    ECG_NOTCH_OFF  = 2
} ecg_notch_t;

#define ECG_NOTCH_DEFAULT           ECG_NOTCH_50HZ

/* ----------------------------------------------------- QRS energy pathway */

/*
 * Derivative of the band-limited signal. Shift 1 keeps the 3-point difference
 * from overflowing while retaining slope sensitivity; the value is not a
 * physical derivative, only a monotone proxy for it.
 */
#define ECG_DERIV_SHIFT             1U

/**
 * Right shift applied to the squared derivative. Sized from measurement, not
 * guesswork: with the 5-44 Hz QRS band the derivative of a physiologically sharp
 * R wave stays below ~800 counts, so >>6 keeps the square under 65535 and the
 * uint16 storage clamp in ecg_signal.c stays a guard rather than a working limit.
 */
#define ECG_SQUARE_SHIFT            6U

/* ------------------------------------------------------------- detection */

/*
 * Adaptive detection, verified by driving the real C over synthetic beats in
 * tests/host/test_ecg_pipeline.c:
 *
 *   floor   long-term average of the QRS energy, stall-free EMA
 *   above   energy - floor                    (>= 0 by construction)
 *   ref     this signal's own beat amplitude, learned in the calibration window
 *           and then tracked slowly
 *   thresh  RATIO% of ref, never below JITTER_MIN
 *   fire    above > thresh and outside the refractory period
 *
 * Everything is relative to the patient's own amplitude on purpose. The analog
 * front-end gain and bias are UNVERIFIED (see app_config.h), so an absolute
 * count threshold would be tuned to a circuit nobody has built yet - and was
 * measured doing exactly the wrong thing: a clean 60 bpm beat that produced 249
 * energy units was invisible to a floor calibrated at 256 from a noisier
 * waveform that produced 441.
 */
#define ECG_ENERGY_FLOOR_SHIFT      12U   /* ~4 s time constant */
#define ECG_THRESH_RATIO_PERCENT    40U
#define ECG_REF_TRACK_SHIFT         5U    /* ref follows accepted beats slowly */

/**
 * Jitter guard, not a signal threshold: it only stops the detector inventing
 * beats when ref has decayed all the way down on a dead input. Deliberately
 * orders of magnitude below any plausible QRS energy.
 */
#define ECG_THRESH_JITTER_MIN       16UL

/** Samples of filter settling before the detector looks at anything. */
#define ECG_SETTLE_SAMPLES          ECG_MS_TO_SAMPLES(2000U)

/** Samples spent learning the signal's amplitude before any beat is reported. */
#define ECG_CALIBRATE_SAMPLES       ECG_MS_TO_SAMPLES(2000U)

/**
 * Silence after which the reference is halved, so a signal that becomes much
 * quieter - or an electrode freshly applied - is reacquired rather than stayed
 * out by a loud moment in the history. Sized to exceed the 3 s a valid reading
 * is allowed to persist without a beat.
 */
#define ECG_QUIET_REACQUIRE_SAMPLES ECG_MS_TO_SAMPLES(4000U)

/** Refractory period: no second R within this, capping detection at 240 bpm. */
#define ECG_REFRACTORY_SAMPLES      ECG_MS_TO_SAMPLES(250U)

/** Peak-to-peak below which the display path is declared to carry no signal. */
#define ECG_FLATLINE_PTP_CODES      25U

/* --------------------------------------------------------- heart-rate logic */

#define ECG_RR_MIN_SAMPLES          ((uint32_t)(ECG_MS_TO_SAMPLES(60000U / HR_BPM_MAX_VALID)))
#define ECG_RR_MAX_SAMPLES          ((uint32_t)(ECG_MS_TO_SAMPLES(60000U / HR_BPM_MIN_VALID)))

/** Beats required before the reading stops being HR_ACQUIRING. */
#define HR_ACQUIRING_MIN_BEATS      3U

/**
 * Odd so the median is a real observation rather than an average of two
 * outliers. One missed or extra beat moves a single element of this window.
 */
#define HR_MEDIAN_WINDOW_ODD        ((HR_MEDIAN_WINDOW | 1U))

/* ------------------------------------------------- display / OLED waveform */

/** One OLED column stands for this many samples: 128 columns ~= 1.02 s of trace. */
#define WAVE_COLUMN_SAMPLES         8U
#define WAVE_COLUMNS                128U
#define WAVE_RING_SAMPLES           (WAVE_COLUMN_SAMPLES * WAVE_COLUMNS)

/** Trace area inside the 128x64 panel, leaving a header and a footer row. */
#define WAVE_TOP_ROW                16
#define WAVE_BOTTOM_ROW             56
#define WAVE_HEIGHT_ROWS            (WAVE_BOTTOM_ROW - WAVE_TOP_ROW + 1U)

#define WAVE_GAIN_Q4_SHIFT          4U
#define WAVE_MAX_DEVIATION_ROWS     20

#endif /* ECG_CONFIG_H */
