"""Realtime monitor: worker thread -> engine -> widgets, repainted at 25 FPS.

Data flow, which is the whole design::

    SerialWorker (QThread)          GUI thread
    ========================        =========================================
    blocking read(4096)
      -> StreamParser.feed()          (never touched by the GUI thread)
      -> framesReady.emit([frames]) -> AcquisitionEngine.handle_frames()
                                        -> decode, StreamTracker, ring buffer,
                                           recording session
                                     QTimer(40 ms) -> MonitorWindow._repaint()
                                        -> one snapshot, one setData, one label pass

Three rules this file exists to enforce:

* **No per-sample emission.**  A 68-byte ``ECG_BATCH`` frame carries 20 samples,
  so 50 frames/s become ~6 queued signal emissions per second, and 1000 samples/s
  become 25 repaints.  Nothing scales with the sample rate.
* **No redraw from the reader.**  The plot reads a lock-guarded snapshot of a
  fixed-capacity :class:`~pc_monitor.ring_buffer.IndexedRingBuffer`; a paused or
  slow repaint therefore costs dropped *frames*, never dropped *data*.
* **Protocol state becomes screen state in exactly one place.**  The widgets take
  decoded values and render them; the engine decides what a value *means*
  (including whether degrees may be shown at all).

``UI_FRAME_INTERVAL_MS`` is 40 in ``App/config/app_config.h`` -- the OLED runs at
25 FPS, so the PC matches it instead of inventing its own cadence.
"""

from __future__ import annotations

import datetime as dt
import time
from pathlib import Path
from typing import Any, Callable

from PySide6 import QtCore, QtGui, QtWidgets

from . import protocol
from . import rtc as rtc_module
from .demo_source import (
    DEMO_BANNER,
    DEMO_ORIGIN,
    DEMO_PORT_LABEL,
    DemoByteSource,
    DemoDevice,
    looks_like_demo,
)
from .export import default_stem, export_csv, export_xlsx
from .recorder import ORIGIN_DEVICE, RecordingSession, reconcile_hr, temp_certified
from .ring_buffer import EventRateMeter, IndexedRingBuffer
from .serial_worker import DEFAULT_BAUD, SerialWorker, open_serial_port
from .widgets import ConnectionPanel, EcgPane, MetricStrip, StatusStrip

__all__ = ["AcquisitionEngine", "MonitorWindow", "run", "REPAINT_INTERVAL_MS"]

#: 40 ms == 25 FPS, matching UI_FRAME_INTERVAL_MS in app_config.h.
REPAINT_INTERVAL_MS = 40
#: Ring capacity: the widest window preset (60 s at 1 kHz) plus headroom.
PLOT_CAPACITY_SAMPLES = 64000
#: Default rolling plot span, also the CLI's --window-seconds default.
DEFAULT_WINDOW_SECONDS = 10.0


# ============================================================== the engine
class AcquisitionEngine(QtCore.QObject):
    """Turns CRC-verified frames into samples, counters and card values.

    Lives entirely on the GUI thread: frames reach it through a queued signal
    from :class:`~pc_monitor.serial_worker.SerialWorker`, and the repaint timer
    runs on the same thread, so no state here needs a lock beyond the ring
    buffer's own (the worker's data can arrive between repaints, and it does).
    """

    logMessage = QtCore.Signal(str)
    demoSuspected = QtCore.Signal(str)
    rtcResponded = QtCore.Signal(object)
    commandAnswered = QtCore.Signal(str)

    def __init__(
        self,
        worker: SerialWorker,
        *,
        demo: bool = False,
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.worker = worker
        self.demo = bool(demo)
        self.buffer = IndexedRingBuffer(PLOT_CAPACITY_SAMPLES)
        self.tracker = protocol.StreamTracker()
        self.rate = EventRateMeter(window_seconds=2.0)
        self.session = RecordingSession(
            origin=DEMO_ORIGIN if self.demo else ORIGIN_DEVICE, source_label="not opened"
        )
        self.hello: protocol.Hello | None = None
        self.status: protocol.StatusPacket | None = None
        self.temp_status: protocol.TempStatus | None = None
        self.rtc: protocol.RtcCalendar | None = None
        self.link: dict[str, Any] = {"open": False, "label": "", "crc_errors": 0}
        self._batch_temp: tuple[int, int, int] | None = None  # (centi, state, ts_ms)
        self._status_temp: tuple[int, int, int] | None = None
        self.temp_disagreements = 0
        self._last_batch_ts = 0
        self._sample_period_us = protocol.SAMPLE_PERIOD_US
        self._ping_token: bytes | None = None
        self._ping_sent_at: float | None = None
        self.ping_rtt_ms: float | None = None
        self._first_seen_host: float | None = None
        self._recording_started_host: float | None = None
        self._events_seen = 0
        self.last_event_text = ""
        self.session.hello = None  # type: ignore[attr-defined]

    # ------------------------------------------------------------------ ingest
    @QtCore.Slot(object)
    def handle_frames(self, frames: list) -> None:
        """Slot for ``SerialWorker.framesReady`` -- a whole read, not a sample."""
        for frame in frames:
            self.rate.tick()
            self._handle_frame(frame)

    def _handle_frame(self, frame: protocol.Frame) -> None:
        for event in self.tracker.observe(frame):
            self._events_seen += 1
            self.last_event_text = event.detail
            self.logMessage.emit("%s: %s" % (event.kind.name, event.detail))
        if frame.type == protocol.PacketType.HELLO:
            self._on_hello(frame)
        elif frame.type == protocol.PacketType.STATUS:
            self._on_status(frame)
        elif frame.type == protocol.PacketType.TEMP_STATUS:
            self._on_temp_status(frame)
        elif frame.type == protocol.PacketType.ECG_BATCH:
            self._on_ecg_batch(frame)
        elif frame.type == protocol.PacketType.RTC_RESPONSE:
            self._on_rtc_response(frame)
        elif frame.type == protocol.PacketType.PONG:
            self._on_pong(frame)

    def _on_hello(self, frame: protocol.Frame) -> None:
        try:
            hello = protocol.Hello.decode(frame.payload)
        except protocol.MalformedPayload as exc:
            self.logMessage.emit("HELLO rejected: %s" % exc)
            return
        self.hello = hello
        self.session.hello = hello  # type: ignore[attr-defined]
        if hello.proto_version != protocol.PROTOCOL_VERSION:
            self.logMessage.emit(
                "WARNING device protocol 0x%02X, host mirrors 0x%02X"
                % (hello.proto_version, protocol.PROTOCOL_VERSION)
            )
        self.logMessage.emit(
            "HELLO fw %s, %d Hz, batch<=%d, %d-bit, caps 0x%04X"
            % (hello.fw_version, hello.sample_rate_hz, hello.batch_max_samples, hello.adc_bits, hello.caps)
        )
        if not self.demo and looks_like_demo(hello, frame.payload):
            self.demoSuspected.emit("device looks synthetic (firmware 0.0.0)")

    def _on_status(self, frame: protocol.Frame) -> None:
        try:
            self.status = protocol.StatusPacket.decode(frame.payload)
        except protocol.MalformedPayload as exc:
            self.logMessage.emit("STATUS rejected: %s" % exc)

    def _on_temp_status(self, frame: protocol.Frame) -> None:
        try:
            self.temp_status = protocol.TempStatus.decode(frame.payload)
        except protocol.MalformedPayload as exc:
            self.logMessage.emit("TEMP_STATUS rejected: %s" % exc)
            return
        self._status_temp = (
            self.temp_status.temp_centi,
            int(self.temp_status.temp_state),
            frame.device_ts_ms,
        )
        self._check_temp_routes()

    def _on_ecg_batch(self, frame: protocol.Frame) -> None:
        try:
            batch = protocol.EcgBatch.decode(frame.payload)
        except protocol.MalformedPayload as exc:
            self.logMessage.emit("ECG_BATCH rejected: %s" % exc)
            return
        self._sample_period_us = batch.sample_period_us or self._sample_period_us
        codes = batch.samples
        mv = [protocol.ecg_raw_to_mv(code) for code in codes]
        self.buffer.append_block(batch.first_sample_index, mv)
        if self._first_seen_host is None:
            self._first_seen_host = time.monotonic()
        flags = batch.flags_decoded
        self._batch_temp = (batch.temp_centi, int(flags.temp), frame.device_ts_ms)
        hr_valid, _disagreed = reconcile_hr(batch, flags)
        # Store the derived quantities the cards need; no per-sample work beyond
        # the conversion above, which the recorder would otherwise repeat.
        self._last_batch = (batch, hr_valid)
        self._last_batch_ts = frame.device_ts_ms
        chunk = self.session.add_batch(frame, batch)
        if chunk is not None and self._recording_started_host is None:
            self._recording_started_host = time.monotonic()

    def _on_rtc_response(self, frame: protocol.Frame) -> None:
        try:
            calendar = protocol.RtcCalendar.decode(frame.payload)
        except protocol.MalformedPayload as exc:
            self.logMessage.emit("RTC_RESPONSE rejected: %s" % exc)
            return
        self.rtc = calendar
        self.rtcResponded.emit(calendar)
        self.logMessage.emit("device clock: %s" % calendar.text)

    def _on_pong(self, frame: protocol.Frame) -> None:
        if self._ping_sent_at is not None:
            self.ping_rtt_ms = (time.monotonic() - self._ping_sent_at) * 1000.0
            self._ping_sent_at = None
        self.logMessage.emit("PONG %s (%s ms round trip)" % (frame.payload.hex(), "n/a" if self.ping_rtt_ms is None else "%.0f" % self.ping_rtt_ms))

    def _check_temp_routes(self) -> None:
        """``temp_state`` is carried by ECG_BATCH flags *and* TEMP_STATUS.

        The contract sends it twice with no stated tie-break, so the freshest
        reading (by ``device_ts_ms``) wins for the card and a disagreement is
        counted rather than smoothed over.
        """
        if self._batch_temp is None or self._status_temp is None:
            return
        if self._batch_temp[1] != self._status_temp[1]:
            self.temp_disagreements += 1

    # ------------------------------------------------------------- link events
    @QtCore.Slot(object)
    def handle_stats(self, stats: dict) -> None:
        self.link = dict(stats)

    @QtCore.Slot(str)
    def handle_port_opened(self, label: str) -> None:
        self.link["open"] = True
        self.link["label"] = label
        self.session.source_label = label
        self.logMessage.emit("port open: %s" % label)

    @QtCore.Slot(str)
    def handle_port_closed(self, label: str) -> None:
        self.link["open"] = False
        self.logMessage.emit("port closed: %s" % label)

    @QtCore.Slot(str)
    def handle_port_error(self, message: str) -> None:
        self.logMessage.emit("link error: %s" % message)

    @QtCore.Slot(object)
    def handle_command_result(self, result: tuple) -> None:
        acked_type, sequence, ok, reason = result
        text = "%s of seq %d: %s" % (
            protocol.packet_type_name(int(acked_type)),
            int(sequence),
            "accepted" if ok else "rejected (%s)" % reason,
        )
        self.commandAnswered.emit(text)
        self.logMessage.emit(text)

    # ------------------------------------------------------------------ counts
    def plot_window(self, seconds: float) -> tuple[Any, Any]:
        return self.buffer.snapshot_window(seconds, self.sample_rate_hz)

    @property
    def sample_rate_hz(self) -> float:
        return 1e6 / self._sample_period_us if self._sample_period_us else float(protocol.SAMPLE_RATE_HZ)

    @property
    def streaming(self) -> bool:
        """Whether ECG_BATCH frames are arriving (the device's own ADC is always on)."""
        return bool(self.link.get("open")) and self.rate.total > 0 and (
            self.tracker.by_type.get(int(protocol.PacketType.ECG_BATCH), 0) > 0
        )

    def _temperature(self) -> dict[str, Any]:
        """Reconcile the two routes ``temp_centi``/``temp_state`` arrive by."""
        candidates = []
        if self._batch_temp is not None:
            candidates.append(("ECG_BATCH", self._batch_temp))
        if self._status_temp is not None:
            candidates.append(("TEMP_STATUS", self._status_temp))
        if not candidates:
            return {"centi": 0, "state": int(protocol.TempState.UNCALIBRATED), "source": "none", "certified": False}
        source, (centi, state, _ts) = max(candidates, key=lambda item: item[1][2])
        flags = protocol.split_flags(
            protocol.make_flags(temp=state, temp_uncalibrated=state == int(protocol.TempState.UNCALIBRATED))
        )
        certified = temp_certified_from(flags, state)
        return {"centi": centi, "state": state, "source": source, "certified": certified}

    def metrics(self) -> dict[str, Any]:
        """One flat dict per repaint: the only bridge from protocol to pixels."""
        caps = self.hello.caps if self.hello else 0
        hello_present = self.hello is not None
        batch = getattr(self, "_last_batch", None)
        if batch is not None:
            ecg_batch, hr_valid = batch
            flags = ecg_batch.flags_decoded
            hr_bpm = ecg_batch.hr_bpm
            hr_state = int(ecg_batch.hr_state)
        else:
            ecg_batch, flags, hr_bpm, hr_state, hr_valid = None, None, 0, 0, False
        if flags is None and self.status is not None:
            flags = self.status.flags_decoded
        temperature = self._temperature()
        recording_seconds = self._recording_seconds()
        return {
            "demo": self.demo,
            "hr_bpm": hr_bpm,
            "hr_state": hr_state,
            "hr_valid": bool(hr_valid),
            "temp_centi": temperature["centi"],
            "temp_state": temperature["state"],
            "temp_certified": temperature["certified"],
            "temp_source": temperature["source"],
            "lead": int(flags.lead) if flags else int(protocol.LeadState.UNKNOWN),
            "flags": flags,
            "flags_raw": int(ecg_batch.flags) if ecg_batch else (self.status.flags if self.status else 0),
            "recording_active": self.session.is_recording,
            "recording_seconds": recording_seconds,
            "recording_rows": self.session.row_count,
            "samples": self.tracker.total_samples,
            "sample_rate_hz": self.sample_rate_hz,
            "packets": self.tracker.packets,
            "packets_per_s": self.rate.rate,
            "bytes_per_s": float(self.link.get("bytes_per_second", 0.0)),
            "crc_errors": int(self.link.get("crc_errors", 0)),
            "discarded": int(self.link.get("discarded", 0)),
            "resyncs": int(self.link.get("resyncs", 0)),
            "sequence_gaps": self.tracker.sequence_gaps,
            "sequence_missing": self.tracker.sequence_missing,
            "index_gaps": self.tracker.index_gaps,
            "samples_missing": self.tracker.samples_missing,
            "malformed_batches": self.tracker.malformed_batches,
            "temp_disagreements": self.temp_disagreements,
            "hello_present": hello_present,
            "caps": caps,
            "lead_hw_detect": bool(caps & protocol.Capability.LEAD_HW_DETECT),
            "probe_hw_detect": bool(caps & protocol.Capability.PROBE_HW_DETECT),
            "link_open": bool(self.link.get("open")),
            "link_label": str(self.link.get("label", "")),
            "pending_bytes": int(self.link.get("pending_bytes", 0)),
            "buffered": len(self.buffer),
            "device_line": self._device_line(),
            "identity": (
                "fw %s proto 0x%02X, %d Hz" % (self.hello.fw_version, self.hello.proto_version, self.hello.sample_rate_hz)
                if self.hello
                else "no HELLO received"
            ),
            "ping_rtt_ms": self.ping_rtt_ms,
            "batch": batch[0] if batch else None,
        }

    def _recording_seconds(self) -> float:
        if self.session.row_count:
            return self.session.duration_s()
        if self.session.is_recording and self._recording_started_host is not None:
            return time.monotonic() - self._recording_started_host
        return 0.0

    def _device_line(self) -> str:
        """One line from STATUS, in the same words the OLED STATUS page uses."""
        status = self.status
        if status is None:
            return "waiting for STATUS"
        parts = [
            "uptime %s" % _hms(status.uptime_s),
            "ADC %s" % ("running" if status.adc_running else "stopped"),
            "DMA %d blocks / %d dropped" % (status.dma_blocks, status.dma_dropped),
            "%d ECG samples" % status.ecg_samples,
            "UART tx %d rx %d crc %d proto %d"
            % (status.uart_tx, status.uart_rx, status.uart_crc_err, status.proto_err),
            "OLED %s" % ("0x%02X" % status.oled_addr if status.oled_present else "absent"),
            "RTC %s" % ("valid" if status.rtc_valid else "not set"),
        ]
        return ", ".join(parts)

    # ------------------------------------------------------------- recording
    def start_recording(self) -> dt.datetime:
        moment = self.session.start()
        if self.session.row_count == 0:
            self._recording_started_host = time.monotonic()
        self.logMessage.emit("recording started at %s" % moment.isoformat(timespec="seconds"))
        return moment

    def stop_recording(self) -> dt.datetime:
        moment = self.session.stop(counters=self.counters())
        self.logMessage.emit(
            "recording stopped: %s rows, %.1f s" % (f"{self.session.row_count:,}", self.session.duration_s())
        )
        return moment

    def counters(self) -> dict[str, int]:
        """Link counters, for the Summary sheet's drop and CRC columns."""
        return {
            "packets": self.tracker.packets,
            "sequence_gaps": self.tracker.sequence_gaps,
            "sequence_missing": self.tracker.sequence_missing,
            "index_gaps": self.tracker.index_gaps,
            "crc_errors": int(self.link.get("crc_errors", 0)),
            "discarded": int(self.link.get("discarded", 0)),
            "samples_missing": self.tracker.samples_missing,
        }

    def new_recording(self) -> None:
        self.session.clear()
        self._recording_started_host = None
        self.logMessage.emit("recording buffer cleared")

    # --------------------------------------------------------------- commands
    def start_stream(self) -> int:
        return self.worker.send_command(protocol.PacketType.START_STREAM)

    def stop_stream(self) -> int:
        return self.worker.send_command(protocol.PacketType.STOP_STREAM)

    def get_rtc(self) -> int:
        return self.worker.send_command(protocol.PacketType.GET_RTC)

    def set_rtc(self, calendar: protocol.RtcCalendar) -> int:
        payload = rtc_module.to_payload(calendar)
        return self.worker.send_command(protocol.PacketType.SET_RTC, payload)

    def sync_rtc_from_pc(self) -> int:
        return self.set_rtc(rtc_module.pc_calendar())

    def ping(self) -> int:
        self._ping_token = bytes((time.monotonic() * 1000 % 0xFFFFFFFF).to_bytes(4, "little"))
        self._ping_sent_at = time.monotonic()
        return self.worker.send_command(protocol.PacketType.PING, self._ping_token)


def temp_certified_from(flags: protocol.Flags, state: int) -> bool:
    """Degrees exist only for OK/LOW/HIGH and never while uncalibrated.

    Same rule as :func:`pc_monitor.recorder.temp_certified`, applied to a
    ``temp_state`` that may have come from ``TEMP_STATUS`` rather than a batch.
    """
    if flags.temp_uncalibrated or state == int(protocol.TempState.UNCALIBRATED):
        return False
    return state in (int(protocol.TempState.OK), int(protocol.TempState.LOW), int(protocol.TempState.HIGH))


def _hms(seconds: int) -> str:
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return "%d:%02d:%02d" % (hours, minutes, secs)


# ================================================================ the window
class MonitorWindow(QtWidgets.QMainWindow):
    """Main window: connection column, metric cards, ECG pane, status strip."""

    def __init__(
        self,
        *,
        demo: bool = False,
        port: str = "",
        baud: int = DEFAULT_BAUD,
        demo_hr: float = 72.0,
        demo_uncalibrated: bool = False,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        autoconnect: bool = True,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.demo = bool(demo)
        self._port = port
        self._baud = int(baud)
        self._demo_hr = float(demo_hr)
        self._demo_uncalibrated = bool(demo_uncalibrated)
        self._autoconnect = bool(autoconnect)
        self.setWindowTitle(
            "Human Heart & Body Temperature Monitor -- PC host"
            + ("  [DEMO / SYNTHETIC DATA]" if self.demo else "")
        )
        self.resize(1360, 860)

        self.worker = SerialWorker(self)
        self.engine = AcquisitionEngine(self.worker, demo=self.demo, parent=self)
        self.worker.framesReady.connect(self.engine.handle_frames)
        self.worker.linkStats.connect(self.engine.handle_stats)
        self.worker.portOpened.connect(self.engine.handle_port_opened)
        self.worker.portClosed.connect(self.engine.handle_port_closed)
        self.worker.portError.connect(self.engine.handle_port_error)
        self.worker.portsListed.connect(self._on_ports_listed)
        self.worker.commandResult.connect(self.engine.handle_command_result)
        self.engine.demoSuspected.connect(self._on_demo_suspected)

        self._build_ui(window_seconds)
        self._wire()

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(REPAINT_INTERVAL_MS)
        self.timer.timeout.connect(self._repaint)
        self.timer.start()

        self.worker.start()
        self.worker.request_port_scan()
        if self._autoconnect and (self.demo or self._port):
            QtCore.QTimer.singleShot(150, self._auto_open)

    # ------------------------------------------------------------------- build
    def _build_ui(self, window_seconds: float) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        outer = QtWidgets.QHBoxLayout(central)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        left = QtWidgets.QVBoxLayout()
        left.setSpacing(8)
        self.panel = ConnectionPanel()
        left.addWidget(self.panel)
        left.addWidget(self._build_controls_group())
        left.addWidget(self._build_log_group(), 1)
        left_container = QtWidgets.QWidget()
        left_container.setLayout(left)
        left_container.setFixedWidth(390)
        outer.addWidget(left_container)

        right = QtWidgets.QVBoxLayout()
        right.setSpacing(6)
        self.banner = self._build_banner()
        if self.banner is not None:
            right.addWidget(self.banner)
        self.cards = MetricStrip()
        right.addWidget(self.cards)
        self.pane = EcgPane(window_seconds=window_seconds)
        right.addWidget(self.pane, 1)
        right_container = QtWidgets.QWidget()
        right_container.setLayout(right)
        outer.addWidget(right_container, 1)

        self.strip = StatusStrip()
        bar = self.statusBar()
        bar.setSizeGripEnabled(True)
        bar.addPermanentWidget(self.strip, 1)
        bar.showMessage("ready", 5000)

    def _build_banner(self) -> QtWidgets.QWidget | None:
        if not self.demo:
            return None
        label = QtWidgets.QLabel(DEMO_BANNER)
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        label.setObjectName("DemoBanner")
        label.setStyleSheet(
            "QLabel#DemoBanner { background-color: #7a1f1f; color: #fff3cd;"
            " font-size: 15px; font-weight: 800; padding: 8px; letter-spacing: 1px; }"
        )
        label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        return label

    def _build_controls_group(self) -> QtWidgets.QWidget:
        box = QtWidgets.QGroupBox("Acquisition")
        grid = QtWidgets.QGridLayout(box)
        grid.setContentsMargins(8, 4, 8, 4)

        self.stream_button = QtWidgets.QPushButton("Start acquisition")
        self.stream_button.setCheckable(True)
        self.stream_button.setToolTip(
            "Sends START_STREAM / STOP_STREAM. Those control *reporting* only: the ADC, TIM3 "
            "and the DMA run from boot, so stopping never stops the converter."
        )
        grid.addWidget(self.stream_button, 0, 0)

        self.record_button = QtWidgets.QPushButton("Start recording")
        self.record_button.setCheckable(True)
        self.record_button.setToolTip("Rows are only written to the export buffer while this is on.")
        grid.addWidget(self.record_button, 0, 1)

        self.new_button = QtWidgets.QPushButton("New")
        self.new_button.setToolTip("Discard the held rows so the next export cannot mix two sessions.")
        grid.addWidget(self.new_button, 0, 2)

        self.export_csv_button = QtWidgets.QPushButton("Export CSV")
        self.export_xlsx_button = QtWidgets.QPushButton("Export XLSX")
        grid.addWidget(self.export_csv_button, 1, 0)
        grid.addWidget(self.export_xlsx_button, 1, 1, 1, 2)

        self.status_note = QtWidgets.QLabel("idle")
        self.status_note.setObjectName("CardSub")
        self.status_note.setWordWrap(True)
        grid.addWidget(self.status_note, 2, 0, 1, 3)
        return box

    def _build_log_group(self) -> QtWidgets.QWidget:
        box = QtWidgets.QGroupBox("Events")
        layout = QtWidgets.QVBoxLayout(box)
        layout.setContentsMargins(8, 4, 8, 4)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(400)
        self.log.setObjectName("EventLog")
        self.log.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.log)
        return box

    def _wire(self) -> None:
        self.panel.connectRequested.connect(self._on_connect)
        self.panel.disconnectRequested.connect(self._on_disconnect)
        self.panel.refreshRequested.connect(self.worker.request_port_scan)
        self.panel.rtcSyncRequested.connect(self._on_sync_time)
        self.panel.rtcSetRequested.connect(self._on_set_time)
        self.panel.rtcGetRequested.connect(lambda: self._send("GET_RTC", self.engine.get_rtc))
        self.panel.pingRequested.connect(lambda: self._send("PING", self.engine.ping))
        self.engine.rtcResponded.connect(self.panel.show_rtc)
        self.engine.commandAnswered.connect(self.panel.show_command_result)
        self.engine.logMessage.connect(self._append_log)
        self.stream_button.toggled.connect(self._on_stream_toggled)
        self.record_button.toggled.connect(self._on_record_toggled)
        self.new_button.clicked.connect(self._on_new_recording)
        self.export_csv_button.clicked.connect(lambda: self._on_export(csv=True))
        self.export_xlsx_button.clicked.connect(lambda: self._on_export(csv=False))

    # ------------------------------------------------------------- port handling
    def _make_source(self):
        """The byte source for the current mode: a UART, or the synthetic device."""
        if self.demo:
            device = DemoDevice(
                target_hr=self._demo_hr,
                calibrated_temperature=not self._demo_uncalibrated,
            )
            return DemoByteSource(device), DEMO_PORT_LABEL
        return open_serial_port(self._port, self._baud), "%s @ %d" % (self._port, self._baud)

    def _auto_open(self) -> None:
        self._open_requested(*self._source_for_current_settings())

    def _source_for_current_settings(self) -> tuple[str, int]:
        if self.demo:
            return DEMO_PORT_LABEL, self._baud
        return self.panel.current_port(), self.panel.current_baud()

    def _on_connect(self, port: str, baud: int) -> None:
        self._port, self._baud = port, int(baud)
        self._open_requested(port, baud)

    def _open_requested(self, port: str, baud: int) -> None:
        try:
            source, label = self._make_source()
        except Exception as exc:  # a bad port name is a user error, not a crash
            self.stream_button.setChecked(False)
            self.stream_button.setEnabled(True)
            self.panel.set_link_error("%s" % exc)
            self._append_log("open failed: %s" % exc)
            return
        self.panel.set_detail("opening %s" % label)
        self.worker.request_open(source, label)

    def _on_disconnect(self) -> None:
        self.worker.request_close()
        self.stream_button.setChecked(False)

    @QtCore.Slot(object)
    def _on_ports_listed(self, ports: list) -> None:
        self.panel.set_ports(list(ports or []))

    @QtCore.Slot(str)
    def _on_demo_suspected(self, text: str) -> None:
        self._append_log("WARNING %s" % text)
        self.pane.set_note(text)

    # ---------------------------------------------------------------- commands
    def _send(self, what: str, action: Callable[[], int]) -> None:
        if not self.engine.link.get("open"):
            self._append_log("%s not sent: port is closed" % what)
            self.panel.show_command_result("%s not sent: port is closed" % what, ok=False)
            return
        sequence = action()
        self._append_log("%s sent (seq %d)" % (what, sequence))

    def _on_stream_toggled(self, checked: bool) -> None:
        if checked:
            self._send("START_STREAM", self.engine.start_stream)
            self.stream_button.setText("Stop acquisition")
        else:
            self._send("STOP_STREAM", self.engine.stop_stream)
            self.stream_button.setText("Start acquisition")

    def _on_record_toggled(self, checked: bool) -> None:
        if checked:
            self.engine.start_recording()
            self.record_button.setText("Stop recording")
        else:
            self.engine.stop_recording()
            self.record_button.setText("Start recording")

    def _on_new_recording(self) -> None:
        if self.engine.session.row_count and self.record_button.isChecked():
            QtWidgets.QMessageBox.information(self, "Still recording", "Stop the recording first.")
            return
        if self.engine.session.row_count:
            answer = QtWidgets.QMessageBox.question(
                self,
                "Discard rows",
                "Discard the %s rows held for export?" % f"{self.engine.session.row_count:,}",
            )
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                self.record_button.setChecked(True)
                return
        self.engine.new_recording()

    def _on_sync_time(self) -> None:
        calendar = rtc_module.pc_calendar()
        self._append_log(
            "SET_RTC %s (%s)" % (calendar.text, "PC wall clock" if not self.demo else "PC wall clock -> demo device")
        )
        self._send("SET_RTC", self.engine.sync_rtc_from_pc)

    def _on_set_time(self, calendar: protocol.RtcCalendar) -> None:
        self._append_log("SET_RTC %s (picked)" % calendar.text)
        self._send("SET_RTC", lambda: self.engine.set_rtc(calendar))

    # ------------------------------------------------------------------ exports
    def _on_export(self, *, csv: bool) -> None:
        session = self.engine.session
        if session.row_count == 0:
            QtWidgets.QMessageBox.warning(
                self,
                "Nothing recorded",
                "No rows have been recorded, so there is nothing to export.\n"
                "Start recording and let ECG_BATCH frames arrive first.",
            )
            return
        stem = default_stem(session)
        if csv:
            path, _chosen = QtWidgets.QFileDialog.getSaveFileName(
                self, "Export CSV", "%s.csv" % stem, "CSV files (*.csv);;All files (*)"
            )
            writer = export_csv
        else:
            path, _chosen = QtWidgets.QFileDialog.getSaveFileName(
                self, "Export XLSX", "%s.xlsx" % stem, "Excel workbook (*.xlsx);;All files (*)"
            )
            writer = export_xlsx
        if not path:
            return
        QtWidgets.QApplication.setOverrideCursor(QtGui.QCursor(QtCore.Qt.CursorShape.WaitCursor))
        try:
            result = writer(session, path)
        except Exception as exc:
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.critical(self, "Export failed", "%s" % exc)
            self._append_log("export failed: %s" % exc)
            return
        QtWidgets.QApplication.restoreOverrideCursor()
        self._append_log("wrote %s" % result.describe())
        self.status_note.setText("last export: %s" % result.describe())
        self.panel.show_command_result(result.describe(), ok=True)

    # ------------------------------------------------------------------ repaint
    def _repaint(self) -> None:
        """The only place protocol state becomes screen state. 25 times a second."""
        metrics = self.engine.metrics()
        indices, values = self.engine.plot_window(self.pane.window_seconds)
        self.pane.set_data(
            indices,
            values,
            sample_rate_hz=metrics["sample_rate_hz"],
            note=DEMO_SHORT if metrics["demo"] else "",
        )
        self._paint_cards(metrics)
        self._paint_strip(metrics)
        self._paint_panel(metrics)

    def _paint_cards(self, m: dict[str, Any]) -> None:
        self.cards.set_heart_rate(
            m["hr_bpm"], m["hr_state"], hr_valid=m["hr_valid"], flags_present=m["hello_present"]
        )
        self.cards.set_temperature(
            m["temp_centi"],
            m["temp_state"],
            certified=m["temp_certified"],
            probe_hw_detect=m["probe_hw_detect"],
        )
        self.cards.set_lead(m["lead"], lead_hw_detect=m["lead_hw_detect"])
        self.cards.set_recording(
            active=m["recording_active"], seconds=m["recording_seconds"], rows=m["recording_rows"]
        )
        self.cards.set_loss(m["samples_missing"], m["index_gaps"], m["sequence_missing"])
        self.cards.set_crc_errors(m["crc_errors"], m["discarded"])

    def _paint_strip(self, m: dict[str, Any]) -> None:
        state = "demo (synthetic)" if m["demo"] else (m["link_label"] or "closed")
        self.strip.set_link(state if m["link_open"] else ("demo closed" if m["demo"] else state), "good" if m["link_open"] else "idle")
        self.strip.set_throughput(m["packets_per_s"], m["bytes_per_s"])
        self.strip.set_packets(m["packets"])
        self.strip.set_dropped(m["sequence_missing"])
        self.strip.set_index_gaps(m["index_gaps"], m["samples_missing"])
        self.strip.set_crc_errors(m["crc_errors"], m["discarded"])
        self.strip.set_samples(m["samples"], m["sample_rate_hz"])
        notes = []
        if m["malformed_batches"]:
            notes.append("%d malformed batch(es)" % m["malformed_batches"])
        if m["temp_disagreements"]:
            notes.append("%d temp_state route disagreements" % m["temp_disagreements"])
        if m["pending_bytes"]:
            notes.append("%d byte(s) awaiting the rest of a frame" % m["pending_bytes"])
        self.strip.set_note(" | ".join(notes))

    def _paint_panel(self, m: dict[str, Any]) -> None:
        if m["hello_present"]:
            self.panel.show_hello(self.engine.hello)
        self.panel.show_status_line(m["device_line"])
        if m["link_open"]:
            self.panel.set_detail(
                "%s | %s samples buffered for the plot | %s"
                % (m["link_label"], f"{m['buffered']:,}", _flag_text(m["flags"], m["flags_raw"]))
            )

    # -------------------------------------------------------------------- misc
    def _append_log(self, text: str) -> None:
        stamp = dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.log.appendPlainText("[%s] %s" % (stamp, text))

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802 - Qt naming
        """Stop the reader thread before the window goes away."""
        self.timer.stop()
        # A session left "recording" past the window's death has no stop time and
        # no owner, so its Summary would describe a recording still in progress.
        if self.engine.session.is_recording:
            self.engine.stop_recording()
        try:
            self.worker.request_close()
        except Exception:
            pass
        self.worker.stop()
        super().closeEvent(event)


DEMO_SHORT = "DEMO / SYNTHETIC -- generated on this PC"


def _flag_text(flags: protocol.Flags | None, raw: int) -> str:
    if flags is None:
        return "flags 0x%04X" % raw
    return (
        "flags 0x%04X lead=%s temp=%s hr_valid=%d notch=%s"
        % (
            raw,
            flags.lead.name,
            flags.temp.name,
            int(flags.hr_valid),
            flags.notch.text,
        )
    )


# =================================================================== bootstrap
def _apply_style(app: QtWidgets.QApplication) -> None:
    """A dark, low-glare theme: an ECG trace is easier to read against it."""
    app.setStyle("Fusion")
    palette = QtGui.QPalette()
    colours = {
        QtGui.QPalette.Window: "#15191f",
        QtGui.QPalette.Base: "#10151b",
        QtGui.QPalette.Text: "#dbe4ee",
        QtGui.QPalette.WindowText: "#dbe4ee",
        QtGui.QPalette.Button: "#222a33",
        QtGui.QPalette.ButtonText: "#dbe4ee",
        QtGui.QPalette.Highlight: "#2f6fb0",
        QtGui.QPalette.HighlightedText: "#ffffff",
        QtGui.QPalette.ToolTipBase: "#222a33",
        QtGui.QPalette.ToolTipText: "#dbe4ee",
        QtGui.QPalette.PlaceholderText: "#6b7885",
    }
    for role, hex_value in colours.items():
        palette.setColor(role, QtGui.QColor(hex_value))
    palette.setColor(QtGui.QPalette.BrightText, QtGui.QColor("#ff6b6b"))
    app.setPalette(palette)
    app.setStyleSheet(
        """
        QGroupBox { border: 1px solid #2b333c; border-radius: 5px; margin-top: 10px;
                    padding: 6px; font-weight: 600; }
        QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
        QFrame#MetricCard { background-color: #1a2129; border: 1px solid #2b333c;
                            border-radius: 5px; }
        QLabel#CardTitle { color: #8ab4f8; font-size: 10px; font-weight: 700; }
        QLabel#CardValue { color: #dbe4ee; }
        QLabel#CardSub { color: #7d8b99; font-size: 9px; }
        QPushButton { padding: 4px 10px; }
        QPushButton:checked { background-color: #2f6fb0; color: #ffffff; }
        QPlainTextEdit#EventLog { color: #a9b7c6; font-size: 9px; }
        QStatusBar { background-color: #12161b; }
        """
    )


def run(
    *,
    demo: bool = False,
    port: str = "",
    baud: int = DEFAULT_BAUD,
    demo_hr: float = 72.0,
    demo_uncalibrated: bool = False,
    window_seconds: float = DEFAULT_WINDOW_SECONDS,
    smoke_seconds: float = 0.0,
    argv: list[str] | None = None,
) -> int:
    """Build the QApplication and enter the event loop; returns the exit code.

    ``smoke_seconds`` is the headless self-check: the window is built, the loop
    runs for that long, and a one-line counter summary goes to stdout instead of
    a window staying open.  That is what makes "it works" verifiable on a machine
    with no display, and it is what the tests use.
    """
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(argv or [])
    _apply_style(app)
    window = MonitorWindow(
        demo=demo,
        port=port,
        baud=baud,
        demo_hr=demo_hr,
        demo_uncalibrated=demo_uncalibrated,
        window_seconds=window_seconds,
    )
    window.show()
    if smoke_seconds > 0:
        report: dict[str, Any] = {}

        def _finish() -> None:
            metrics = window.engine.metrics()
            report.update(
                {
                    "packets": metrics["packets"],
                    "samples": metrics["samples"],
                    "plotted": window.pane.plotted_points,
                    "crc_errors": metrics["crc_errors"],
                    "index_gaps": metrics["index_gaps"],
                    "rows": metrics["recording_rows"],
                    "hello": metrics["hello_present"],
                    "link_open": metrics["link_open"],
                    "demo": metrics["demo"],
                }
            )
            print("SMOKE " + " ".join("%s=%s" % (key, value) for key, value in sorted(report.items())))
            window.close()
            app.quit()

        QtCore.QTimer.singleShot(int(smoke_seconds * 1000), _finish)
    return int(app.exec())
