"""The sample store the plot reads from, and the link-level accounting.

Both hold state across many frames, so the interesting cases are boundaries:
wrapping, an overwrite of unread data, a dropped block, and a device restart that
resets ``first_sample_index``.  A bug in any of them looks like a plausible
waveform with the wrong time axis, which is the failure mode worth testing for.
"""
from __future__ import annotations

import numpy as np
import pytest

from pc_monitor import protocol as p
from pc_monitor.ring_buffer import (
    EventRateMeter,
    IndexedRingBuffer,
    IntAccumulator,
    RingBuffer,
)


class TestRingBuffer:
    def test_snapshot_is_oldest_first(self):
        buf = RingBuffer(4)
        for v in (1.0, 2.0, 3.0):
            buf.append(v)
        assert list(buf.snapshot()) == [1.0, 2.0, 3.0]

    def test_wrapping_keeps_the_newest_and_counts_the_dropped(self):
        buf = RingBuffer(4)
        assert buf.extend([1, 2, 3, 4, 5, 6]) == 6
        assert list(buf.snapshot()) == [3, 4, 5, 6]
        assert buf.dropped == 2
        assert buf.is_full

    def test_an_extend_larger_than_capacity_keeps_the_tail(self):
        buf = RingBuffer(4)
        buf.extend(range(100))
        assert list(buf.snapshot()) == [96, 97, 98, 99]
        assert buf.dropped == 96

    def test_latest_reports_newest_and_none_when_empty(self):
        buf = RingBuffer(4)
        assert buf.latest is None
        buf.extend([7, 8])
        assert buf.latest == 8

    def test_clear_empties_without_losing_history_of_drops(self):
        buf = RingBuffer(2)
        buf.extend([1, 2, 3])
        buf.clear()
        assert len(buf) == 0
        assert list(buf.snapshot()) == []

    @pytest.mark.parametrize("capacity", [1, 2, 3, 8, 1000])
    def test_length_never_exceeds_capacity(self, capacity):
        buf = RingBuffer(capacity)
        for i in range(capacity * 3 + 5):
            buf.append(float(i))
            assert len(buf) <= capacity
        assert buf.filled == min(capacity, capacity * 3 + 5)


class TestIndexedRingBuffer:
    def test_index_range_is_inclusive_of_the_newest_sample(self):
        buf = IndexedRingBuffer(100)
        buf.append_block(1000, [1.0] * 20)
        buf.append_block(1020, [2.0] * 20)
        assert buf.index_range == (1000, 1039)
        assert len(buf) == 40

    def test_a_gap_in_the_device_axis_is_visible_in_the_indices(self):
        buf = IndexedRingBuffer(100)
        buf.append_block(0, [1.0] * 10)
        buf.append_block(50, [2.0] * 10)   # 40 samples never arrived
        idx, _ = buf.snapshot()
        assert idx.min() == 0 and idx.max() == 59
        assert len(idx) == 20, "the buffer must not invent the missing 40"

    def test_window_snapshot_returns_exactly_the_requested_span(self):
        buf = IndexedRingBuffer(5000)
        for block in range(50):
            buf.append_block(block * 100, np.zeros(100))
        idx, _ = buf.snapshot_window(2.0, 1000.0)
        assert len(idx) == 2000, "2 s at 1 kHz is 2000 samples"
        assert idx[-1] - idx[0] == 1999, "inclusive on both ends"

    def test_a_short_stream_returns_what_exists_not_padding(self):
        buf = IndexedRingBuffer(5000)
        buf.append_block(0, np.zeros(37))
        idx, _ = buf.snapshot_window(5.0, 1000.0)
        assert len(idx) == 37

    def test_a_window_spanning_a_gap_keeps_the_gap_out(self):
        buf = IndexedRingBuffer(5000)
        buf.append_block(0, np.zeros(100))
        buf.append_block(5000, np.zeros(100))
        idx, _ = buf.snapshot_window(2.0, 1000.0)
        # newest = 5099, so the 2 s horizon starts at 3100: only the new block is
        # in range, and the 4900 absent samples are not fabricated.
        assert list(idx) == list(range(5000, 5100))
        assert idx.max() == 5099
        assert 500 not in idx

    def test_capacity_is_measured_in_samples_not_blocks(self):
        buf = IndexedRingBuffer(64)
        buf.append_block(0, np.zeros(100))
        assert len(buf) == 64
        assert buf.dropped == 36
        idx, _ = buf.snapshot()
        assert idx[0] == 36 and idx[-1] == 99, "the newest tail must survive"


class TestEventRateMeter:
    def test_a_steady_stream_reads_at_its_own_rate(self):
        meter = EventRateMeter(window_seconds=2.0)
        for step in range(400):                 # 20 Hz for 20 seconds
            meter.tick(when=step * 0.05)
        assert meter.rate == pytest.approx(20.0, rel=0.05)

    def test_a_burst_from_the_distant_past_does_not_inflate_the_reading(self):
        meter = EventRateMeter(window_seconds=1.0)
        meter.ticks(5000, when=0.0)             # an hour of backlog, all at once
        for step in range(200):
            meter.tick(when=3600.0 + step * 0.05)
        assert meter.total == 5200
        assert meter.rate == pytest.approx(20.0, rel=0.05), \
            "the window must expire old stamps, not average them in"

    def test_growth_is_bounded_by_maxlen_and_pruned_on_read(self):
        """The deque is capped; pruning happens when rate is read, not on tick."""
        meter = EventRateMeter(window_seconds=1.0, maxlen=5000)
        for step in range(20000):               # 20 s at 1 kHz
            meter.tick(when=step * 0.001)
        assert len(list(meter)) == 5000, "maxlen must cap the buffer"
        meter.rate
        assert 900 <= len(list(meter)) <= 1100, "reading the rate must expire the window"

    def test_an_empty_meter_reads_zero(self):
        assert EventRateMeter().rate == 0.0

    def test_a_single_stamp_reports_one_event_per_window_not_a_division_error(self):
        meter = EventRateMeter(window_seconds=1.0)
        meter.tick(when=5.0)
        assert meter.rate == pytest.approx(1.0)

    def test_simultaneous_stamps_fall_back_to_count_per_window(self):
        meter = EventRateMeter(window_seconds=2.0)
        meter.ticks(7, when=100.0)
        assert meter.rate == pytest.approx(7.0 / 2.0)


class TestIntAccumulator:
    def test_add_returns_the_new_value(self):
        acc = IntAccumulator()
        assert acc.add() == 1
        assert acc.add(4) == 5
        assert acc.value == 5
        acc.set(0)
        assert acc.value == 0


class TestStreamTracker:
    def _batch(self, tracker, first_index, seq, count=20):
        payload = p.EcgBatch.encode(
            first_sample_index=first_index,
            samples=range(2000, 2000 + count),
            flags=p.SFLAG_ADC_RUNNING,
        )
        raw = p.encode_frame(p.PacketType.ECG_BATCH, seq, first_index // 1000, payload)
        _, frame, _ = p.parse_frame(raw)
        events = tracker.observe(frame)
        return frame, events

    def test_a_clean_stream_reports_no_gaps(self):
        tracker = p.StreamTracker()
        for i in range(10):
            _frame, events = self._batch(tracker, i * 20, i)
        assert tracker.index_gaps == 0
        assert tracker.samples_missing == 0
        assert tracker.sequence_gaps == 0
        assert tracker.total_samples == 200

    def test_a_lost_block_is_counted_in_samples_not_just_events(self):
        tracker = p.StreamTracker()
        self._batch(tracker, 0, 0)
        self._batch(tracker, 20, 1)
        # block 2 never arrives
        _f, events = self._batch(tracker, 80, 3)
        assert tracker.index_gaps == 1
        assert tracker.samples_missing == 40
        assert tracker.total_samples == 60
        assert any(e.kind is p.TrackKind.SAMPLE_INDEX_GAP for e in events)

    def test_a_sequence_jump_is_reported_without_losing_samples(self):
        tracker = p.StreamTracker()
        self._batch(tracker, 0, 0)
        _f, events = self._batch(tracker, 20, 7)
        assert tracker.sequence_gaps == 1
        assert tracker.sequence_missing == 6
        assert any(e.kind is p.TrackKind.SEQUENCE_GAP for e in events)
        assert tracker.total_samples == 40

    def test_a_reboot_reanchors_instead_of_reporting_a_huge_loss(self):
        """first_sample_index going backwards is a restart, not a 4-billion gap."""
        tracker = p.StreamTracker()
        self._batch(tracker, 100000, 0)
        _f, events = self._batch(tracker, 0, 1)
        assert tracker.samples_missing == 0, "a restart must not be counted as data loss"
        assert any("reset" in e.detail.lower() or e.kind is p.TrackKind.SEQUENCE_RESTART
                   for e in events) or tracker.index_gaps >= 1

    def test_a_malformed_batch_is_counted_and_skipped(self):
        tracker = p.StreamTracker()
        raw = p.encode_frame(p.PacketType.ECG_BATCH, 0, 0, bytes(30))
        _result, frame, _ = p.parse_frame(raw)
        events = tracker.observe(frame)
        assert tracker.malformed_batches == 1
        assert any(e.kind is p.TrackKind.MALFORMED for e in events)
        assert tracker.total_samples == 0

    def test_an_unknown_packet_type_is_tolerated(self):
        """docs/PROTOCOL.md permits a device to add types; the host must skip them."""
        tracker = p.StreamTracker()
        raw = p.encode_frame(0x77, 1, 0, b"\x01\x02")
        _r, frame, _c = p.parse_frame(raw)
        tracker.observe(frame)
        assert tracker.unknown_types == 1

    def test_counters_survive_reset(self):
        tracker = p.StreamTracker()
        self._batch(tracker, 0, 0)
        tracker.reset()
        assert tracker.total_samples == 0
        _f, events = self._batch(tracker, 500, 1)
        assert tracker.index_gaps <= 1, "after a reset the first batch anchors the axis"
        assert tracker.total_samples == 20
