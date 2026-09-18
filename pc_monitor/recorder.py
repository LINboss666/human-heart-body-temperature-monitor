"""Recording sessions: turn ``ECG_BATCH`` frames into exportable rows.

The record is the *raw* signal, deliberately.  ``ecg_config.h`` states that the
RAW path leaves the filter stage untouched because the course metric is a
0.05-150 Hz recording bandwidth, so the host must not quietly persist a filtered
trace and call it the measurement.

Storage is a **column store of chunks**, not a list of row objects: 1000 rows a
second is 60000 a minute, and materialising that as tuples costs tens of
megabytes per minute for no benefit.  Each ``ECG_BATCH`` is kept as it arrived
(the ADC codes in one ``uint16`` array plus the eight tail fields), and rows are
*expanded* lazily at export time by :meth:`RecordingSession.iter_rows`.

Two honesty rules live here:

* A row's ``host_timestamp`` is the moment the *batch* arrived from the UART.  All
  samples in a batch share it, because that is what was actually measured; the
  exact per-sample instant is the device's ``first_sample_index`` / 1 kHz axis,
  which is stored separately as ``sample_time_s``.  Interpolating host time
  between samples would invent precision the wire does not carry.
* ``temp_c`` is written **only** when the device certified the temperature.
  While ``TEMP_UNCALIBRATED`` the cell is empty, never a plausible number.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from dataclasses import dataclass
from typing import Iterable, Iterator

import numpy as np

from . import protocol
from .demo_source import DEMO_ORIGIN
from .protocol import (
    EcgBatch,
    Flags,
    HrState,
    LeadState,
    TempState,
    centi_to_c,
    ecg_raw_to_mv,
    split_flags,
)

__all__ = [
    "RecordChunk",
    "RecordingSession",
    "RecordingSummary",
    "ROW_COLUMNS",
    "ORIGIN_DEVICE",
    "reconcile_hr",
    "temp_certified",
]

ORIGIN_DEVICE = "DEVICE (hardware)"

#: Column order for both CSV and the XLSX ``Data`` sheet.  Keep in sync with
#: :meth:`RecordingSession.iter_rows`.
ROW_COLUMNS: tuple[str, ...] = (
    "host_timestamp",
    "host_epoch_s",
    "device_ts_ms",
    "sample_index",
    "sample_time_s",
    "ecg_raw",
    "ecg_pin_mv",
    "temp_raw",
    "temp_centi",
    "temp_c",
    "hr_bpm",
    "hr_valid",
    "hr_state",
    "lead_state",
    "probe_state",
    "status_flags",
    "origin",
)


@dataclass(frozen=True)
class RecordChunk:
    """One ``ECG_BATCH`` as received, plus when the host saw it."""

    host_epoch_s: float
    device_ts_ms: int
    first_sample_index: int
    samples: np.ndarray  # uint16 ADC codes
    temp_raw: int
    temp_centi: int
    temp_valid: bool
    hr_bpm: int
    hr_valid: bool
    hr_state: int
    lead_state: int
    probe_state: int
    flags: int
    origin: str
    sample_period_us: int

    @property
    def count(self) -> int:
        return int(self.samples.size)

    @property
    def sample_rate_hz(self) -> float:
        return 1e6 / self.sample_period_us if self.sample_period_us else float(protocol.SAMPLE_RATE_HZ)

    @property
    def last_sample_index(self) -> int:
        return self.first_sample_index + self.count - 1


@dataclass
class RecordingSummary:
    """Everything the ``Summary`` sheet states, computed once."""

    origin: str
    source_label: str
    device_identity: str
    started_at: dt.datetime | None
    stopped_at: dt.datetime | None
    duration_s: float
    row_count: int
    sample_count: int
    first_sample_index: int | None
    last_sample_index: int | None
    index_span: int
    missing_samples: int
    sample_rate_hz: float
    hr_valid_reports: int
    hr_mean: float | None
    hr_min: int | None
    hr_max: int | None
    temp_valid_rows: int
    temp_mean_c: float | None
    temp_min_c: float | None
    temp_max_c: float | None
    temp_uncalibrated: bool
    lead_unknown_only: bool
    probe_state_text: str
    packets_total: int
    sequence_gaps: int
    sequence_missing: int
    index_gaps: int
    crc_errors: int
    discarded_bytes: int
    ecg_min_raw: int | None
    ecg_max_raw: int | None
    caveats: tuple[str, ...] = ()


class RecordingSession:
    """Accumulates the rows of one recording.

    ``origin`` is fixed at construction, from the ``--demo`` flag, not inferred
    from the bytes: that is what makes a synthetic recording un-reinterpretable
    as a hardware one later.
    """

    def __init__(
        self,
        *,
        origin: str = ORIGIN_DEVICE,
        source_label: str = "",
        device_identity: str = "no HELLO received",
    ) -> None:
        self.origin = origin
        self.source_label = source_label
        self.device_identity = device_identity
        #: The ``HELLO`` payload the engine saw, attached for the export header.
        #: ``None`` means no HELLO, which the Summary sheet states explicitly.
        self.hello = None
        self._chunks: list[RecordChunk] = []
        self._lock = threading.Lock()
        self._row_count = 0
        self._started_at: dt.datetime | None = None
        self._stopped_at: dt.datetime | None = None
        self._is_recording = False
        # Link-level counters snapshotted when the recording stops, so the
        # Summary describes the recording rather than the whole session.
        self._counters: dict[str, int] = {}
        self._hr_disagreements = 0

    # -- lifecycle ---------------------------------------------------------
    @property
    def is_recording(self) -> bool:
        return self._is_recording

    @property
    def is_demo(self) -> bool:
        return self.origin == DEMO_ORIGIN

    def start(self, *, counters: dict[str, int] | None = None) -> dt.datetime:
        with self._lock:
            if self._is_recording:
                return self._started_at or dt.datetime.now()
            self._is_recording = True
            self._started_at = dt.datetime.now()
            self._stopped_at = None
            return self._started_at

    def stop(self, *, counters: dict[str, int] | None = None) -> dt.datetime:
        with self._lock:
            self._is_recording = False
            self._stopped_at = dt.datetime.now()
            if counters:
                self._counters = dict(counters)
            return self._stopped_at

    def set_counters(self, counters: dict[str, int]) -> None:
        with self._lock:
            self._counters = dict(counters)

    def clear(self) -> None:
        with self._lock:
            self._chunks.clear()
            self._row_count = 0
            self._started_at = None
            self._stopped_at = None
            self._counters = {}
            self._hr_disagreements = 0

    # -- ingest ------------------------------------------------------------
    def add_batch(
        self,
        frame: protocol.Frame,
        batch: EcgBatch,
        *,
        host_epoch_s: float | None = None,
    ) -> RecordChunk | None:
        """Persist one ``ECG_BATCH``, or return ``None`` if not recording.

        The chunk stores the sample codes by reference -- the caller has just
        sliced them out of a fresh ``struct.unpack`` result, so no copy is needed
        and the GUI thread does no per-sample work beyond this call.
        """
        if not self._is_recording:
            return None
        flags = split_flags(batch.flags)
        hr_valid, disagreed = reconcile_hr(batch, flags)
        chunk = RecordChunk(
            host_epoch_s=time.time() if host_epoch_s is None else float(host_epoch_s),
            device_ts_ms=frame.device_ts_ms,
            first_sample_index=batch.first_sample_index,
            samples=np.asarray(batch.samples, dtype=np.uint16),
            temp_raw=batch.temp_raw,
            temp_centi=batch.temp_centi,
            temp_valid=temp_certified(batch, flags),
            hr_bpm=batch.hr_bpm,
            hr_valid=hr_valid,
            hr_state=batch.hr_state,
            lead_state=int(flags.lead),
            probe_state=int(flags.temp),
            flags=batch.flags,
            origin=self.origin,
            sample_period_us=batch.sample_period_us,
        )
        if disagreed:
            self._hr_disagreements += 1
        with self._lock:
            if not self._is_recording:
                return None
            self._chunks.append(chunk)
            self._row_count += chunk.count
        return chunk

    # -- read side ---------------------------------------------------------
    @property
    def row_count(self) -> int:
        with self._lock:
            return self._row_count

    @property
    def chunk_count(self) -> int:
        with self._lock:
            return len(self._chunks)

    @property
    def chunks(self) -> tuple[RecordChunk, ...]:
        with self._lock:
            return tuple(self._chunks)

    @property
    def started_at(self) -> dt.datetime | None:
        return self._started_at

    @property
    def stopped_at(self) -> dt.datetime | None:
        return self._stopped_at

    def duration_s(self) -> float:
        """Device-clock duration of the record, falling back to host time."""
        chunks = self.chunks
        if chunks:
            first = chunks[0]
            last = chunks[-1]
            span = last.last_sample_index - first.first_sample_index + 1
            if span > 0:
                return span / first.sample_rate_hz
        if self._started_at is not None:
            end = self._stopped_at or dt.datetime.now()
            return (end - self._started_at).total_seconds()
        return 0.0

    def iter_rows(self) -> Iterator[tuple]:
        """Yield export rows in :data:`ROW_COLUMNS` order."""
        for chunk in self.chunks:
            yield from _expand(chunk)

    def iter_csv_lines(self, delimiter: str = ",") -> Iterator[str]:
        header = delimiter.join(ROW_COLUMNS)
        yield header
        for row in self.iter_rows():
            yield delimiter.join(_csv_cell(cell, delimiter) for cell in row)

    def ecg_time_mv(self) -> tuple[np.ndarray, np.ndarray]:
        """(device seconds, pin millivolts) for every recorded sample."""
        chunks = self.chunks
        if not chunks:
            return np.empty(0), np.empty(0)
        times = []
        values = []
        for chunk in chunks:
            rate = chunk.sample_rate_hz
            idx = np.arange(
                chunk.first_sample_index, chunk.first_sample_index + chunk.count, dtype=np.float64
            )
            times.append(idx / rate)
            values.append(chunk.samples.astype(np.float64) * (protocol.VDDA_MV / protocol.ADC_FULL_SCALE_CODES))
        return np.concatenate(times), np.concatenate(values)

    def summary(self) -> RecordingSummary:
        chunks = self.chunks
        valid_hr = [c.hr_bpm for c in chunks if c.hr_valid and c.hr_bpm > 0]
        valid_temps = [c.temp_centi for c in chunks if c.temp_valid]
        all_codes = np.concatenate([c.samples for c in chunks]) if chunks else np.empty(0, np.uint16)
        first_index = chunks[0].first_sample_index if chunks else None
        last_index = chunks[-1].last_sample_index if chunks else None
        index_span = (last_index - first_index + 1) if chunks else 0
        counters = dict(self._counters)
        temps_c = [centi_to_c(v) for v in valid_temps]
        lead_states = {c.lead_state for c in chunks}
        probe_states = {c.probe_state for c in chunks}
        uncalibrated = any(not c.temp_valid for c in chunks)
        summary = RecordingSummary(
            origin=self.origin,
            source_label=self.source_label,
            device_identity=self.device_identity,
            started_at=self._started_at,
            stopped_at=self._stopped_at,
            duration_s=self.duration_s(),
            row_count=self.row_count,
            sample_count=int(all_codes.size),
            first_sample_index=first_index,
            last_sample_index=last_index,
            index_span=int(index_span),
            missing_samples=int(index_span - all_codes.size) if chunks else 0,
            sample_rate_hz=chunks[0].sample_rate_hz if chunks else float(protocol.SAMPLE_RATE_HZ),
            hr_valid_reports=len(valid_hr),
            hr_mean=(sum(valid_hr) / len(valid_hr)) if valid_hr else None,
            hr_min=min(valid_hr) if valid_hr else None,
            hr_max=max(valid_hr) if valid_hr else None,
            temp_valid_rows=len(temps_c),
            temp_mean_c=(sum(temps_c) / len(temps_c)) if temps_c else None,
            temp_min_c=min(temps_c) if temps_c else None,
            temp_max_c=max(temps_c) if temps_c else None,
            temp_uncalibrated=bool(uncalibrated or not temps_c),
            lead_unknown_only=bool(lead_states) and lead_states == {int(LeadState.UNKNOWN)},
            probe_state_text=_probe_text(probe_states, uncalibrated),
            packets_total=int(counters.get("packets", 0)),
            sequence_gaps=int(counters.get("sequence_gaps", 0)),
            sequence_missing=int(counters.get("sequence_missing", 0)),
            index_gaps=int(counters.get("index_gaps", 0)),
            crc_errors=int(counters.get("crc_errors", 0)),
            discarded_bytes=int(counters.get("discarded", 0)),
            ecg_min_raw=int(all_codes.min()) if all_codes.size else None,
            ecg_max_raw=int(all_codes.max()) if all_codes.size else None,
            caveats=self.caveats(summary_like=uncalibrated, lead_unknown=lead_states == {int(LeadState.UNKNOWN)}),
        )
        return summary

    def caveats(self, *, summary_like: bool = False, lead_unknown: bool = False) -> tuple[str, ...]:
        """The sentences that stop the export over-claiming."""
        notes: list[str] = []
        if self.is_demo:
            notes.append(
                "SYNTHETIC DATA: this recording was generated by --demo. It is not a "
                "measurement and must not be presented as one."
            )
        notes.append(
            "ecg_pin_mv is raw, unfiltered ADC code converted to millivolts at the MCU pin. "
            "It is NOT a body-surface potential: ECG_FRONTEND_GAIN and _OFFSET_MV are marked "
            "UNVERIFIED in App/config/ecg_config.h."
        )
        if summary_like:
            notes.append(
                "Temperature degrees are blank wherever the device reported "
                "TEMP_UNCALIBRATED (TEMP_SENSOR_MODEL is uncalibrated in this baseline), "
                "so the temperature statistics cover only certified samples."
            )
        else:
            notes.append("temp_c is blank whenever the device did not certify the temperature.")
        if lead_unknown:
            notes.append(
                "lead_state is UNKNOWN throughout: CAP_LEAD_HW_DETECT is clear, so there is "
                "no lead-off detection hardware and no electrode verdict exists."
            )
        notes.append(
            "host_timestamp is per-batch arrival time; per-sample timing comes from "
            "first_sample_index at 1 kHz, which is the contract's authoritative time axis."
        )
        if self._hr_disagreements:
            notes.append(
                "%d batch(es) disagreed about heart-rate validity across hr_bpm / hr_state / "
                "SFLAG_HR_VALID; those were recorded as invalid." % self._hr_disagreements
            )
        return tuple(notes)


# ---------------------------------------------------------------- helpers
def reconcile_hr(batch: EcgBatch, flags: Flags) -> tuple[bool, bool]:
    """Decide HR validity from the three signals the wire carries.

    ``hr_bpm != 0``, ``hr_state != HR_INVALID`` and ``SFLAG_HR_VALID`` should all
    say the same thing; ``docs/PROTOCOL.md`` does not name one of them
    authoritative (flagged as an ambiguity).  The conservative reading wins --
    the host only shows a heart rate the device certified -- and a disagreement
    is counted instead of silently smoothed over.
    """
    by_bpm = batch.hr_bpm != 0
    by_state = batch.hr_state != int(HrState.INVALID)
    by_flag = flags.hr_valid
    votes = (by_bpm, by_state, by_flag)
    valid = all(votes)
    disagreed = len(set(votes)) > 1
    return valid, disagreed


def temp_certified(batch: EcgBatch, flags: Flags) -> bool:
    """Degrees exist only for OK/LOW/HIGH, never for UNCALIBRATED or PROBE_FAULT."""
    if flags.temp_uncalibrated:
        return False
    return flags.temp in (TempState.OK, TempState.LOW, TempState.HIGH)


def _probe_text(states: Iterable[int], uncalibrated: bool) -> str:
    values = {int(s) for s in states}
    if not values:
        return "no data"
    names = []
    for value in sorted(values):
        try:
            names.append(TempState(value).name)
        except ValueError:
            names.append("STATE_%d" % value)
    text = "/".join(names)
    return "%s%s" % (text, "" if not uncalibrated else " (temperature uncertified)")


def _expand(chunk: RecordChunk) -> Iterator[tuple]:
    """Turn one stored chunk into per-sample export rows."""
    host_dt = dt.datetime.fromtimestamp(chunk.host_epoch_s)
    host_iso = host_dt.isoformat(timespec="milliseconds")
    rate = chunk.sample_rate_hz
    mv = ecg_raw_to_mv
    temp_cell = centi_to_c(chunk.temp_centi) if chunk.temp_valid else ""
    lead_name = _lead_name(chunk.lead_state)
    probe_name = _temp_name(chunk.probe_state)
    hr_cell = chunk.hr_bpm if chunk.hr_valid else ""
    for offset in range(chunk.count):
        index = chunk.first_sample_index + offset
        raw = int(chunk.samples[offset])
        yield (
            host_iso,
            round(chunk.host_epoch_s, 6),
            chunk.device_ts_ms,
            index,
            round(index / rate, 6),
            raw,
            round(mv(raw), 4),
            chunk.temp_raw,
            chunk.temp_centi if chunk.temp_valid else "",
            temp_cell,
            hr_cell,
            1 if chunk.hr_valid else 0,
            chunk.hr_state,
            lead_name,
            probe_name,
            "0x%04X" % chunk.flags,
            chunk.origin,
        )


def _lead_name(value: int) -> str:
    try:
        return LeadState(value).name
    except ValueError:
        return "LEAD_%d" % value


def _temp_name(value: int) -> str:
    try:
        return TempState(value).name
    except ValueError:
        return "TEMP_%d" % value


def _csv_cell(value: object, delimiter: str) -> str:
    text = "" if value is None else str(value)
    if delimiter in text or '"' in text or "\n" in text:
        return '"%s"' % text.replace('"', '""')
    return text
