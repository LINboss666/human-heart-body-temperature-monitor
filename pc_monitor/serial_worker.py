"""The thread that owns the serial port.

Two hard rules, both from the brief:

* **The GUI thread never touches the port.**  Every ``open``/``read``/``write``/
  ``close`` happens inside :meth:`SerialWorker.run`, which executes on this
  thread.  ``pyserial`` reads are blocking with a timeout, so a read on the GUI
  thread would freeze the window for the whole timeout.
* **Nothing is emitted per sample.**  One ``read`` returns whatever bytes the OS
  had (typically several frames), the parser frames them, and the complete
  frames leave this thread as a *list* in a single signal emission: roughly six
  emissions per second at the steady-state 3608 byte/s of this protocol,
  against 1000 samples per second.

The worker owns the :class:`~pc_monitor.protocol.StreamParser`, because that
state belongs to whoever feeds it bytes.  Sequence and sample-index accounting
is deliberately *not* done here: it would mean decoding every ``ECG_BATCH``
payload twice, so :class:`AcquisitionEngine` in ``app.py`` does it once, as part
of turning frames into samples.

The port object is injected, not constructed, so that
:class:`~pc_monitor.demo_source.DemoByteSource` -- which exposes the same
``open/read/write/close`` surface -- can be driven through this exact class.
That is what makes demo mode evidence about the real code path.
"""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from typing import Callable

from PySide6 import QtCore

from . import protocol

__all__ = ["SerialWorker", "open_serial_port", "list_serial_ports"]

DEFAULT_BAUD = 230400  # UART_BAUD_RATE in app_config.h
READ_BYTES = 4096
STATS_INTERVAL_S = 0.25


def open_serial_port(port: str, baud: int = DEFAULT_BAUD):
    """Open a real pyserial port configured the way the contract expects.

    8N1 at ``baud``, with a 50 ms read timeout so the worker can notice a stop
    request without spinning, and a write timeout so a stuck device cannot hang
    this thread forever.
    """
    import serial  # imported lazily: the demo path must work without a UART

    return serial.Serial(
        port=port,
        baudrate=int(baud),
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.05,
        write_timeout=1.0,
    )


def list_serial_ports() -> list[tuple[str, str]]:
    """``(device, description)`` pairs.  Slow enough to belong on a thread."""
    try:
        from serial.tools import list_ports
    except ImportError:  # pragma: no cover - pyserial is a hard dependency
        return []
    out: list[tuple[str, str]] = []
    for info in list_ports.comports():
        label = info.description or ""
        hwid = info.hwid or ""
        out.append((info.device, "%s [%s]" % (label, hwid) if hwid else label))
    return sorted(out)


class SerialWorker(QtCore.QThread):
    """A QThread that owns one byte source and emits framed packets.

    Signals:

    * ``framesReady(list)``  -- complete, CRC-verified device->host frames.
    * ``linkStats(dict)``    -- bytes, packets, CRC errors, resyncs, backlog.
    * ``portOpened(str)`` / ``portClosed(str)`` / ``portError(str)``
    * ``portsListed(list)``  -- result of a refresh request.
    * ``commandResult(object)`` -- ``(acked_type, sequence, ok, reason)``.
    """

    framesReady = QtCore.Signal(object)
    linkStats = QtCore.Signal(object)
    portOpened = QtCore.Signal(str)
    portClosed = QtCore.Signal(str)
    portError = QtCore.Signal(str)
    portsListed = QtCore.Signal(object)
    commandResult = QtCore.Signal(object)

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._requests: "queue.Queue[tuple]" = queue.Queue()
        self._outbox: deque[bytes] = deque()
        self._outbox_lock = threading.Lock()
        self._stop = threading.Event()
        self._source = None
        self._source_label = ""
        self._parser = protocol.StreamParser()
        self._tx_sequence = 0
        self._pending_acks: dict[int, int] = {}
        self._bytes_read = 0
        self._bytes_written = 0
        self._reads = 0
        self._empty_reads = 0
        self._last_stats_emit = 0.0
        self._last_stats_bytes = 0

    # ------------------------------------------------------------- requests
    def request_open(self, source, label: str) -> None:
        """Ask the thread to adopt ``source`` (a port or a demo byte source)."""
        self._requests.put(("open", source, label))

    def request_close(self) -> None:
        self._requests.put(("close",))

    def request_port_scan(self) -> None:
        self._requests.put(("scan",))

    def send(self, frame: bytes) -> None:
        """Queue a host->device frame; the write happens on the worker thread."""
        with self._outbox_lock:
            self._outbox.append(bytes(frame))

    def send_command(
        self, type_: protocol.PacketType | int, payload: bytes = b""
    ) -> int:
        """Build, queue and return the sequence of a host command frame.

        The device answers with ACK/NACK carrying that sequence, which is how
        the status line can say whether a command actually took effect.
        """
        seq = self._tx_sequence
        self._tx_sequence = (self._tx_sequence + 1) & 0xFFFF
        self._pending_acks[seq] = int(type_)
        self.send(protocol.build_frame(type_, seq, int(time.monotonic() * 1000) & 0xFFFFFFFF, payload))
        return seq

    def stop(self) -> None:
        """Ask for a clean shutdown and wait for this thread to finish."""
        self._stop.set()
        self.wait(3000)

    @property
    def is_port_open(self) -> bool:
        return self._source is not None

    # --------------------------------------------------------------- the loop
    def run(self) -> None:  # noqa: C901 - a linear state machine, not nesting
        self._stop.clear()
        while not self._stop.is_set():
            self._serve_requests()
            self._drain_outbox()
            if self._source is None:
                self._sleep_briefly()
                self._maybe_emit_stats(force=False)
                continue
            try:
                chunk = self._source.read(READ_BYTES)
            except Exception as exc:  # a unplugged cable is an OSError, not a crash
                self._fail("read failed: %s" % (exc,), close=True)
                continue
            if chunk:
                self._reads += 1
                self._bytes_read += len(chunk)
                frames = self._parser.feed(chunk)
                reports = [f for f in frames if f.is_device_report]
                for frame in frames:
                    self._match_ack(frame)
                if reports:
                    self.framesReady.emit(reports)
            else:
                self._empty_reads += 1
                self._sleep_briefly()
            self._maybe_emit_stats(force=False)
        self._teardown("thread stopped")

    def _serve_requests(self) -> None:
        while True:
            try:
                request = self._requests.get_nowait()
            except queue.Empty:
                return
            kind = request[0]
            if kind == "open":
                self._do_open(request[1], request[2])
            elif kind == "close":
                self._do_close("requested")
            elif kind == "scan":
                try:
                    ports = list_serial_ports()
                except Exception as exc:  # pragma: no cover - defensive
                    ports = []
                    self.portError.emit("port enumeration failed: %s" % (exc,))
                self.portsListed.emit(ports)

    def _do_open(self, source, label: str) -> None:
        self._do_close("reconnecting")
        try:
            opener: Callable | None = getattr(source, "open", None)
            if opener is not None:
                opener()
        except Exception as exc:
            self._fail("open %s failed: %s" % (label, exc), close=False)
            try:
                source.close()
            except Exception:
                pass
            return
        self._source = source
        self._source_label = label
        self._parser.reset()
        self._bytes_read = 0
        self._reads = 0
        self._last_stats_bytes = 0
        self._port_log(label, opening=True)

    def _port_log(self, label: str, opening: bool) -> None:
        (self.portOpened if opening else self.portClosed).emit(label)

    def _do_close(self, reason: str) -> None:
        if self._source is None:
            return
        label = self._source_label
        try:
            self._source.close()
        except Exception as exc:  # pragma: no cover - defensive
            self.portError.emit("close failed: %s" % (exc,))
        self._source = None
        self._source_label = ""
        self._port_log("%s (%s)" % (label, reason), opening=False)

    def _fail(self, message: str, close: bool) -> None:
        self.portError.emit(message)
        if close:
            self._do_close("link error")

    def _drain_outbox(self) -> None:
        while True:
            with self._outbox_lock:
                if not self._outbox:
                    return
                frame = self._outbox.popleft()
            if self._source is None:
                self.portError.emit("dropped command, port is closed: %s" % (
                    protocol.packet_type_name(frame[3]),))
                continue
            try:
                written = self._source.write(frame)
            except Exception as exc:
                self._fail("write failed: %s" % (exc,), close=True)
                return
            self._bytes_written += int(written or len(frame))

    def _match_ack(self, frame: protocol.Frame) -> None:
        if frame.type == protocol.PacketType.ACK:
            try:
                ack = protocol.Ack.decode(frame.payload)
            except protocol.MalformedPayload:
                return
            self._pending_acks.pop(ack.acked_sequence, None)
            self.commandResult.emit((ack.acked_type, ack.acked_sequence, True, ""))
        elif frame.type == protocol.PacketType.NACK:
            try:
                nack = protocol.Nack.decode(frame.payload)
            except protocol.MalformedPayload:
                return
            self._pending_acks.pop(nack.acked_sequence, None)
            self.commandResult.emit((nack.acked_type, nack.acked_sequence, False, nack.reason_name))

    def _sleep_briefly(self) -> None:
        self._stop.wait(0.002)

    def _maybe_emit_stats(self, force: bool) -> None:
        now = time.monotonic()
        if not force and now - self._last_stats_emit < STATS_INTERVAL_S:
            return
        elapsed = now - self._last_stats_emit if self._last_stats_emit else 0.0
        moved = self._bytes_read - self._last_stats_bytes
        self._last_stats_bytes = self._bytes_read
        self._last_stats_emit = now
        rate = moved / elapsed if elapsed > 0 else 0.0
        stats = self.snapshot_stats()
        stats["bytes_per_second"] = rate
        self.linkStats.emit(stats)

    def snapshot_stats(self) -> dict:
        """A plain dict copy, safe to hand to another thread."""
        return {
            "open": self._source is not None,
            "label": self._source_label,
            "bytes_read": self._bytes_read,
            "bytes_written": self._bytes_written,
            "reads": self._reads,
            "empty_reads": self._empty_reads,
            "pending_bytes": self._parser.pending,
            "crc_errors": self._parser.crc_errors,
            "resyncs": self._parser.resyncs,
            "discarded": self._parser.bytes_discarded,
            "frames": self._parser.frames_parsed,
            "bytes_per_second": 0.0,
            "unacked_commands": len(self._pending_acks),
        }

    def _teardown(self, reason: str) -> None:
        self._do_close(reason)
        self._maybe_emit_stats(force=True)
