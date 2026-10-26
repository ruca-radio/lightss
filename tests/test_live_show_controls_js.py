"""Execute the shipped live-control JS, including overlapping request races."""
import json
import subprocess
import light_gui


def run_controls(extra):
    html=light_gui.render_html()
    code=html[html.index('let smartDirectorDirty'):html.index('function controllerVizActive')]
    script='''
    const nodes={};const document={getElementById:id=>nodes[id]||(nodes[id]={value:'',textContent:'',checked:false})};
    const setText=(id,text)=>document.getElementById(id).textContent=text;
    const timers=[];const setTimeout=fn=>(timers.push(fn),timers.length);const clearTimeout=()=>{};
    const posts=[];let resolvePost;
    const postJson=(path,body)=>{posts.push({path,body});return new Promise(resolve=>resolvePost=resolve);};
    const renderSmartDirectorResponse=()=>{};
    '''+code+'\n(async()=>{'+extra+'})().catch(e=>{console.error(e);process.exit(1)});'
    result=subprocess.run(['node','-e',script],text=True,capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr
    return json.loads(result.stdout)


def test_drag_coalesces_partial_live_updates_and_never_toggles_mode():
    result=run_controls('''
    queueLiveShowControl('music_speed', .5);queueLiveShowControl('music_speed', 1.5);
    queueLiveShowControl('music_intensity', .9);
    const request=flushLiveShowControls();
    resolvePost({ok:true,settings:{music_speed:1.5,music_intensity:.9},status:{}});await request;
    console.log(JSON.stringify({posts,pending:liveShowPending}));
    ''')
    assert result['posts']==[{'path':'/api/smart-director','body':{'music_speed':1.5,'music_intensity':.9}}]
    assert result['pending']=={}


def test_old_live_response_cannot_erase_a_newer_slider_edit_or_unsaved_schedule():
    result=run_controls('''
    smartDirectorDirty=true;
    queueLiveShowControl('music_speed', .5);const request=flushLiveShowControls();
    queueLiveShowControl('music_speed', 1.8);
    resolvePost({ok:true,settings:{music_speed:.5},status:{}});await request;
    console.log(JSON.stringify({pending:liveShowPending,dirty:smartDirectorDirty}));
    ''')
    assert result['pending']=={'music_speed':1.8}
    assert result['dirty'] is True


def test_live_update_failure_retains_edit_and_exposes_error():
    result=run_controls('''
    queueLiveShowControl('music_colorfulness', .8);const request=flushLiveShowControls();
    resolvePost({ok:false,error:'Controller unavailable'});await request;
    console.log(JSON.stringify({pending:liveShowPending,message:nodes.liveShowMessage.textContent}));
    ''')
    assert result['pending']=={'music_colorfulness':.8}
    assert 'Controller unavailable' in result['message']


def test_poll_cannot_snap_pending_live_slider_back():
    result=run_controls('''
    document.getElementById('smartDirectorSpeed').value='180';
    queueLiveShowControl('music_speed',1.8);
    populateSmartDirectorSettings({music_speed:.5});
    console.log(JSON.stringify({value:document.getElementById('smartDirectorSpeed').value}));
    ''')
    assert result['value']=='180'


def test_explicit_stop_waits_for_inflight_slider_then_stops_instead_of_dropping_click():
    html=light_gui.render_html()
    start=html.index('async function setSmartDirectorMode(')
    end=html.index('async function startMusicMode(',start)
    mode_code=html[start:end]
    result=run_controls(mode_code+'''
    const stopMusicMode=()=>{};
    queueLiveShowControl('music_speed',1.2);const live=flushLiveShowControls();
    const stopping=stopSmartMusicMode();
    resolvePost({ok:true,status:{}});await live;
    for(let i=0;i<5 && posts.length<2;i++){const timer=timers.pop();if(timer)timer();await Promise.resolve();}
    if(posts.length>1){resolvePost({ok:true,status:{running:false}});}
    await stopping;
    console.log(JSON.stringify({posts,pending:liveShowPending,message:nodes.autoStatus?.textContent}));
    ''')
    assert result['posts'][-1]['body']=={'enabled':False}, result
    assert result['pending']=={}


def test_visualizer_shortcuts_steer_active_ddp_instead_of_sending_rejected_legacy_beats():
    html=light_gui.render_html()
    code=html[html.index('// --- Creative + Capable Visualizer-driven features ---'):html.index('function flashOrb(')]
    for call in ['creativeBoostBin(2,musicAnalysis)','creativeSpectrumMap()','creativeEnergyPulse()','creativeEvolve()']:
        script='''const changes={};const legacy=[];const posts=[];
        const musicAnalysis={energy:.65,bass:.7,mid:.2,treble:.1,drive:.7};
        const controllerVizActive=()=>true;const controllerVizStatus={mode:'music',renderer:{running:true,motion:'flow'}};
        const queueLiveShowControl=(key,value)=>changes[key]=value;
        const sendBeatUpdate=p=>legacy.push(p);const askAI=()=>legacy.push('ai');const flashOrb=()=>{};
        const setText=()=>{};const renderSmartDirectorStatus=()=>{};
        const postJson=async(path,body)=>(posts.push({path,body}),{ok:true,status:{}});
        const document={getElementById:()=>({value:''})};
        '''+code+'\n'+call+';console.log(JSON.stringify({changes,legacy,posts}));'
        result=subprocess.run(['node','-e',script],capture_output=True,text=True,timeout=5)
        assert result.returncode==0,result.stderr
        actual=json.loads(result.stdout)
        assert not actual['legacy'],call
        if call.startswith('creativeBoostBin'):
            assert actual['posts']==[{'path':'/api/music-show','body':{'action':'accent','band':2,'strength':1}}]
            assert not actual['changes']
        else:
            assert actual['changes'],call
            assert 'music_motion' in actual['changes'],call


def test_shortcut_values_survive_apply_with_unsaved_tv_settings():
    result=run_controls('''
    smartDirectorDirty=true;document.getElementById('smartDirectorNightBrightness').value='20';
    queueLiveShowControl('music_motion','chase');queueLiveShowControl('music_speed',1.25);
    queueLiveShowControl('music_colorfulness',1);queueLiveShowControl('music_intensity',.95);
    const form=readSmartDirectorForm();
    console.log(JSON.stringify({form,dirty:smartDirectorDirty,speed:nodes.smartDirectorSpeedText?.textContent}));
    ''')
    assert result['form']['music_motion']=='chase'
    assert result['form']['music_speed']==1.25
    assert result['form']['music_colorfulness']==1
    assert result['form']['music_intensity']==.95
    assert result['form']['night_brightness']==.2
    assert result['dirty'] is True
    assert result['speed']=='1.25×'


def run_spectrum_click(response, *, controller=False, energy=128):
    html = light_gui.render_html()
    code = html[html.index('// --- Creative + Capable Visualizer-driven features ---'):html.index('function flashOrb(')]
    script = '''
    const messages={},legacy=[],flashes=[],timeouts=[];
    const setText=(id,text)=>messages[id]=text;
    const controllerVizActive=()=>false;
    const flashOrb=value=>flashes.push(value);
    const sendBeatUpdate=async(values,timeoutMs)=>{legacy.push(values);timeouts.push(timeoutMs);return RESPONSE;};
    '''.replace('RESPONSE', json.dumps(response)) + code + '''
    (async()=>{
      await creativeBoostBin(3,{source:SOURCE,energy:ENERGY,spectrum:Array(16).fill(128)});
      console.log(JSON.stringify({messages,legacy,flashes,timeouts}));
    })().catch(e=>{console.error(e);process.exit(1)});
    '''.replace('SOURCE', json.dumps('controller' if controller else 'browser')).replace('ENERGY', str(energy))
    result = subprocess.run(['node','-e',script], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_spectrum_click_reports_rejected_write_without_flashing_success():
    result = run_spectrum_click({'ok': False, 'error': 'Controller unavailable'})
    assert 'Controller unavailable' in result['messages'].get('spectrumMessage', '')
    assert result['flashes'] == []


def test_spectrum_click_confirms_success_in_visible_panel():
    result = run_spectrum_click({'ok': True})
    assert 'Band 4' in result['messages'].get('spectrumMessage', '')
    assert all(0 <= value <= 255 for value in result['legacy'][0].values())
    assert result['flashes']


def test_manual_spectrum_click_waits_for_controller_acknowledgement():
    result = run_spectrum_click({'ok': True})
    assert result['timeouts'] == [4000]


def test_spectrum_click_uses_same_energy_scale_for_browser_and_controller():
    browser = run_spectrum_click({'ok': True}, energy=128)
    controller = run_spectrum_click({'ok': True}, controller=True, energy=128/255)
    assert browser['legacy'] == controller['legacy']


def test_spectrum_click_feedback_is_present_in_both_page_variants():
    for player_enabled in (True, False):
        html = light_gui.render_html(player_enabled=player_enabled)
        assert html.count('id="spectrumMessage"') == 1


def test_inactive_controller_stream_keeps_visible_unavailable_status():
    html = light_gui.render_html()
    code = html[html.index('function controllerAudioActive()'):html.index('function startControllerAudioStream()')]
    script = '''
    let controllerAudioStatus=null,controllerAudioAt=0,controllerAudioPending=null;
    let controllerLastSequence=null,controllerLevelHistory=[],audioReactiveRunning=false;
    const musicAnalysis={},messages={};const performance={now:()=>1000};
    const controllerVizActive=()=>false;
    const renderAnalyzerFrame=()=>{};
    const resetAnalysisDisplay=()=>messages.reset=true;
    const setText=(id,text)=>messages[id]=text;
    '''+code+'''
    renderControllerAudioTelemetry({active:false,fft:Array(16).fill(0),receive_sequence:null});
    expireControllerAudioTelemetry();
    console.log(JSON.stringify({messages,controllerAudioStatus}));
    '''
    result=subprocess.run(['node','-e',script],capture_output=True,text=True,timeout=5)
    assert result.returncode==0,result.stderr
    actual=json.loads(result.stdout)
    assert actual['controllerAudioStatus'] is not None
    assert 'Waiting for controller audio' in actual['messages']['vizStatus']
    assert not actual['messages'].get('reset')
