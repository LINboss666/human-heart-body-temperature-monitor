/**
 * ctest.h - the smallest useful assertion harness for host-side C tests.
 *
 * Deliberately hand-rolled rather than downloaded: the build has to work with
 * nothing but a C compiler and the standard library, so the firmware's pure
 * modules can be exercised on a PC without a target board or a package manager.
 *
 * Usage in a test file:
 *
 *     static void test_something(void) { CTEST_CASE("..."); CHECK(...); }
 *     static void ctest_run_all(void)  { test_something(); }
 *     CTEST_MAIN("suite name")
 */
#ifndef CTEST_H
#define CTEST_H

#include <stdio.h>
#include <string.h>

static int ctest_failures = 0;
static int ctest_checks = 0;
static const char *ctest_current = "none";

#define CTEST_CASE(name)                          \
    do {                                          \
        ctest_current = (name);                   \
        printf("  - %s\n", ctest_current);        \
    } while (0)

#define CHECK(cond)                                               \
    do {                                                          \
        ctest_checks++;                                           \
        if (!(cond)) {                                            \
            ctest_failures++;                                     \
            printf("    FAIL %s:%d [%s] %s\n", __FILE__,          \
                   __LINE__, ctest_current, #cond);               \
        }                                                         \
    } while (0)

#define CHECK_EQ(actual, expected)                                            \
    do {                                                                      \
        long _a = (long)(actual);                                             \
        long _e = (long)(expected);                                           \
        ctest_checks++;                                                       \
        if (_a != _e) {                                                       \
            ctest_failures++;                                                 \
            printf("    FAIL %s:%d [%s] %s == %s  (%ld != %ld)\n",            \
                   __FILE__, __LINE__, ctest_current, #actual, #expected,     \
                   _a, _e);                                                   \
        }                                                                     \
    } while (0)

#define CTEST_NEAR(actual, expected, tol)                                     \
    do {                                                                      \
        double _a = (double)(actual);                                         \
        double _e = (double)(expected);                                       \
        double _d = _a - _e;                                                  \
        ctest_checks++;                                                       \
        if (_d < 0.0) { _d = -_d; }                                           \
        if (_d > (double)(tol)) {                                             \
            ctest_failures++;                                                 \
            printf("    FAIL %s:%d [%s] %s ~= %s  (%.6f vs %.6f, tol %g)\n",  \
                   __FILE__, __LINE__, ctest_current, #actual, #expected,      \
                   _a, _e, (double)(tol));                                    \
        }                                                                     \
    } while (0)

#define CTEST_MAIN(suite)                                        \
    static void ctest_run_all(void);                             \
    int main(void)                                               \
    {                                                            \
        ctest_run_all();                                         \
        printf("%s: %d checks, %d failed -> %s\n", (suite),      \
               ctest_checks, ctest_failures,                     \
               ctest_failures == 0 ? "PASS" : "FAIL");           \
        return ctest_failures == 0 ? 0 : 1;                      \
    }                                                            \
    static void ctest_run_all(void)

#endif /* CTEST_H */
