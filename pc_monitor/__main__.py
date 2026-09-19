"""``python -m pc_monitor`` -- the entry point.

Run from the repository root::

    pc_monitor/.venv/Scripts/python.exe -m pc_monitor --demo
    pc_monitor/.venv/Scripts/python.exe -m pc_monitor --port COM7 --baud 230400

``--demo`` synthesises ECG so the whole application is verifiable with no board;
see :mod:`pc_monitor.demo_source` for why the synthetic path can never be
confused with hardware data.
"""

from __future__ import annotations

import argparse
import sys

from .app import DEFAULT_WINDOW_SECONDS, run
from .serial_worker import DEFAULT_BAUD

_EPILOG = """\
examples:
  python -m pc_monitor --demo                 synthetic waveform, no hardware
  python -m pc_monitor --demo --demo-hr 120   fast synthetic heart rate
  python -m pc_monitor --demo --demo-uncalibrated   shows the --.- temperature path
  python -m pc_monitor --port COM7            open this port at startup
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pc_monitor",
        description="PC host monitor for the STM32 ECG + body-temperature project.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="synthesise ECG/temperature instead of using a serial port; "
        "the window then displays a DEMO / SYNTHETIC banner",
    )
    parser.add_argument(
        "--port",
        default="",
        metavar="PORT",
        help="serial port to open at startup, e.g. COM7 or /dev/ttyACM0",
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=DEFAULT_BAUD,
        metavar="BAUD",
        help="baud rate (default %(default)s, which is UART_BAUD_RATE in app_config.h)",
    )
    parser.add_argument(
        "--window-seconds",
        type=float,
        default=DEFAULT_WINDOW_SECONDS,
        metavar="S",
        help="rolling plot span in seconds (default %(default)s = 10000 samples at 1 kHz)",
    )
    parser.add_argument(
        "--demo-hr",
        type=float,
        default=72.0,
        metavar="BPM",
        help="target heart rate for --demo (default %(default)s)",
    )
    parser.add_argument(
        "--demo-uncalibrated",
        action="store_true",
        help="with --demo, report TEMP_UNCALIBRATED so the temperature card shows --.-",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="print the tool and protocol version and exit",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.version:
        from . import __version__, protocol

        print("pc_monitor %s, protocol 0x%02X" % (__version__, protocol.PROTOCOL_VERSION))
        return 0
    if args.baud < 1200:
        print("--baud must be at least 1200", file=sys.stderr)
        return 2
    if args.window_seconds <= 0:
        print("--window-seconds must be positive", file=sys.stderr)
        return 2
    return run(
        demo=args.demo,
        port=args.port,
        baud=args.baud,
        window_seconds=args.window_seconds,
        demo_hr=args.demo_hr,
        demo_uncalibrated=args.demo_uncalibrated,
    )


if __name__ == "__main__":
    raise SystemExit(main())
