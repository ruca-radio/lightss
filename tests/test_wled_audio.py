from __future__ import annotations

import socket
import struct
import time

import pytest

import wled_audio


def make_packet(
    sample_raw=0.0,
    sample_avg=0.0,
    sample_peak=0,
    frame=0,
    fft=None,
    zero_crossings=0,
    magnitude=0.0,
    major_peak=0.0,
    pressure=(0, 0),
    header=b"00002\x00",
):
    fft = fft if fft is not None else [0] * 16
    assert len(fft) == 16
    return struct.pack(
        "<6sBBffBB16sHff",
        header,
        pressure[0],
        pressure[1],
        sample_raw,
        sample_avg,
        sample_peak,
        frame,
        bytes(fft),
        zero_crossings,
        magnitude,
        major_peak,
    )


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, dt):
        self.now += dt


class FakeSocket:
    def __init__(self, packets=None):
        self.packets = list(packets or [])
        self.closed = False
        self.timeout = None

    def recvfrom(self, size):
        if self.packets:
            return self.packets.pop(0), ("10.27.27.110", 11988)
        raise socket.timeout()

    def settimeout(self, value):
        self.timeout = value

    def close(self):
        self.closed = True


class ConfiguringSocket(FakeSocket):
    def __init__(self):
        super().__init__()
        self.options = []
        self.bound = None

    def setsockopt(self, level, option, value):
        self.options.append((level, option, value))

    def bind(self, address):
        self.bound = address


def test_packet_layout_is_44_bytes():
    assert len(make_packet()) == wled_audio.PACKET_V2_SIZE == 44


def test_parse_valid_packet():
    fft = list(range(16))
    data = make_packet(
        sample_raw=123.5,
        sample_avg=64.0,
        sample_peak=1,
        frame=7,
        fft=fft,
        zero_crossings=42,
        magnitude=2048.0,
        major_peak=440.0,
        pressure=(55, 128),
    )
    parsed = wled_audio.parse_packet(data)
    assert parsed is not None
    assert parsed["sample_raw"] == pytest.approx(123.5)
    assert parsed["sample_avg"] == pytest.approx(64.0)
    assert parsed["sample_peak"] == 1
    assert parsed["frame_counter"] == 7
    assert parsed["fft"] == fft
    assert parsed["zero_crossings"] == 42
    assert parsed["fft_magnitude"] == pytest.approx(2048.0)
    assert parsed["fft_major_peak"] == pytest.approx(440.0)
    assert parsed["pressure"] == pytest.approx(55.5)


def test_parse_rejects_bad_header():
    assert wled_audio.parse_packet(make_packet(header=b"XXXXXX")) is None


def test_parse_rejects_v1_header():
    assert wled_audio.parse_packet(make_packet(header=b"00001\x00")) is None


def test_parse_rejects_wrong_length():
    data = make_packet()
    assert wled_audio.parse_packet(data[:-1]) is None
    assert wled_audio.parse_packet(data + b"\x00") is None
    assert wled_audio.parse_packet(b"") is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sample_raw", float("nan")),
        ("sample_avg", float("inf")),
        ("magnitude", float("-inf")),
        ("major_peak", float("nan")),
    ],
)
def test_parse_rejects_non_finite_float_fields(field, value):
    assert wled_audio.parse_packet(make_packet(**{field: value})) is None


def test_snapshot_empty_before_any_packet():
    listener = wled_audio.WledAudioListener(clock=FakeClock())
    snapshot = listener.get_snapshot()
    assert snapshot["active"] is False
    assert snapshot["age_s"] == 0.0
    assert snapshot["level"] == 0.0
    assert snapshot["peak"] is False
    assert snapshot["beat"] is False
    assert snapshot["sample_avg"] == 0.0
    assert snapshot["sample_peak"] == 0
    assert snapshot["fft"] == [0.0] * 16


def test_snapshot_after_fed_packet():
    clock = FakeClock()
    listener = wled_audio.WledAudioListener(clock=clock)
    fft = [255, 128] + [0] * 14
    parsed = listener.feed_packet(make_packet(sample_avg=127.5, sample_peak=1, fft=fft))
    assert parsed is not None
    snapshot = listener.get_snapshot()
    assert snapshot["active"] is True
    assert snapshot["age_s"] == pytest.approx(0.0)
    assert snapshot["level"] == pytest.approx(0.5)
    assert snapshot["peak"] is True
    assert snapshot["sample_avg"] == pytest.approx(127.5)
    assert snapshot["sample_peak"] == 1
    assert snapshot["fft"][0] == pytest.approx(1.0)
    assert snapshot["fft"][1] == pytest.approx(128 / 255)
    assert snapshot["fft"][2:] == [0.0] * 14


def test_snapshot_exposes_source_ip_and_local_receive_sequence():
    listener = wled_audio.WledAudioListener(clock=FakeClock())
    listener.feed_packet(make_packet(frame=0), ("10.27.27.110", 11988))
    first = listener.get_snapshot()
    listener.feed_packet(make_packet(frame=0), ("10.27.27.110", 11988))
    second = listener.get_snapshot()

    assert first["source_ip"] == "10.27.27.110"
    assert second["source_ip"] == "10.27.27.110"
    assert first["frame_counter"] == second["frame_counter"] == 0
    assert second["receive_sequence"] == first["receive_sequence"] + 1


def test_allowed_source_filters_other_senders_without_advancing_sequence():
    listener = wled_audio.WledAudioListener(
        clock=FakeClock(), allowed_source="10.27.27.110"
    )

    assert listener.feed_packet(make_packet(), ("10.27.27.111", 11988)) is None
    assert listener.get_snapshot()["active"] is False
    listener.feed_packet(make_packet(), ("10.27.27.110", 11988))
    assert listener.get_snapshot()["receive_sequence"] == 1


def test_feed_packet_ignores_garbage():
    listener = wled_audio.WledAudioListener(clock=FakeClock())
    assert listener.feed_packet(b"not a wled packet") is None
    assert listener.get_snapshot()["active"] is False


def test_snapshot_goes_stale_after_two_seconds():
    clock = FakeClock()
    listener = wled_audio.WledAudioListener(clock=clock)
    listener.feed_packet(make_packet(sample_avg=200.0, sample_peak=1, fft=[200] * 16))
    assert listener.get_snapshot()["active"] is True
    clock.advance(2.5)
    snapshot = listener.get_snapshot()
    assert snapshot["active"] is False
    assert snapshot["age_s"] == pytest.approx(2.5)
    assert snapshot["level"] == 0.0
    assert snapshot["peak"] is False
    assert snapshot["beat"] is False
    assert snapshot["sample_avg"] == 0.0
    assert snapshot["sample_peak"] == 0
    assert snapshot["fft"] == [0.0] * 16


def test_level_clamped_to_one():
    listener = wled_audio.WledAudioListener(clock=FakeClock())
    listener.feed_packet(make_packet(sample_avg=4096.0))
    assert listener.get_snapshot()["level"] == 1.0


def test_beat_detected_after_quiet_history():
    clock = FakeClock()
    listener = wled_audio.WledAudioListener(clock=clock)
    for _ in range(12):
        listener.feed_packet(make_packet(sample_avg=25.0))
        clock.advance(0.02)
    assert listener.get_snapshot()["beat"] is False
    listener.feed_packet(make_packet(sample_avg=200.0))
    assert listener.get_snapshot()["beat"] is True


def test_beat_refractory_suppresses_packet_rate_retriggering():
    clock = FakeClock()
    listener = wled_audio.WledAudioListener(clock=clock)
    for _ in range(12):
        listener.feed_packet(make_packet(sample_avg=25.0))
    listener.feed_packet(make_packet(sample_avg=200.0))
    assert listener.get_snapshot()["beat"] is True

    listener.feed_packet(make_packet(sample_avg=200.0))
    assert listener.get_snapshot()["beat"] is False


def test_two_beats_with_constant_reserved_wire_byte_have_distinct_sequences():
    clock = FakeClock()
    listener = wled_audio.WledAudioListener(clock=clock)
    for _ in range(12):
        listener.feed_packet(make_packet(sample_avg=25.0, frame=0))
    listener.feed_packet(make_packet(sample_avg=200.0, frame=0))
    first = listener.get_snapshot()

    clock.advance(0.2)
    listener.feed_packet(make_packet(sample_avg=25.0, frame=0))
    listener.feed_packet(make_packet(sample_avg=200.0, frame=0))
    second = listener.get_snapshot()

    assert first["beat"] is True
    assert second["beat"] is True
    assert first["frame_counter"] == second["frame_counter"] == 0
    assert second["receive_sequence"] > first["receive_sequence"]


def test_no_beat_on_steady_level():
    clock = FakeClock()
    listener = wled_audio.WledAudioListener(clock=clock)
    for _ in range(12):
        listener.feed_packet(make_packet(sample_avg=100.0))
        clock.advance(0.02)
    assert listener.get_snapshot()["beat"] is False


def test_beat_needs_minimum_history():
    listener = wled_audio.WledAudioListener(clock=FakeClock())
    listener.feed_packet(make_packet(sample_avg=1.0))
    listener.feed_packet(make_packet(sample_avg=255.0))
    assert listener.get_snapshot()["beat"] is False


def test_listener_thread_consumes_fake_socket():
    sock = FakeSocket(packets=[make_packet(sample_avg=180.0, fft=[255] * 16)])
    listener = wled_audio.WledAudioListener(sock=sock)
    listener.start()
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            snapshot = listener.get_snapshot()
            if snapshot["active"]:
                break
            time.sleep(0.01)
        snapshot = listener.get_snapshot()
        assert snapshot["active"] is True
        assert snapshot["level"] == pytest.approx(180.0 / 255)
    finally:
        listener.stop()
    assert sock.closed is True


def test_created_socket_joins_multicast_group_on_selected_interface(monkeypatch):
    sock = ConfiguringSocket()
    monkeypatch.setattr(wled_audio.socket, "socket", lambda *args: sock)
    listener = wled_audio.WledAudioListener(interface_ip="10.27.27.5")

    listener.start()
    listener.stop()

    expected_membership = socket.inet_aton(wled_audio.AUDIO_SYNC_GROUP) + socket.inet_aton(
        "10.27.27.5"
    )
    assert sock.bound == ("", wled_audio.AUDIO_SYNC_PORT)
    assert (
        socket.IPPROTO_IP,
        socket.IP_ADD_MEMBERSHIP,
        expected_membership,
    ) in sock.options


def test_injected_socket_does_not_require_configuration_methods():
    sock = FakeSocket()
    listener = wled_audio.WledAudioListener(
        sock=sock, interface_ip="10.27.27.5", allowed_source="10.27.27.110"
    )
    listener.start()
    listener.stop()
    assert sock.closed is True


def test_start_and_stop_reset_snapshot_and_history():
    sock = FakeSocket()
    listener = wled_audio.WledAudioListener(sock=sock, clock=FakeClock())
    for _ in range(12):
        listener.feed_packet(make_packet(sample_avg=25.0))
    assert listener.get_snapshot()["active"] is True

    listener.start()
    assert listener.get_snapshot()["active"] is False
    listener.stop()
    snapshot = listener.get_snapshot()
    assert snapshot["active"] is False
    assert snapshot["receive_sequence"] is None


def test_stop_without_start_is_safe():
    listener = wled_audio.WledAudioListener(sock=FakeSocket())
    listener.stop()


def test_start_is_idempotent():
    sock = FakeSocket()
    listener = wled_audio.WledAudioListener(sock=sock)
    listener.start()
    listener.start()
    listener.stop()
    assert sock.closed is True


def test_snapshot_exposes_frame_counter_for_edge_triggering():
    listener = wled_audio.WledAudioListener(clock=FakeClock())
    assert listener.get_snapshot()["frame_counter"] is None
    listener.feed_packet(make_packet(sample_avg=120.0, frame=7))
    assert listener.get_snapshot()["frame_counter"] == 7


class FlakySocket(FakeSocket):
    """Socket whose recvfrom raises OSError `fail_times` times, then works."""

    def __init__(self, packets=None, fail_times=0):
        super().__init__(packets)
        self.fail_times = fail_times

    def recvfrom(self, size):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise OSError("network is down")
        return super().recvfrom(size)


def test_listener_thread_survives_transient_oserrors():
    sock = FlakySocket(packets=[make_packet(sample_avg=180.0, fft=[255] * 16)], fail_times=2)
    listener = wled_audio.WledAudioListener(sock=sock)
    listener.start()
    try:
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            if listener.get_snapshot()["active"]:
                break
            time.sleep(0.05)
        assert listener.get_snapshot()["active"] is True
    finally:
        listener.stop()
