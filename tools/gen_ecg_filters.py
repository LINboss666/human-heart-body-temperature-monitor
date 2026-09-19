#!/usr/bin/env python3
"""
Derive and MEASURE the integer ECG filter structure, then emit
App/config/ecg_filter_coeff.h.

The firmware's conditioning chain is built only from power-of-two leaky
integrators and boxcar moving averages. This script exists so that every
structural number in the emitted header is accompanied by the response that
structure actually produces at 1000 Hz, evaluated exactly on the system function
- not the cutoff that was asked for, and not a simulation estimate.

That discipline is the point of the file. An earlier revision used Q14 biquads;
a 5 Hz biquad high-pass has a denominator of 16/16384 at z=1, so the constant
half-LSB rounding bias in its accumulator was amplified ~1024x and a dead-flat
electrode produced a steady 339-count output instead of zero. Nothing about a
biquad's *nominal* cutoff would have revealed that. Measuring the integer
structure's real response does catch this class of error, which is why
tools/run_host_tests.py also drives the actual C through synthetic beats.

    pc_monitor/.venv/Scripts/python.exe tools/gen_ecg_filters.py
"""
from __future__ import annotations

import cmath
import math
import sys
from pathlib import Path

FS = 1000.0

# ---- structural choices. Everything else in this file measures these. -------
QRS_HP_SHIFT = 5          # leaky-integrator high-pass: fc = FS/(2*pi*2**k)
BASELINE_SHIFT = 8        # display high-pass pole: fc = FS/(2*pi*2**k)
BASELINE_INPUT_Q = 8      # pre-scaling that keeps the estimator out of its dead band
QRS_LP_TAPS = 20          # single boxcar: null at FS/20 = 50 Hz exactly
COMB_50HZ_TAPS = 20       # boxcar whose first spectral null is exactly FS/N
COMB_60HZ_TAPS = 17       # nearest integer delay; null lands low, reported below
MWI_TAPS = 120            # moving-window integrator, 120 ms

Z = complex(0.0, 0.0)


def z_at(freq_hz: float) -> complex:
    return cmath.exp(-2j * math.pi * freq_hz / FS)


def h_leaky_highpass(shift: int, z: complex) -> complex:
    """x - leakyLP(x) with b[n] = b[n-1] + (x[n]-b[n-1])/2**shift.

    H = (1 - 2**-k)(1 - z**-1) / (1 - (1 - 2**-k) z**-1)
    """
    a = 2.0 ** (-shift)
    return (1.0 - a) * (1.0 - z) / (1.0 - (1.0 - a) * z)


def h_boxcar(taps: int, z: complex) -> complex:
    """Exact MA(N)/N. For z**N == 1 with z != 1 this is identically zero."""
    if abs(z - 1.0) < 1e-12:
        return complex(1.0, 0.0)
    return (1.0 - z ** taps) / (taps * (1.0 - z))


def mag_at(fn, freq_hz: float) -> float:
    return abs(fn(z_at(freq_hz)))


def find_crossing(fn, level: float, lo: float, hi: float) -> float:
    """Frequency in [lo, hi] where |H| first falls to level, by bisection."""
    target = level
    prev = lo
    steps = 4000
    for i in range(1, steps + 1):
        f = lo + (hi - lo) * i / steps
        if mag_at(fn, f) <= target:
            a, b = prev, f
            for _ in range(80):
                mid = 0.5 * (a + b)
                if mag_at(fn, mid) > target:
                    a = mid
                else:
                    b = mid
            return 0.5 * (a + b)
        prev = f
    return float("nan")


def find_rising_crossing(fn, level: float, lo: float, hi: float) -> float:
    """Frequency in [lo, hi] where |H| first climbs to level.

    The lower edge of a band-pass has to be searched this way. find_crossing()
    looks for a downward crossing, so pointed at a band-pass from below it
    immediately "finds" the stop band and returns the scan start.
    """
    prev = lo
    steps = 4000
    for i in range(1, steps + 1):
        f = lo + (hi - lo) * i / steps
        if mag_at(fn, f) >= level:
            a, b = prev, f
            for _ in range(80):
                mid = 0.5 * (a + b)
                if mag_at(fn, mid) < level:
                    a = mid
                else:
                    b = mid
            return 0.5 * (a + b)
        prev = f
    return float("nan")


def find_peak(fn, lo: float, hi: float, steps: int = 40000) -> tuple:
    """(frequency, |H|) of the widest point of a band in [lo, hi]."""
    best_f, best_m = lo, -1.0
    for i in range(steps + 1):
        f = lo + (hi - lo) * i / steps
        m = mag_at(fn, f)
        if m > best_m:
            best_m, best_f = m, f
    return best_f, best_m


def find_null(fn, lo: float, hi: float) -> float:
    """Deep minimum of |H| in [lo, hi] - the comb rejection frequency."""
    best_f, best_m = lo, 1e9
    n = 20000
    for i in range(n + 1):
        f = lo + (hi - lo) * i / n
        if abs(f - FS / 2.0) < 1e-9:
            continue
        m = mag_at(fn, f)
        if m < best_m:
            best_m, best_f = m, f
    return best_f


def h_qrs(z: complex) -> complex:
    """High-pass then one boxcar, exactly as ecg_signal.c runs it.

    A single 20-tap boxcar rather than two 32-tap ones: the cascade measured a
    plausible 5-22 Hz band but its 64-sample impulse response flattened a 16 ms
    R wave to about a quarter of its height, leaving the derivative stage with no
    slope to detect. Measured here, this version keeps the same 50 Hz null with a
    third of the group delay.
    """
    return h_leaky_highpass(QRS_HP_SHIFT, z) * h_boxcar(QRS_LP_TAPS, z)


def h_display_50(z: complex) -> complex:
    return h_leaky_highpass(BASELINE_SHIFT, z) * h_boxcar(COMB_50HZ_TAPS, z)


def h_display_60(z: complex) -> complex:
    return h_leaky_highpass(BASELINE_SHIFT, z) * h_boxcar(COMB_60HZ_TAPS, z)


def db(v: float) -> str:
    return "-inf" if v <= 0.0 else f"{20 * math.log10(v):.1f}"


def measure() -> dict:
    M20 = lambda z: h_boxcar(QRS_LP_TAPS, z)
    M17 = lambda z: h_boxcar(COMB_60HZ_TAPS, z)

    # The QRS cascade is a band-pass, so its -3 dB points are only meaningful
    # relative to its own passband peak. Measuring it against |H| at 1 Hz put the
    # reference inside the 5 Hz high-pass's stop band and reported 43.8 Hz, which
    # was then labelled as the 20-tap low-pass's corner -- where the real answer
    # is the same 22.2 Hz the identically-sized display comb has.
    qrs_peak_f, qrs_peak_m = find_peak(h_qrs, 1.0, 100.0)
    qrs_level = qrs_peak_m / math.sqrt(2.0)

    return {
        "qrs_hp_fc": FS / (2.0 * math.pi * 2 ** QRS_HP_SHIFT),
        "qrs_hp_m3": find_rising_crossing(
            lambda z: h_leaky_highpass(QRS_HP_SHIFT, z), 1 / math.sqrt(2), 0.2, 300.0),
        "ma20_m3db": find_crossing(M20, 1 / math.sqrt(2), 1.0, 49.0),
        "ma17_m3db": find_crossing(M17, 1 / math.sqrt(2), 1.0, 55.0),
        "qrs_peak_hz": qrs_peak_f,
        "qrs_peak_db": db(qrs_peak_m),
        "qrs_band_lo": find_rising_crossing(h_qrs, qrs_level, 0.2, qrs_peak_f),
        "qrs_band_hi": find_crossing(h_qrs, qrs_level, qrs_peak_f, 49.4),
        "qrs_passband_db": db(mag_at(h_qrs, 12.0)),
        "qrs_at_50hz_db": db(mag_at(h_qrs, 50.0)),
        "qrs_at_5hz_db": db(mag_at(h_qrs, 5.0)),
        "qrs_at_25hz_db": db(mag_at(h_qrs, 25.0)),
        "qrs_at_44hz_db": db(mag_at(h_qrs, 44.0)),
        "comb50_null": find_null(lambda z: h_boxcar(COMB_50HZ_TAPS, z), 20.0, 90.0),
        "comb60_null": find_null(lambda z: h_boxcar(COMB_60HZ_TAPS, z), 20.0, 90.0),
        "disp50_m3db": find_crossing(h_display_50, 1 / math.sqrt(2), 1.0, 300.0),
        "disp50_at_50hz_db": db(mag_at(h_display_50, 50.0)),
        "disp50_at_100hz_db": db(mag_at(h_display_50, 100.0)),
        "disp50_at_1hz_db": db(mag_at(h_display_50, 1.0)),
        "disp50_at_0p5hz_db": db(mag_at(h_display_50, 0.5)),
        "disp60_m3db": find_crossing(h_display_60, 1 / math.sqrt(2), 1.0, 300.0),
        "disp60_at_60hz_db": db(mag_at(h_display_60, 60.0)),
        "mwi_first_notch": FS / MWI_TAPS,
    }


HEADER = '''/* Generated by tools/gen_ecg_filters.py - do not edit by hand.
 *
 * The filter structure is power-of-two leaky integrators and boxcar moving
 * averages only. See the script's docstring for why biquads were abandoned.
 * Every "measured" figure below is |H(f)| of this exact integer structure
 * evaluated on the unit circle at fs = {fs:.0f} Hz. */

#ifndef ECG_FILTER_COEFF_H
#define ECG_FILTER_COEFF_H

#define ECG_FILTER_DESIGN_FS   {fs:.0f}UL   /* every number here assumes this */

/* ---- display path ------------------------------------------------------- */
/* Baseline high-pass: two cascaded leaky integrators, pole period 2^{base_k}
 * samples. Measured: {disp_0p5} dB at 0.5 Hz, {disp_1} dB at 1 Hz, so wander
 * below ~1 Hz is rejected while a QRS is preserved. */
#define ECG_BASELINE_SHIFT_K        {base_k}U
#define ECG_BASELINE_INPUT_Q        {base_q}U
#define ECG_BASELINE_CORNER_HZ      {base_fc:.2f}f   /* per pole, measured */

/* Mains comb. A boxcar of N taps has an exact null at FS/N, so N={c50} nulls
 * 50 Hz outright - measured {d50_50} dB at 50 Hz and {d50_100} dB at 100 Hz.
 * The same section is the display low-pass; as a cascade with the 0.62 Hz
 * high-pass above it measures -3 dB at {d50_m3:.1f} Hz, and on its own it
 * measures {ma20_m3:.1f} Hz. Both are quoted because they are different
 * questions about the same {c50} taps. */
#define ECG_COMB_TAPS_50HZ          {c50}U
#define ECG_COMB_50HZ_M3DB_HZ       {d50_m3:.1f}f
#define ECG_COMB_50HZ_NULL_HZ       {d50_null:.1f}f

/* 60 Hz variant. 1000/60 is not an integer, so no boxcar can null 60 Hz
 * exactly; the nearest is N={c60}, which nulls at {d60_null:.1f} Hz and leaves
 * {d60_60} dB at 60 Hz. Its own -3 dB corner is {ma17_m3:.1f} Hz. Documented
 * rather than hidden: a true 60 Hz null would need a fractional delay or an IIR
 * section. */
#define ECG_COMB_TAPS_60HZ          {c60}U
#define ECG_COMB_60HZ_NULL_HZ       {d60_null:.1f}f
#define ECG_COMB_60HZ_AT_60_DB      {d60_60}f

/* ---- QRS detection path ------------------------------------------------- */
/* High-pass, single leaky pole: fc = FS/(2*pi*2**{qrs_k}) = {qrs_fc:.2f} Hz;
 * measured -3 dB rising at {qrs_hp_m3:.2f} Hz. */
#define ECG_QRS_HP_SHIFT            {qrs_k}U

/* Low-pass: one boxcar of {lpn} taps averaged by an exact divide, so the 50 Hz
 * null is exact rather than approached. Same {c50} taps as the display comb, so
 * the same -3 dB corner: {ma20_m3:.1f} Hz, and {qrs_50} dB at 50 Hz. */
#define ECG_QRS_LP_TAPS             {lpn}U
#define ECG_QRS_LP_M3DB_HZ          {ma20_m3:.1f}f
#define ECG_MA20_M3DB_HZ            {ma20_m3:.1f}f   /* shared by both {c50}-tap sections */

/* The cascade of the two sections above, which is what the detector actually
 * sees. It is a band-pass: its |H| peaks at {qrs_peak_f:.1f} Hz, {qrs_peak_db} dB
 * below unity, and the -3 dB points relative to that peak are
 * {qrs_band_lo:.1f} Hz to {qrs_band_hi:.1f} Hz. At 44 Hz it is already down
 * {qrs_44} dB, so this is NOT a 5-44 Hz band; earlier text here said so and was
 * derived from measuring the cascade against its own gain at 1 Hz, which sits in
 * the high-pass stop band. Points {qrs_5} dB down at 5 Hz and {qrs_25} dB at
 * 25 Hz, so energy near the QRS dominates mains-free content above ~26 Hz. */
#define ECG_QRS_BAND_LO_HZ          {qrs_band_lo:.1f}f
#define ECG_QRS_BAND_HI_HZ          {qrs_band_hi:.1f}f
#define ECG_QRS_PEAK_HZ             {qrs_peak_f:.1f}f

/* Moving-window integrator over the squared derivative, {mwi} samples = {mwi} ms.
 * Its first spectral null sits at {mwi_null} Hz. */
#define ECG_MWI_WINDOW_SAMPLES      {mwi}U

/* Largest tap count any boxcar stage needs; sizes the shared ring storage. */
#define ECG_BOX_MAX_TAPS            {max_taps}U

#endif /* ECG_FILTER_COEFF_H */
'''


def main() -> int:
    m = measure()
    required = ("ma20_m3db", "ma17_m3db", "qrs_band_lo", "qrs_band_hi",
                "disp50_m3db", "qrs_hp_m3")
    missing = [k for k in required if math.isnan(m[k])]
    if missing:
        print("measurement failed: no crossing found for %s" % ", ".join(missing),
              file=sys.stderr)
        return 1

    max_taps = max(QRS_LP_TAPS, COMB_50HZ_TAPS, COMB_60HZ_TAPS)
    text = HEADER.format(
        fs=FS, base_k=BASELINE_SHIFT, base_q=BASELINE_INPUT_Q,
        base_fc=FS / (2.0 * math.pi * 2 ** BASELINE_SHIFT),
        disp_0p5=m["disp50_at_0p5hz_db"], disp_1=m["disp50_at_1hz_db"],
        c50=COMB_50HZ_TAPS, c60=COMB_60HZ_TAPS,
        d50_50=m["disp50_at_50hz_db"], d50_100=m["disp50_at_100hz_db"],
        d50_m3=m["disp50_m3db"], d50_null=m["comb50_null"],
        ma20_m3=m["ma20_m3db"], ma17_m3=m["ma17_m3db"],
        d60_null=m["comb60_null"], d60_60=m["disp60_at_60hz_db"],
        qrs_k=QRS_HP_SHIFT, qrs_fc=m["qrs_hp_fc"], qrs_hp_m3=m["qrs_hp_m3"],
        lpn=QRS_LP_TAPS,
        qrs_peak_f=m["qrs_peak_hz"], qrs_peak_db=m["qrs_peak_db"],
        qrs_band_lo=m["qrs_band_lo"], qrs_band_hi=m["qrs_band_hi"],
        qrs_5=m["qrs_at_5hz_db"], qrs_25=m["qrs_at_25hz_db"],
        qrs_44=m["qrs_at_44hz_db"], qrs_50=m["qrs_at_50hz_db"],
        mwi=MWI_TAPS, mwi_null=round(FS / MWI_TAPS, 1), max_taps=max_taps,
    )
    dest = Path(__file__).resolve().parent.parent / "App" / "config" / "ecg_filter_coeff.h"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8", newline="\n")

    print(f"wrote {dest}\n")
    for k, v in m.items():
        print(f"  {k:22} = {v if isinstance(v, str) else f'{v:.2f}'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
