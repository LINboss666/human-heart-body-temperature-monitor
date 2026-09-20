"""The language layer's own contract, and a completeness gate.

The point of these tests is not that a dictionary has entries -- it is that a
string cannot be added to the interface without either being translated or
failing here. That is what keeps a bilingual UI from rotting into a half-English
one, which is the failure mode this tool would otherwise drift to: the English
fallback is silent by design, so without these assertions nobody notices.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pc_monitor import i18n  # noqa: E402

CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")

#: Text that is protocol or unit vocabulary rather than interface copy, so it is
#: correct in either language. Keep this short and justified: every entry is a
#: string the tool shows untranslated on purpose.
NOT_TRANSLATED = {
    "CSV",
    "XLSX",
    "Export CSV",
    "Export XLSX",
    "COM",
    "HELLO",
    "STATUS",
    "ECG_BATCH",
    "TEMP_STATUS",
    "PING",
    "PONG",
    "mV",
    "bpm",
    "Hz",
    "DEMO",
}


@pytest.fixture(scope="module")
def qapp():
    """One QApplication per module, on the offscreen platform: no monitor needed."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


def pump(seconds: float) -> None:
    """Let the event loop run for real time, so the worker thread can deliver."""
    from PySide6 import QtCore, QtWidgets

    clock = QtCore.QElapsedTimer()
    clock.start()
    while clock.elapsed() < seconds * 1000:
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 20)
        QtCore.QThread.msleep(5)


@pytest.fixture(autouse=True)
def _restore_language():
    previous = i18n.language()
    i18n.reset_missed()
    yield
    i18n.set_language(previous)
    i18n.reset_missed()


class TestLanguageSelection:
    def test_chinese_is_the_default(self):
        # The tool is for people who read Chinese; English stays available.
        assert i18n.DEFAULT_LANGUAGE == "zh"
        assert "en" in i18n.LANGUAGES

    def test_unknown_language_is_refused_not_guessed(self):
        with pytest.raises(ValueError):
            i18n.set_language("fr")

    def test_english_returns_every_input_unchanged(self):
        i18n.set_language("en")
        i18n.reset_missed()
        assert i18n.t("Start acquisition") == "Start acquisition"
        assert i18n.t("no such key anywhere") == "no such key anywhere"
        # Falling back must not be mistaken for a gap that needs filling.
        assert i18n.missed() == ()

    def test_untranslated_string_falls_back_and_is_recorded(self):
        i18n.set_language("zh")
        assert i18n.t("A string nobody registered") == "A string nobody registered"
        assert "A string nobody registered" in i18n.missed()


class TestTableIntegrity:
    def test_tables_do_not_collide_across_modules(self):
        # Two modules translating the same English literal differently would make
        # the UI inconsistent and the winner depend on import order.
        seen: dict[str, str] = {}
        for name in i18n._TABLE_MODULES:
            module = __import__("pc_monitor.%s" % name, fromlist=["ZH"])
            for key, value in module.ZH.items():
                if key in seen and seen[key] != value:
                    pytest.fail(
                        "%r translated two ways: %r and %r" % (key, seen[key], value)
                    )
                seen[key] = value

    def test_no_entry_is_a_no_op(self):
        for key, value in i18n.entries():
            assert key != value, "translation entry that changes nothing: %r" % key
            assert value.strip(), "blank translation for %r" % key

    def test_an_identity_entry_is_refused_at_registration(self):
        # The tempting way to silence a missed() report is to map a string to
        # itself, which proves nothing. Notation that is correct in every
        # language has to be declared, so it stays visible what was skipped.
        with pytest.raises(ValueError):
            i18n.register({"A string shown as written": "A string shown as written"})

    def test_exempt_notation_is_never_reported_as_a_gap(self):
        i18n.set_language("zh")
        i18n.reset_missed()
        for text in ("flags 0x%04X", "ADC %s"):
            assert i18n.t(text) == text
            assert i18n.is_exempt(text)
        assert i18n.missed() == ()

    def test_a_string_cannot_be_both_translated_and_exempt(self):
        with pytest.raises(ValueError):
            i18n.exempt({"Start acquisition"})

    def test_placeholders_survive_translation(self):
        # A Chinese template that drops a %s breaks the call site, and one that
        # gains one raises at the caller -- both are checked here instead.
        import pc_monitor.i18n_app
        import pc_monitor.i18n_export
        import pc_monitor.i18n_widgets

        tables = (
            pc_monitor.i18n_app.ZH,
            pc_monitor.i18n_widgets.ZH,
            pc_monitor.i18n_export.ZH,
        )
        for table in tables:
            for key, value in table.items():
                assert sorted(re.findall(r"%[-+ #0-9.]*[sdfrx%]", key)) == sorted(
                    re.findall(r"%[-+ #0-9.]*[sdfrx%]", value)
                ), "format specifiers changed in %r" % key

    def test_protocol_vocabulary_is_not_translated_away(self):
        # These names come off the wire; a translation that rewrites them inside a
        # message makes the log unreadable against docs/PROTOCOL.md.
        tokens = ("HELLO", "STATUS", "ECG_BATCH", "TEMP_STATUS", "SET_RTC", "PING")
        for key, value in i18n.entries():
            for token in tokens:
                if re.search(r"\b%s\b" % token, key):
                    assert token in value, "%r lost the packet name %s" % (key, token)


def _visible_texts(window):
    out = []
    from PySide6 import QtWidgets

    for child in window.findChildren(QtWidgets.QWidget):
        for getter in ("text", "title", "toolTip", "placeholderText"):
            fn = getattr(child, getter, None)
            if fn is None:
                continue
            try:
                value = fn()
            except Exception:
                continue
            if isinstance(value, str) and value.strip():
                out.append(value.strip())
    return out


class TestNothingIsLeftBehind:
    """Build the real window and prove the interface has no silent gaps."""

    def _build(self, qapp, lang):
        from pc_monitor.app import MonitorWindow

        i18n.set_language(lang)
        i18n.reset_missed()
        window = MonitorWindow(demo=True)
        window.show()
        return window

    def test_every_label_is_chinese_by_default(self, qapp):
        window = self._build(qapp, "zh")
        texts = _visible_texts(window)
        assert texts, "the window produced no labels at all"
        plain = [
            t
            for t in texts
            if not CJK.search(t)
            and re.search(r"[A-Za-z]{3,}", t)
            and t not in NOT_TRANSLATED
        ]
        window.close()
        assert not plain, "labels still English in zh mode: %s" % sorted(set(plain))

    def test_no_lookup_missed_while_building(self, qapp):
        window = self._build(qapp, "zh")
        missed = i18n.missed()
        window.close()
        assert not missed, "strings shown without a translation: %s" % list(missed)

    def test_runtime_messages_are_translated_too(self, qapp):
        # The log lines only exist once the engine has talked to something, so
        # drive the synthetic device and read what came out.
        window = self._build(qapp, "zh")
        window.stream_button.setChecked(True)
        window.record_button.setChecked(True)
        pump(3.0)
        log = window.log.toPlainText()
        missed = i18n.missed()
        window.record_button.setChecked(False)
        window.stream_button.setChecked(False)
        window.close()
        assert log.strip(), "no log lines were produced, so this test proved nothing"
        assert not missed, "log strings shown without a translation: %s" % list(missed)
        assert CJK.search(log), "the event log is still English in zh mode: %r" % log[:200]

    def test_english_mode_shows_no_chinese(self, qapp):
        window = self._build(qapp, "en")
        texts = _visible_texts(window)
        missed = i18n.missed()
        window.close()
        assert not missed, "English mode must not consult the tables at all"
        assert not [t for t in texts if CJK.search(t)], "Chinese leaked into --lang en"


class TestChineseExports:
    """The exported files follow the same rule as the window: Chinese by default,
    field names intact, and the synthetic-data warning unmistakable."""

    @staticmethod
    def _session():
        from pc_monitor.recorder import RecordingSession

        session = RecordingSession(source_label="COM7", device_identity="fw 1.0.0 proto 0x02")
        session.start()
        for block in range(5):
            # Imported lazily so this file does not depend on test collection order.
            from test_recording_and_export import batch_frame

            frame, batch = batch_frame(block * 20, block)
            session.add_batch(frame, batch, host_epoch_s=1700000000.0 + block * 0.02)
        session.stop()
        return session

    def test_csv_header_is_chinese_and_keeps_the_field_names(self, tmp_path):
        import csv

        from pc_monitor import export, recorder

        i18n.set_language("zh")
        i18n.reset_missed()
        path = tmp_path / "zh.csv"
        export.export_csv(self._session(), path)
        header = next(csv.reader(path.open(encoding="utf-8-sig", newline="")))
        assert any(CJK.search(cell) for cell in header), header
        # The protocol field names are the contract with anyone reading the file.
        joined = " ".join(header)
        for token in recorder.ROW_COLUMNS:
            assert token in joined, "%s lost from the CSV header" % token
        assert i18n.missed() == ()

    def test_workbook_sheets_and_summary_are_chinese(self, tmp_path):
        from pc_monitor import export

        i18n.set_language("zh")
        session = self._session()
        path = tmp_path / "zh.xlsx"
        export.export_xlsx(session, path)
        book = export.readback_workbook(path)
        try:
            assert all(CJK.search(name) or name == "ECG 序列" for name in book.sheetnames), book.sheetnames
            summary = book[book.sheetnames[1]]
            labels = [summary.cell(row, 1).value for row in range(1, 25)]
            assert any(isinstance(v, str) and CJK.search(v) for v in labels), labels
            assert not any(v == "no HELLO received" for v in labels if isinstance(v, str))
        finally:
            book.close()
        assert i18n.missed() == ()

    def test_data_cells_do_not_depend_on_language(self, tmp_path):
        # Translation may rename a column; it must never move or restate a value.
        import csv

        from pc_monitor import export

        session = self._session()
        rows = {}
        for lang in ("zh", "en"):
            i18n.set_language(lang)
            path = tmp_path / ("cmp_%s.csv" % lang)
            export.export_csv(session, path)
            with path.open(encoding="utf-8-sig", newline="") as handle:
                parsed = list(csv.reader(handle))
            rows[lang] = (parsed[0], parsed[1:])
        assert rows["zh"][1] == rows["en"][1], "data rows changed with the language"
        assert len(rows["zh"][0]) == len(rows["en"][0])
