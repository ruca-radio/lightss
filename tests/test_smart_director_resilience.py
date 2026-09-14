from __future__ import annotations

from datetime import datetime

import fleet
import pytest
import smart_director as sd


class Clock:
    def __init__(self): self.now = 100.0
    def __call__(self): return self.now
    def advance(self, seconds): self.now += seconds


class FakeFleet:
    def __init__(self):
        self.installation, self.controllers = fleet.load_topology({})
        self.posts = []
    def post_state(self, payload, target='all'):
        self.posts.append(payload)
        return {'left': {'ok': True}, 'right': {'ok': True}}


class Listener:
    def __init__(self):
        self.snapshot = {'active': True, 'level': .7, 'fft': [.5] * 16,
                         'beat': True, 'receive_sequence': 1}
    def get_snapshot(self): return dict(self.snapshot)
    def start(self): pass
    def stop(self): pass


class Renderer:
    instances = []
    def __init__(self, *args, **kwargs):
        self.running = False
        Renderer.instances.append(self)
    def start(self): self.running = True
    def stop(self): self.running = False
    def join(self, timeout=None): pass
    def is_alive(self): return self.running
    def update_look(self, **kwargs): pass
    def status(self): return {'running': self.running}


def make_director(clock=None):
    clock = clock or Clock()
    Renderer.instances.clear()
    return sd.SmartDirector(FakeFleet(), {'enabled': True, 'mode': 'music'},
        listener=Listener(), renderer_factory=Renderer, clock=clock,
        wall_clock=lambda: datetime(2026, 9, 12, 23, 0)), clock


def test_observation_key_includes_activity_hint_and_cache_expires():
    base = {'connected': True, 'awake': True, 'foreground_app': 'app',
            'media_session': {'package': 'app', 'active': True, 'state': 3, 'description': 'Track'}}
    assert sd.observation_key({**base, 'activity_hint': 'music'}) != sd.observation_key({**base, 'activity_hint': 'unknown'})
    director, clock = make_director(clock=Clock())
    key = sd.observation_key(base)
    director._cache[key] = (clock() - 301, {'kind': 'music'})
    assert director._cached_decision(key, clock()) is None
    assert key not in director._cache


def test_auto_disconnected_rhythmic_audio_stays_steady_tv():
    director, clock = make_director()
    director.configure({'mode': 'auto', 'debounce_s': 0})
    observation = {'connected': False, 'awake': False}
    for sequence in range(1, 8):
        audio = {'active': True, 'level': .8, 'beat': True, 'receive_sequence': sequence}
        director.step(observation, {'kind': 'unknown'}, audio)
        clock.advance(.5)
    assert director.status()['mode'] == 'tv'
    assert director.renderer is None


def test_renderer_restart_is_backed_off_bounded_and_fault_persists_until_configure():
    director, clock = make_director()
    audio = director.listener.get_snapshot()
    director.step({}, {}, audio)
    assert len(Renderer.instances) == 1
    Renderer.instances[-1].running = False

    clock.advance(1)
    director.step({}, {}, audio)
    assert len(Renderer.instances) == 1
    clock.advance(2)
    director.step({}, {}, audio)
    assert len(Renderer.instances) == 2
    Renderer.instances[-1].running = False
    clock.advance(3)
    director.step({}, {}, audio)
    assert len(Renderer.instances) == 2
    assert director.status()['mode'] == 'tv'
    assert 'renderer' in director.status()['last_error'].lower()

    director.step({}, {}, audio)
    assert director.status()['last_error']
    director.configure({'mode': 'music'})
    director.step({}, {}, audio)
    assert len(Renderer.instances) == 3


def test_stop_retains_registry_and_process_lock_when_writer_is_stuck(monkeypatch):
    class Stuck:
        fleet = object()
        renderer = Renderer()
        def configure(self, updates): pass
        def step(self, *args): pass
        def shutdown(self): pass
        def is_alive(self): return True
    stuck = Stuck()
    released = []
    monkeypatch.setattr(sd, '_director', stuck)
    monkeypatch.setattr(sd, '_release_process_lock', lambda owner=None: released.append(owner))
    with pytest.raises(RuntimeError, match='still running'):
        sd.stop(steady=False)
    assert sd._director is stuck
    assert released == []


def test_unstarted_observer_is_not_joined_during_cleanup():
    director, _ = make_director()
    class NeverStarted:
        def join(self, timeout=None): raise AssertionError('must not join')
        def is_alive(self): return False
    director._observer = NeverStarted()
    director._observer_started = False
    director._stop_observer()


def test_three_normal_tv_music_transitions_do_not_exhaust_restart_budget():
    director, _ = make_director()
    director.configure({'mode': 'auto', 'debounce_s': 0})
    audio = director.listener.get_snapshot()
    playing = {'connected': True, 'awake': True, 'foreground_app': 'music.app',
               'activity_hint': 'music', 'media_session': {'package': 'music.app', 'active': True, 'state': 3}}
    video = {'connected': True, 'awake': True, 'foreground_app': 'video.app',
             'activity_hint': 'tv', 'media_session': {'package': 'video.app', 'active': True, 'state': 3}}
    for _ in range(3):
        director.step(playing, {'kind': 'music', 'confidence': 1}, audio)
        assert director.status()['mode'] == 'music'
        director.step(video, {'kind': 'tv', 'confidence': 1}, audio)
        assert director.status()['mode'] == 'tv'
    assert len(Renderer.instances) == 3


def test_renderer_start_failure_falls_back_steady_without_joining_unstarted():
    class StartFails(Renderer):
        def start(self): raise RuntimeError('thread start failed')
        def join(self, timeout=None): raise AssertionError('must not join unstarted renderer')
    director, _ = make_director()
    director.renderer_factory = StartFails
    director.step({}, {}, director.listener.get_snapshot())
    assert director.status()['mode'] == 'tv'
    assert 'thread start failed' in director.status()['last_error']


def test_startup_failure_retains_registry_when_failed_director_is_still_alive(monkeypatch):
    class StuckStartup:
        renderer = None
        def start(self): pass
        def wait_until_started(self): raise RuntimeError('startup failed')
        def shutdown(self): raise RuntimeError('still running')
        def is_alive(self): return True
    stuck = StuckStartup()
    monkeypatch.setattr(sd, 'SmartDirector', lambda *a, **kw: stuck)
    monkeypatch.setattr(sd, '_director', None)
    monkeypatch.setattr(sd, '_acquire_process_lock', lambda: None)
    released = []
    monkeypatch.setattr(sd, '_release_process_lock', lambda owner=None: released.append(owner))
    import mcp_light, music_director, realtime, shows
    monkeypatch.setattr(mcp_light, '_stop_timers', lambda: None)
    monkeypatch.setattr(music_director, 'stop_director', lambda: None)
    monkeypatch.setattr(realtime, 'realtime_stop', lambda: None)
    monkeypatch.setattr(shows, 'stop_show', lambda: None)
    with pytest.raises(RuntimeError, match='ownership retained'):
        sd.start(FakeFleet(), {'enabled': True})
    assert sd._director is stuck
    assert released == []
