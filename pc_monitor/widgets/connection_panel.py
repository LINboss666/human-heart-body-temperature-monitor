"""Connection panel: port, baud, connect/disconnect, live device identity.

Three groups stacked vertically, in the order a student actually needs them:

1. **Link** -- which serial port, which baud, and one button that is both
   connect and disconnect.  The port list is filled by the worker thread
   (:func:`pc_monitor.serial_worker.list_serial_ports` is slow enough that it
   must never run on the GUI thread), and the combo is editable because a
   board that enumerates badly still has a COM number that works.
2. **Device** -- what ``HELLO`` said, plus a live line from ``STATUS``.
   Capabilities are rendered as *present* and *absent* rather than as a hex
   mask, because ``docs/PROTOCOL.md`` is explicit that "the host must not
   render a capability it has not been given": the absent list is the point.
3. **Device time** -- ``SET_RTC`` / ``GET_RTC``.  "Sync Device Time" sends the
   PC's wall clock; the picker sends a chosen instant.  See
   :mod:`pc_monitor.rtc` for why local wall-clock time is the default.

The panel owns no state beyond widget values: it emits requests and exposes
setters.  :mod:`pc_monitor.app` decides what a request means.
"""

from __future__ import annotations

import datetime as dt

from PySide6 import QtCore, QtGui, QtWidgets

from .. import protocol
from ..rtc import describe_epoch, validate_calendar
from .metric_cards import QUALITY_COLOURS

__all__ = ["ConnectionPanel", "COMMON_BAUD_RATES", "DEFAULT_BAUD"]

#: ``UART_BAUD_RATE`` in ``app_config.h`` is 230400; the rest are conveniences.
DEFAULT_BAUD = 230400
COMMON_BAUD_RATES: tuple[int, ...] = (9600, 19200, 57600, 115200, 230400, 460800, 921600)

_LINK_STYLE = (
    "QLabel#LinkState {{ padding: 3px 8px; border-radius: 4px;"
    " background-color: {bg}; color: {fg}; font-weight: 600; }}"
)
_CLOSED = ("#2b333c", "#c8d2dc")
_OPEN = ("#134f2c", "#d6f5e3")
_ERROR = ("#6b1420", "#ffdde1")


class ConnectionPanel(QtWidgets.QGroupBox):
    """Left-hand control column: link, device identity, device clock."""

    connectRequested = QtCore.Signal(str, int)  # port, baud
    disconnectRequested = QtCore.Signal()
    refreshRequested = QtCore.Signal()
    rtcSyncRequested = QtCore.Signal()  # "Sync Device Time" == PC wall clock
    rtcSetRequested = QtCore.Signal(object)  # protocol.RtcCalendar
    rtcGetRequested = QtCore.Signal()
    pingRequested = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None, *, demo: bool = False) -> None:
        super().__init__("Link & device", parent)
        self._demo = False
        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(8)
        root.addWidget(self._build_link_group())
        root.addWidget(self._build_device_group())
        root.addWidget(self._build_rtc_group())
        root.addStretch(1)
        self._connected = False
        self._pending_refresh = False
        self._set_link("closed", *_CLOSED)
        self.set_demo_mode(demo)

    # ------------------------------------------------------------------ demo
    def set_demo_mode(self, demo: bool) -> None:
        """Swap the port picker for an unmistakable synthetic-source label.

        The port row stays visible but inert: in demo mode no serial port is
        opened at all, and :meth:`current_port` returns the demo label so the
        window can log which source it actually used.
        """
        self._demo = bool(demo)
        if self._demo:
            from ..demo_source import DEMO_PORT_LABEL

            self.setTitle("Link & device -- DEMO: no hardware, no serial port")
            self.port_combo.blockSignals(True)
            self.port_combo.clear()
            self.port_combo.addItem(DEMO_PORT_LABEL)
            self.port_combo.setEditable(False)
            self.port_combo.setEnabled(False)
            self.port_combo.blockSignals(False)
            self.refresh_button.setEnabled(False)
            self.baud_combo.setEnabled(False)
            self.set_detail("synthetic ECG generated on this PC")
        else:
            self.setTitle("Link & device")
            self.port_combo.setEnabled(not self._connected)
            self.port_combo.setEditable(True)
            self.refresh_button.setEnabled(not self._connected)
            self.baud_combo.setEnabled(not self._connected)

    @property
    def is_demo(self) -> bool:
        return self._demo

    # ------------------------------------------------------------------ build
    def _build_link_group(self) -> QtWidgets.QWidget:
        box = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(box)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)

        grid.addWidget(QtWidgets.QLabel("Serial port"), 0, 0)
        self.port_combo = QtWidgets.QComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
        self.port_combo.setToolTip(
            "Ports are enumerated by the worker thread. Type a port name (e.g. COM7) if "
            "the list is empty or stale."
        )
        self.port_combo.lineEdit().setPlaceholderText("COMn / /dev/ttyUSBn")
        grid.addWidget(self.port_combo, 0, 1)

        self.refresh_button = QtWidgets.QPushButton("Refresh")
        self.refresh_button.clicked.connect(self._on_refresh)
        grid.addWidget(self.refresh_button, 0, 2)

        grid.addWidget(QtWidgets.QLabel("Baud"), 1, 0)
        self.baud_combo = QtWidgets.QComboBox()
        self.baud_combo.setEditable(True)
        for rate in COMMON_BAUD_RATES:
            self.baud_combo.addItem(f"{rate:,}", rate)
        self.baud_combo.setCurrentIndex(self.baud_combo.findData(DEFAULT_BAUD))
        self.baud_combo.setToolTip(
            "Contract default is 230400 8N1 (UART_BAUD_RATE), 23040 byte/s of wire capacity."
        )
        grid.addWidget(self.baud_combo, 1, 1)

        self.connect_button = QtWidgets.QPushButton("Connect")
        self.connect_button.setCheckable(True)
        self.connect_button.clicked.connect(self._on_connect_toggled)
        grid.addWidget(self.connect_button, 1, 2)

        self.link_label = QtWidgets.QLabel("closed")
        self.link_label.setObjectName("LinkState")
        self.link_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(self.link_label, 2, 0, 1, 3)

        self.detail_label = QtWidgets.QLabel("idle")
        self.detail_label.setObjectName("CardSub")
        self.detail_label.setWordWrap(True)
        grid.addWidget(self.detail_label, 3, 0, 1, 3)
        return box

    def _build_device_group(self) -> QtWidgets.QWidget:
        box = QtWidgets.QGroupBox("Device")
        form = QtWidgets.QFormLayout(box)
        form.setContentsMargins(8, 4, 8, 4)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(2)
        self.identity_label = QtWidgets.QLabel("no HELLO received")
        self.identity_label.setWordWrap(True)
        self.caps_label = QtWidgets.QLabel("unknown until HELLO")
        self.caps_label.setWordWrap(True)
        self.caps_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self.device_label = QtWidgets.QLabel("waiting for STATUS")
        self.device_label.setWordWrap(True)
        self.echo_label = QtWidgets.QLabel("no command answered yet")
        self.echo_label.setWordWrap(True)
        self.echo_label.setObjectName("CardSub")
        form.addRow("Identity:", self.identity_label)
        form.addRow("Can do:", self.caps_label)
        form.addRow("Now:", self.device_label)
        form.addRow("Device said:", self.echo_label)
        return box

    def _build_rtc_group(self) -> QtWidgets.QWidget:
        box = QtWidgets.QGroupBox("Device time (RTC)")
        grid = QtWidgets.QGridLayout(box)
        grid.setContentsMargins(8, 4, 8, 4)
        self.rtc_edit = QtWidgets.QDateTimeEdit(dt.datetime.now())
        self.rtc_edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.rtc_edit.setCalendarPopup(True)
        self.rtc_edit.setToolTip(
            "SET_RTC carries calendar fields only: no zone, no UTC offset. The picker is "
            "local wall-clock time, matching what the OLED shows. See pc_monitor/rtc.py."
        )
        grid.addWidget(self.rtc_edit, 0, 0, 1, 3)

        self.sync_button = QtWidgets.QPushButton("Sync Device Time")
        self.sync_button.setToolTip("Send the PC's current wall clock with SET_RTC.")
        self.sync_button.clicked.connect(self.rtcSyncRequested.emit)
        grid.addWidget(self.sync_button, 1, 0)

        self.apply_time_button = QtWidgets.QPushButton("Set picked time")
        self.apply_time_button.clicked.connect(self._on_set_picked)
        grid.addWidget(self.apply_time_button, 1, 1)

        self.get_time_button = QtWidgets.QPushButton("Query")
        self.get_time_button.setToolTip("Send GET_RTC and show the RTC_RESPONSE.")
        self.get_time_button.clicked.connect(self.rtcGetRequested.emit)
        grid.addWidget(self.get_time_button, 1, 2)

        self.ping_button = QtWidgets.QPushButton("Ping")
        self.ping_button.setToolTip("PKT_PING with a 4-byte token; the device echoes a PONG.")
        self.ping_button.clicked.connect(self.pingRequested.emit)
        grid.addWidget(self.ping_button, 2, 0)

        self.rtc_label = QtWidgets.QLabel("device clock not set from this host")
        self.rtc_label.setObjectName("CardSub")
        self.rtc_label.setWordWrap(True)
        grid.addWidget(self.rtc_label, 2, 1, 1, 3)
        return box

    # ------------------------------------------------------------------ values
    def current_port(self) -> str:
        return self.port_combo.currentText().strip()

    def current_baud(self) -> int:
        raw = self.baud_combo.currentText().replace(",", "").replace("_", "").strip()
        data = self.baud_combo.currentData()
        try:
            return int(raw) if raw else int(data or DEFAULT_BAUD)
        except ValueError:
            return int(data or DEFAULT_BAUD)

    def picked_calendar(self) -> protocol.RtcCalendar:
        moment: QtCore.QDateTime = self.rtc_edit.dateTime()
        py: dt.datetime = moment.toPython()
        return protocol.RtcCalendar(
            year=py.year, month=py.month, day=py.day, hour=py.hour, minute=py.minute, second=py.second
        )

    # ------------------------------------------------------------------ slots
    def set_ports(self, ports: list[tuple[str, str]]) -> None:
        """Replace the dropdown, preserving the user's typed selection."""
        if self._demo:
            return  # enumeration is meaningless in demo mode; keep the DEMO row
        keep = self.current_port()
        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        for device, description in ports:
            self.port_combo.addItem(f"{device}  {description}", device)
            self.port_combo.setItemData(self.port_combo.count() - 1, device, QtCore.Qt.ItemDataRole.UserRole)
        if not ports:
            self.port_combo.setEditText(keep)
        else:
            index = self._index_for(keep, ports)
            self.port_combo.setCurrentIndex(index)
        self.port_combo.blockSignals(False)
        self.refresh_button.setEnabled(True)
        self._pending_refresh = False
        if not ports and not keep:
            self.set_detail("no serial ports enumerated -- type a port name or use --demo")

    def _index_for(self, wanted: str, ports: list[tuple[str, str]]) -> int:
        for row, (device, _description) in enumerate(ports):
            if device == wanted:
                return row
        return 0

    def set_detail(self, text: str) -> None:
        self.detail_label.setText(text)

    def set_busy(self, busy: bool) -> None:
        """Grey out connect/refresh while a port open is in flight."""
        self._pending_refresh = bool(busy)
        self.refresh_button.setEnabled(not busy)
        if not self._connected:
            self.connect_button.setEnabled(not busy)

    def set_connected(self, connected: bool, label: str = "") -> None:
        self._connected = bool(connected)
        self.connect_button.blockSignals(True)
        self.connect_button.setChecked(bool(connected))
        self.connect_button.setText("Disconnect" if connected else "Connect")
        self.connect_button.blockSignals(False)
        self.connect_button.setEnabled(True)
        self.port_combo.setEnabled(not connected and not self._demo)
        if not self._demo:
            self.port_combo.setEditable(not connected)
            self.baud_combo.setEnabled(not connected)
            self.refresh_button.setEnabled(not connected)
        if connected:
            self._set_link(f"open: {label}", *_OPEN)
        else:
            self._set_link("closed", *_CLOSED)

    def set_link_error(self, message: str) -> None:
        self._set_link(f"error: {message}", *_ERROR)

    def _set_link(self, text: str, bg: str, fg: str) -> None:
        self.link_label.setText(text)
        self.link_label.setStyleSheet(_LINK_STYLE.format(bg=bg, fg=fg))

    # -- device side ---------------------------------------------------------
    def show_hello(self, hello: protocol.Hello) -> None:
        self.identity_label.setText(
            "fw %s, proto 0x%02X, %d Hz, batch<=%d, %d-bit ADC, caps 0x%04X"
            % (
                hello.fw_version,
                hello.proto_version,
                hello.sample_rate_hz,
                hello.batch_max_samples,
                hello.adc_bits,
                hello.caps,
            )
        )
        caps = protocol.Capability(hello.caps)
        present = [cap.name for cap in protocol.Capability if cap is not protocol.Capability.NONE and caps & cap]
        absent = [
            cap.name for cap in protocol.Capability if cap is not protocol.Capability.NONE and not caps & cap
        ]
        self.caps_label.setText(
            "present: %s\nabsent: %s" % (", ".join(present) or "none", ", ".join(absent) or "none")
        )

    def clear_device(self) -> None:
        self.identity_label.setText("no HELLO received")
        self.caps_label.setText("unknown until HELLO")
        self.device_label.setText("waiting for STATUS")
        self.echo_label.setText("no command answered yet")

    def show_status_line(self, text: str) -> None:
        self.device_label.setText(text)

    def show_command_result(self, text: str, *, ok: bool | None = None) -> None:
        colour = (
            QUALITY_COLOURS["good"]
            if ok
            else QUALITY_COLOURS["bad"]
            if ok is False
            else QUALITY_COLOURS["normal"]
        )
        self.echo_label.setText(text)
        self.echo_label.setStyleSheet("color: %s;" % colour)

    def show_rtc(self, calendar: protocol.RtcCalendar) -> None:
        if calendar.epoch is None:
            self.rtc_label.setText("device reports %s" % calendar.text)
        else:
            self.rtc_label.setText(
                "device reports %s (epoch %d = %s read as UTC)"
                % (calendar.text, calendar.epoch, describe_epoch(calendar.epoch))
            )
        parsed = QtCore.QDateTime.fromString(calendar.text, "yyyy-MM-dd HH:mm:ss")
        if parsed.isValid():
            self.rtc_edit.setDateTime(parsed)
        else:  # a device that answers nonsense must not crash the panel
            self.rtc_label.setText("device reported an unreadable calendar: %s" % (calendar.text,))

    # ---------------------------------------------------------------- handlers
    def _on_refresh(self) -> None:
        self.refresh_button.setEnabled(False)
        self._pending_refresh = True
        self.set_detail("enumerating ports...")
        self.refreshRequested.emit()

    def _on_connect_toggled(self, checked: bool) -> None:
        if checked:
            self.connect_button.setEnabled(False)
            self._set_link("opening...", *_CLOSED)
            self.connectRequested.emit(self.current_port(), self.current_baud())
        else:
            self.disconnectRequested.emit()

    def _on_set_picked(self) -> None:
        """Validate before sending: the firmware answers an impossible date with
        ``NACK_BAD_VALUE`` and does not change the clock, so the host should not
        pretend otherwise by sending one."""
        calendar = self.picked_calendar()
        try:
            validate_calendar(
                calendar.year,
                calendar.month,
                calendar.day,
                calendar.hour,
                calendar.minute,
                calendar.second,
            )
        except ValueError as exc:
            self.show_command_result("picker rejected, nothing sent: %s" % exc, ok=False)
            return
        self.rtcSetRequested.emit(calendar)
