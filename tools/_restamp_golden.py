#!/usr/bin/env python3
"""One-off: re-stamp protocol.py's hand-written GOLDEN_FRAMES for PROTOCOL_VERSION 2.

Byte 2 of every frame is the protocol version, and HELLO repeats it inside its
payload at HELPP_PROTO_VER, so both move from 0x01 to 0x02 and every trailing
CRC-16 has to be recomputed.

The CRC here is written again from the specification rather than imported from
pc_monitor.protocol, which is the whole point: a table-driven variant sharing no
code with the mirror it is fixing, cross-checked against the published check
value 0x29B1 for "123456789".

Delete this script once the vectors are restamped; it exists so the hex in
protocol.py is reproducible rather than mysterious.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROTO = Path(__file__).resolve().parent.parent / "pc_monitor" / "protocol.py"

OLD_VER = 0x01
NEW_VER = 0x02
WRAP = 40  # hex characters per source line, matching the file's existing style


def crc16_table_driven(data: bytes) -> int:
    """CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, MSB-first, no reflect."""
    table = []
    for byte in range(256):
        crc = byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
        table.append(crc)
    crc = 0xFFFF
    for byte in data:
        crc = ((crc << 8) & 0xFFFF) ^ table[((crc >> 8) ^ byte) & 0xFF]
    return crc


assert crc16_table_driven(b"123456789") == 0x29B1, "independent CRC is wrong"
assert crc16_table_driven(b"") == 0xFFFF


def restamp(raw: bytearray) -> bytearray:
    assert len(raw) >= 14, "frame shorter than the 14-byte overhead"
    assert raw[0] == 0xA5 and raw[1] == 0x5A, "missing magic"
    assert raw[2] == OLD_VER, "version byte is not 0x01: %02X" % raw[2]
    declared = int.from_bytes(raw[6:8], "little")
    assert len(raw) == 14 + declared, "length field disagrees with the hex"
    raw[2] = NEW_VER
    if raw[3] == 0x01:  # PKT_HELLO echoes the version in its payload
        assert declared > 3 and raw[12 + 3] == OLD_VER, "HELLO payload proto byte wrong"
        raw[12 + 3] = NEW_VER
    crc = crc16_table_driven(bytes(raw[:-2]))
    raw[-2] = crc & 0xFF
    raw[-1] = crc >> 8
    return raw


def as_source(hexits: str, indent: str) -> str:
    chunks = [hexits[i:i + WRAP] for i in range(0, len(hexits), WRAP)]
    return ("\n" + indent).join('%s"%s"' % (indent, c) for c in chunks)


# GoldenVector( "name", <one or more adjacent string literals>, <type>, ...
ENTRY = re.compile(
    r'(GoldenVector\([ \t]*\n(?P<ind>[ \t]*)"(?P<name>[a-z0-9_]+)",[ \t]*\n)'
    r'(?P<hex>(?:[ \t]*"[0-9A-Fa-f]+"\s*)+)'
    r'(?P<rest>,)',
)


def main() -> int:
    text = PROTO.read_text(encoding="utf-8")
    count = 0

    def sub(m: re.Match) -> str:
        nonlocal count
        indent = m.group("ind")
        hexits = "".join(re.findall(r'"([0-9A-Fa-f]+)"', m.group("hex")))
        raw = restamp(bytearray(bytes.fromhex(hexits)))
        count += 1
        return '%s%s%s' % (m.group(1), as_source(raw.hex().upper(), indent), m.group("rest"))

    new_text = ENTRY.sub(sub, text)
    if count < 10:
        print("restamped only %d vectors -- the pattern is not matching, "
              "nothing written" % count, file=sys.stderr)
        return 1

    new_text = new_text.replace("proto 1 rate 1000", "proto 2 rate 1000")
    PROTO.write_text(new_text, encoding="utf-8", newline="\n")
    print("restamped %d golden frames to protocol version %d" % (count, NEW_VER))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
