"""End-to-end GUI verification without a display.

`--demo` is the only way this application can be exercised before the board
exists, so these tests drive the real widgets: they click *Start acquisition* and
*Record*, let the Qt event loop run, and then assert that pixels and rows actually
appeared.  ``test_recording_and_export.py`` proves the model; this file proves the
view is wired to it.

Runs under ``QT_QPA_PLATFORM=offscreen``, which needs no monitor.
"""
from __future__ import annotations

import csv
import os

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtWidgets  # noqa: E402

from pc_monitor import export, protocol as p  # noqa: E402
from pc_monitor.app import MonitorWindow  # noqa: E402
from pc_monitor.demo_source import DEMO_ORIGIN  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


def pump(seconds: float) -> None:
    """Let the event loop run for real time, so the worker thread can deliver."""
    clock = QtCore.QElapsedTimer()
    clock.start()
    while clock.elapsed() < seconds * 1000:
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 20)
        QtCore.QThread.msleep(5)


def label_text(widget) -> str:
    return " ".join(w.text() for w in widget.findChildren(QtWidgets.QLabel) if w.text())


@pytest.fixture
def window(qapp):
    win = MonitorWindow(demo=True, window_seconds=5.0)
    win.show()
    pump(0.3)
    yield win
    win.close()


class TestTheWindowComesUp:
    def test_a_demo_window_builds_and_finds_its_device(self, window):
        assert window.isVisible()
        metrics = window.engine.metrics()
        assert metrics["demo"] is True
        assert metrics["link_open"] is True
        assert metrics["hello_present"] is True

    def test_the_demo_banner_is_on_screen_not_just_in_the_model(self, window):
        text = label_text(window).upper()
        assert "SYNTHETIC" in text or "DEMO" in text

    def test_nothing_arrives_until_acquisition_is_started(self, window):
        pump(0.6)
        assert window.engine.metrics()["samples"] == 0
        assert window.stream_button.isChecked() is False


class TestAcquisitionPath:
    def test_clicking_start_makes_samples_and_pixels_appear(self, window):
        window.stream_button.click()
        pump(1.2)
        metrics = window.engine.metrics()
        assert window.stream_button.isChecked()
        assert metrics["samples"] > 500, "1 kHz for 1.2 s should fill the buffer"
        assert window.pane.plotted_points > 500, "the curve must actually be drawn"
        assert metrics["crc_errors"] == 0
        assert metrics["index_gaps"] == 0

    def test_the_plot_window_stays_inside_the_requested_span(self, window):
        window.stream_button.click()
        pump(1.5)
        times, _values = window.engine.plot_window(window.pane.window_seconds)
        assert 0 < len(times) <= window.pane.window_seconds * 1000 + 1

    def test_the_rate_is_withheld_while_the_detector_is_acquiring(self, window):
        """The first 3 s mirror ECG_SETTLE_SAMPLES: no bpm, state ACQUIRING.

        A card that showed 0 or a stale number here would look like bradycardia,
        so the honest rendering is the one under test.
        """
        window.stream_button.click()
        pump(1.0)
        metrics = window.engine.metrics()
        assert metrics["hr_bpm"] == 0
        assert metrics["hr_valid"] is False
        assert metrics["hr_state"] == int(p.HrState.ACQUIRING)
        text = label_text(window.cards).upper()
        assert "ACQ" in text or "--" in text, "the card shows a bare 0 during acquisition"

    def test_the_metric_cards_show_the_rate_once_it_locks(self, window):
        window.stream_button.click()
        pump(4.0)  # past the demo device's 3 s settling window
        metrics = window.engine.metrics()
        assert metrics["hr_bpm"] > 0
        assert metrics["hr_valid"] is True
        assert str(metrics["hr_bpm"]) in label_text(window.cards), \
            "the card never received the rate"

    def test_clicking_again_stops_the_stream(self, window):
        window.stream_button.click()
        pump(0.6)
        window.stream_button.click()
        assert not window.stream_button.isChecked()
        pump(0.3)
        settled = window.engine.metrics()["samples"]
        pump(0.8)
        assert window.engine.metrics()["samples"] == settled, "STOP_STREAM was ignored"


class TestRecordingPath:
    def test_recording_accumulates_rows_the_model_can_count(self, window):
        window.stream_button.click()
        pump(0.4)
        window.record_button.click()
        pump(1.2)
        window.record_button.click()
        session = window.engine.session
        assert window.engine.metrics()["recording_rows"] > 1000
        assert session.row_count == window.engine.metrics()["recording_rows"]
        assert session.origin == DEMO_ORIGIN, "--demo must stay marked"

    def test_a_demo_export_is_tagged_on_every_row(self, window, tmp_path):
        window.stream_button.click()
        pump(0.3)
        window.record_button.click()
        pump(0.8)
        window.record_button.click()
        path = tmp_path / "out.csv"
        export.export_csv(window.engine.session, path)
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == window.engine.session.row_count
        assert {r["origin"] for r in rows} == {DEMO_ORIGIN}
        stem = export.default_stem(window.engine.session)
        assert "demo" in stem.lower(), "the file name hides that it is synthetic"

    def test_closing_the_window_stops_the_recording(self, window):
        window.stream_button.click()
        window.record_button.click()
        pump(0.3)
        window.close()
        pump(0.2)
        assert window.engine.session.is_recording is False


class TestErrorSurfacing:
    def test_a_malformed_batch_is_counted_not_swallowed(self, window):
        window.stream_button.click()
        pump(0.4)
        bogus = p.encode_frame(p.PacketType.ECG_BATCH, 1, 0, bytes(30))
        _result, frame, _consumed = p.parse_frame(bogus)
        window.engine.handle_frames([frame])
        pump(0.2)
        assert window.engine.tracker.malformed_batches == 1
        assert window.engine.metrics()["malformed_batches"] == 1

    def test_a_command_on_a_closed_link_is_reported_not_lost(self, window, qapp):
        win = MonitorWindow(demo=False, autoconnect=False)
        win.show()
        pump(0.2)
        before = label_text(win.panel)
        win.stream_button.click()
        pump(0.2)
        assert label_text(win.panel) != before or win.log.toPlainText()
        win.close()
