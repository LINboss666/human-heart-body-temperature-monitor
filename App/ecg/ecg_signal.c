#include "ecg_signal.h"

#include <string.h>

#include "ecg_filter_coeff.h"

/*
 * All stages here are power-of-two leaky integrators and boxcar moving averages.
 *
 * Why not the biquads that gen_ecg_filters.py originally designed: a Q14
 * direct-form-I biquad with a rounding term on the accumulator is biased, and a
 * high-pass section's denominator at z=1 is deliberately near zero. Measured on
 * a constant input, a 5 Hz Q14 high-pass amplified that constant rounding bias
 * by 16384/16 = 1024x and settled at a steady 339-count output instead of zero,
 * which the energy stage then turned into a permanent stream of false beats on a
 * dead-flat electrode. Cascaded boxcars and shift-based integrators have no
 * recursion to amplify a bias, their DC nulls are exact by construction, and
 * their arithmetic is identical under ARMCC and under the host compiler, so a
 * synthetic-beat pass is a statement about the shipping firmware.
 */

/* --------------------------------------------------------- integer helpers */

/* Truncation toward zero, which C99 defines for every sign. Shifting a negative
 * signed value is only implementation-defined, and these modules are compiled by
 * two different compilers. */
static int32_t idiv_pow2(int32_t v, uint8_t k)
{
    return v / (int32_t)(1L << k);
}

static int32_t clamp_i16(int32_t v)
{
    if (v > 32767) { return 32767; }
    if (v < -32768) { return -32768; }
    return v;
}

/* ------------------------------------------------------------ boxcar stage */

typedef struct {
    int32_t ring[ECG_BOX_MAX_TAPS];
    int32_t sum;
    uint16_t head;
    uint16_t fill;
    uint16_t taps;
    uint8_t  shift;      /* log2(taps) when taps is a power of two, else unused */
    uint8_t  use_div;    /* 1 -> divide by taps (for the 50/60 Hz comb) */
} boxcar_t;

static void box_init(boxcar_t *b, uint16_t taps, uint8_t shift, uint8_t use_div)
{
    memset(b, 0, sizeof(*b));
    b->taps = taps;
    b->shift = shift;
    b->use_div = use_div;
}

static int32_t box_run(boxcar_t *b, int32_t x)
{
    int32_t avg;

    if (b->fill == 0U) {
        b->sum = 0;
    }
    if (b->fill < b->taps) {
        b->fill++;
    } else {
        b->sum -= b->ring[b->head];   /* drop the sample about to be overwritten */
    }
    b->ring[b->head] = x;
    b->sum += x;
    b->head = (uint16_t)((b->head + 1U) % b->taps);

    if (b->use_div != 0U) {
        avg = b->sum / (int32_t)b->fill;   /* exact comb while still filling */
    } else {
        avg = idiv_pow2(b->sum, b->shift);
    }
    return clamp_i16(avg);
}

/* --------------------------------------------------------------- filter state */

/* display path: baseline estimate, then the mains comb */
static int32_t s_base1;
static int32_t s_base2;
static boxcar_t s_comb;

/* QRS path: a second, much faster baseline removal giving the ~5 Hz high-pass,
 * then a single 20-tap boxcar that both smooths and nulls 50 Hz. */
static int32_t s_qbase;
static boxcar_t s_lp_qrs;

static int32_t s_deriv_x1;
static int32_t s_deriv_x2;

/* moving-window integrator over the squared derivative */
static uint16_t s_mwi_ring[ECG_MWI_WINDOW_SAMPLES];
static uint32_t s_mwi_head;
static uint32_t s_mwi_fill;
static int64_t  s_mwi_sum;

/* adaptive detector */
static int32_t  s_energy_floor;
static bool     s_floor_seeded;
static uint32_t s_ref;           /* learned beat amplitude, the threshold basis */
static uint32_t s_quiet_left;    /* samples since the last accepted beat */
static uint32_t s_refractory_left;
static uint32_t s_beat_count;
static uint32_t s_sample_index;
static ecg_notch_t s_notch_mode = ECG_NOTCH_DEFAULT;

/* signal quality, bucketed so the window slides without a 1000-entry ring */
#define QUALITY_BUCKETS         20U
#define QUALITY_BUCKET_SAMPLES  50U
static int16_t  s_q_min[QUALITY_BUCKETS];
static int16_t  s_q_max[QUALITY_BUCKETS];
static uint8_t  s_q_bucket;
static uint8_t  s_q_in_bucket;
static bool     s_q_filled;
static uint16_t s_rail_streak;
static bool     s_saturated;

static void apply_notch_config(void)
{
    switch (s_notch_mode) {
    case ECG_NOTCH_60HZ:
        box_init(&s_comb, ECG_COMB_TAPS_60HZ, 0U, 1U);
        break;
    case ECG_NOTCH_OFF:
        box_init(&s_comb, 1U, 0U, 1U);   /* unity passthrough, same code path */
        break;
    case ECG_NOTCH_50HZ:
    default:
        box_init(&s_comb, ECG_COMB_TAPS_50HZ, 0U, 1U);
        break;
    }
}

void ecg_signal_reset(void)
{
    s_base1 = 0;
    s_base2 = 0;
    s_qbase = 0;
    apply_notch_config();
    box_init(&s_lp_qrs, ECG_QRS_LP_TAPS, 0U, 1U);

    s_deriv_x1 = 0;
    s_deriv_x2 = 0;

    memset(s_mwi_ring, 0, sizeof(s_mwi_ring));
    s_mwi_head = 0U;
    s_mwi_fill = 0U;
    s_mwi_sum = 0;

    /* Adaptive detection state: a long-term energy floor, the reference beat
     * amplitude learned during auto-calibration, and a quiet-run counter. */
    s_energy_floor = 0;
    s_floor_seeded = false;
    s_ref = 0U;
    s_quiet_left = 0U;
    s_refractory_left = 0U;
    s_beat_count = 0U;
    s_sample_index = 0U;

    for (uint8_t i = 0U; i < QUALITY_BUCKETS; i++) {
        s_q_min[i] = INT16_MAX;
        s_q_max[i] = INT16_MIN;
    }
    s_q_bucket = 0U;
    s_q_in_bucket = 0U;
    s_q_filled = false;
    s_rail_streak = 0U;
    s_saturated = false;
}

void ecg_signal_set_notch(ecg_notch_t notch)
{
    if (notch != s_notch_mode) {
        s_notch_mode = notch;
        apply_notch_config();
    }
}

ecg_notch_t ecg_signal_get_notch(void)
{
    return s_notch_mode;
}

/* ------------------------------------------------------------- the stages */

/* Two cascaded leaky integrators at 2^8 samples give ~0.62 Hz per pole, which
 * removes wander without eating a QRS complex. */
static int32_t remove_baseline(int32_t x)
{
    int32_t scaled = x << ECG_BASELINE_INPUT_Q;

    s_base1 += idiv_pow2(scaled - s_base1, ECG_BASELINE_SHIFT_K);
    s_base2 += idiv_pow2(s_base1 - s_base2, ECG_BASELINE_SHIFT_K);
    return x - idiv_pow2(s_base2, ECG_BASELINE_INPUT_Q);
}

/* The QRS high-pass is the same construction with a much shorter time constant:
 * fc = FS / (2*pi*2^ECG_QRS_HP_SHIFT) ~= 5 Hz. */
static int32_t qrs_highpass(int32_t x)
{
    s_qbase += idiv_pow2(x - s_qbase, ECG_QRS_HP_SHIFT);
    return x - s_qbase;
}

static uint32_t integrate_qrs(int32_t bandlimited)
{
    int32_t d;
    int64_t sq;
    uint16_t e;
    int64_t avg;

    d = ((bandlimited << 1) + s_deriv_x1 - s_deriv_x2) >> ECG_DERIV_SHIFT;
    s_deriv_x2 = s_deriv_x1;
    s_deriv_x1 = bandlimited;

    if (d < 0) {
        d = -d;
    }
    sq = ((int64_t)d * d) >> ECG_SQUARE_SHIFT;
    if (sq < 0) {
        sq = 0;
    }
    if (sq > 0xFFFF) {
        sq = 0xFFFF;    /* only reachable on a wildly over-ranged front-end */
    }
    e = (uint16_t)sq;

    s_mwi_sum -= s_mwi_ring[s_mwi_head];
    s_mwi_ring[s_mwi_head] = e;
    s_mwi_sum += e;
    s_mwi_head = (uint32_t)((s_mwi_head + 1U) % ECG_MWI_WINDOW_SAMPLES);
    if (s_mwi_fill < ECG_MWI_WINDOW_SAMPLES) {
        s_mwi_fill++;
    }

    avg = s_mwi_sum / (int64_t)s_mwi_fill;
    return (avg < 0) ? 0U : (uint32_t)avg;
}

/*
 * Self-scaling detection: compare the current excess over the long-term energy
 * floor with a fraction of the largest such excess seen recently. The envelope
 * rises instantly and decays with a fixed time constant, so the threshold
 * follows a patient's actual signal amplitude instead of a hard-coded number -
 * which matters because front-end gain is UNVERIFIED and could be anything.
 */
/*
 * Leaky first-order average with a symmetric, stall-free update.
 *
 * The obvious `acc += (x - acc) >> k` is a trap: once |x - acc| < 2**k the shift
 * yields 0 and the accumulator freezes wherever it happens to be. Measured here,
 * the energy floor latched at 779 during start-up while the true inter-beat
 * energy was 15, so the excess was pinned at zero and no beat could ever be
 * detected - the same defect that had to be fixed in the envelope decay.
 * Rounding the step away from zero guarantees convergence and keeps the time
 * constant for large differences, which is what an EMA is supposed to do.
 */
static int32_t ema_step(int32_t acc, int32_t x, uint8_t k)
{
    int32_t diff = x - acc;
    int32_t mask = (int32_t)1 << k;
    int32_t mag;

    if (diff >= 0) {
        mag = (diff + mask - 1) >> k;
        if (mag > diff) {
            mag = diff;            /* a difference of 1 must move by 1, not 2 */
        }
    } else {
        mag = -(((-diff) + mask - 1) >> k);
        if (mag < diff) {
            mag = diff;
        }
    }
    return acc + mag;
}

static bool detect_qrs(uint32_t energy)
{
    int32_t above;

    /* Freeze the detector while the boxcars fill and the leaky integrators
     * converge from zero. Their start-up excursion is far larger than a QRS, and
     * the envelope below peak-holds, so capturing it would inflate the threshold
     * for as long as it took to decay away. */
    if (s_sample_index < ECG_SETTLE_SAMPLES) {
        return false;
    }
    if (!s_floor_seeded) {
        s_floor_seeded = true;
        s_energy_floor = (int32_t)energy;
    }

    s_energy_floor = ema_step(s_energy_floor, (int32_t)energy, ECG_ENERGY_FLOOR_SHIFT);

    above = (int32_t)energy - s_energy_floor;
    if (above < 0) {
        above = 0;
    }

    if (s_refractory_left > 0U) {
        s_refractory_left--;
        s_quiet_left = 0U;
        return false;
    }

    /* Auto-calibration: learn this signal's own beat amplitude before judging
     * anything against it. Two seconds covers at least one beat at the slowest
     * rate the detector accepts, and it is why no absolute code threshold is
     * needed - the analog front-end gain is unverified, so any fixed count
     * value would be tuned to a circuit nobody has built. */
    if (s_sample_index < ECG_SETTLE_SAMPLES + ECG_CALIBRATE_SAMPLES) {
        if ((uint32_t)above > s_ref) {
            s_ref = (uint32_t)above;
        }
        return false;
    }

    {
        uint32_t thresh = (uint32_t)(((int64_t)s_ref * ECG_THRESH_RATIO_PERCENT) / 100U);
        if (thresh < ECG_THRESH_JITTER_MIN) {
            thresh = ECG_THRESH_JITTER_MIN;
        }

        if ((uint32_t)above > thresh) {
            /* Track the accepted beat so the reference follows a real change in
             * amplitude without chasing a single artefact. */
            s_ref = (uint32_t)ema_step((int32_t)s_ref, above, ECG_REF_TRACK_SHIFT);
            s_beat_count++;
            s_refractory_left = ECG_REFRACTORY_SAMPLES;
            s_quiet_left = 0U;
            return true;
        }

        /* Nothing accepted for a long time: shrink the reference so a genuinely
         * smaller - or newly connected - signal can be found again instead of
         * being masked by one historical loud moment. */
        if (++s_quiet_left >= ECG_QUIET_REACQUIRE_SAMPLES) {
            s_quiet_left = 0U;
            if (s_ref > ECG_THRESH_JITTER_MIN) {
                s_ref /= 2U;
            }
        }
    }
    return false;
}

static void update_quality(int16_t display)
{
    if (s_q_in_bucket == 0U) {
        s_q_min[s_q_bucket] = display;
        s_q_max[s_q_bucket] = display;
    } else {
        if (display < s_q_min[s_q_bucket]) { s_q_min[s_q_bucket] = display; }
        if (display > s_q_max[s_q_bucket]) { s_q_max[s_q_bucket] = display; }
    }
    if (++s_q_in_bucket >= QUALITY_BUCKET_SAMPLES) {
        s_q_in_bucket = 0U;
        s_q_bucket = (uint8_t)((s_q_bucket + 1U) % QUALITY_BUCKETS);
        if (s_q_bucket == 0U) {
            s_q_filled = true;
        }
    }
}

uint16_t ecg_signal_peak_to_peak(void)
{
    int16_t lo = INT16_MAX;
    int16_t hi = INT16_MIN;
    uint8_t limit = s_q_filled ? QUALITY_BUCKETS : s_q_bucket;
    uint8_t i;

    if (limit == 0U) {
        return 0U;
    }
    for (i = 0U; i < limit; i++) {
        if (s_q_min[i] < lo) { lo = s_q_min[i]; }
        if (s_q_max[i] > hi) { hi = s_q_max[i]; }
    }
    return (uint16_t)(hi - lo);
}

lead_state_t ecg_signal_lead_state(void)
{
    /* No lead-off hardware exists in this design, so the firmware can only say
     * the waveform looks unusable. Reporting DISCONNECTED would be inventing a
     * measurement nobody took; reporting CONNECTED would be worse. */
    if (s_saturated || ecg_signal_peak_to_peak() < ECG_FLATLINE_PTP_CODES) {
        return LEAD_SIGNAL_POOR;
    }
    return LEAD_UNKNOWN;
}

bool ecg_signal_saturated(void)
{
    return s_saturated;
}

uint32_t ecg_signal_beat_count(void)
{
    return s_beat_count;
}

/* -------------------------------------------------------------- main entry */

void ecg_signal_process(uint16_t raw_adc_code, ecg_sample_t *out)
{
    int32_t hp;
    int32_t notched;
    int32_t display;
    int32_t qrs;
    uint32_t energy;
    bool r_peak;

    hp = remove_baseline((int32_t)raw_adc_code);

    notched = box_run(&s_comb, hp);
    display = notched;

    /* The QRS branch takes the baseline-removed signal, NOT the display signal.
     * The display comb is simultaneously the 22 Hz low-pass, so branching the
     * detector off it would band-limit the QRS twice and remove the very energy
     * the detector needs. Mains rejection on this branch comes from the QRS
     * bandpass itself, measured at -29 dB at 50 Hz. */
    qrs = box_run(&s_lp_qrs, qrs_highpass(hp));
    energy = integrate_qrs(qrs);
    r_peak = detect_qrs(energy);

    if (raw_adc_code <= ECG_RAIL_LOW_CODE || raw_adc_code >= ECG_RAIL_HIGH_CODE) {
        if (++s_rail_streak >= ECG_MS_TO_SAMPLES(200U)) {
            s_saturated = true;
        }
    } else {
        s_rail_streak = 0U;
        s_saturated = false;
    }

    update_quality((int16_t)display);

    out->raw = raw_adc_code;
    out->mv = (int16_t)ECG_RAW_TO_MV(raw_adc_code);
    out->display = (int16_t)clamp_i16(display);
    out->energy = energy;
    out->r_peak = r_peak;
    out->sample_index = s_sample_index++;
}
