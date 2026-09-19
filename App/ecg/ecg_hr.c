#include "ecg_hr.h"

/*
 * Why a median and not an average or the last interval: one missed or spurious
 * beat moves a single element of the window, so the median of a handful of
 * recent intervals is stable where a mean is not, and it stays responsive where
 * a long average would lag behind a real change in rate.
 */
static uint32_t  s_rr[HR_MEDIAN_WINDOW_ODD];
static uint8_t   s_rr_count;
static uint8_t   s_rr_next;
static uint32_t  s_last_beat_index;
static bool      s_have_last;
static uint16_t  s_low_bpm;
static uint16_t  s_high_bpm;
static ecg_hr_t  s_current;

static uint32_t rr_from_index(uint32_t a, uint32_t b)
{
    return (b > a) ? (b - a) : 0U;
}

/* Insertion sort of at most five u32s; cheaper and smaller than qsort, and it
 * has no library dependency at all. */
static uint32_t median_rr(void)
{
    uint32_t v[HR_MEDIAN_WINDOW_ODD];
    uint8_t  n = s_rr_count;
    uint8_t  i;
    uint8_t  j;

    for (i = 0U; i < n; i++) {
        uint32_t key = s_rr[i];
        for (j = i; j > 0U && v[j - 1U] > key; j--) {
            v[j] = v[j - 1U];
        }
        v[j] = key;
    }
    return v[n / 2U];
}

/* Samples with no accepted beat after which the reading is declared stale.
 * Three seconds is long enough to survive a deep breath or a single dropped
 * beat, short enough that a detached electrode does not keep showing a rate. */
#define HR_STALE_SAMPLES  ECG_MS_TO_SAMPLES(3000U)

void ecg_hr_reset(uint16_t low_bpm, uint16_t high_bpm)
{
    s_rr_count = 0U;
    s_rr_next = 0U;
    s_have_last = false;
    s_last_beat_index = 0U;
    s_low_bpm = low_bpm;
    s_high_bpm = high_bpm;

    s_current.bpm = 0U;
    s_current.state = HR_INVALID;
    s_current.rr_ms = 0U;
    s_current.valid = false;
    s_current.beats = 0U;
    s_current.rejected = 0U;
}

void ecg_hr_set_bands(uint16_t low_bpm, uint16_t high_bpm)
{
    s_low_bpm = low_bpm;
    s_high_bpm = high_bpm;
}

const ecg_hr_t *ecg_hr_current(void)
{
    return &s_current;
}

static void publish(uint32_t rr_samples)
{
    uint32_t ms;
    uint32_t bpm;

    if (rr_samples == 0U) {
        return;
    }
    ms = ECG_SAMPLES_TO_MS(rr_samples);
    if (ms == 0U) {
        return;
    }
    bpm = 60000U / ms;
    if (bpm > 255U) {
        bpm = 255U;
    }

    s_current.bpm = (uint8_t)bpm;
    s_current.rr_ms = (uint16_t)ms;
    s_current.valid = true;
    if (bpm < s_low_bpm) {
        s_current.state = HR_LOW;
    } else if (bpm > s_high_bpm) {
        s_current.state = HR_HIGH;
    } else {
        s_current.state = HR_NORMAL;
    }
}

static void go_invalid(void)
{
    s_current.state = HR_INVALID;
    s_current.valid = false;
    s_current.bpm = 0U;
    s_current.rr_ms = 0U;
}

void ecg_hr_tick(uint32_t sample_index, ecg_hr_t *out)
{

    if (s_current.beats == 0U) {
        /* Nothing has ever been detected: showing ACQUIRING here would already be
         * a promise that a number is coming. */
        go_invalid();
        *out = s_current;
        return;
    }

    /* Beats stopped arriving. The reading is dropped rather than left frozen on
     * screen, and it is NOT downgraded to ACQUIRING - that state means "beats are
     * coming in but the window is not full yet", which would be a lie here. */
    if ((sample_index - s_last_beat_index) > HR_STALE_SAMPLES) {
        s_rr_count = 0U;
        s_rr_next = 0U;
        s_have_last = false;
        go_invalid();
        *out = s_current;
        return;
    }

    if (s_rr_count >= HR_MEDIAN_WINDOW_ODD) {
        publish(median_rr());
    } else {
        s_current.state = HR_ACQUIRING;
        s_current.valid = false;
        s_current.bpm = 0U;
    }
    *out = s_current;
}

void ecg_hr_notify_beat(uint32_t sample_index, ecg_hr_t *out)
{
    uint32_t rr;

    s_current.beats++;

    if (!s_have_last) {
        s_have_last = true;
        s_last_beat_index = sample_index;
        s_current.state = HR_ACQUIRING;
        s_current.valid = false;
        *out = s_current;
        return;
    }

    rr = rr_from_index(s_last_beat_index, sample_index);
    s_last_beat_index = sample_index;

    if (rr < ECG_RR_MIN_SAMPLES || rr > ECG_RR_MAX_SAMPLES) {
        /* Implausible interval: count it, drop it, and stop reporting a number
         * until the window refills rather than interpolating across the gap. */
        s_current.rejected++;
        s_rr_count = 0U;
        s_rr_next = 0U;
        go_invalid();
        s_current.state = HR_ACQUIRING;
        *out = s_current;
        return;
    }

    s_rr[s_rr_next] = rr;
    s_rr_next = (uint8_t)((s_rr_next + 1U) % HR_MEDIAN_WINDOW_ODD);
    if (s_rr_count < HR_MEDIAN_WINDOW_ODD) {
        s_rr_count++;
    }

    if (s_rr_count >= HR_ACQUIRING_MIN_BEATS) {
        publish(median_rr());
    } else {
        s_current.state = HR_ACQUIRING;
        s_current.valid = false;
    }
    *out = s_current;
}
