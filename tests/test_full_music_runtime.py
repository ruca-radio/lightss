"""Live performance regressions: palette fidelity, EQ impulses and additive tuning."""
import http.client
import json
from unittest.mock import patch

import pytest
import realtime
import smart_director as sd
from tests.test_audio_reactive_renderer import FakeFleet, FakeTransport, snapshot
from tests.test_smart_director import rig, tv, music
from tests.test_smart_director_api import api_server


def runner():
    return realtime.AudioReactiveRunner(FakeFleet(), lambda: {}, colors=[[210,0,0],[0,210,210]], transport=FakeTransport())


def advance(r, fft=None, count=60):
    for i in range(count):
        r._consume_snapshot(snapshot(level=.45,fft=fft or [.3]*16,sequence=i),1/30,i/30)
        r._render_frames()


def test_palette_does_not_invent_hues_between_selected_stops():
    colors=[(210,0,0),(0,210,210)]
    palette=realtime._music_palette(colors,1)
    assert set(palette) <= set(colors)
    assert set(realtime._sample_music_palette(palette,i/100) for i in range(101)) <= set(colors)


def test_white_and_black_accents_are_not_recolored():
    palette=realtime._music_palette([(210,0,0),(210,210,210),(0,0,0)],1)
    assert (210,210,210) in palette and (0,0,0) in palette
    assert all(g==b for r,g,b in palette)


def test_more_than_three_distinct_audio_geometries():
    histories=[]
    for motion in ['flow','punch','chase','spectrum','comet','ripple']:
        r=runner();r.update_look(motion=motion);advance(r)
        assert r.status()['requested_motion']==motion
        histories.append(r._render_frames()['left'])
    assert len(set(histories))==6


def test_same_eq_band_retriggers_transient_geometry_without_changing_show():
    r=runner();r.update_look(motion='flow');advance(r)
    before=r._render_frames()
    r.accent(3)
    first=r._render_frames()
    assert first != before
    assert r.status()['accent_count']==1 and r.status()['last_accent_band']==3
    advance(r,count=30)
    decayed=r.status()['accent_levels'][3]
    r.accent(3)
    assert r.status()['accent_levels'][3]>decayed
    assert r.status()['accent_count']==2 and r.status()['requested_motion']=='flow'
    for i in range(180):r._consume_snapshot({'active':False},1/30,i/30)
    assert r.status()['accent_levels']==[0.0]*16


def test_eq_gain_changes_corresponding_band_and_pixel_output():
    low=runner();high=runner()
    low.update_look(motion='spectrum',band_gains=[0.0]*16)
    high.update_look(motion='spectrum',band_gains=[3.0]*16)
    advance(low);advance(high)
    assert high.status()['band_levels'][4] > low.status()['band_levels'][4]
    assert sum(high._render_frames()['left']) > sum(low._render_frames()['left'])


def test_full_software_brightness_and_speed_are_user_selectable():
    cfg=sd.validate_config({'music_brightness':1,'music_speed':4,'music_motion':'comet'})
    r=runner();r.update_look(brightness=cfg['music_brightness'],speed=cfg['music_speed'])
    assert r.brightness==1 and r.speed==4


@pytest.mark.parametrize('command',[
 {'action':'accent','band':True}, {'action':'accent','band':16},
 {'action':'accent','band':2,'strength':float('nan')},
 {'action':'tune','band_gains':[1]*15}, {'action':'tune','colors':[[300,0,0]]},
 {'action':'tune','speed':5}, {'action':'tune','speed':10**400}, {'action':'tune','arbitrary':1},
 {'action':'status','brightness':1}, {'action':'tune','motion':['flow']},
 {'action':['tune']},
])
def test_bad_live_controls_reject_before_mutation(rig,monkeypatch,command):
    d,f,l,c=rig;d.configure({'mode':'music'});d.step(tv(),music(),l.get_snapshot())
    monkeypatch.setattr(sd,'_director',d)
    before=len(d.renderer.looks)
    with pytest.raises(ValueError):sd.control_show(f,command)
    assert len(d.renderer.looks)==before


def test_tuning_retained_through_metadata_ticks_without_restarting(rig,monkeypatch):
    d,f,l,c=rig;d.configure({'mode':'music'});d.step(tv(),music(),l.get_snapshot())
    monkeypatch.setattr(sd,'_director',d)
    original=d.renderer;posts=len(f.posts)
    result=sd.control_show(f,{'action':'tune','colors':[[210,30,0]],'motion':'comet','speed':3,'brightness':.8})
    assert result['ok']
    d.step(tv(),music(),l.get_snapshot())
    assert d.renderer is original and len(f.posts)==posts
    assert d.renderer.looks[-1]['motion']=='comet'
    assert d.renderer.looks[-1]['speed']==3
    assert d.renderer.looks[-1]['colors']==[[210,30,0]]
    d.configure({'music_motion':'ripple'})
    d.step(tv(),music(),l.get_snapshot())
    assert d.renderer.looks[-1]['motion']=='ripple'
    assert d.renderer.looks[-1]['speed']==3


def test_stopped_renderer_is_not_started_by_accent(rig,monkeypatch):
    d,f,l,c=rig;monkeypatch.setattr(sd,'_director',d)
    with pytest.raises(RuntimeError,match='music'):sd.control_show(f,{'action':'accent','band':3})
    assert not f.posts


def test_music_show_native_and_ddp_handoff_keeps_listener(rig,monkeypatch):
    d,f,l,c=rig;l.start();d.configure({'mode':'music'});d.step(tv(),music(),l.get_snapshot())
    monkeypatch.setattr(sd,'_director',d)
    monkeypatch.setattr('music_chapters.available_native_effects',lambda fleet:[{'id':155,'name':'Freqmap','audio':'f'}])
    old=d.renderer
    result=sd.control_show(f,{'action':'native','effect':155,'colors':[[12,100,180]],
                              'native_speed':120,'native_intensity':200})
    assert result['status']['output_engine']=='native'
    assert old.running is False and d.renderer is None and l.started is True
    assert f.posts[-1][0]['seg'][0]['fx']==155
    assert f.posts[-1][0]['seg'][0]['col']==[[12,100,180]]
    d.step(tv(),music(),l.get_snapshot())
    assert d.renderer is None
    result=sd.control_show(f,{'action':'ddp'})
    assert result['status']['output_engine']=='ddp'
    assert d.renderer is not None and d.renderer.running


def test_invalid_native_handoff_never_stops_ddp(rig,monkeypatch):
    d,f,l,c=rig;d.configure({'mode':'music'});d.step(tv(),music(),l.get_snapshot())
    monkeypatch.setattr(sd,'_director',d)
    monkeypatch.setattr('music_chapters.available_native_effects',lambda fleet:[])
    renderer=d.renderer;before=len(f.posts)
    with pytest.raises(ValueError):
        sd.control_show(f,{'action':'native','effect':155,'colors':[[12,100,180]]})
    assert renderer.running and d.renderer is renderer and len(f.posts)==before


def test_native_post_failure_recovers_ddp_on_next_tick(rig,monkeypatch):
    d,f,l,c=rig;d.configure({'mode':'music'});d.step(tv(),music(),l.get_snapshot())
    monkeypatch.setattr(sd,'_director',d)
    monkeypatch.setattr('music_chapters.available_native_effects',lambda fleet:[{'id':155,'name':'Freqmap','audio':'f'}])
    original=f.post_state
    def fail_once(payload,target='all'):
        f.post_state=original
        return {'left':{'ok':False,'error':'timeout'}}
    f.post_state=fail_once
    with pytest.raises(RuntimeError,match='timeout'):
        sd.control_show(f,{'action':'native','effect':155,'colors':[[12,100,180]]})
    assert d.renderer is None and d._output_engine=='ddp'
    d.step(tv(),music(),l.get_snapshot())
    assert d.renderer is not None and d.renderer.running


def request_api(server,body,origin=None):
    conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
    headers={'Content-Type':'application/json'}
    if origin:headers['Origin']=origin
    conn.request('POST','/api/music-show',json.dumps(body),headers)
    response=conn.getresponse();result=(response.status,json.loads(response.read()));conn.close();return result


def test_music_show_api_dispatch_and_guards(api_server):
    server,state,_=api_server
    with patch.object(sd,'control_show',return_value={'ok':True,'status':{'mode':'music'}},create=True) as control:
        status,data=request_api(server,{'action':'accent','band':4})
        assert status==200 and data['ok']
        control.assert_called_once_with(state.client,{'action':'accent','band':4})
        assert request_api(server,{'action':'accent','band':4},'https://elsewhere.test')[0]==403
        control.side_effect=RuntimeError('Start music mode first')
        assert request_api(server,{'action':'accent','band':4})[0]==409


def test_model_response_api_keeps_complete_long_text(api_server):
    import light_gui
    import ai_chat
    server,state,_=api_server
    text=('Readable model explanation, not a truncated ticker.\n' * 40)+'END OF MODEL RESPONSE'
    with (patch.object(ai_chat,'run_chat',return_value={'text':text,'log':[],'rounds':1}),
          patch.object(light_gui,'ai_settings',return_value={}),
          patch.object(light_gui,'ai_context_text',return_value=''),
          patch.object(light_gui,'playback_clock_payload',return_value={'clock':{}})):
        conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
        conn.request('POST','/api/ai',json.dumps({'prompt':'Explain this show','now_playing':{}}),{'Content-Type':'application/json'})
        response=conn.getresponse();data=json.loads(response.read());conn.close()
    assert response.status==200
    assert data['response']==text


def test_match_song_keeps_complete_model_text():
    import light_gui
    import ai_chat
    text='Song choreography explanation.\n'*30+'END OF SONG RESPONSE'
    with (patch.object(ai_chat,'run_chat',return_value={'text':text,'log':[],'rounds':1}),
          patch.object(light_gui,'ai_settings',return_value={}),
          patch.object(light_gui,'ai_context_text',return_value='')):
        result=light_gui.match_lights_to_song(object(),{'title':'Song','artist':'Artist'})
    assert result['response']==text


def test_speed_tune_during_metadata_gap_retains_song_palette(rig,monkeypatch):
    d,f,l,c=rig;d.configure({'mode':'music'})
    song={**music(),'show':{'motion':'comet','speed':1.3}}
    d.step(tv(),song,l.get_snapshot());monkeypatch.setattr(sd,'_director',d)
    original=d.renderer.looks[-1]
    sd.control_show(f,{'action':'tune','speed':3})
    d.step(tv(description=''),{'kind':'unknown'},l.get_snapshot())
    look=d.renderer.looks[-1]
    assert look['colors']==original['colors']
    assert look['composition_mode']==original['composition_mode']
    assert look['motion']=='comet'


def test_explicit_persisted_value_replaces_live_override_even_if_unchanged(rig,monkeypatch):
    import light_gui
    from tests.test_smart_director_api import _state
    d,f,l,c=rig;d.configure({'mode':'music'});d.step(tv(),music(),l.get_snapshot())
    monkeypatch.setattr(sd,'_director',d)
    sd.control_show(f,{'action':'tune','speed':3,'motion':'comet'})
    with (patch.object(light_gui.lightctl,'load_config',return_value={'smart_director':dict(d.config)}),
          patch.object(light_gui.lightctl,'save_config'),patch.object(light_gui,'ai_settings',return_value={}),
          patch.object(sd,'start',return_value=d),patch.object(light_gui,'_stop_smart_director_conflicts')):
        light_gui.configure_smart_director(_state(f),{'music_speed':1})
    d.step(tv(),music(),l.get_snapshot())
    assert d.renderer.looks[-1]['speed']==1
    assert d.renderer.looks[-1]['motion']=='comet'


def test_explicit_control_wins_even_before_next_director_tick(rig,monkeypatch):
    d,f,l,c=rig;d.configure({'mode':'music'});d.step(tv(),music(),l.get_snapshot())
    monkeypatch.setattr(sd,'_director',d)
    sd.control_show(f,{'action':'tune','speed':3})
    d.configure({'music_speed':1})
    d.step(tv(),music(),l.get_snapshot())
    assert d.renderer.looks[-1]['speed']==1


def test_status_distinguishes_live_overrides_from_ai_biased_recipe(rig,monkeypatch):
    d,f,l,c=rig;d.configure({'mode':'music'});d.step(tv(),music(),l.get_snapshot())
    monkeypatch.setattr(sd,'_director',d)
    sd.control_show(f,{'action':'tune','speed':3})
    assert d.status()['show_overrides']=={'speed':3}
    d.configure({'music_speed':1})
    assert d.status()['show_overrides']=={}


def test_legacy_model_envelope_displays_reply_without_executing_claimed_actions():
    import light_gui
    text='Complete human-readable reply.\n'*20
    payload=json.dumps({'response':text,'confirmations':['model narrative'],'actions':[{'action':'off'}]})
    assert light_gui.model_reply_text(payload)==text
    assert light_gui.model_reply_text('A plain explanation')=='A plain explanation'
    assert light_gui.model_reply_text('{incomplete JSON')=='{incomplete JSON'
    unrelated=json.dumps({'response':'example','other':'technical output'})
    assert light_gui.model_reply_text(unrelated)==unrelated
