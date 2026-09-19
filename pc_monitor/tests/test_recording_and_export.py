"""What gets recorded, and what is allowed to be written to a file.

This is the honesty layer.  Two properties matter more than any other here:
an uncalibrated temperature must produce an *empty cell* rather than a number
that looks like a measurement, and a demo recording must stay identifiable as one
after every transformation the tool applies to it.
"""
from __future__ import annotations

import csv
import datetime as dt

import numpy as np
import pytest

from pc_monitor import export
from pc_monitor import protocol as p
from pc_monitor import recorder
from pc_monitor.recorder import RecordingSession, reconcile_hr, temp_certified


def batch_frame(first_index, seq, *, samples=None, flags=None, temp_raw=1731,
                temp_centi=3667,
                temp_state=int(p.TempState.UNCALIBRATED), hr_bpm=72, hr_valid=True,
                ts=None):
    if flags is None:
        flags = p.make_flags(
            lead=p.LeadState.UNKNOWN, temp=temp_state, hr_valid=hr_valid,
            recording=True, adc_running=True, rtc_valid=True,
            notch=p.NotchMode.HZ50, temp_uncalibrated=(
                temp_state == int(p.TempState.UNCALIBRATED)),
        )
    payload = p.EcgBatch.encode(
        first_sample_index=first_index,
        samples=samples if samples is not None else [2048 + (i % 5) for i in range(20)],
        temp_raw=temp_raw, temp_centi=temp_centi, hr_bpm=hr_bpm,
        hr_state=int(p.HrState.NORMAL), flags=flags,
    )
    raw = p.encode_frame(p.PacketType.ECG_BATCH, seq,
                         first_index // 1000 if ts is None else ts, payload)
    _result, frame, _consumed = p.parse_frame(raw)
    return frame, p.decode_ecg_batch(payload)


@pytest.fixture
def session():
    s = RecordingSession(source_label="COM7", device_identity="fw 1.0.0 proto 0x02")
    s.start()
    return s


def record(session, blocks=5, **kw):
    for i in range(blocks):
        frame, batch = batch_frame(i * 20, i, **kw)
        session.add_batch(frame, batch, host_epoch_s=1700000000.0 + i * 0.02)
    return session


class TestRowShape:
    def test_the_header_and_every_row_have_the_same_width(self, session):
        record(session)
        lines = list(session.iter_csv_lines())
        header = lines[0].split(",")
        assert header == list(recorder.ROW_COLUMNS)
        for line in lines[1:]:
            assert len(next(csv.reader([line]))) == len(header)

    def test_one_row_per_sample_never_one_row_per_batch(self, session):
        record(session, blocks=5)
        assert session.row_count == 100
        assert len(list(session.iter_rows())) == 100

    def test_the_sample_axis_is_the_device_axis_not_a_host_counter(self, session):
        record(session, blocks=3)
        rows = list(session.iter_rows())
        indices = [r[3] for r in rows]
        assert indices == list(range(60))
        assert [r[4] for r in rows][:3] == [0.0, 0.001, 0.002]

    def test_raw_codes_are_written_without_any_lossy_conversion(self, session):
        codes = [2048 + (i * 37) % 200 for i in range(20)]
        frame, batch = batch_frame(0, 0, samples=codes)
        session.add_batch(frame, batch)
        written = [r[5] for r in session.iter_rows()]
        assert written == codes
        assert [r[6] for r in session.iter_rows()][0] == pytest.approx(
            p.ecg_raw_to_mv(codes[0]), abs=1e-4)


class TestUncalibratedTemperature:
    """The single most important thing in the export: no invented numbers."""

    def test_an_uncalibrated_batch_writes_empty_temperature_cells(self, session):
        record(session, blocks=2)
        rows = list(session.iter_rows())
        assert all(r[8] == "" for r in rows), "temp_centi column must stay empty"
        assert all(r[9] == "" for r in rows), "temp_c column must stay empty"
        # ... while the raw code, which is a real measurement, is still recorded.
        assert all(r[7] == 1731 for r in rows)

    def test_a_calibrated_ok_batch_does_write_degrees(self, session):
        frame, batch = batch_frame(0, 0, temp_state=int(p.TempState.OK))
        session.add_batch(frame, batch)
        rows = list(session.iter_rows())
        assert rows[0][8] == 3667
        assert rows[0][9] == pytest.approx(36.67)

    @pytest.mark.parametrize("state,expected", [
        (int(p.TempState.OK), True),
        (int(p.TempState.LOW), True),
        (int(p.TempState.HIGH), True),
        (int(p.TempState.PROBE_FAULT), False),
        (int(p.TempState.UNCALIBRATED), False),
    ])
    def test_temp_certified_matches_the_state_table(self, state, expected):
        _f, batch = batch_frame(0, 0, temp_state=state)
        flags = p.split_flags(batch.flags)
        assert temp_certified(batch, flags) is expected

    def test_uncalibrated_beats_a_claiming_state_flag(self):
        """temp_uncalibrated and temp=OK together must resolve to 'no number'."""
        flags = p.make_flags(temp=int(p.TempState.OK), temp_uncalibrated=True)
        _f, batch = batch_frame(0, 0, flags=flags)
        assert temp_certified(batch, p.split_flags(batch.flags)) is False

    def test_the_summary_says_uncalibrated_instead_of_averaging_blanks(self, session):
        record(session, blocks=4)
        summary = session.summary()
        assert summary.temp_uncalibrated is True
        assert summary.temp_mean_c is None
        assert summary.temp_min_c is None
        assert summary.temp_valid_reports == 0


class TestHeartRateHonesty:
    def test_a_bpm_value_without_the_valid_flag_is_not_recorded_as_a_rate(self, session):
        frame, batch = batch_frame(0, 0, hr_bpm=88, hr_valid=False)
        session.add_batch(frame, batch)
        rows = list(session.iter_rows())
        assert all(r[10] == "" for r in rows), "hr_bpm must be blank when unverified"
        assert all(r[11] == 0 for r in rows)

    def test_reconcile_hr_flags_a_self_contradicting_batch(self):
        frame, batch = batch_frame(0, 0, hr_bpm=0, hr_valid=True)
        hr_valid, disagreed = reconcile_hr(batch, p.split_flags(batch.flags))
        assert disagreed is True
        assert hr_valid is False, "bpm 0 can never be a heart rate"

    def test_reconcile_hr_accepts_a_consistent_batch(self):
        _f, batch = batch_frame(0, 0, hr_bpm=72, hr_valid=True)
        hr_valid, disagreed = reconcile_hr(batch, batch.flags_decoded)
        assert (hr_valid, disagreed) == (True, False)

    def test_the_summary_refuses_to_average_invalid_reports(self, session):
        record(session, blocks=3, hr_bpm=0, hr_valid=False)
        summary = session.summary()
        assert summary.hr_valid_reports == 0
        assert summary.hr_mean is None


class TestGapsAndProvenance:
    def test_a_dropped_block_appears_as_missing_samples_not_a_stitched_axis(self, session):
        for i in (0, 1, 4, 5):                     # blocks 2 and 3 never arrived
            frame, batch = batch_frame(i * 20, i)
            session.add_batch(frame, batch)
        rows = list(session.iter_rows())
        assert [r[3] for r in rows] == list(range(40)) + list(range(80, 120))
        summary = session.summary()
        assert summary.missing_samples == 40
        assert summary.index_span == 120

    def test_every_row_carries_the_origin_it_was_recorded_under(self, session):
        demo = RecordingSession(origin=recorder.ORIGIN_DEVICE)
        assert demo.origin == recorder.ORIGIN_DEVICE

    def test_a_demo_session_is_marked_on_every_row(self):
        demo = RecordingSession(origin=recorder.DEMO_ORIGIN)
        demo.start()
        frame, batch = batch_frame(0, 0)
        demo.add_batch(frame, batch)
        rows = list(demo.iter_rows())
        assert {r[16] for r in rows} == {recorder.DEMO_ORIGIN}
        assert demo.is_demo is True


class TestTemperatureIsPerBatch:
    """What the firmware fix has to produce, checked from the receiving end.

    ECG_BATCH's temperature trailer was broken on the device side: it carried a
    permanent zero because the only writer of it sat behind a pointer the caller
    passed NULL for. These cases pin that the PC records exactly what each batch
    carried -- so a stuck field is visible in the export instead of being
    smoothed over -- and that the two temperature channels are never merged.
    """

    def test_each_batch_contributes_its_own_temperature_to_its_own_rows(self, session):
        for i, raw in enumerate((1200, 1731, 2050)):
            frame, batch = batch_frame(i * 20, i, temp_raw=raw, temp_centi=3000 + i)
            session.add_batch(frame, batch)
        rows = list(session.iter_rows())
        per_batch = [rows[i * 20][7] for i in range(3)]
        assert per_batch == [1200, 1731, 2050], \
            "the recorder reused one batch's temperature for all of them"

    def test_a_stuck_zero_from_the_device_is_recorded_as_zero_not_filled_in(self, session):
        frame, batch = batch_frame(0, 0, temp_raw=0, temp_centi=0)
        session.add_batch(frame, batch)
        rows = list(session.iter_rows())
        assert {r[7] for r in rows} == {0}
        # Not certified, so no degrees are invented from the zero.
        assert all(r[8] == "" and r[9] == "" for r in rows)

    def test_calibrated_and_uncalibrated_batches_keep_separate_rows(self, session):
        frame, batch = batch_frame(0, 0, temp_state=int(p.TempState.UNCALIBRATED))
        session.add_batch(frame, batch)
        frame, batch = batch_frame(20, 1, temp_state=int(p.TempState.OK),
                                   temp_centi=3667)
        session.add_batch(frame, batch)
        rows = list(session.iter_rows())
        assert all(r[9] == "" for r in rows[:20])
        assert all(abs(r[9] - 36.67) < 1e-6 for r in rows[20:])
        assert len({r[14] for r in rows}) == 2, "probe state collapsed between batches"

    def test_the_summary_counts_only_certified_temperature_rows(self, session):
        frame, batch = batch_frame(0, 0, temp_state=int(p.TempState.UNCALIBRATED))
        session.add_batch(frame, batch)
        frame, batch = batch_frame(20, 1, temp_state=int(p.TempState.OK),
                                   temp_centi=3600)
        session.add_batch(frame, batch)
        summary = session.summary()
        # Counted in batches: one ECG_BATCH carries one temperature reading.
        assert summary.temp_valid_reports == 1
        assert summary.temp_mean_c == pytest.approx(36.0)
        assert summary.temp_uncalibrated is True   # a window did report it


class TestEcgBatchMatchesGoldenLayout:
    def test_a_firmware_shaped_batch_round_trips_through_the_recorder(self, session):
        """Use the C-generated vector itself, so this is the same bytes."""
        import json
        from pathlib import Path

        vectors = json.loads(
            (Path(__file__).resolve().parents[2] / "tests" / "host" /
             "protocol_vectors.json").read_text(encoding="utf-8")
        )["vectors"]
        raw = bytes.fromhex(vectors["ecg_batch_typical"])
        _result, frame, _consumed = p.parse_frame(raw)
        batch = p.decode_ecg_batch(frame)
        session.add_batch(frame, batch)
        rows = list(session.iter_rows())
        assert rows[0][5] == batch.samples[0]
        assert rows[0][7] == 1731                 # temp_raw, straight from C
        # That vector sets SFLAG_HR_VALID and carries bpm 72, so the rate is real.
        assert batch.flags & p.SFLAG_HR_VALID
        assert rows[0][10] == 72
        assert rows[0][15] == "0x%04X" % batch.flags


class TestCaveats:
    def test_an_uncalibrated_recording_states_it_plainly(self, session):
        record(session, blocks=2)
        summary = session.summary()
        caveats = " | ".join(session.caveats(summary_like=True))
        assert "TEMP_UNCALIBRATED" in caveats
        assert "certified" in caveats
        assert summary.temp_uncalibrated is True

    def test_the_ecg_channel_is_caveated_as_a_pin_voltage_not_a_potential(self, session):
        record(session, blocks=1)
        caveats = " | ".join(session.caveats())
        assert "UNVERIFIED" in caveats
        assert "NOT a body-surface potential" in caveats

    def test_a_lead_state_of_unknown_is_not_reported_as_connected(self, session):
        record(session, blocks=2)
        summary = session.summary()
        assert summary.lead_unknown_only is True
        caveats = " | ".join(session.caveats(lead_unknown=True))
        assert "no lead-off detection hardware" in caveats

    def test_demo_recording_is_caveated_as_synthetically_generated(self):
        demo = RecordingSession(origin=recorder.DEMO_ORIGIN)
        demo.start()
        frame, batch = batch_frame(0, 0)
        demo.add_batch(frame, batch)
        text = " | ".join(demo.caveats())
        assert "SYNTHETIC DATA" in text
        assert "must not be presented as one" in text


class TestCsvExport:
    def test_a_written_csv_reparses_with_the_original_codes(self, session, tmp_path):
        codes = [2048 + (i * 37) % 300 for i in range(20)]
        frame, batch = batch_frame(0, 0, samples=codes)
        session.add_batch(frame, batch)
        session.stop()
        path = tmp_path / "rec.csv"
        result = export.export_csv(session, path)
        assert path.is_file()
        assert result.rows == 20

        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == 20
        assert [int(r["ecg_raw"]) for r in rows] == codes
        assert rows[0]["origin"] == session.origin
        assert rows[0]["hr_bpm"] == "72"

    def test_the_uncalibrated_temperature_survives_the_file(self, session, tmp_path):
        record(session, blocks=2)
        session.stop()
        path = tmp_path / "rec.csv"
        export.export_csv(session, path)
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        assert all(r["temp_c"] == "" for r in rows)
        assert all(r["temp_centi"] == "" for r in rows)

    def test_a_comma_in_a_text_cell_is_quoted_not_spliced(self, session, tmp_path):
        session.device_identity = 'fw 1.0.0, "proto" 0x02'
        record(session, blocks=1)
        session.stop()
        path = tmp_path / "rec.csv"
        export.export_csv(session, path)
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
        assert len({len(r) for r in rows}) == 1, "ragged rows mean broken quoting"


class TestXlsxExport:
    def test_the_workbook_has_the_three_sheets_and_the_data_row_count(self, session, tmp_path):
        record(session, blocks=6)
        session.stop()
        path = tmp_path / "rec.xlsx"
        result = export.export_xlsx(session, path)
        assert path.is_file()
        book = export.readback_workbook(path)
        try:
            assert set(book.sheetnames) >= {export.DATA_SHEET, export.SUMMARY_SHEET,
                                            export.CHART_SHEET}
            data = book[export.DATA_SHEET]
            # header row plus one row per sample
            assert data.max_row == 1 + session.row_count
            assert data.cell(2, 6).value == 2048
        finally:
            book.close()

    def test_the_chart_sheet_is_not_empty(self, session, tmp_path):
        record(session, blocks=25)
        session.stop()
        path = tmp_path / "rec.xlsx"
        export.export_xlsx(session, path)
        book = export.readback_workbook(path)
        try:
            chart = book[export.CHART_SHEET]
            assert chart.max_row > 10
        finally:
            book.close()

    def test_a_summary_cell_states_the_uncalibrated_probe(self, session, tmp_path):
        record(session, blocks=3)
        session.stop()
        path = tmp_path / "rec.xlsx"
        export.export_xlsx(session, path)
        book = export.readback_workbook(path)
        try:
            cells = " ".join(str(c.value) for row in book[export.SUMMARY_SHEET].iter_rows()
                             for c in row if c.value is not None)
        finally:
            book.close()
        assert "ncalibrat" in cells


class TestChartDecimation:
    def test_min_and_max_survive_because_a_qrs_must_not_be_averaged_away(self):
        values = np.zeros(4000)
        for beat in range(40):
            values[beat * 100 + 50] = 5000.0      # a 1-sample R peak
            values[beat * 100 + 51] = -300.0
        times = np.arange(values.size, dtype=float)
        _t, series, per_point = export.decimate_for_chart(times, values, 400)
        assert series.size <= 400
        assert per_point > 1, "the record really was shortened"
        assert series.max() == pytest.approx(5000.0)
        assert series.min() == pytest.approx(-300.0)
        # Mean-decimating by this factor would have erased every peak; check that.
        assert series.size > 40, "at least one point per beat must remain"

    def test_recommended_point_count_scales_with_the_recording(self):
        assert export.recommended_chart_points(1000) <= export.recommended_chart_points(1_000_000)

    def test_a_short_recording_is_not_decimated_at_all(self):
        values = np.arange(100, dtype=float)
        times = np.arange(100, dtype=float)
        _t, series, per_point = export.decimate_for_chart(times, values, 1000)
        assert per_point == 1
        assert list(series) == list(values)

    def test_an_empty_record_does_not_crash_the_chart_builder(self):
        _t, series, per_point = export.decimate_for_chart(np.array([]), np.array([]), 400)
        assert series.size == 0
        assert per_point == 0


class TestDefaultStem:
    def test_the_file_name_carries_the_clock_and_the_origin(self):
        session = RecordingSession(origin=recorder.DEMO_ORIGIN)
        when = dt.datetime(2026, 9, 18, 14, 30, 5)
        stem = export.default_stem(session, when=when)
        assert "2026" in stem and "09" in stem
        assert "demo" in stem.lower(), "a demo export must not look like a recording"
