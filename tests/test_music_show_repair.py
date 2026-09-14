"""Regressions for dull output and controls that could not steer a running show."""

import pytest
import realtime
import smart_director as sd
from tests.test_audio_reactive_renderer import FakeFleet, FakeTransport, snapshot
from tests.test_smart_director import rig, tv, music


def render_history(*, colors=None, controls=None, level=.3, fft=None):
    runner = realtime.AudioReactiveRunner(
        FakeFleet(), lambda: {}, brightness=.65, colors=colors, transport=FakeTransport())
    if controls:
        runner.update_look(**controls)
    frames = []
    for i in range(90):
        runner._consume_snapshot(snapshot(level=level, fft=fft or [.3]*4+[.02]*12,
                                          beat=i % 15 == 0, sequence=i), 1/30, i/30)
        frames.append(runner._render_frames()['left'])
    return runner, frames


def test_real_world_red_gray_white_recipe_uses_useful_brightness_and_chroma():
    _, frames = render_history(colors=[[210,47,47],[33,33,33],[210,210,210]])
    pixels = [tuple(frames[-1][i:i+3]) for i in range(0, len(frames[-1]), 3)]
    assert max(map(max, pixels)) >= 75, '65% music must not produce only 20/255 pixels'
    assert sum(map(max, pixels))/len(pixels) >= 45
    vivid = [p for p in pixels if max(p) and (max(p)-min(p))/max(p) >= .6]
    assert len(vivid)/len(pixels) >= .8, 'gray/white must not wash out the whole song'
    assert max(map(max, pixels)) <= 137, 'retain the existing 65% and 210 RGB limits'


def test_music_palette_interpolation_does_not_go_gray_between_complementary_colors():
    _, frames = render_history(colors=['#ff0000','#00ffff'])
    pixels = [frames[-1][i:i+3] for i in range(0,len(frames[-1]),3)]
    assert all((max(p)-min(p))/max(p) >= .6 for p in pixels if max(p)>20)


def test_motion_and_speed_steer_the_same_renderer_without_resets():
    runner, before = render_history()
    assert 'motion' in runner.status(), 'active motion must be observable, not just a frame counter'
    beats = runner.beat_count
    runner.update_look(motion='chase', speed=1.8, intensity=.9, colorfulness=1)
    runner._consume_snapshot(snapshot(level=.6,fft=[.5]*16,sequence=91),1/30,3.01)
    assert runner.beat_count == beats
    assert runner.status()['motion'] == 'chase'
    assert runner.status()['speed'] == 1.8
    assert runner._render_frames()['left'] != before[-1]


@pytest.mark.parametrize('control,low,high', [('speed',.5,2),('intensity',0,1)])
def test_live_speed_and_intensity_change_actual_pixel_history(control,low,high):
    _, first = render_history(controls={control:low})
    _, second = render_history(controls={control:high})
    assert first[-10:] != second[-10:]
    if control == 'intensity':
        assert sum(second[-1]) > sum(first[-1])


def test_automatic_motion_follows_sustained_audio_not_a_random_timer():
    quiet, _ = render_history(level=.06,fft=[.03]*16)
    loud, _ = render_history(level=.8,fft=[.8]*4+[.1]*12)
    assert quiet.status().get('motion') == 'flow'
    assert loud.status().get('motion') == 'punch'


def test_silence_remains_dim_and_still_after_new_controls():
    runner,_ = render_history(controls={'motion':'chase','speed':2,'intensity':1})
    frames=[]
    for i in range(150):
        runner._consume_snapshot(snapshot(active=False,sequence=100+i),1/30,4+i/30)
        frames.append(runner._render_frames()['left'])
    assert frames[-1] == frames[-2] == frames[-3]
    assert max(frames[-1]) <= 25


@pytest.mark.parametrize('updates', [
    {'music_speed':0}, {'music_speed':float('nan')}, {'music_speed':True},
    {'music_intensity':-1}, {'music_colorfulness':2}, {'music_motion':'strobe'},
])
def test_bad_live_show_controls_rejected_before_mutation(updates):
    with pytest.raises(ValueError): sd.validate_config(updates)


def test_live_controls_apply_during_unknown_metadata_without_losing_the_song(rig):
    d,f,l,c=rig
    d.configure({'mode':'music'})
    d.step(tv(),music(),l.get_snapshot())
    renderer=d.renderer;posts=len(f.posts)
    original=renderer.looks[-1]['colors']
    d.configure({'music_brightness':.42,'music_speed':1.5,'music_intensity':.9,
                 'music_colorfulness':1,'music_motion':'chase'})
    d.step(tv(description=''),{'kind':'unknown'},l.get_snapshot())
    assert d.renderer is renderer and len(f.posts)==posts
    assert renderer.looks[-1]['brightness']==.42
    assert renderer.looks[-1]['colors']==original
    assert renderer.looks[-1]['speed']==1.5
    assert renderer.looks[-1]['motion']=='chase'


def test_ai_recipe_can_shape_motion_not_only_palette():
    from activity_intelligence import classify_context
    from tests.test_activity_intelligence import observation
    result=classify_context(observation(),complete=lambda *a,**kw:{
        'kind':'music','confidence':.95,'colors':['#ff2200','#0022ff'],
        'show':{'motion':'chase','speed':1.4,'intensity':.9,'raw_pixels':[255]}})
    assert result.get('show') == {'motion':'chase','speed':1.4,'intensity':.9}


def test_invalid_ai_motion_and_nonfinite_controls_do_not_reach_renderer():
    from activity_intelligence import classify_context
    from tests.test_activity_intelligence import observation
    result=classify_context(observation(),complete=lambda *a,**kw:{
        'kind':'music','confidence':.95,
        'show':{'motion':'strobe','speed':float('nan'),'intensity':True}})
    assert result.get('show',{}) == {}


def test_ai_show_recipe_reaches_renderer_and_manual_motion_wins(rig):
    d,f,l,c=rig;d.configure({'mode':'music'})
    decision={**music(),'show':{'motion':'chase','speed':1.4,'intensity':.9}}
    d.step(tv(),decision,l.get_snapshot())
    assert d.renderer.looks[-1]['motion']=='chase'
    assert d.renderer.looks[-1]['speed']==1.4
    d.configure({'music_motion':'punch'})
    d.step(tv(),decision,l.get_snapshot())
    assert d.renderer.looks[-1]['motion']=='punch'


def test_original_color_setting_preserves_low_saturation_hue_and_endpoints():
    colors=[[100,110,120],[120,100,110]]
    palette=realtime._music_palette(colors,0)
    assert palette[0]==tuple(colors[0]) and palette[-1]==tuple(colors[-1])


def test_unison_chase_aligns_equal_normalized_positions_across_controllers():
    runner,_=render_history(controls={'motion':'chase','composition_mode':'unison'})
    frames=runner._render_frames()
    # Physical lengths differ, but pixel-zero (bottom) has the same coordinate.
    starts=[frames[e.controller][e.ddp_offset*3:e.ddp_offset*3+3] for e in runner.entries]
    assert len(set(starts))==1
