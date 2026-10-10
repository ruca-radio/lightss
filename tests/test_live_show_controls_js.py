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
