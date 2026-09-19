"""Realtime metric cards.

Each card is a title, a large value, and a qualifier line.  The qualifier is not
decoration: it is where the honest hedging goes, because this protocol carries
"we did not measure that" as a first-class answer.

Two rules from the contract are enforced here rather than in the caller:

* ``LEAD_UNKNOWN`` renders as its own neutral state, never as ``DISCONNECTED``.
  The firmware reports UNKNOWN because ``CAP_LEAD_HW_DETECT`` is clear -- there
  is no lead-off hardware -- and the UI must not upgrade that into an electrode
  verdict.
* Temperature renders ``--.-`` whenever the device did not certify degrees
  (``TEMP_UNCALIBRATED``), so an uncalibrated build can never flash a
  plausible-looking 36.6 on screen.
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from ..protocol import LeadState, TempState

__all__ = ["MetricCard", "MetricStrip", "QUALITY_COLOURS", "TEMP_BLANK"]

#: What the temperature card shows when the device has not certified degrees.
TEMP_BLANK = "--.-"

QUALITY_COLOURS: dict[str, str] = {
    "idle": "#8ab4f8",
    "good": "#1a8f4c",
    "normal": "#1f2933",
    "warn": "#b26a00",
    "bad": "#b00020",
    # Neutral, deliberately *not* red: "we do not know" is not "it fell off".
    "unknown": "#5c6b7a",
}

_FONTS = {
    "idle": "font-size: 20px; font-weight: 600; color: %s;",
    "good": "font-size: 20px; font-weight: 600; color: %s;",
    "normal": "font-size: 20px; font-weight: 600; color: %s;",
    "warn": "font-size: 20px; font-weight: 700; color: %s;",
    "bad": "font-size: 20px; font-weight: 700; color: %s;",
    "unknown": "font-size: 17px; font-weight: 600; color: %s;",
}


class MetricCard(QtWidgets.QFrame):
    """One labelled number with a colour-coded confidence."""

    def __init__(
        self,
        title: str,
        *,
        unit: str = "",
        initial: str = "--",
        sub: str = "waiting for device",
        tooltip: str = "",
        quality: str = "idle",
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.setObjectName("MetricCard")
        self._quality = ""
        self._unit = unit
        self._value_text = initial

        box = QtWidgets.QVBoxLayout(self)
        box.setContentsMargins(8, 6, 8, 6)
        box.setSpacing(1)

        self.title_label = QtWidgets.QLabel(title)
        self.title_label.setObjectName("CardTitle")
        self.value_label = QtWidgets.QLabel(self._compose(initial))
        self.value_label.setObjectName("CardValue")
        self.sub_label = QtWidgets.QLabel(sub)
        self.sub_label.setObjectName("CardSub")
        self.sub_label.setWordWrap(True)
        self.sub_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)

        box.addWidget(self.title_label)
        box.addWidget(self.value_label)
        box.addWidget(self.sub_label)
        if tooltip:
            self.setToolTip(tooltip)
            self.title_label.setToolTip(tooltip)
        self.set_quality(quality)

    # -- value -------------------------------------------------------------
    def _compose(self, value: str) -> str:
        return "%s %s" % (value, self._unit) if self._unit and value not in ("", "--") else value

    def set_value(self, value: str, *, quality: str | None = None) -> None:
        if value != self._value_text:
            self._value_text = value
            self.value_label.setText(self._compose(value))
        if quality is not None:
            self.set_quality(quality)

    def set_sub(self, text: str) -> None:
        if text != self.sub_label.text():
            self.sub_label.setText(text)

    def set_quality(self, quality: str) -> None:
        if quality == self._quality:
            return
        self._quality = quality
        colour = QUALITY_COLOURS.get(quality, QUALITY_COLOURS["normal"])
        self.value_label.setStyleSheet(_FONTS.get(quality, _FONTS["normal"]) % colour)

    def set_unit(self, unit: str) -> None:
        self._unit = unit
        self.value_label.setText(self._compose(self._value_text))


class MetricStrip(QtWidgets.QWidget):
    """The seven cards the brief asks for, plus the setters that fill them.

    The setters take decoded protocol values, not strings, so the honesty rules
    live in exactly one place.
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        grid = QtWidgets.QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)

        self.hr = MetricCard(
            "Heart rate",
            unit="bpm",
            tooltip=(
                "Only a rate the device certified is shown. hr_bpm, hr_state and "
                "SFLAG_HR_VALID must all agree, otherwise the value is treated as "
                "uncertified."
            ),
        )
        self.temperature = MetricCard(
            "Temperature",
            unit="\u00b0C",
            initial=TEMP_BLANK,
            tooltip=(
                "Blank as --.- while the device reports TEMP_UNCALIBRATED. The firmware's "
                "temperature model is uncalibrated in this baseline, so no degrees claim exists."
            ),
        )
        self.lead = MetricCard(
            "Lead state",
            tooltip=(
                "UNKNOWN means no lead-off detection hardware is configured "
                "(CAP_LEAD_HW_DETECT clear). It is rendered differently from "
                "DISCONNECTED on purpose."
            ),
        )
        self.probe = MetricCard(
            "Probe state",
            tooltip=(
                "Derived from temp_state. Without CAP_PROBE_HW_DETECT the device can only "
                "infer a fault from the ADC range, which is a heuristic, not a probe test."
            ),
        )
        self.recording = MetricCard(
            "Recording",
            tooltip="Elapsed time of the local recording, from the first stored sample.",
        )
        self.loss = MetricCard(
            "Packet loss",
            tooltip=(
                "Authoritative measure: first_sample_index discontinuity in ECG_BATCH, which "
                "also catches the device dropping its own DMA blocks. Sequence gaps are "
                "reported alongside as the link-level view."
            ),
        )
        self.crc = MetricCard(
            "CRC errors",
            tooltip="Frames rejected by CRC-16/CCITT-FALSE and resynchronised past.",
        )

        cards = (self.hr, self.temperature, self.lead, self.probe, self.recording, self.loss, self.crc)
        columns = 7
        for index, card in enumerate(cards):
            grid.addWidget(card, index // columns, index % columns)

    # -- fillers -----------------------------------------------------------
    def set_heart_rate(self, bpm: int, state: int, *, hr_valid: bool, flags_present: bool = True) -> None:
        from ..protocol import HrState, hr_state_name

        name = hr_state_name(state)
        if not hr_valid or bpm <= 0:
            self.hr.set_value("--", quality="idle")
            self.hr.set_sub(
                "%s: no certified rate from the device" % name if flags_present else name
            )
            return
        quality = {
            HrState.NORMAL: "good",
            HrState.LOW: "warn",
            HrState.HIGH: "warn",
            HrState.ACQUIRING: "idle",
            HrState.INVALID: "bad",
        }.get(_as_hr(state), "normal")
        self.hr.set_value("%d" % bpm, quality=quality)
        self.hr.set_sub(name + (" (certified)" if hr_valid else ""))

    def set_temperature(
        self,
        centi: int,
        state: int,
        *,
        certified: bool,
        probe_hw_detect: bool = False,
        route: str = "",
    ) -> None:
        """``certified`` is the caller's verdict; blank beats a plausible number.

        ``route`` names which packet the value came from, because ``TEMP_STATUS``
        and the ``ECG_BATCH`` tail both carry a temperature view at different
        cadences and the last writer wins.
        """
        label = TempState(state).text if _in_range(TempState, state) else "STATE_%d" % state
        suffix = " | via %s" % route if route else ""
        if not certified:
            self.temperature.set_value(TEMP_BLANK, quality="unknown")
            self.temperature.set_sub("%s -- degrees are not claimed%s" % (label, suffix))
        else:
            celsius = centi / 100.0
            quality = {
                TempState.OK: "good",
                TempState.LOW: "warn",
                TempState.HIGH: "warn",
                TempState.PROBE_FAULT: "bad",
                TempState.UNCALIBRATED: "unknown",
            }.get(_as_temp(state), "normal")
            self.temperature.set_value("%.1f" % celsius, quality=quality)
            self.temperature.set_sub("%s%s" % (label, suffix))
        self.set_probe(state, probe_hw_detect=probe_hw_detect, certified=certified)

    def set_probe(self, state: int, *, probe_hw_detect: bool, certified: bool) -> None:
        if state == int(TempState.PROBE_FAULT):
            self.probe.set_value("FAULT", quality="bad")
            self.probe.set_sub(
                "probe fault reported"
                + ("" if probe_hw_detect else " -- inferred from the ADC range, no probe hardware")
            )
            return
        if not certified:
            self.probe.set_value("UNCAL", quality="unknown")
            self.probe.set_sub(
                "temperature uncalibrated"
                + ("" if probe_hw_detect else "; no probe-detect hardware")
            )
            return
        self.probe.set_value("OK", quality="good")
        self.probe.set_sub(
            "in range" + ("" if probe_hw_detect else " -- heuristic only (CAP_PROBE_HW_DETECT clear)")
        )

    def set_lead(self, state: int, *, lead_hw_detect: bool) -> None:
        known = _in_range(LeadState, state)
        lead = LeadState(state) if known else LeadState.UNKNOWN
        if not known:
            self.lead.set_value("REPORTED %d" % state, quality="unknown")
            self.lead.set_sub("outside lead_state_t")
            return
        quality = {
            LeadState.UNKNOWN: "unknown",
            LeadState.CONNECTED: "good",
            LeadState.DISCONNECTED: "bad",
            LeadState.SIGNAL_POOR: "warn",
        }[lead]
        self.lead.set_value(lead.text, quality=quality)
        if lead is LeadState.UNKNOWN:
            self.lead.set_sub(
                "no lead-off hardware configured"
                if not lead_hw_detect
                else "hardware present, reports UNKNOWN"
            )
        elif lead is LeadState.SIGNAL_POOR:
            self.lead.set_sub("software judgement, NOT an electrode verdict")
        elif lead is LeadState.DISCONNECTED:
            self.lead.set_sub("reported by lead-off hardware" if lead_hw_detect else "unexpected without hardware")
        else:
            self.lead.set_sub("electrodes reported connected")

    def set_recording(self, *, active: bool, seconds: float, rows: int) -> None:
        stamp = _mmss(seconds)
        if active:
            self.recording.set_value(stamp, quality="bad" if rows else "warn")
            self.recording.set_sub("recording, %s rows" % f"{rows:,}")
        elif rows:
            self.recording.set_value(stamp, quality="idle")
            self.recording.set_sub("stopped, %s rows held" % f"{rows:,}")
        else:
            self.recording.set_value("00:00", quality="idle")
            self.recording.set_sub("not recording")

    def set_loss(self, samples_missing: int, index_gaps: int, sequence_missing: int) -> None:
        total = samples_missing + sequence_missing
        self.loss.set_value(f"{samples_missing:,}", quality="good" if not total else "warn")
        self.loss.set_sub(
            "%d index gap(s); %d packet(s) missing by sequence" % (index_gaps, sequence_missing)
        )

    def set_crc_errors(self, count: int, discarded: int = 0) -> None:
        self.crc.set_value(f"{count:,}", quality="good" if not count else "bad")
        self.crc.set_sub("%d byte(s) discarded while resyncing" % discarded)


# ---------------------------------------------------------------- small helpers
def _in_range(enum_cls, value: int) -> bool:
    try:
        enum_cls(value)
    except ValueError:
        return False
    return True


def _as_hr(state: int):
    from ..protocol import HrState

    return _enum_or(HrState, state, HrState.INVALID)


def _as_temp(state: int):
    return _enum_or(TempState, state, TempState.UNCALIBRATED)


def _enum_or(enum_cls, value: int, fallback):
    try:
        return enum_cls(value)
    except ValueError:
        return fallback


def _mmss(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    minutes, rest = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return "%02d:%02d:%02d" % (hours, minutes, rest) if hours else "%02d:%02d" % (minutes, rest)
