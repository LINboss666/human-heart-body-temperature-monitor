"""Framing, CRC and the resynchronising byte-stream parser.

These are the parts both implementations write from scratch, so a disagreement is
a wire-format bug that would otherwise appear on the bench as unexplained data
loss. ``test_protocol_vectors.py`` covers the byte-for-byte comparison against C;
this file covers the behaviour of the receiver.
"""
from __future__ import annotations

import pytest

from pc_monitor import protocol as p


def frame_bytes(type_=p.PacketType.PONG, sequence=1, ts=0, payload=b"", version=None):
    return p.encode_frame(type_, sequence, ts, payload,
                          version=p.PROTOCOL_VERSION if version is None else version)


class TestCrc16:
    def test_published_check_value(self):
        assert p.crc16_ccitt_buf(b"123456789") == 0x29B1

    def test_empty_input_returns_the_init_value(self):
        assert p.crc16_ccitt(b"") == p.CRC16_INIT

    def test_incremental_equals_one_shot(self):
        data = bytes(range(200))
        one = p.crc16_ccitt(data)
        acc = p.CRC16_INIT
        for i in range(0, len(data), 7):
            acc = p.crc16_ccitt(data[i:i + 7], crc=acc)
        assert acc == one

    @pytest.mark.parametrize("bit", [0, 7, 8, 15, 31, 63])
    def test_one_flipped_bit_changes_the_crc(self, bit):
        base = bytearray(b"\x01\x02\x03\x04\x05\x06\x07\x08" * 10)
        flipped = bytearray(base)
        flipped[bit // 8] ^= 1 << (bit % 8)
        assert p.crc16_ccitt(bytes(base)) != p.crc16_ccitt(bytes(flipped))


class TestBuildParse:
    @pytest.mark.parametrize("n", [0, 1, 7, 8, 43, 55, 63, 64])
    def test_every_legal_payload_size_round_trips(self, n):
        payload = bytes((i * 37 + 11) & 0xFF for i in range(n))
        raw = frame_bytes(p.PacketType.ECG_BATCH, 0xBEEF, 0x0BADF00D, payload)
        assert len(raw) == p.OVERHEAD + n
        result, got, consumed = p.parse_frame(raw)
        assert result is p.ParseResult.OK
        assert consumed == len(raw)
        assert got.type == p.PacketType.ECG_BATCH
        assert got.sequence == 0xBEEF
        assert got.device_ts_ms == 0x0BADF00D
        assert got.payload == payload

    def test_oversized_payload_is_refused_not_truncated(self):
        with pytest.raises(ValueError):
            frame_bytes(payload=bytes(p.MAX_PAYLOAD + 1))

    def test_out_of_range_header_fields_are_refused(self):
        with pytest.raises(ValueError):
            p.encode_frame(p.PacketType.PING, 0x10000, 0)
        with pytest.raises(ValueError):
            p.encode_frame(p.PacketType.PING, 0, 0x100000000)

    def test_a_version_this_build_does_not_speak_is_rescanned_not_accepted(self):
        """``pkt_parse()`` treats an unparseable candidate as junk and moves on.

        It deliberately does not surface ERR_VERSION to a stream reader: a random
        0xA5 0x5A pair inside noise must not be able to wedge the link, so the
        answer is NEED_MORE plus the number of bytes it is safe to discard.
        """
        raw = bytearray(frame_bytes(payload=b"\xAA\xBB"))
        raw[2] = 0x03
        result, frame, consumed = p.parse_frame(bytes(raw))
        assert result is p.ParseResult.NEED_MORE
        assert frame is None
        assert consumed > 0

    def test_a_bad_crc_is_distinct_from_a_bad_version(self):
        raw = bytearray(frame_bytes(payload=b"\xAA\xBB"))
        raw[-1] ^= 0xFF
        assert p.parse_frame(bytes(raw))[0] is p.ParseResult.ERR_CRC

    def test_an_impossible_declared_length_cannot_read_past_the_buffer(self):
        raw = bytearray(frame_bytes(payload=b"\x00" * 4))
        raw[6:8] = (0x7FFF).to_bytes(2, "little")
        result, frame, consumed = p.parse_frame(bytes(raw))
        assert result is p.ParseResult.NEED_MORE
        assert frame is None
        assert consumed > 0


class TestStreamParser:
    def setup_method(self):
        self.parser = p.IncrementalParser()

    def feed_all(self, chunks):
        out = []
        for chunk in chunks:
            out.extend(self.parser.feed(chunk))
        return out

    def test_a_frame_split_across_every_boundary_is_still_reassembled(self):
        raw = frame_bytes(p.PacketType.STATUS, 9, 1234, bytes(43))
        for cut in range(1, len(raw)):
            parser = p.IncrementalParser()
            frames = parser.feed(raw[:cut]) + parser.feed(raw[cut:])
            assert len(frames) == 1, "lost the frame when split at %d" % cut
            assert frames[0].payload == bytes(43)

    def test_several_frames_in_one_read_all_come_out_in_order(self):
        raw = b"".join(
            frame_bytes(p.PacketType.ECG_BATCH, i, i * 100, bytes([i]) * 55)
            for i in range(6)
        )
        frames = self.parser.feed(raw)
        assert [f.sequence for f in frames] == list(range(6))

    def test_byte_at_a_time_feeding_recovers_the_same_frames(self):
        raw = frame_bytes(p.PacketType.TEMP_STATUS, 3, 3, bytes(8))
        frames = self.feed_all([raw[i:i + 1] for i in range(len(raw))])
        assert len(frames) == 1
        assert self.parser.crc_errors == 0

    def test_junk_before_the_magic_is_discarded_and_counted(self):
        junk = bytes([0x00, 0xA5, 0x11, 0x5A, 0xA5, 0xA5])
        frames = self.feed_all([junk, frame_bytes(payload=b"\x01\x02")])
        assert len(frames) == 1
        assert self.parser.bytes_discarded >= 4

    def test_a_corrupt_frame_costs_one_frame_not_the_stream(self):
        good1 = bytearray(frame_bytes(p.PacketType.PONG, 1, 1, b"aaaa"))
        good2 = frame_bytes(p.PacketType.PONG, 2, 2, b"bbbb")
        bad = bytearray(good1)
        bad[-2] ^= 0x55
        frames = self.feed_all([bytes(bad), good2])
        assert self.parser.crc_errors == 1
        assert [f.sequence for f in frames] == [2], "lost the good frame behind the bad one"

    def test_a_lone_trailing_magic_is_held_not_consumed(self):
        """A 0xA5 at the end of a read might be the first half of the next magic."""
        raw = frame_bytes(payload=b"\x01")
        frames = self.feed_all([raw, bytes([0xA5]), frame_bytes(p.PacketType.PING, 5, 5, b"")])
        assert [f.type for f in frames] == [p.PacketType.PONG, p.PacketType.PING]

    def test_resync_terminates_on_a_payload_of_only_magic_bytes(self):
        """Pathological input must not spin: every feed has to return quickly."""
        frames = self.feed_all([bytes([0xA5, 0x5A]) * 40, frame_bytes(payload=b"z")])
        assert frames[-1].payload == b"z"

    def test_parser_counters_match_what_was_seen(self):
        self.feed_all([frame_bytes(p.PacketType.ECG_BATCH, i, i, bytes(55)) for i in range(3)])
        assert self.parser.frames_parsed == 3
        assert self.parser.type_counts[int(p.PacketType.ECG_BATCH)] == 3

    def test_reset_drops_partial_state(self):
        raw = frame_bytes(p.PacketType.STATUS, 1, 1, bytes(43))  # 57 bytes
        assert len(raw) > 30
        self.parser.feed(raw[:30])
        assert self.parser.pending > 0
        self.parser.reset()
        assert self.parser.pending == 0


class TestFlags:
    ALL_BITS = [
        ("lead", p.SFLAG_LEAD_SHIFT, p.SFLAG_LEAD_MASK),
        ("temp", p.SFLAG_TEMP_SHIFT, p.SFLAG_TEMP_MASK),
        ("notch", p.SFLAG_NOTCH_SHIFT, p.SFLAG_NOTCH_MASK),
    ]
    SINGLE = [
        ("hr_valid", p.SFLAG_HR_VALID),
        ("recording", p.SFLAG_RECORDING),
        ("oled_present", p.SFLAG_OLED),
        ("adc_running", p.SFLAG_ADC_RUNNING),
        ("rtc_valid", p.SFLAG_RTC_VALID),
        ("dma_dropped", p.SFLAG_DMA_DROPPED),
        ("temp_uncalibrated", p.SFLAG_TEMP_UNCALIB),
    ]

    def test_make_and_split_are_inverse(self):
        word = p.make_flags(lead=p.LeadState.DISCONNECTED, temp=p.TempState.LOW,
                            hr_valid=True, recording=True, oled_present=True,
                            adc_running=True, rtc_valid=True, dma_dropped=True,
                            notch=p.NotchMode.HZ60, temp_uncalibrated=False)
        flags = p.split_flags(word)
        assert int(flags.lead) == int(p.LeadState.DISCONNECTED)
        assert int(flags.temp) == int(p.TempState.LOW)
        assert int(flags.notch) == int(p.NotchMode.HZ60)
        assert flags.hr_valid and flags.recording and flags.oled_present
        assert flags.adc_running and flags.rtc_valid and flags.dma_dropped
        assert not flags.temp_uncalibrated
        assert flags.raw == word

    @pytest.mark.parametrize("attr,bit", SINGLE)
    def test_each_single_bit_lives_where_protocol_h_says(self, attr, bit):
        """The high byte matters: ECGP_TAIL used to be one short and lose it."""
        assert getattr(p.split_flags(bit), attr) is True
        assert getattr(p.split_flags(bit ^ 0xFFFF), attr) is False

    @pytest.mark.parametrize("attr,enum_cls,shift", [
        ("lead", p.LeadState, p.SFLAG_LEAD_SHIFT),
        ("temp", p.TempState, p.SFLAG_TEMP_SHIFT),
        ("notch", p.NotchMode, p.SFLAG_NOTCH_SHIFT),
    ])
    def test_every_defined_enum_value_survives_the_round_trip(self, attr, enum_cls, shift):
        for member in enum_cls:
            flags = p.split_flags(int(member) << shift)
            assert int(getattr(flags, attr)) == int(member)

    BLANK = dict(lead=0, temp=0, notch=0, hr_valid=False, recording=False,
                 oled_present=False, adc_running=False, rtc_valid=False,
                 dma_dropped=False, temp_uncalibrated=False)

    @pytest.mark.parametrize("attr,shift,mask", ALL_BITS)
    def test_a_field_never_shifts_into_its_neighbour(self, attr, shift, mask):
        """Setting one field must leave every bit outside its own mask clear."""
        for value in range(16):
            word = p.make_flags(**{**self.BLANK, attr: value})
            assert word & ~mask == 0, "%s=%d leaked outside its mask" % (attr, value)
            assert word == ((value << shift) & mask)

    def test_an_out_of_range_enum_value_falls_back_without_losing_the_word(self):
        """lead = 7 is not a member; the field reads UNKNOWN but ``raw`` is honest."""
        flags = p.split_flags(0x07)
        assert flags.lead is p.LeadState.UNKNOWN
        assert flags.raw == 0x07

    def test_the_defined_flags_cover_every_bit_but_one_and_say_which(self):
        """``status_flags_t`` is 16 bits; bit 15 is defined by nobody.

        Pinned rather than left implicit: an undefined bit reads as clear on both
        sides forever, so the day someone allocates it this test has to be told.
        """
        word = 0
        for _, bit in self.SINGLE:
            word |= bit
        for _, shift, mask in self.ALL_BITS:
            word |= mask
        assert word == 0x7FFF
        assert p.split_flags(0x8000).raw == 0x8000
