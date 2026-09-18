/*
 * Host tests for the temperature service as shipped: uncalibrated.
 *
 * The behaviour under test is mostly about what the module must NOT do. The
 * front-end is another member's design and is not in this repository, so the
 * dangerous failure is not a wrong number, it is a plausible number - a display
 * reading 36.6 that came from nobody knows what. These tests pin the honest
 * states down so a later "improvement" cannot quietly remove them.
 */
#include <stdint.h>
#include <string.h>

#include "ctest.h"

#include "temperature/temperature.h"

static void fill(uint16_t code, uint32_t windows)
{
    uint32_t i;
    for (i = 0U; i < TEMP_AVERAGE_WINDOW * windows; i++) {
        temperature_feed(code);
    }
}

static void test_uncalibrated_never_invents_a_temperature(void)
{
    temperature_t t;

    CTEST_CASE("shipping default reports UNCALIBRATED and no value");
    temperature_init();
    fill(2000U, 3U);
    temperature_get(&t);

    CHECK_EQ(t.state, TEMP_UNCALIBRATED);
    CHECK(!t.valid);
    CHECK(!t.calibrated);
    /* The raw observation is still there, which is what makes calibration
     * possible later without re-running the experiment. */
    CHECK_EQ(t.raw, 2000U);
    CHECK_EQ(t.mv, (uint16_t)ADC_RAW_TO_MV(2000U));
    CHECK_EQ(t.centi_c, 0);
    CHECK(t.updates >= 3U);
}

static void test_decimation_cadence(void)
{
    temperature_t t;

    CTEST_CASE("one update per averaging window, not per sample");
    temperature_init();
    fill(1500U, 1U);
    temperature_get(&t);
    CHECK_EQ(t.updates, 1U);
    /* The sample before the window completes must not produce an update. */
    temperature_feed(1500U);
    temperature_get(&t);
    CHECK_EQ(t.updates, 1U);
    fill(1500U, 1U);
    temperature_get(&t);
    CHECK_EQ(t.updates, 2U);
}

static void test_averaging_reduces_noise(void)
{
    temperature_t t;
    uint32_t i;
    int32_t sum = 0;

    CTEST_CASE("alternating codes average rather than latch onto one");
    temperature_init();
    for (i = 0U; i < TEMP_AVERAGE_WINDOW; i++) {
        uint16_t c = ((i & 1U) == 0U) ? 1000U : 1200U;
        sum += (int32_t)c;
        temperature_feed(c);
    }
    temperature_get(&t);
    CHECK_EQ(t.raw, (uint16_t)(sum / (int32_t)TEMP_AVERAGE_WINDOW));
}

static void test_probe_fault_is_latched_and_clears(void)
{
    temperature_t t;

    CTEST_CASE("a rail-high input eventually flags a probe fault");
    temperature_init();
    fill(TEMP_ADC_OPEN_THRESHOLD, 1U);
    temperature_get(&t);
    CHECK(!t.probe_fault);            /* one window is not enough to conclude */
    fill(TEMP_ADC_OPEN_THRESHOLD, TEMP_PROBE_FAULT_CONFIRM);
    temperature_get(&t);
    CHECK(t.probe_fault);
    /* Still uncalibrated, so the state is not mislabelled as a probe reading. */
    CHECK_EQ(t.state, TEMP_UNCALIBRATED);
    CHECK(!t.valid);

    CTEST_CASE("returning to range clears the fault after the confirm window");
    fill(2000U, TEMP_PROBE_OK_CONFIRM);
    temperature_get(&t);
    CHECK(!t.probe_fault);
}

static void test_alarm_bands_are_validated(void)
{
    int16_t lo = 0, hi = 0;

    CTEST_CASE("a reversed or out-of-range band is refused");
    temperature_init();
    temperature_get_alarm_bands(&lo, &hi);
    CHECK(lo < hi);

    temperature_set_alarm_bands(4000, 3000);        /* reversed */
    temperature_get_alarm_bands(&lo, &hi);
    CHECK(lo < hi);

    temperature_set_alarm_bands(2000, 3000);        /* below plausible minimum */
    temperature_get_alarm_bands(&lo, &hi);
    CHECK(lo >= TEMP_CENTI_MIN_VALID);

    temperature_set_alarm_bands(3400, 5000);        /* above plausible maximum */
    temperature_get_alarm_bands(&lo, &hi);
    CHECK(hi <= TEMP_CENTI_MAX_VALID);

    temperature_set_alarm_bands(3500, 3800);        /* the one that is legal */
    temperature_get_alarm_bands(&lo, &hi);
    CHECK_EQ(lo, 3500);
    CHECK_EQ(hi, 3800);
}

static void test_get_is_null_safe(void)
{
    CTEST_CASE("temperature_get(NULL) does not crash");
    temperature_init();
    temperature_get(NULL);
    CHECK(1);
}

CTEST_MAIN("temperature (uncalibrated)")
{
    test_uncalibrated_never_invents_a_temperature();
    test_decimation_cadence();
    test_averaging_reduces_noise();
    test_probe_fault_is_latched_and_clears();
    test_alarm_bands_are_validated();
    test_get_is_null_safe();
}
