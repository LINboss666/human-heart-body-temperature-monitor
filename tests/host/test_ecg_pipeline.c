/*
 * Synthetic-beat tests for the ECG conditioning chain and the heart-rate
 * tracker.
 *
 * This is the evidence behind "the algorithm works", and the only kind
 * available without a patient: the filters and detector are pure integer code,
 * compiled here for the host by the same source files the firmware links, so a
 * pass is a statement about the shipping code path rather than about a
 * reimplementation of it.
 *
 * The stimulus is deliberately hostile: baseline wander, 50 Hz mains at an
 * amplitude that is large relative to the P wave, and broadband noise. A
 * detector that only works on a clean synthetic spike is worth nothing.
 */
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "ctest.h"

#include "ecg/ecg_signal.h"
#include "ecg/ecg_hr.h"

/* M_PI is a BSD-ism, not C99; the host test needs it and nothing else does. */
#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#define FS            1000U
#define MID_RAIL      1650.0

/* Deterministic pseudo-noise: a repeatably wrong LCG is better than rand(),
 * because a failing run must be reproducible from the test output alone. */
static uint32_t s_lcg;
static int noise_uniform(int amplitude)
{
    s_lcg = s_lcg * 1664525U + 1013904223U;
    return (int)((s_lcg >> 16) % (uint32_t)(2 * amplitude + 1)) - amplitude;
}

/* One beat, expressed as a sum of Gaussian bumps, at time t seconds after the
 * R peak of this beat. */
static double beat_waveform(double t)
{
    static const struct { double amp; double centre_ms; double width_ms; } g[] = {
        {  60.0, -160.0, 45.0 },   /* P  */
        { -55.0,   -16.0, 10.0 },  /* Q  */
        { 700.0,     0.0, 11.0 },  /* R  */
        {-150.0,    18.0, 13.0 },  /* S  */
        { 160.0,   240.0, 95.0 },  /* T  */
    };
    double v = 0.0;
    size_t i;
    for (i = 0U; i < sizeof(g) / sizeof(g[0]); i++) {
        double d = (t * 1000.0) - g[i].centre_ms;
        v += g[i].amp * exp(-(d * d) / (2.0 * g[i].width_ms * g[i].width_ms));
    }
    return v;
}

static uint32_t run_stream(uint32_t bpm, uint32_t seconds,
                           int add_mains, int wander,
                           ecg_hr_t *final_hr, uint32_t *detected_beats)
{
    const uint32_t total = FS * seconds;
    const double period = 60.0 / (double)bpm;
    uint32_t i;
    uint32_t hits = 0U;
    ecg_sample_t s;
    ecg_hr_t hr;

    ecg_signal_reset();
    ecg_hr_reset(HR_BPM_LOW_DEFAULT, HR_BPM_HIGH_DEFAULT);
    ecg_signal_set_notch(add_mains ? ECG_NOTCH_50HZ : ECG_NOTCH_OFF);

    memset(&hr, 0, sizeof(hr));
    *final_hr = hr;

    for (i = 0U; i < total; i++) {
        double t = (double)i / (double)FS;
        double phase_in_beat = fmod(t, period);
        double v = MID_RAIL;

        v += beat_waveform(phase_in_beat - period);   /* current beat */
        v += beat_waveform(phase_in_beat);            /* next beat arriving */
        if (wander) {
            v += 300.0 * sin(2.0 * M_PI * 0.3 * t);  /* baseline wander */
        }
        if (add_mains) {
            v += 120.0 * sin(2.0 * M_PI * 50.0 * t); /* mains interference */
        }
        v += (double)noise_uniform(30);

        if (v < 0.0) { v = 0.0; }
        if (v > 4095.0) { v = 4095.0; }

        ecg_signal_process((uint16_t)(v + 0.5), &s);
        ecg_hr_tick(s.sample_index, &hr);
        if (s.r_peak) {
            hits++;
            ecg_hr_notify_beat(s.sample_index, &hr);
        }
    }

    *final_hr = hr;
    *detected_beats = hits;
    return hr.bpm;
}

static void test_heart_rate_accuracy(void)
{
    uint32_t i;
    static const uint32_t rates[] = { 50U, 60U, 72U, 95U, 120U, 150U };
    /* The detector is deliberately blind during settling and auto-calibration,
     * so the expected beat count must be taken over the live window only. */
    const double dead_s = (double)(ECG_SETTLE_SAMPLES + ECG_CALIBRATE_SAMPLES) / 1000.0;
    const double live_s = 25.0 - dead_s;

    printf("    (bpm -> measured, with 0.3 Hz wander, 50 Hz mains and noise)\n");
    for (i = 0U; i < sizeof(rates) / sizeof(rates[0]); i++) {
        ecg_hr_t hr;
        uint32_t beats = 0U;
        uint32_t bpm;
        uint32_t expect;
        int diff;

        bpm = run_stream(rates[i], 25U, 1, 1, &hr, &beats);
        diff = (int)bpm - (int)rates[i];
        if (diff < 0) { diff = -diff; }
        expect = (uint32_t)((double)rates[i] * live_s / 60.0);

        printf("      %3u -> %3u bpm (%+d), %u/%u beats, state=%d\n",
               rates[i], bpm, (int)bpm - (int)rates[i], beats, expect, (int)hr.state);

        CHECK(hr.valid);
        /* +/-5 bpm is what a course demo needs to look right; the brief forbids
         * claiming a tighter figure than hardware can confirm anyway. */
        CHECK(diff <= 5);
        CHECK(hr.state == HR_NORMAL || hr.state == HR_LOW || hr.state == HR_HIGH);
        /* At least 80 % of the beats in the live window, and never a runaway:
         * over-detection would show up as a plausible median with too many hits. */
        CHECK(beats >= expect - expect / 5U);
        CHECK(beats <= expect + expect / 5U + 2U);
    }
}

static void test_raw_is_never_filtered(void)
{
    ecg_sample_t s;
    uint16_t codes[] = { 0U, 17U, 1024U, 2049U, 4095U };
    size_t i;
    uint32_t idx = 0U;

    CTEST_CASE("the RAW path reports exactly the ADC code that went in");
    ecg_signal_reset();
    for (i = 0U; i < sizeof(codes) / sizeof(codes[0]); i++) {
        ecg_signal_process(codes[i], &s);
        CHECK_EQ(s.raw, codes[i]);
        CHECK_EQ(s.sample_index, idx++);
    }
    /* And the millivolt view is the documented linear map, not a guess. */
    ecg_signal_reset();
    ecg_signal_process(4095U, &s);
    CHECK_EQ(s.mv, (int16_t)VDDA_MV);
    ecg_signal_reset();
    ecg_signal_process(0U, &s);
    CHECK_EQ(s.mv, (int16_t)0);
    ecg_signal_reset();
    ecg_signal_process(2048U, &s);
    CHECK(s.mv > 1640 && s.mv < 1660);
}

static void test_acquiring_before_confident(void)
{
    ecg_sample_t s;
    ecg_hr_t hr;
    uint32_t i;
    uint32_t beats_seen = 0U;
    bool saw_acquiring = false;
    bool confident_too_early = false;

    CTEST_CASE("no heart rate is reported before enough RR intervals exist");
    ecg_signal_reset();
    ecg_hr_reset(HR_BPM_LOW_DEFAULT, HR_BPM_HIGH_DEFAULT);

    /* Regular 60 bpm, with the Gaussian R centred mid-window so the pulse is not
     * split across the modulo boundary. A rectangular block is not a QRS: its
     * energy smears across the whole spectrum and the QRS band rejects most of
     * it, which would exercise the band-limit rather than the state machine this
     * test is about. 18 s is long enough to clear both the settling and the
     * auto-calibration windows and still leave five R-R intervals. */
    for (i = 0U; i < 18000U; i++) {
        double rel = (double)(i % 1000U) - 500.0;   /* R sits at 500 ms */
        double t_off = rel - 240.0;                 /* T follows 240 ms later */
        double shape = exp(-(rel * rel) / (2.0 * 11.0 * 11.0))
                     + 0.22 * exp(-(t_off * t_off) / (2.0 * 95.0 * 95.0));
        uint16_t code = (uint16_t)(1650.0 + 700.0 * shape);
        ecg_signal_process(code, &s);
        ecg_hr_tick(s.sample_index, &hr);
        if (s.r_peak) {
            beats_seen++;
            ecg_hr_notify_beat(s.sample_index, &hr);
            if (beats_seen <= 2U && hr.valid) {
                confident_too_early = true;
            }
        }
        if (hr.state == HR_ACQUIRING) {
            saw_acquiring = true;
        }
    }
    CHECK(saw_acquiring);
    CHECK(!confident_too_early);
    CHECK(hr.valid);
    CHECK_EQ(hr.state, HR_NORMAL);
    CHECK(beats_seen >= 5U);
}

static void test_flatline_is_signal_poor_not_disconnected(void)
{
    ecg_sample_t s;
    uint32_t i;
    uint32_t beats_settled = 0U;

    CTEST_CASE("a dead-flat input is SIGNAL_POOR and never a lead verdict");
    ecg_signal_reset();
    for (i = 0U; i < 3000U; i++) {
        ecg_signal_process(1650U, &s);
        /* Ignore the filter start-up transient; a step input into a band-limited
         * chain always produces one edge, and that is not a sustained rhythm. */
        if (s.r_peak && i > 500U) { beats_settled++; }
    }
    CHECK_EQ(beats_settled, 0U);
    CHECK_EQ(ecg_signal_lead_state(), LEAD_SIGNAL_POOR);
    /* The two must not be conflated: no electrode measurement exists here. */
    CHECK(ecg_signal_lead_state() != LEAD_DISCONNECTED);
    CHECK(ecg_signal_lead_state() != LEAD_CONNECTED);
    /* A few counts of residue from the integer baseline estimator's dead band is
     * expected and is what the flatline threshold exists to absorb. */
    CHECK(ecg_signal_peak_to_peak() < ECG_FLATLINE_PTP_CODES);
}

static void test_rail_input_is_saturated(void)
{
    ecg_sample_t s;
    uint32_t i;

    CTEST_CASE("an input pinned near a rail is flagged as saturated");
    ecg_signal_reset();
    for (i = 0U; i < 600U; i++) {
        ecg_signal_process(4090U, &s);
    }
    CHECK(ecg_signal_saturated());
    CHECK_EQ(ecg_signal_lead_state(), LEAD_SIGNAL_POOR);

    CTEST_CASE("a healthy amplitude clears the saturation flag");
    ecg_signal_reset();
    for (i = 0U; i < 600U; i++) {
        ecg_signal_process((uint16_t)(1650U + ((i / 25U) % 2U ? 200U : 0U)), &s);
    }
    CHECK(!ecg_signal_saturated());
}

static void test_implausible_rr_is_rejected(void)
{
    ecg_hr_t hr;
    uint32_t i;

    CTEST_CASE("an interval implying an impossible rate is rejected, not shown");
    ecg_hr_reset(60U, 100U);
    /* Establish a believable 60 bpm track first. */
    for (i = 0U; i < 6U; i++) {
        ecg_hr_tick(1000U * i, &hr);
        ecg_hr_notify_beat(1000U * i, &hr);
    }
    CHECK(hr.valid);
    CHECK_EQ(hr.bpm, 60U);
    /* Now a 40 ms interval after the beat at 5000: 1500 bpm, physically absurd. */
    ecg_hr_tick(5040U, &hr);
    ecg_hr_notify_beat(5040U, &hr);
    CHECK(!hr.valid);
    CHECK(hr.rejected >= 1U);
    CHECK_EQ(hr.state, HR_ACQUIRING);
}

static void test_reading_expires_when_beats_stop(void)
{
    ecg_hr_t hr;
    uint32_t i;

    CTEST_CASE("a stale reading is dropped rather than left on screen");
    ecg_hr_reset(60U, 100U);
    for (i = 0U; i < 8U; i++) {
        ecg_hr_tick(1000U * i, &hr);
        ecg_hr_notify_beat(1000U * i, &hr);
    }
    CHECK(hr.valid);
    /* Five seconds of silence, no notify at all. */
    for (i = 0U; i < 10U; i++) {
        ecg_hr_tick(8000U + 500U * i, &hr);
    }
    CHECK(!hr.valid);
    CHECK_EQ(hr.state, HR_INVALID);
    CHECK_EQ(hr.bpm, 0U);
}

static int64_t display_abs_sum_at(double freq_hz, ecg_notch_t notch, uint32_t skip)
{
    ecg_sample_t s;
    int64_t sum = 0;
    uint32_t i;
    int64_t peak = 0;

    ecg_signal_reset();
    ecg_signal_set_notch(notch);
    for (i = 0U; i < 3000U; i++) {
        double t = (double)i / 1000.0;
        uint16_t code = (uint16_t)(MID_RAIL + 400.0 * sin(2.0 * M_PI * freq_hz * t));
        ecg_signal_process(code, &s);
        if (i >= skip) {
            int64_t a = s.display;
            if (a < 0) { a = -a; }
            sum += a;
            if (a > peak) { peak = a; }
        }
    }
    (void)peak;
    return (int64_t)(sum / (int64_t)(3000U - skip));
}

static void test_notch_is_a_measured_comb_null(void)
{
    int64_t at50_on, at50_off, at10_on;

    CTEST_CASE("the 50 Hz comb nulls 50 Hz but leaves 10 Hz alone");
    at50_on  = display_abs_sum_at(50.0, ECG_NOTCH_50HZ, 2000U);
    at50_off = display_abs_sum_at(50.0, ECG_NOTCH_OFF, 2000U);
    at10_on  = display_abs_sum_at(10.0, ECG_NOTCH_50HZ, 2000U);

    printf("      mean|display|  50Hz+notch=%lld  50Hz no-notch=%lld  10Hz+notch=%lld\n",
           (long long)at50_on, (long long)at50_off, (long long)at10_on);

    /* The comb is a 20-point moving average, so its null at FS/20 = 50 Hz is
     * exact by construction and 50 Hz must collapse to estimator residue. */
    CHECK(at50_off > 100);                       /* signal really is present */
    CHECK(at50_on < at50_off / 10);              /* and really is removed */
    CHECK(at10_on > at50_off / 2);               /* in-band QRS content kept */
    CHECK_EQ(ecg_signal_get_notch(), ECG_NOTCH_50HZ);

    CTEST_CASE("selecting OFF is not the same as selecting 50 Hz");
    ecg_signal_reset();
    ecg_signal_set_notch(ECG_NOTCH_OFF);
    CHECK_EQ(ecg_signal_get_notch(), ECG_NOTCH_OFF);
}

static void test_ecg_ms_macros(void)
{
    CTEST_CASE("time conversions at 1 kHz are exact for the values used");
    CHECK_EQ(ECG_MS_TO_SAMPLES(120U), 120U);
    CHECK_EQ(ECG_MS_TO_SAMPLES(250U), 250U);
    CHECK_EQ(ECG_SAMPLES_TO_MS(1000U), 1000U);
    CHECK_EQ(ECG_RR_MIN_SAMPLES, 272U);   /* 60000/220 bpm */
    CHECK(ECG_RR_MAX_SAMPLES == 2000U);   /* 60000/30 bpm */
    CHECK_EQ(ECG_RAW_TO_MV(4095U), VDDA_MV);
    CHECK_EQ(ECG_RAW_TO_MV(0U), 0);
}

CTEST_MAIN("ecg pipeline")
{
    s_lcg = 20260918U;
    test_heart_rate_accuracy();
    test_raw_is_never_filtered();
    test_acquiring_before_confident();
    test_flatline_is_signal_poor_not_disconnected();
    test_rail_input_is_saturated();
    test_implausible_rr_is_rejected();
    test_reading_expires_when_beats_stop();
    test_notch_is_a_measured_comb_null();
    test_ecg_ms_macros();
}
