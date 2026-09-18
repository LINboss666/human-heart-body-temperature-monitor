"""The synthetic device behind ``--demo``.

The demo mode is the only way anyone can exercise the PC tool before the board
exists, so two things have to be true of it: it has to speak the real protocol
well enough to drive every code path, and it has to be un-forgettable about being
synthetic.  A demo that could be quietly re-labelled as a measurement would be
worse than no demo at all.
"""
from __future__ import annotations

import pytest

from pc_monitor import demo_source as d
from pc_monitor import protocol as p


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class TickingClock(Clock):
    """Moves forward a step every time it is read.

    ``DemoDevice.poll()`` with no argument and the helpers built on it ask the
    device clock how far we have got.  A frozen clock therefore never terminates
    those loops, so anything that goes through ``_now()`` needs one that advances
    itself.
    """

    def __init__(self, step: float = 0.02):
        super().__init__()
        self.step = float(step)

    def __call__(self) -> float:
        self.now += self.step
        return self.now


def collect(device, clock, seconds, step=0.02, start_stream=True):
    """Advance a fake clock and return every frame the device produced.

    The device only streams once asked, exactly like the real firmware, so the
    helper asks first unless a test is checking the idle case.
    """
    if start_stream:
        device.handle_command(p.encode_start_stream(0))
    out = []
    elapsed = 0.0
    while elapsed < seconds:
        elapsed += step
        clock.now = elapsed
        out.extend(device.poll(elapsed))
    return out


class TestSyntheticWaveform:
    def test_codes_stay_inside_the_adc_range(self):
        ecg = d.SyntheticEcg(target_hr=72.0, seed=7)
        codes = ecg.codes(0, 5000)
        assert codes.dtype.kind in "ui"
        assert codes.min() >= 0
        assert codes.max() <= p.ADC_FULL_SCALE_CODES

    def test_the_beat_rate_is_roughly_what_was_asked_for(self):
        """Not a claim about the detector -- about the *stimulus* being sane."""
        ecg = d.SyntheticEcg(target_hr=60.0, seed=11)
        assert abs(ecg.hr_bpm(10.0) - 60) <= 12

    def test_a_rising_baseline_never_leaves_the_code_range(self):
        ecg = d.SyntheticEcg(target_hr=150.0, seed=3)
        codes = ecg.codes(0, 20000)
        assert 0 <= codes.min() and codes.max() <= p.ADC_FULL_SCALE_CODES

    def test_the_generator_counts_what_it_emitted(self):
        ecg = d.SyntheticEcg(seed=5)
        ecg.codes(0, 100)
        ecg.codes(100, 50)
        assert ecg.generated_codes == 150

    def test_reset_returns_to_the_initial_state(self):
        ecg = d.SyntheticEcg(seed=5)
        first = ecg.codes(0, 40)
        ecg.reset()
        assert list(ecg.codes(0, 40)) == list(first), "seeded output must be repeatable"


class TestDemoDeviceSpeaksTheProtocol:
    def setup_method(self):
        self.clock = Clock()
        self.device = d.DemoDevice(clock=self.clock, inject_dropped_batches=0.0,
                                   inject_crc_errors=0.0)
        self.device.connect()

    def test_every_frame_it_emits_parses(self):
        frames = collect(self.device, self.clock, 2.0)
        parser = p.IncrementalParser()
        parsed = []
        for raw in frames:
            parsed.extend(parser.feed(raw))
        assert parsed, "the demo device produced nothing"
        assert parser.crc_errors == 0
        assert len(parsed) == len(frames)

    def test_the_boot_frame_is_a_hello_that_advertises_the_real_rate(self):
        parser = p.IncrementalParser()
        hello = parser.feed(self.device.hello_frame())[0]
        body = p.decode_hello(hello)
        assert hello.type == int(p.PacketType.HELLO)
        assert body.sample_rate_hz == 1000
        assert body.batch_max_samples == p.ECG_BATCH_MAX_SAMPLES
        assert body.proto_version == p.PROTOCOL_VERSION

    def test_the_hello_claims_only_capabilities_a_demo_can_actually_simulate(self):
        """A demo may pretend to have a panel and a calibrated probe.

        What it must never claim are the four the real firmware also cannot:
        lead-off hardware, probe fault hardware, a verified front-end and a
        battery-backed RTC.  Those stay clear so the GUI's honest branches are
        the branches --demo exercises.
        """
        hello = hello_body(self.device)
        assert hello.caps & int(p.Capability.OLED_PRESENT)
        unclaimed = (int(p.Capability.LEAD_HW_DETECT) | int(p.Capability.PROBE_HW_DETECT)
                     | int(p.Capability.FRONTEND_VERIFIED)
                     | int(p.Capability.RTC_BATTERY_BACKED))
        assert hello.caps & unclaimed == 0, "the demo advertised hardware nobody built"

    def test_the_uncalibrated_demo_path_matches_the_shipping_firmware(self):
        """``calibrated_temperature=False`` is what the real board reports today."""
        clock = Clock()
        device = d.DemoDevice(clock=clock, calibrated_temperature=False)
        device.connect()
        hello = hello_body(device)
        assert hello.caps & int(p.Capability.TEMP_CALIBRATED) == 0
        frames = collect(device, clock, 0.6, start_stream=False)
        parser = p.IncrementalParser()
        states = [p.decode_temp_status(f).temp_state
                  for raw in frames for f in parser.feed(raw)
                  if f.type == int(p.PacketType.TEMP_STATUS)]
        assert states, "no TEMP_STATUS in 0.6 s at 2 Hz"
        assert all(s == int(p.TempState.UNCALIBRATED) for s in states)

    def test_batch_cadence_matches_the_firmware_contract(self):
        """50 batches/s of 20 samples is what app_config.h says the device does."""
        frames = collect(self.device, self.clock, 4.0)
        parser = p.IncrementalParser()
        parsed = [f for raw in frames for f in parser.feed(raw)]
        batches = [f for f in parsed if f.type == int(p.PacketType.ECG_BATCH)]
        assert len(batches) == pytest.approx(200, abs=4)
        total = sum(p.decode_ecg_batch(b).count for b in batches)
        assert total == pytest.approx(4000, abs=80)

    def test_the_sample_axis_is_contiguous_when_nothing_is_dropped(self):
        frames = collect(self.device, self.clock, 2.0)
        parser = p.IncrementalParser()
        tracker = p.StreamTracker()
        for raw in frames:
            for frame in parser.feed(raw):
                tracker.observe(frame)
        assert tracker.samples_missing == 0
        assert tracker.index_gaps == 0
        assert tracker.total_samples > 1000

    def test_injected_drops_are_visible_as_gaps_not_as_corruption(self):
        clock = Clock()
        leaky = d.DemoDevice(clock=clock, inject_dropped_batches=0.1,
                             inject_crc_errors=0.0)
        leaky.connect()
        frames = collect(leaky, clock, 4.0)
        parser = p.IncrementalParser()
        tracker = p.StreamTracker()
        for raw in frames:
            for frame in parser.feed(raw):
                tracker.observe(frame)
        assert tracker.index_gaps > 0, "the drop injection is not reaching the wire"
        assert parser.crc_errors == 0
        assert tracker.malformed_batches == 0

    def test_injected_crc_errors_are_counted_and_recovered_from(self):
        clock = Clock()
        noisy = d.DemoDevice(clock=clock, inject_dropped_batches=0.0,
                             inject_crc_errors=0.05)
        noisy.connect()
        frames = collect(noisy, clock, 4.0)
        parser = p.IncrementalParser()
        parsed = [f for raw in frames for f in parser.feed(raw)]
        assert parser.crc_errors > 0
        assert len(parsed) > 0, "the stream must survive its own corruption"
        assert noisy.frames_corrupted > 0

    def test_streaming_is_off_until_the_host_asks(self):
        device = d.DemoDevice(clock=self.clock, inject_dropped_batches=0.0,
                              inject_crc_errors=0.0)
        device.connect()
        self.clock.now = 0.5
        idle = device.poll(self.clock.now)
        kinds = {p.parse_frame(f)[1].type for f in idle}
        assert int(p.PacketType.ECG_BATCH) not in kinds, \
            "the record must not stream before START_STREAM"
        device.handle_command(p.encode_start_stream(1))
        assert device.streaming
        self.clock.now = 0.52
        frames = device.poll(self.clock.now)
        assert any(p.parse_frame(f)[1].type == int(p.PacketType.ECG_BATCH)
                   for f in frames)
        device.handle_command(p.encode_stop_stream(2))
        assert not device.streaming

    def test_it_answers_ping_get_rtc_and_set_rtc(self):
        for command in (p.encode_ping(b"tok\x01", sequence=1),
                        p.encode_get_rtc(2),
                        p.encode_set_rtc(year=2026, month=9, day=18, hour=14,
                                         minute=30, second=0, sequence=3)):
            replies = self.device.handle_command(command)
            assert replies, "no answer to %02X" % command[3]
            parser = p.IncrementalParser()
            frames = [f for raw in replies for f in parser.feed(raw)]
            assert frames
            assert parser.crc_errors == 0
            assert all(f.type in {int(t) for t in p.PacketType} for f in frames)

    def test_a_set_rtc_is_echoed_back_as_the_same_calendar(self):
        self.device.handle_command(p.encode_get_rtc(1))
        self.device.handle_command(p.encode_set_rtc(year=2031, month=11, day=23,
                                                    hour=6, minute=45, second=12,
                                                    sequence=2))
        replies = self.device.handle_command(p.encode_get_rtc(3))
        parser = p.IncrementalParser()
        frames = [f for raw in replies for f in parser.feed(raw)]
        rtc = [f for f in frames if f.type == int(p.PacketType.RTC_RESPONSE)]
        assert rtc, "no RTC_RESPONSE after SET_RTC"
        cal = p.decode_rtc_response(rtc[-1])
        assert (cal.year, cal.month, cal.day, cal.hour) == (2031, 11, 23, 6)

    def test_a_rejected_set_rtc_reports_why_instead_of_going_quiet(self):
        """2026-02-30 is illegal; the host would refuse it, so force it onto the wire."""
        command = p.encode_set_rtc(year=2026, month=2, day=30, sequence=4,
                                   validate=False)
        replies = self.device.handle_command(command)
        parser = p.IncrementalParser()
        frames = [f for raw in replies for f in parser.feed(raw)]
        kinds = {f.type for f in frames}
        assert int(p.PacketType.ACK) not in kinds, "an illegal date was accepted"
        assert int(p.PacketType.NACK) in kinds
        nack = [f for f in frames if f.type == int(p.PacketType.NACK)][0]
        assert p.decode_nack(nack).reason == int(p.NackReason.BAD_VALUE)

    def test_an_unparseable_command_is_ignored_not_raised(self):
        assert isinstance(self.device.handle_command(b"\x00\x01junk"), list)


def hello_body(device) -> p.Hello:
    """The demo device's HELLO as a decoded body, not as frame bytes."""
    _result, frame, _consumed = p.parse_frame(device.hello_frame())
    return p.decode_hello(frame)


class TestDemoIsUnmistakable:
    def setup_method(self):
        self.device = d.DemoDevice(clock=Clock())
        self.device.connect()

    def test_the_banner_says_so_in_words(self):
        assert "DEMO" in d.DEMO_BANNER.upper()
        assert "SYNTHETIC" in d.DEMO_BANNER.upper()
        assert d.DEMO_SHORT_BANNER.upper() in d.DEMO_BANNER.upper()

    def test_looks_like_demo_recognises_the_devices_own_hello(self):
        assert d.looks_like_demo(hello_body(self.device)) is True

    def test_looks_like_demo_accepts_the_heuristic_it_documents(self):
        """Firmware 0.0.0 is the signature, so a real board could match by bad luck.

        The function's own docstring says it may only ever *add* to the banner;
        pinning that keeps a future edit from promoting it to an authority.
        """
        zero = hello_body(self.device)
        assert (zero.fw_major, zero.fw_minor, zero.fw_patch) == (0, 0, 0)
        assert d.looks_like_demo(zero) is True

    def test_a_versioned_hello_from_a_real_board_is_not_called_demo(self):
        payload = bytearray(p.HELLO_SIZE)
        payload[p.HELPP_FW_MAJOR] = 1
        payload[p.HELPP_PROTO_VER] = p.PROTOCOL_VERSION
        raw = bytes(payload)
        hello = p.decode_hello(raw)
        assert (hello.fw_major, hello.fw_minor, hello.fw_patch) == (1, 0, 0)
        assert d.looks_like_demo(hello, raw) is False

    def test_the_port_label_is_not_a_real_com_port_name(self):
        assert not d.DEMO_PORT_LABEL.upper().startswith("COM")


class TestByteSource:
    def test_it_behaves_like_a_serial_port_for_the_reader_thread(self):
        device = d.DemoDevice(clock=TickingClock(), inject_dropped_batches=0.0,
                              inject_crc_errors=0.0)
        source = d.DemoByteSource(device)
        source.open()
        try:
            total = b""
            for _ in range(80):
                total += source.read(256)
            assert total, "no bytes came out of the demo source"
            parser = p.IncrementalParser()
            assert parser.feed(total)
            assert source.total_read == len(total)
        finally:
            source.close()

    def test_closing_it_stops_producing_bytes(self):
        source = d.DemoByteSource(d.DemoDevice(clock=TickingClock()))
        source.open()
        source.close()
        assert source.read(256) == b""

    def test_writing_to_it_feeds_the_devices_command_parser(self):
        device = d.DemoDevice(clock=TickingClock())
        device.connect()
        source = d.DemoByteSource(device)
        source.open()
        try:
            source.write(p.encode_get_rtc(1))
            reply = b""
            for _ in range(10):
                reply += source.read(4096)
        finally:
            source.close()
        parser = p.IncrementalParser()
        frames = parser.feed(reply)
        assert any(f.type == int(p.PacketType.RTC_RESPONSE) for f in frames)

    def test_iter_demo_frames_covers_the_requested_duration(self):
        device = d.DemoDevice(clock=TickingClock(), inject_dropped_batches=0.0,
                              inject_crc_errors=0.0)
        device.connect()
        device.handle_command(p.encode_start_stream(0))
        frames = list(d.iter_demo_frames(device, 1.0, step_s=0.0))
        parser = p.IncrementalParser()
        parsed = [f for raw in frames for f in parser.feed(raw)]
        batches = [f for f in parsed if f.type == int(p.PacketType.ECG_BATCH)]
        assert len(batches) >= 40

    def test_the_declared_uart_baud_is_the_one_the_firmware_uses(self):
        assert d.protocol_uart_baud() == 230400
