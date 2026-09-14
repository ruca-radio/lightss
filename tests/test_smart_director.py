from datetime import datetime
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import fleet
import lightctl
import smart_director as sd

class Clock:
    def __init__(self): self.now=100.0
    def __call__(self): return self.now
    def advance(self,s): self.now+=s

class FakeFleet:
    def __init__(self):
        self.installation,self.controllers=fleet.load_topology({});self.posts=[]
    def post_state(self,payload,target='all'):
        self.posts.append((payload,target));return {'left':{'ok':True},'right':{'ok':True}}

class Listener:
    def __init__(self):self.snapshot={'active':True,'level':.4,'fft':[.2]*16,'beat':False,'receive_sequence':1};self.started=False
    def start(self):self.started=True
    def stop(self):self.started=False
    def get_snapshot(self):return dict(self.snapshot)

class Renderer:
    def __init__(self,fleet,audio_source,**kwargs):self.running=False;self.looks=[];self.kwargs=kwargs
    def start(self):self.running=True
    def stop(self):self.running=False
    def join(self,timeout=None):pass
    def is_alive(self):return self.running
    def update_look(self,**kwargs):self.looks.append(kwargs)
    def status(self):return {'running':self.running,'sent_frames':1}

@pytest.fixture
def rig():
    clock=Clock(); f=FakeFleet();listener=Listener()
    d=sd.SmartDirector(f, {'enabled':True},listener=listener,renderer_factory=Renderer,
                       clock=clock,wall_clock=lambda:datetime(2026,9,12,23,0))
    yield d,f,listener,clock
    d.shutdown()

def tv(app='com.amazon.firetv.youtube',hint='unknown',description='Track, Artist, null',state=3):
    return {'connected':True,'awake':True,'foreground_app':app,'activity_hint':hint,
            'media_session':{'package':app,'active':True,'state':state,'description':description}}

def music():return {'kind':'music','confidence':.95,'colors':[[180,20,40],[20,80,180]],'composition_mode':'center_vs_outer','reason':'Music identified'}

@pytest.mark.parametrize('hour,value',[(0,.05),(6,.05),(7,.3),(17,.3),(18,.15),(21,.15),(22,.05),(23,.05)])
def test_tv_brightness_tracks_local_hours(hour,value):
    assert sd.tv_brightness(sd.validate_config({}),datetime(2026,9,12,hour,0))==value

@pytest.mark.parametrize('config',[{'mode':'party'},{'enabled':'true'},{'night_brightness':float('nan')},{'day_brightness':2},{'day_start':20,'evening_start':18},{'timezone':'Invalid/Zone'},{'tv_theme':'strobe'}])
def test_rejects_invalid_smart_config(config):
    with pytest.raises(ValueError):sd.validate_config(config)

def test_unknown_tv_stays_static_at_night(rig):
    d,f,l,c=rig;d.step(tv(),{'kind':'unknown'},l.get_snapshot())
    assert d.status()['mode']=='tv'
    assert f.posts[-1][0]['bri']==13
    assert all(s['fx']==0 and s['frz'] is False for p,_ in f.posts for s in p.get('seg',[]))
    assert d.status()['renderer']['running'] is False
    n=len(f.posts);c.advance(1);d.step(tv(),{},l.get_snapshot());assert len(f.posts)==n

def test_confident_music_starts_after_debounce_not_each_track_beat(rig):
    d,f,l,c=rig;d.step(tv(),music(),l.get_snapshot());assert d.status()['mode']=='tv'
    c.advance(7);d.step(tv(),music(),l.get_snapshot());assert d.status()['mode']=='music'
    renderer=d.renderer;n=len(f.posts)
    c.advance(10);d.step(tv(description='Another song'),music(),l.get_snapshot())
    assert d.renderer is renderer and len(f.posts)==n

def test_video_app_overrides_soundtrack_music_decision(rig):
    d,f,l,c=rig;d.configure({'mode':'music'});d.step(tv(),music(),l.get_snapshot());r=d.renderer
    d.configure({'mode':'auto'});d.step(tv('com.netflix.ninja','tv'),music(),l.get_snapshot())
    assert d.status()['mode']=='tv' and not r.is_alive()
    assert f.posts[-1][0]['bri']==13

def test_brief_metadata_gap_does_not_flip_music(rig):
    d,f,l,c=rig;d.configure({'mode':'music'});d.step(tv(),music(),l.get_snapshot());d.configure({'mode':'auto'})
    d.step(tv(description='Advertisement'),{'kind':'unknown'},l.get_snapshot());assert d.status()['mode']=='music'
    c.advance(2);d.step(tv(),music(),l.get_snapshot());assert d.status()['mode']=='music'

def test_manual_override_stops_renderer_and_blocks_future_automatic_writes(rig):
    d,f,l,c=rig;d.configure({'mode':'music'});d.step(tv(),music(),l.get_snapshot());r=d.renderer
    d.manual_override();n=len(f.posts);c.advance(60);d.step(tv(),music(),l.get_snapshot())
    assert d.status()['mode']=='manual' and not r.is_alive() and len(f.posts)==n
    d.configure({'mode':'tv'});d.step(tv(),{},l.get_snapshot());assert d.status()['mode']=='tv'

def test_clock_change_updates_only_steady_tv_brightness(rig):
    d,f,l,c=rig;d.configure({'mode':'tv'});d.wall_clock=lambda:datetime(2026,9,12,17,59)
    d.step(tv(),{},l.get_snapshot());assert f.posts[-1][0]['bri']==76 or f.posts[-1][0]['bri']==77
    d.wall_clock=lambda:datetime(2026,9,12,22,0);d.step(tv(),{},l.get_snapshot())
    assert f.posts[-1][0]['bri']==13 and f.posts[-1][0]['transition']>=100

def test_audio_lost_in_auto_eventually_returns_to_tv(rig):
    d,f,l,c=rig;d.configure({'mode':'music'});d.step(tv(),music(),l.get_snapshot());d.configure({'mode':'auto'})
    l.snapshot={'active':False,'level':0,'fft':[0]*16};d.step(tv(),music(),l.get_snapshot())
    c.advance(25);d.step(tv(),music(),l.get_snapshot());assert d.status()['mode']=='tv'

def test_failed_controller_post_is_reported_not_claimed_applied(rig):
    d,f,l,c=rig;f.post_state=lambda *a,**kw:{'left':{'ok':False,'error':'offline'}}
    d.step(tv(),{},l.get_snapshot());assert 'offline' in d.status()['last_error']

def test_shutdown_stops_renderer_without_new_lighting_writes(rig):
    d,f,l,c=rig;d.configure({'mode':'music'});d.step(tv(),music(),l.get_snapshot());r=d.renderer;n=len(f.posts)
    d.shutdown();assert not r.is_alive() and len(f.posts)==n


def test_tv_classification_immediately_wins_for_youtube_video(rig):
    d,f,l,c=rig
    d.configure({'mode':'music'});d.step(tv(),music(),l.get_snapshot());renderer=d.renderer
    d.configure({'mode':'auto'})

    d.step(tv(hint='unknown'),{'kind':'tv','confidence':.9,'reason':'Video identified'},l.get_snapshot())

    assert d.status()['mode']=='tv'
    assert not renderer.is_alive()
    assert f.posts[-1][0]['bri']==13


def test_failed_observation_cannot_start_microphone_show(rig):
    d,f,l,c=rig
    failed={'connected':False,'error':'adb unavailable'}
    for sequence in range(1,8):
        l.snapshot.update(active=True,level=.5,beat=True,receive_sequence=sequence)
        d.step(failed,{'kind':'unknown'},l.get_snapshot())
        c.advance(.6)
    c.advance(7)
    l.snapshot['receive_sequence']=8
    d.step(failed,{'kind':'unknown'},l.get_snapshot())

    assert d.status()['mode']=='tv'
    assert d.status()['renderer']['running'] is False


def test_brief_unknown_song_gap_preserves_current_renderer_recipe(rig):
    d,f,l,c=rig
    d.configure({'mode':'music'});d.step(tv(),music(),l.get_snapshot())
    renderer=d.renderer
    recipe=d._last_recipe
    look_count=len(renderer.looks)
    d.configure({'mode':'auto'})

    d.step(tv(description=''),{'kind':'unknown'},l.get_snapshot())

    assert d.status()['mode']=='music'
    assert d.renderer is renderer
    assert d._last_recipe==recipe
    assert len(renderer.looks)==look_count


def test_explicit_observation_does_not_reuse_unrelated_cached_decision(rig):
    d,f,l,c=rig
    d._decision=music()
    d.step(tv(hint='unknown'),audio=l.get_snapshot())
    c.advance(7)
    d.step(tv(hint='unknown'),audio=l.get_snapshot())

    assert d.status()['mode']=='tv'
    assert d.status()['renderer']['running'] is False


def test_music_brightness_cannot_exceed_renderer_cap():
    with pytest.raises(ValueError,match='music_brightness'):
        sd.validate_config({'music_brightness':1.01})
    assert sd.validate_config({'music_brightness':.65})['music_brightness']==.65


def test_start_stops_pending_timers_before_starting_owner(tmp_path,monkeypatch):
    import mcp_light
    import music_director
    import realtime
    import shows

    calls=[]
    monkeypatch.setattr(mcp_light,'_stop_timers',lambda:calls.append('timers'))
    monkeypatch.setattr(music_director,'stop_director',lambda:calls.append('music-director'))
    monkeypatch.setattr(shows,'stop_show',lambda:calls.append('show'))
    monkeypatch.setattr(realtime,'realtime_stop',lambda:calls.append('realtime'))
    monkeypatch.setattr(sd.SmartDirector,'start',lambda self:calls.append('smart'))
    monkeypatch.setattr(sd.SmartDirector,'wait_until_started',lambda self:calls.append('ready'))
    monkeypatch.setattr(sd,'_director',None)
    monkeypatch.setattr(lightctl,'_CONFIG_PATH',str(tmp_path/'config.json'))

    try:
        sd.start(FakeFleet(),{'enabled':True})
        assert calls==['timers','music-director','show','realtime','smart','ready']
    finally:
        sd.stop(steady=False)


def test_manual_override_survives_persistence_failure(rig,monkeypatch):
    d,f,l,c=rig
    d.persist_manual=True
    monkeypatch.setattr(lightctl,'load_config',Mock(side_effect=OSError('read only')))

    d.manual_override()

    assert d.status()['mode']=='manual'
    assert 'read only' in d.status()['last_error']


def test_fleet_post_hands_off_before_resolving_or_writing(monkeypatch):
    events=[]
    installation,controllers=fleet.load_topology({})

    class Client:
        def post_state(self,payload):
            events.append('write')
            return {'ok':True}

    wall=fleet.LightFleet(
        {controller.name:Client() for controller in controllers},
        controllers,
        installation=installation,
    )
    hook=SimpleNamespace(before_external_write=lambda owner:events.append(('handoff',owner)))
    monkeypatch.setitem(sys.modules,'smart_director',hook)

    wall.post_state({'on':True})

    assert events[0]==('handoff',wall)
    assert events[1:]==['write','write']


def test_director_owned_fleet_post_does_not_trigger_manual_override(monkeypatch):
    installation,controllers=fleet.load_topology({})
    clients={controller.name:Mock(post_state=Mock(return_value={})) for controller in controllers}
    wall=fleet.LightFleet(clients,controllers,installation=installation)
    director=sd.SmartDirector(
        wall,{'enabled':True,'mode':'tv'},listener=Listener(),renderer_factory=Renderer,
        wall_clock=lambda:datetime(2026,9,12,23,0),
    )
    monkeypatch.setattr(director,'manual_override',Mock())
    monkeypatch.setattr(sd,'_director',director)

    director.step(tv(),{},director.listener.get_snapshot())

    director.manual_override.assert_not_called()


def test_global_start_refuses_when_another_process_owns_lock(tmp_path,monkeypatch):
    import fcntl

    lock_path=tmp_path/'smart-director.lock'
    owner=lock_path.open('a+')
    fcntl.flock(owner.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    monkeypatch.setattr(lightctl,'_CONFIG_PATH',str(tmp_path/'config.json'))
    monkeypatch.setattr(sd,'_director',None)
    try:
        with pytest.raises(RuntimeError,match='another instance owns automatic lighting'):
            sd.start(FakeFleet(),{'enabled':True})
    finally:
        owner.close()


def test_global_start_reports_listener_startup_failure_and_releases_lock(tmp_path,monkeypatch):
    import mcp_light
    import music_director
    import realtime
    import shows

    class BrokenListener(Listener):
        def start(self):
            raise OSError('multicast unavailable')

    monkeypatch.setattr(lightctl,'_CONFIG_PATH',str(tmp_path/'config.json'))
    monkeypatch.setattr(mcp_light,'_stop_timers',lambda:None)
    monkeypatch.setattr(music_director,'stop_director',lambda:None)
    monkeypatch.setattr(shows,'stop_show',lambda:None)
    monkeypatch.setattr(realtime,'realtime_stop',lambda:None)
    monkeypatch.setattr(sd.SmartDirector,'_make_listener',lambda self:BrokenListener())
    monkeypatch.setattr(sd,'_director',None)

    with pytest.raises(RuntimeError,match='multicast unavailable'):
        sd.start(FakeFleet(),{'enabled':True})

    assert sd.current() is None
    assert sd._process_lock_file is None
