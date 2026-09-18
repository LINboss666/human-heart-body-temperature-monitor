#!/usr/bin/env python3
"""
Build and run the firmware's host-side C unit tests.

The pure algorithm modules (crc16, framing, calendar conversion, ECG filters and
the R-peak detector) do not touch HAL, so they can be compiled for the host and
actually executed. That is the only way to satisfy "the algorithm must work on
synthetic tests" without a board, so this script is part of the verification
story, not a convenience.

Compiler: the `ziglang` wheel in pc_monitor/.venv provides a full clang-based C
toolchain (`python -m ziglang cc`), which means no system compiler install is
needed on Windows.

    pc_monitor/.venv/Scripts/python.exe tools/run_host_tests.py
    pc_monitor/.venv/Scripts/python.exe tools/run_host_tests.py -v
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOST_TESTS = ROOT / "tests" / "host"
BUILD_DIR = ROOT / "build" / "host-tests"

INCLUDES = [
    ROOT / "App",
    ROOT / "App" / "config",
    ROOT / "App" / "protocol",
    ROOT / "App" / "ecg",
    ROOT / "App" / "temperature",
    ROOT / "App" / "rtc_service",
    ROOT / "tests" / "host",
    ROOT / "ThirdParty" / "kk_oled" / "include",
]

# Sources linked into every test, plus any test-specific extra sources.
COMMON_SOURCES = [
    ROOT / "App" / "protocol" / "crc16.c",
    ROOT / "App" / "protocol" / "protocol.c",
]

EXTRA_SOURCES = {
    "test_ecg_pipeline.c": [
        ROOT / "App" / "ecg" / "ecg_signal.c",
        ROOT / "App" / "ecg" / "ecg_hr.c",
    ],
    "test_rtc_calendar.c": [
        ROOT / "App" / "rtc_service" / "rtc_calendar.c",
    ],
    "test_font_format.c": [
        ROOT / "ThirdParty" / "kk_oled" / "graphics" / "kk_oled_font.c",
        ROOT / "tests" / "host" / "kk_oled_font_stubs.c",
    ],
}

CFLAGS = [
    "-std=c99", "-O1", "-g0",
    "-Wall", "-Wextra", "-Werror",
    "-Wno-unused-parameter",
]

# Per-test link needs: maths for the synthetic stimulus in the ECG test.
EXTRA_LIBS = {
    "test_ecg_pipeline.c": ["-lm"],
}


def zig_cc() -> list[str]:
    return [sys.executable, "-m", "ziglang", "cc"]


def build_one(test_src: Path, verbose: bool) -> tuple[Path | None, str]:
    exe = BUILD_DIR / (test_src.stem + ".exe")
    sources = [str(p) for p in COMMON_SOURCES]
    sources += [str(p) for p in EXTRA_SOURCES.get(test_src.name, [])]
    cmd = (zig_cc() + CFLAGS + [f"-I{p}" for p in INCLUDES] + sources
           + [str(test_src)] + EXTRA_LIBS.get(test_src.name, []) + ["-o", str(exe)])
    if verbose:
        print("  " + " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return None, (proc.stdout + proc.stderr).strip()
    return exe, ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("only", nargs="?", help="run a single test file name")
    args = ap.parse_args()

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    tests = sorted(HOST_TESTS.glob("test_*.c"))
    if args.only:
        tests = [t for t in tests if t.name == args.only or t.stem == args.only]
    if not tests:
        print("no host tests found", file=sys.stderr)
        return 2

    failed: list[str] = []
    for test in tests:
        print(f"\n=== {test.name} ===")
        exe, err = build_one(test, args.verbose)
        if exe is None:
            print("  COMPILE FAILED:\n" + "\n".join("    " + l for l in err.splitlines()))
            failed.append(f"{test.name} (compile)")
            continue
        proc = subprocess.run([str(exe)], capture_output=True, text=True)
        print(proc.stdout.rstrip())
        if proc.stderr.strip():
            print("  stderr:", proc.stderr.strip())
        if proc.returncode != 0:
            failed.append(f"{test.name} (exit {proc.returncode})")

    print("\n" + "=" * 60)
    if failed:
        print("HOST TESTS FAILED:")
        for f in failed:
            print("  -", f)
        return 1
    print(f"all {len(tests)} host test binaries passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
