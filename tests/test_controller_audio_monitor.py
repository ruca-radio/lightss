"""Read-only visualization telemetry must not depend on player/lighting ownership."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import light_gui


def test_monitor_reads_audio_without_starting_player_or_lighting():
    client = SimpleNamespace(host='http://10.27.27.110', post_state=Mock())
    listener = Mock()
    listener.get_snapshot.return_value = {'active': True, 'level': .4, 'fft': [.3] * 16}
    monitor = light_gui.ControllerAudioMonitor(client)
    with patch.object(monitor, '_make_listener', return_value=listener):
        assert monitor.get_snapshot()['active'] is True
        assert monitor.get_snapshot()['level'] == .4
    listener.start.assert_called_once()
    client.post_state.assert_not_called()
    monitor.stop()
    listener.stop.assert_called_once()


def test_dry_run_does_not_open_a_controller_audio_socket():
    monitor = light_gui.ControllerAudioMonitor(SimpleNamespace(), dry_run=True)
    with patch.object(monitor, '_make_listener') as create:
        assert monitor.get_snapshot()['active'] is False
    create.assert_not_called()


def test_stopped_monitor_does_not_restart_from_a_late_request():
    monitor = light_gui.ControllerAudioMonitor(SimpleNamespace())
    monitor.stop()
    with patch.object(monitor, '_make_listener') as create:
        assert monitor.get_snapshot()['active'] is False
    create.assert_not_called()


def test_listener_failure_is_reported_and_backed_off_instead_of_rebinding_per_poll():
    monitor = light_gui.ControllerAudioMonitor(SimpleNamespace())
    with patch.object(monitor, '_make_listener', side_effect=OSError('Address in use')) as create:
        first = monitor.get_snapshot()
        second = monitor.get_snapshot()
    assert first['active'] is False and first['error'] == 'Address in use'
    assert second == first
    create.assert_called_once()


def test_ai_summary_uses_measured_controller_audio_without_raw_samples():
    client = SimpleNamespace()
    monitor = light_gui.ControllerAudioMonitor(client)
    listener = Mock()
    listener.get_snapshot.return_value = {'active': True, 'receive_sequence': 1, 'level': .6, 'fft': [.4] * 16, 'beat': True}
    with patch.object(monitor, '_make_listener', return_value=listener):
        summary = monitor.ai_summary()
    assert summary['active'] is True
    assert summary['level_average'] == .6
    assert summary['fft'] == [.4] * 16
    assert summary['samples'] == 1
    assert 'raw_audio' not in summary
    client._controller_audio_monitor = monitor
    with patch.object(light_gui, '_device_snapshot', return_value={}):
        context = light_gui.ai_context_text(client, playback_clock={})
    assert 'Measured controller audio' in context
    monitor.stop()


def test_ai_history_receives_every_controller_packet_without_ui_polling():
    import wled_audio
    from tests.test_wled_audio import make_packet
    monitor = light_gui.ControllerAudioMonitor(SimpleNamespace())
    listener = wled_audio.WledAudioListener(on_packet=monitor._record_audio_packet)
    for _ in range(20):
        listener.feed_packet(make_packet(sample_avg=64, fft=[128] * 16))
    assert len(monitor._history) == 20
    assert monitor._history[-1][1]['fft'] == [128 / 255] * 16
    monitor.stop()


def test_audio_summary_keeps_latest_fft_separate_from_long_window(monkeypatch):
    import wled_audio
    from tests.test_wled_audio import make_packet
    now = [100.0]
    monkeypatch.setattr(light_gui.time, "monotonic", lambda: now[0])
    monitor = light_gui.ControllerAudioMonitor(SimpleNamespace())
    listener = wled_audio.WledAudioListener(clock=lambda: now[0], on_packet=monitor._record_audio_packet)
    monitor._listener = listener
    listener.feed_packet(make_packet(sample_avg=20, fft=[20]*16), ("10.27.27.110", 11988))
    now[0] += 20
    listener.feed_packet(make_packet(sample_avg=200, fft=[200]*16, major_peak=880, magnitude=99),
                         ("10.27.27.110", 11988))
    summary = monitor.ai_summary()
    assert summary["fft_latest"] == [200/255]*16
    assert summary["fft"][0] < summary["fft_latest"][0]
    assert summary["windows"]["2s"]["samples"] == 1
    assert summary["dominant_frequency_hz"] == 880
    assert summary["source_ip"] == "10.27.27.110"
    assert summary["age_s"] == 0
    assert summary["beat_source"] == "local_energy_onset_estimate"
    now[0] += 3
    stale = monitor.ai_summary()
    assert stale["status"] == "stale"
    assert stale["fft_latest"] is None
    assert stale["bpm"] is None
    monitor.stop()


def test_tempo_is_an_estimate_with_confidence_and_expires_without_onsets():
    import wled_audio
    history = [(at, {'level':.5, 'fft':[.3]*16, 'beat':True}) for at in (1,1.5,2,2.5,3)]
    active = {'active':True,'fft':[.3]*16,'level':.5}
    summary = wled_audio.summarize_audio(active,history,3)
    assert summary['bpm'] == 120
    assert summary['bpm_confidence'] > .25
    assert summary['bpm_source'] == 'local_onset_intervals'
    assert wled_audio.summarize_audio(active,history,6)['bpm'] is None


def test_failed_listener_start_closes_the_partial_socket():
    monitor = light_gui.ControllerAudioMonitor(SimpleNamespace())
    listener = Mock()
    listener.start.side_effect = OSError('join failed')
    with patch.object(monitor,'_make_listener',return_value=listener):
        result = monitor.get_snapshot()
    assert result['active'] is False
    assert result['error'] == 'join failed'
    listener.stop.assert_called_once()
    monitor.stop()


def test_chat_context_keeps_both_audio_sections_consistent():
    import json
    client = SimpleNamespace()
    monitor = light_gui.ControllerAudioMonitor(client)
    client._controller_audio_monitor = monitor
    listener = Mock()
    listener.get_snapshot.side_effect = [
        {'active':True,'level':.4,'fft':[.4]*16,'receive_sequence':1},
        {'active':True,'level':.8,'fft':[.8]*16,'receive_sequence':2}]
    monitor._listener = listener
    with patch.object(light_gui,'_device_snapshot',return_value={}):
        context = light_gui.ai_context_text(client,playback_clock={})
    paragraphs = context.split("\n\n")
    measured = json.loads(next(p for p in paragraphs if p.startswith('Measured controller audio')).split(': ',1)[1])
    shared = json.loads(next(p for p in paragraphs if p.startswith('Live lighting observation')).split(': ',1)[1])
    assert measured['level'] == shared['audio']['level']
    assert measured['fft_latest'] == shared['audio']['fft_latest']
    monitor.stop()
