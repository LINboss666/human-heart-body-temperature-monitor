#!/usr/bin/env python3
"""
Regenerate tests/host/protocol_vectors.json from the firmware's own C encoder.

    pc_monitor/.venv/Scripts/python.exe tools/gen_protocol_vectors.py

The JSON is committed, so the PC tool's pytest suite can replay it without a C
toolchain. pc_monitor/tests/test_protocol_vectors.py then asserts that
pc_monitor/protocol.py reproduces every vector byte for byte -- which is what
keeps the two independent implementations of the framing and the CRC honest.

Regenerate and commit together with any change to App/protocol/, PROTOCOL_VERSION
or the packet layouts in protocol.h, and bump PROTOCOL_VERSION if the wire moves.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "host" / "protocol_vectors.json"

INCLUDES = [
    ROOT / "App",
    ROOT / "App" / "config",
    ROOT / "App" / "protocol",
    ROOT / "App" / "ecg",
]

SOURCES = [
    ROOT / "App" / "protocol" / "crc16.c",
    ROOT / "App" / "protocol" / "protocol.c",
    ROOT / "tests" / "host" / "dump_vectors.c",
]

CFLAGS = ["-std=c99", "-O1", "-g0", "-Wall", "-Wextra", "-Werror"]


def main() -> int:
    build = ROOT / "build" / "host-tests"
    build.mkdir(parents=True, exist_ok=True)
    exe = build / "dump_vectors.exe"
    cmd = ([sys.executable, "-m", "ziglang", "cc"] + CFLAGS
           + [f"-I{p}" for p in INCLUDES] + [str(s) for s in SOURCES]
           + ["-o", str(exe)])
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print("compile failed:\n" + proc.stdout + proc.stderr, file=sys.stderr)
        return 1

    proc = subprocess.run([str(exe)], capture_output=True, text=True)
    if proc.returncode != 0:
        print("dump_vectors exited %d" % proc.returncode, file=sys.stderr)
        return 1

    vectors: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if not line or line.startswith("#"):
            continue
        name, _, hexits = line.partition("|")
        if not hexits:
            print("malformed vector line: %r" % line, file=sys.stderr)
            return 1
        vectors[name] = hexits

    if "crc16_check" not in vectors:
        print("no vectors parsed -- did dump_vectors.c run?", file=sys.stderr)
        return 1

    doc = {
        "_generated_by": "tools/gen_protocol_vectors.py",
        "_source": "tests/host/dump_vectors.c linked against App/protocol/{crc16,protocol}.c",
        "_note": ("Do not hand edit. Replay is asserted by "
                  "pc_monitor/tests/test_protocol_vectors.py."),
        "vectors": vectors,
    }
    OUT.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8", newline="\n")
    print("wrote %s (%d vectors, ECG_BATCH frame %d bytes)"
          % (OUT.relative_to(ROOT), len(vectors), len(vectors["ecg_batch_typical"]) // 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
