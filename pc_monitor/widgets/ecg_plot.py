"""ECG plot pane: a rolling 1 kHz window that is redrawn by a timer, never per sample.

Two properties the brief demands, and how this class gets them:

* **The x axis is device time, not arrival order.**  The app hands over
  ``(sample_index, value)`` pairs and the axis is ``index / sample_rate_hz``, so a
  dropout appears as a *hole* in the trace instead of a straight diagonal line
  hiding a lost 20 ms.  :func:`with_gap_breaks` inserts a ``NaN`` at every
  discontinuity and the curve uses ``connect='finite'``, so pyqtgraph lifts the
  pen exactly there.
* **Pause stops the pixels, not the data.**  :meth:`set_paused` only suppresses
  repaints; the caller keeps writing into the ring buffer, which is why resuming
  shows the *current* window rather than the one that was frozen.  A paused view
  is labelled, so nobody mistakes a still trace for a flat one.

Zoom is pyqtgraph's own: wheel to zoom, drag to pan.  The first manual range
change switches the pane out of follow mode (the button says so), because a
rolling axis that fights the user's zoom is worse than no rolling axis.
"""

from __future__ import annotations

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

import pyqtgraph as pg

from ..protocol import SAMPLE_RATE_HZ

__all__ = ["EcgPane", "WINDOW_CHOICES_S", "DEFAULT_WINDOW_S", "with_gap_breaks"]

#: Rolling-window presets, in seconds.  10 s == 10000 samples at 1 kHz.
WINDOW_CHOICES_S: tuple[float, ...] = (1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0)
DEFAULT_WINDOW_S = 10.0

_TRACE_COLOUR = "#7fd1ff"
_DEMO_TRACE_COLOUR = "#ff9100"
_MIDRAIL_COLOUR = "#4d5a66"


def with_gap_breaks(
    indices: np.ndarray,
    values: np.ndarray,
    sample_rate_hz: float = SAMPLE_RATE_HZ,
    expected_step: int = 1,
) -> tuple[np.ndarray, np.ndarray, int]:
    """``(seconds, millivolts-with-NaN-holes, gaps)`` for a truthful trace.

    A ``NaN`` is inserted wherever ``diff(indices) != expected_step``, so the pen
    lifts across a dropout.  Returns the gap count so the pane can display it --
    a hole the viewer cannot see is a hole that gets explained away.
    """
    indices = np.asarray(indices, dtype=np.int64)
    values = np.asarray(values, dtype=np.float64)
    size = min(indices.size, values.size)
    indices, values = indices[:size], values[:size]
    if size == 0:
        return np.empty(0, np.float64), np.empty(0, np.float64), 0
    rate = float(sample_rate_hz) if sample_rate_hz else float(SAMPLE_RATE_HZ)
    times = indices.astype(np.float64) / rate
    if size == 1:
        return times, values, 0
    steps = np.diff(indices)
    breaks = np.nonzero(steps != int(expected_step))[0]
    if breaks.size == 0:
        return times, values, 0
    positions = breaks + 1 + np.arange(breaks.size)
    times = np.insert(times, positions, np.nan)
    values = np.insert(values, positions, np.nan)
    return times, values, int(breaks.size)


class EcgPane(QtWidgets.QWidget):
    """The waveform: controls strip on top, plot below."""

    pauseRequested = QtCore.Signal(bool)
    windowChanged = QtCore.Signal(float)

    def __init__(
        self,
        parent: QtWidgets.QWidget | None = None,
        *,
        window_seconds: float = DEFAULT_WINDOW_S,
        midrail_mv: float | None = 1650.0,
    ) -> None:
        super().__init__(parent)
        self._paused = False
        self._follow = True
        self._window_s = float(window_seconds)
        self._rate_hz = float(SAMPLE_RATE_HZ)
        self._plotted = 0
        self._gaps = 0
        self._latest_t = 0.0
        self._note = ""
        self._auto_y = True
        self._last_volts = np.empty(0, dtype=np.float64)
        self._demo = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self._build_controls())

        pg.setConfigOptions(antialias=False, background="#10151b", foreground="#c8d2dc")
        self.plot = pg.PlotWidget()
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("left", "ECG", units="mV at MCU pin")
        self.plot.setLabel("bottom", "device time", units="s")
        self.plot.setTitle("raw, unfiltered ADC code -- not a body-surface potential")
        self.plot.setMenuEnabled(False)  # right-click menu invites accidental log-scale
        self.plot.setMouseEnabled(x=True, y=True)
        self.plot.getViewBox().setMouseMode(pg.ViewBox.PanMode)
        self.plot.hideButtons()
        self.curve = pg.PlotDataItem(
            pen=pg.mkPen(_TRACE_COLOUR, width=1),
            connect="finite",  # NaNs from with_gap_breaks() lift the pen
        )
        self.curve.setDownsampling(auto=True, method="peak")
        self.curve.setClipToView(True)
        self.plot.addItem(self.curve)

        if midrail_mv is not None:
            # `label` is a format string and `labelOpts` the TextItem kwargs; the
            # two must not be merged into one dict argument.
            rail = pg.InfiniteLine(
                pos=midrail_mv,
                angle=0,
                movable=False,
                pen=pg.mkPen(_MIDRAIL_COLOUR, width=1, style=QtCore.Qt.PenStyle.DashLine),
                label="assumed mid-rail 1650 mV (gain/offset UNVERIFIED)",
                labelOpts={
                    "color": _MIDRAIL_COLOUR,
                    "fill": (0, 0, 0, 150),
                    "position": 0.02,
                    "anchors": [(0.0, 0.5), (1.0, 0.5)],
                },
            )
            self.plot.addItem(rail)

        self.notice = pg.TextItem("", color="#ffb703", anchor=(0.0, 0.0))
        self.notice.setZValue(10)
        self.plot.addItem(self.notice, ignoreBounds=True)

        self.plot.sigRangeChangedManually.connect(self._on_manual_range)
        # We own both axes from here: see _fit_y / set_data.
        self.plot.getViewBox().disableAutoRange()
        self.plot.setYRange(1550, 1750, padding=0)
        layout.addWidget(self.plot, 1)
        self._refresh_notice()

    # ------------------------------------------------------------------ build
    def _build_controls(self) -> QtWidgets.QWidget:
        bar = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        self.pause_button = QtWidgets.QPushButton("Pause display")
        self.pause_button.setCheckable(True)
        self.pause_button.setShortcut(QtGui.QKeySequence("Space"))
        self.pause_button.setToolTip(
            "Freeze the picture only. Acquisition and recording keep running, so nothing "
            "is lost: resuming shows the current window (Space)."
        )
        self.pause_button.toggled.connect(self._on_pause_toggled)
        row.addWidget(self.pause_button)

        row.addWidget(QtWidgets.QLabel("Window"))
        self.window_combo = QtWidgets.QComboBox()
        for seconds in WINDOW_CHOICES_S:
            self.window_combo.addItem("%g s" % seconds, seconds)
        index = self.window_combo.findData(DEFAULT_WINDOW_S)
        self.window_combo.setCurrentIndex(max(0, index))
        self.window_combo.currentIndexChanged.connect(self._on_window_changed)
        self.window_combo.setToolTip("10 s default = 10000 samples at 1 kHz.")
        row.addWidget(self.window_combo)

        self.auto_y_check = QtWidgets.QCheckBox("Auto Y")
        self.auto_y_check.setChecked(True)
        self.auto_y_check.toggled.connect(self._on_auto_y)
        row.addWidget(self.auto_y_check)

        self.follow_check = QtWidgets.QCheckBox("Follow newest")
        self.follow_check.setChecked(True)
        self.follow_check.setToolTip("Turn off to zoom/pan and keep that range.")
        self.follow_check.toggled.connect(self._on_follow_toggled)
        row.addWidget(self.follow_check)

        self.reset_button = QtWidgets.QPushButton("Reset view")
        self.reset_button.clicked.connect(self.reset_view)
        row.addWidget(self.reset_button)

        self.readout = QtWidgets.QLabel("no samples yet")
        self.readout.setObjectName("CardSub")
        self.readout.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        row.addWidget(self.readout, 1)
        return bar

    # ------------------------------------------------------------------- data
    def set_data(
        self,
        indices: np.ndarray,
        values: np.ndarray,
        *,
        sample_rate_hz: float | None = None,
        note: str = "",
    ) -> None:
        """Repaint with the caller's windowed snapshot.  Called by the timer only."""
        if sample_rate_hz:
            self._rate_hz = float(sample_rate_hz)
        self._note = note
        if self._paused:
            # Deliberate: the ring buffer keeps growing, this view just stops moving.
            self._set_readout()
            return
        times, volts, gaps = with_gap_breaks(indices, values, self._rate_hz)
        self._gaps = gaps
        self._plotted = int(np.count_nonzero(~np.isnan(volts)))
        self._last_volts = volts
        self.curve.setData(times, volts)
        if self._auto_y:
            self._fit_y(volts)
        if times.size:
            self._latest_t = float(np.nanmax(times))
            if self._follow:
                self.plot.setXRange(self._latest_t - self._window_s, self._latest_t, padding=0)
        self._set_readout()
        self._refresh_notice()

    def clear_trace(self) -> None:
        self.curve.setData(np.empty(0), np.empty(0))
        self._plotted = 0
        self._gaps = 0
        self._latest_t = 0.0
        self._set_readout()

    # ------------------------------------------------------------------ state
    @property
    def window_seconds(self) -> float:
        return self._window_s

    @property
    def is_paused(self) -> bool:
        return bool(self._paused)

    @property
    def is_following(self) -> bool:
        return bool(self._follow)

    @property
    def plotted_points(self) -> int:
        return self._plotted

    @property
    def gap_count(self) -> int:
        return self._gaps

    def set_paused(self, paused: bool) -> None:
        """Programmatic pause; emits nothing, so the app cannot loop with itself."""
        self.pause_button.setChecked(bool(paused))

    def set_note(self, text: str) -> None:
        self._note = text
        self._refresh_notice()

    def set_demo_style(self, demo: bool) -> None:
        """Make the plot itself say it is synthetic.

        The banner in the window is the loud version; this is the version that
        survives a screenshot cropped to the trace, which is the one that ends up
        in a report.
        """
        self._demo = bool(demo)
        colour = _DEMO_TRACE_COLOUR if self._demo else _TRACE_COLOUR
        self.curve.setPen(pg.mkPen(colour, width=1))
        self.plot.setTitle(
            ("DEMO / SYNTHETIC WAVEFORM -- generated on this PC, not measured\n"
             if self._demo
             else "")
            + "raw, unfiltered ADC code -- not a body-surface potential"
        )

    def _fit_y(self, volts: np.ndarray) -> None:
        """Fit Y to the finite part of the trace, with headroom for the rail line."""
        finite = volts[np.isfinite(volts)]
        if finite.size == 0:
            return
        low = float(finite.min())
        high = float(finite.max())
        margin = max(20.0, (high - low) * 0.08)
        self.plot.setYRange(low - margin, high + margin, padding=0)

    def _set_readout(self) -> None:
        if not self._plotted:
            self.readout.setText("no samples yet")
            return
        stamp = "t=%.2f s" % self._latest_t
        gaps = "" if not self._gaps else " | %d gap(s) in window" % self._gaps
        state = "PAUSED (data still acquired)" if self._paused else "%g s window" % self._window_s
        self.readout.setText("%s | %s pts | %s%s" % (stamp, f"{self._plotted:,}", state, gaps))

    def _refresh_notice(self) -> None:
        parts = []
        if self._paused:
            parts.append("DISPLAY PAUSED -- acquisition continues")
        if self._note:
            parts.append(self._note)
        text = "\n".join(parts)
        self.notice.setText(text)
        self.notice.setColor(QtGui.QColor("#ffb703" if self._paused else "#f1fa8c"))
        left, right = self.plot.getViewBox().viewRange()[0]
        bottom, top = self.plot.getViewBox().viewRange()[1]
        self.notice.setPos(left + 0.01 * (right - left), top - 0.02 * (top - bottom))

    # ---------------------------------------------------------------- handlers
    def _on_pause_toggled(self, checked: bool) -> None:
        self._paused = bool(checked)
        self.pause_button.setText("Resume display" if checked else "Pause display")
        self._set_readout()
        self._refresh_notice()
        self.pauseRequested.emit(bool(checked))

    def _on_window_changed(self, _index: int) -> None:
        seconds = self.window_combo.currentData() or DEFAULT_WINDOW_S
        self._window_s = float(seconds)
        if self._follow and self._latest_t:
            self.plot.setXRange(self._latest_t - self._window_s, self._latest_t, padding=0)
        self._set_readout()
        self.windowChanged.emit(self._window_s)

    def _on_auto_y(self, checked: bool) -> None:
        self._auto_y = bool(checked)
        if checked:
            self._fit_y(self._last_volts)

    def _on_follow_toggled(self, checked: bool) -> None:
        self._follow = bool(checked)
        if self._follow and self._latest_t:
            self.plot.setXRange(self._latest_t - self._window_s, self._latest_t, padding=0)
        self._set_readout()

    def _on_manual_range(self) -> None:
        """A mouse zoom/pan: stop following, and say so on the button."""
        if self._follow:
            self.follow_check.setChecked(False)

    def reset_view(self) -> None:
        """Undo any manual zoom: follow the newest sample again, refit Y."""
        self.follow_check.setChecked(True)
        self.auto_y_check.setChecked(True)
        if self._latest_t:
            self.plot.setXRange(self._latest_t - self._window_s, self._latest_t, padding=0)
        self._fit_y(self._last_volts)
