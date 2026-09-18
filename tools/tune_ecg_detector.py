#!/usr/bin/env python3
"""
Host-side parameter search for the QRS detection branch, written as an exact
integer model of App/ecg/ecg_signal.c so the numbers chosen are the numbers the
firmware will execute - not a float approximation of them.

The failure mode this exists to avoid: a band-pass narrow enough to be called
"the QRS band" (5-15 Hz) removes most of the energy of a physiologically sharp
R wave, and the squared-energy path then right-shifts what is left into zero.
Measured here: at LP=15 Hz the integrated energy peaked at 22 against an absolute
floor of 64, i.e. the detector could never fire, and no amount of threshold
fiddling fixes a signal that has been filtered out.
"""
from __future__ import annotations

import math
import sys

FS = 1000.0
Q = 14


def biquad_design(kind: str, fc: float, q: float = math.sqrt(2) / 2):
    w0 = 2.0 * math.pi * fc / FS
    alpha = math.sin(w0) / (2.0 * q)
    cw = math.cos(w0)
    if kind == "hp":
        b = [(1 + cw) / 2, -(1 + cw), (1 + cw) / 2]
    else:
        b = [(1 - cw) / 2, 1 - cw, (1 - cw) / 2]
    a = [1.0, -2.0 * cw, 1.0 - alpha]
    a0 = 1.0 + alpha
    return [round(c / a0 * (1 << Q)) for c in b], [round(c / a0 * (1 << Q)) for c in a[1:]]


def bq_run(st, coef, x):
    b, a = coef
    acc = (b[0] * x + b[1] * st["x1"] + b[2] * st["x2"]
           - a[0] * st["y1"] - a[1] * st["y2"])
    acc += 1 << (Q - 1)
    y = int(acc >> Q) if acc >= 0 else -int((-acc) >> Q)
    st["x2"], st["x1"] = st["x1"], x
    st["y2"], st["y1"] = st["y1"], y
    return y


def new_stage():
    return {"x1": 0, "x2": 0, "y1": 0, "y2": 0}


def bump(t, amp, centre_ms, width_ms):
    d = t * 1000.0 - centre_ms
    return amp * math.exp(-(d * d) / (2.0 * width_ms * width_ms))


def synth(bpm, seconds, wander=True, mains=True, noise_amp=30, seed=1):
    """Same morphology as the C test, so a win here is a win there."""
    n = int(FS * seconds)
    period = 60.0 / bpm
    state = seed
    out = []
    for i in range(n):
        t = i / FS
        ph = t % period
        v = 1650.0
        for shift in (period, 0.0):
            tt = ph - shift
            v += bump(tt, 60.0, -160.0, 45.0)
            v += bump(tt, -55.0, -16.0, 10.0)
            v += bump(tt, 700.0, 0.0, 16.0)      # R widened toward a real QRS
            v += bump(tt, -150.0, 18.0, 13.0)
            v += bump(tt, 160.0, 240.0, 95.0)
        if wander:
            v += 300.0 * math.sin(2 * math.pi * 0.3 * t)
        if mains:
            v += 120.0 * math.sin(2 * math.pi * 50.0 * t)
        state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
        v += ((state >> 16) % (2 * noise_amp + 1)) - noise_amp
        out.append(max(0, min(4095, int(v + 0.5))))
    return out


def baseline_remove(sig, shift=8, inset_q=8):
    a1 = a2 = 0
    out = []
    d = 1 << shift
    for x in sig:
        xs = x << inset_q
        a1 += int((xs - a1) / d) if (xs - a1) >= 0 else -int((-(xs - a1)) / d)
        a2 += int((a1 - a2) / d) if (a1 - a2) >= 0 else -int((-(a1 - a2)) / d)
        est = a2 >> inset_q
        out.append(x - est)
    return out


def pipeline(sig, hp_fc, lp_fc, deriv_shift, square_shift, mwi):
    hp = new_stage()
    lpf = new_stage()
    hp_c = (biquad_design("hp", hp_fc) if hp_fc else None)
    lp_c = (biquad_design("lp", lp_fc) if lp_fc else None)
    x1 = x2 = 0
    ring = [0] * mwi
    head = 0
    fill = 0
    total = 0
    energy = []
    for x in sig:
        v = x
        if hp_c:
            v = bq_run(hp, hp_c, v)
        if lp_c:
            v = bq_run(lpf, lp_c, v)
        d = ((v << 1) + x1 - x2) >> deriv_shift
        x2, x1 = x1, v
        ad = abs(d)
        sq = (ad * ad) >> square_shift
        total -= ring[head]
        ring[head] = sq
        total += sq
        head = (head + 1) % mwi
        fill = min(fill + 1, mwi)
        energy.append(max(0, total // fill))
    return energy


def detect(energy, abs_min, ratio_num=35, ratio_den=100, refractory=250,
           ref_decay_pct=2, history=8, quiet_reset=200):
    """
    Self-calibrating peak detector, integer only.

      floor   = long time constant average of the energy (the noise floor)
      peak    = largest (energy - floor) seen since the last accepted beat
      thresh  = ratio * median of the last few accepted peaks, at least abs_min
      refractory blocks a second acceptance for 250 ms, i.e. caps at 240 bpm

    The earlier design tried to track separate signal and noise estimates with
    right-shifts of signed differences. That is exactly where C's arithmetic
    shift and Python's floor-shift disagree, so a model that "worked" would not
    have proved anything about the firmware. This version keeps every subtraction
    non-negative by construction, so both languages must agree.
    """
    floor = energy[0]
    floor_k = 7                          # time constant 128 samples
    peaks = []
    ref = 0
    refr = 0
    quiet = 0
    peak = 0
    hits = []

    for n, e in enumerate(energy):
        if e > floor:
            floor += (e - floor) >> floor_k
        else:
            floor -= (floor - e) >> floor_k

        above = e - floor
        if above > peak:
            peak = above

        if refr > 0:
            refr -= 1

        if ref == 0:
            thresh = abs_min
        else:
            thresh = (ref * ratio_num) // ratio_den
            if thresh < abs_min:
                thresh = abs_min

        if above > thresh and refr == 0:
            hits.append(n)
            peaks.append(peak)
            if len(peaks) > history:
                peaks.pop(0)
            s = sorted(peaks)
            ref = s[len(s) // 2]
            peak = 0
            quiet = 0
            refr = refractory
        else:
            quiet += 1
            if quiet >= quiet_reset and peaks:
                # Losing the beat entirely: shrink the reference so the detector
                # can find a genuinely smaller QRS instead of waiting forever.
                s = sorted(peaks)
                ref = s[len(s) // 2]
                ref = (ref * (100 - ref_decay_pct)) // 100
                peaks = [ref] if ref > 0 else []
                quiet = 0
    return hits


def score(hits, bpm, seconds, warmup_s=2.0):
    """
    Beats after the warm-up, and the heart rate implied by their median RR.

    `hits` holds sample indices, so the warm-up must be a time filter. Slicing
    the list by a sample count instead silently discarded every beat for faster
    rates and made a working detector look dead.
    """
    first = int(warmup_s * FS)
    body = [h for h in hits if h >= first]
    if len(body) < 3:
        return len(body), 0.0, float(bpm)
    rrs = sorted(body[i + 1] - body[i] for i in range(len(body) - 1))
    med = rrs[len(rrs) // 2] if rrs else 0
    measured = 60000.0 / med if med else 0.0
    # A detector that found too few beats must not be scored as "0 bpm error".
    # That masking is what let a completely dead detector look optimal earlier.
    return len(body), measured, abs(measured - bpm) if measured else float(bpm)


def main() -> int:
    rates = [60, 90, 130, 170]
    seconds = 14.0
    lps = (15, 20, 25, 30, 35)
    sqs = (0, 2, 4, 6)
    abss = (30, 60, 150, 400)

    # The stimulus and the baseline estimator do not depend on the search axes,
    # so compute them once per rate. The energy array depends on (rate, lp, sq)
    # and only the threshold scan depends on abs_min.
    base_by_rate = {}
    for bpm in rates:
        base_by_rate[bpm] = baseline_remove(synth(bpm, seconds))

    energy_cache = {}
    for bpm in rates:
        for lp in lps:
            for sq in sqs:
                energy_cache[(bpm, lp, sq)] = pipeline(
                    base_by_rate[bpm], 5.0, lp, 1, sq, 120)

    print(f"searching {len(lps)} LP cutoffs x {len(sqs)} square shifts "
          f"x {len(abss)} thresholds over {rates} bpm ...\n")

    results = []
    for lp in lps:
        for sq in sqs:
            for abs_min in abss:
                ok = True
                worst = 0.0
                rows = []
                for bpm in rates:
                    hits = detect(energy_cache[(bpm, lp, sq)], abs_min)
                    cnt, meas, err = score(hits, bpm, seconds)
                    need = int(bpm / 60.0 * (seconds - 2.0))
                    rows.append((bpm, cnt, meas, err))
                    worst = max(worst, err)
                    if err > 5.0 or cnt < need:
                        ok = False
                results.append((0 if ok else 1, worst, lp, sq, abs_min, rows))

    results.sort()
    best = results[0]
    flags, worst, lp, sq, abs_min, rows = best
    print(f"BEST: lp={lp} Hz  square_shift={sq}  abs_min={abs_min}"
          f"  worst_err={worst:.2f} bpm  {'ALL RATES OK' if flags == 0 else 'NONE PASSED'}")
    for bpm, cnt, meas, err in rows:
        print(f"   {bpm:3} bpm -> {cnt:3} beats, median {meas:6.1f} bpm (err {err:4.1f})")

    print("\ntop 8 candidates:")
    for flags, worst, lp, sq, abs_min, rows in results[:8]:
        print(f"   lp={lp:3} sq={sq} abs={abs_min:4}  {'OK ' if flags == 0 else 'bad'}"
              f"  worst={worst:5.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
