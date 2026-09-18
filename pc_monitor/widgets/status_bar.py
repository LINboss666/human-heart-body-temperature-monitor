"""Status strip: serial state, throughput, integrity -- the counters that matter.

Goes in the main window's status bar as a permanent widget.  Everything here is
*derived* state the app already computed once per repaint; the strip owns no
counters and touches no protocol objects, so the 25 FPS timer has one writer.

Why both loss numbers appear side by side: ``docs/PROTOCOL.md`` calls
``first_sample_index`` continuity the authoritative loss measure, because it also
catches the device dropping its own DMA blocks -- but sequence gaps are the
link-level view and they disagree in exactly the interesting cases, so showing
only one of them would hide the other's story.
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

__all__ = ["StatusStrip"]

_FIELDS = (
    ("link", "serial: closed"),
    ("rate", "0.0 pkt/s"),
    ("packets", "0 pkt"),
    ("dropped", "0 dropped"),
    ("gaps", "0 idx gaps"),
    ("crc", "0 CRC err"),
    ("bytes", "0 B/s"),
    ("samples", "0 samples @ 1000 Hz"),
    ("note", ""),
)

_STYLE = (
    "QLabel#statusField {{ padding: 1px 6px; color: {fg};"
    " background-color: {bg}; border-right: 1px solid #2b333c; font-family: {mono}; }}"
)


class StatusStrip(QtWidgets.QWidget):
    """One row of sunken readouts for the window's status bar."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusStrip")
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        mono = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont)
        mono.setStyleHint(QtGui.QFont.StyleHint.Monospace)
        mono.setPointSizeF(max(8.0, mono.pointSizeF() - 1.0))
        self._labels: dict[str, QtWidgets.QLabel] = {}
        for name, initial in _FIELDS:
            label = QtWidgets.QLabel(initial)
            label.setObjectName("statusField")
            label.setFont(mono)
            label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
            self._labels[name] = label
            row.addWidget(label)
        row.addStretch(1)
        self.setStyleSheet(_STYLE.format(fg="#c8d2dc", bg="#1a2129", mono=mono.family()))
        self.set_link("closed", "idle")

    # ------------------------------------------------------------------ setters
    def _set(self, name: str, text: str) -> None:
        label = self._labels[name]
        if label.text() != text:
            label.setText(text)

    def set_link(self, state: str, quality: str = "idle") -> None:
        """``state`` is a short word: closed / opening / open / error / demo."""
        self._set("link", "serial: %s" % state)
        palette = {
            "idle": ("#1a2129", "#8ab4f8"),
            "good": ("#123522", "#7ee2a8"),
            "warn": ("#3a2b06", "#ffd479"),
            "bad": ("#3a0d16", "#ff9aa6"),
        }
        bg, fg = palette.get(quality, palette["idle"])
        self._labels["link"].setStyleSheet(
            "QLabel#statusField { padding: 1px 6px; color: %s; background-color: %s; font-weight: 600; }"
            % (fg, bg)
        )

    def set_throughput(self, packets_per_s: float, bytes_per_s: float) -> None:
        self._set("rate", "%.1f pkt/s" % packets_per_s)
        self._set("bytes", "%.0f B/s" % bytes_per_s)

    def set_packets(self, packets: int) -> None:
        self._set("packets", "%s pkt" % f"{int(packets):,}")

    def set_dropped(self, packets_missing: int) -> None:
        self._set("dropped", "%s dropped" % f"{int(packets_missing):,}")

    def set_index_gaps(self, gaps: int, samples_missing: int) -> None:
        self._set("gaps", "%s gaps / %s samples" % (f"{int(gaps):,}", f"{int(samples_missing):,}"))

    def set_crc_errors(self, count: int, discarded: int) -> None:
        self._set("crc", "%s CRC err / %s B" % (f"{int(count):,}", f"{int(discarded):,}"))

    def set_samples(self, samples: int, rate_hz: float) -> None:
        self._set("samples", "%s samples @ %.0f Hz" % (f"{int(samples):,}", rate_hz))

    def set_note(self, text: str) -> None:
        """Protocol/layout warnings that must be visible without a dialog."""
        self._set("note", text)
