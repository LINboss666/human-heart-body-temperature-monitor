/*
 * The same temperature module compiled against the linear model, so the
 * calibration branch is genuinely executed rather than left as code that
 * happens to compile. Without this, the "just swap the calibration config"
 * claim in temperature_calibration.h would be untested.
 *
 * The placeholder slope and intercept in that header describe no real part, so
 * what is asserted here is the shape of the contract - monotonicity, the
 * out-of-range rejection, and that a valid conversion flips the state off
 * UNCALIBRATED - not any particular number of degrees.
 */
#define TEMP_SENSOR_MODEL TEMP_MODEL_LINEAR_MV

#include <stdint.h>

#include "ctest.h"

#include "temperature/temperature.h"

static void settle(uint16_t code)
{
    uint32_t i;
    for (i = 0U; i < TEMP_AVERAGE_WINDOW; i++) {
        temperature_feed(code);
    }
}

static void test_calibrated_flag_and_validity(void)
{
    temperature_t t;

    CTEST_CASE("a configured model stops reporting UNCALIBRATED");
    temperature_init();
    CHECK(TEMP_SENSOR_MODEL != TEMP_MODEL_UNCALIBRATED);
    settle(2000U);
    temperature_get(&t);
    CHECK(t.calibrated);
    CHECK(t.valid);
    CHECK(t.state != TEMP_UNCALIBRATED);
}

static void test_conversion_is_monotonic(void)
{
    temperature_t a, b, c;

    CTEST_CASE("a rising pin voltage does not produce a falling temperature");
    temperature_init();
    settle(1800U);
    temperature_get(&a);
    settle(2200U);
    temperature_get(&b);
    settle(2600U);
    temperature_get(&c);

    /* The placeholder slope is positive, so the mapping must be increasing
     * wherever both readings are inside the accepted window. */
    if (a.valid && b.valid) { CHECK(b.centi_c > a.centi_c); }
    if (b.valid && c.valid) { CHECK(c.centi_c > b.centi_c); }
}

static void test_out_of_range_is_rejected_not_clamped(void)
{
    temperature_t t;

    CTEST_CASE("a code implying an impossible body temperature is refused");
    temperature_init();
    /* Zero volts at the pin is far below the plausible window. */
    settle(0U);
    temperature_get(&t);
    CHECK(!t.valid);
    CHECK_EQ(t.centi_c, 0);
    CHECK_EQ(t.state, TEMP_PROBE_FAULT);

    /* Full scale likewise. Reported as unusable rather than clipped into range,
     * because a clipped value looks exactly like a measurement. */
    settle(4095U);
    temperature_get(&t);
    CHECK(!t.valid);
}

static void test_fault_overrides_a_valid_conversion(void)
{
    temperature_t t;
    uint32_t i;

    CTEST_CASE("a latched probe fault is not reported as a temperature");
    temperature_init();
    for (i = 0U; i < (uint32_t)TEMP_PROBE_FAULT_CONFIRM * TEMP_AVERAGE_WINDOW; i++) {
        temperature_feed(TEMP_ADC_OPEN_THRESHOLD);
    }
    temperature_get(&t);
    CHECK(t.probe_fault);
    CHECK(!t.valid);
    CHECK_EQ(t.state, TEMP_PROBE_FAULT);
}

CTEST_MAIN("temperature (linear model)")
{
    test_calibrated_flag_and_validity();
    test_conversion_is_monotonic();
    test_out_of_range_is_rejected_not_clamped();
    test_fault_overrides_a_valid_conversion();
}
