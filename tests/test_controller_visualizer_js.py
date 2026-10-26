"""Controller status must drive the existing UI without starting browser audio."""
import json
import subprocess
import light_gui


def run_bridge(extra):
    html=light_gui.render_html()
    assert 'function controllerVizActive' in html
    code=html[html.index('function controllerVizActive'):html.index('// --- Music Director (mood-matching music mode)')]
    code=code[:code.index('function renderSmartDirectorResponse')]
    script='''
    let now=1000;const performance={now:()=>now};
    let controllerVizStatus=null,controllerVizAt=0,controllerLevelHistory=[],controllerLastSequence=null,controllerLastBeatCount=0;
    let audioReactiveRunning=false,musicModeRunning=false,currentTarget='all';const liveShowPending={};
    let controllerAudioStatus=null,controllerAudioAt=0,controllerAudioBusy=false;
    let controllerAudioStream=null,controllerAudioFrame=null,controllerAudioPending=null;
    let bandGainWriting=false,bandGainWritePending=0,bandGainDirty=false;
    const musicAnalysis={};const nodes={};
    const document={getElementById:id=>nodes[id]||(nodes[id]={textContent:'',disabled:false,classList:{toggle(){}}})};
    const setText=(id,t)=>document.getElementById(id).textContent=t;
    const appendModelResponse=()=>{};let lastPlayerStatus=null;const currentPlayerSource=()=> 'youtube_music';
    const setMusicModeUi=(running)=>setText('musicModeState',running?'Running':'Idle');
    let rendered=null;const renderAnalyzerFrame=a=>rendered=JSON.parse(JSON.stringify(a));
    const resetAnalysisDisplay=()=>{rendered={energy:0};};
    '''+code+'\n'+extra
    result=subprocess.run(['node','-e',script],text=True,capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr
    return json.loads(result.stdout)


STATUS={'running':True,'mode':'music','selected_mode':'auto','reason':'Music identified',
 'audio':{'active':True,'level':.6,'fft':[.8,.5,.2,.1]+[.3]*12,'receive_sequence':42,'beat':True},
 'renderer':{'running':True,'energy':.5,'bass':.7,'mid':.4,'treble':.2,'beat_count':7,'bpm':120,'bpm_confidence':.8,'preview':[{'channel':'far-left','controller':'left','colors':[[40,20,10],[50,30,20]]}]}}


def test_running_backend_drives_legacy_meters_and_music_state_without_local_capture():
    result=run_bridge('renderSmartDirectorStatus('+json.dumps(STATUS)+');console.log(JSON.stringify({nodes,rendered,active:controllerVizActive(),pixels:controllerPreviewColors(),browser:audioReactiveRunning}));')
    assert result['nodes']['musicModeState']['textContent']=='Running'
    assert 'Controller mic' in result['nodes']['vizStatus']['textContent']
    assert result['nodes']['autoStatus']['textContent']=='Music identified'
    assert result['rendered']['energy']==.6
    assert len(result['rendered']['spectrum'])==16
    assert result['rendered']['source']=='controller'
    assert result['pixels']==[[40,20,10],[50,30,20]]
    assert result['browser'] is False


def test_stale_status_clears_preview_and_active_indicator():
    result=run_bridge('renderSmartDirectorStatus('+json.dumps(STATUS)+');now+=3000;expireControllerVisualization();console.log(JSON.stringify({nodes,rendered,active:controllerVizActive(),pixels:controllerPreviewColors()}));')
    assert result['active'] is False and result['pixels']==[]
    assert result['rendered']['energy']==0
    assert 'unavailable' in result['nodes']['vizStatus']['textContent'].lower()


def test_meter_display_never_uses_old_audio_when_controller_reports_stale():
    status={**STATUS,'audio':{'active':False,'fft':[1]*16,'level':1}}
    result=run_bridge('renderSmartDirectorStatus('+json.dumps(status)+');console.log(JSON.stringify(rendered));')
    assert result['energy']==0 and all(x==0 for x in result['spectrum'])


def test_controller_vu_uses_normalized_level_not_legacy_byte_scale():
    html=light_gui.render_html();code=html[html.index('function updateVu('):html.index('function averageBand(')]
    script='''let peakLevel=0;const nodes={};const document={getElementById:id=>nodes[id]||(nodes[id]={style:{},classList:{toggle(){},add(){},remove(){}}})};
    const getActiveColors=()=>({on:true,bri:255,c1:'#123456',c2:'#abcdef'});const setTimeout=()=>{};
    '''+code+"\nupdateVu(.6,false,true);console.log(JSON.stringify(nodes.vuFill.style.width));"
    r=subprocess.run(['node','-e',script],capture_output=True,text=True,timeout=5)
    assert r.returncode==0,r.stderr
    assert json.loads(r.stdout)=='60%'


def test_read_only_audio_draws_spectrum_and_level_trace_without_changing_music_mode():
    result = run_bridge('renderControllerAudioTelemetry('+json.dumps(STATUS['audio'])+');console.log(JSON.stringify({nodes,rendered,browser:audioReactiveRunning,smart:controllerVizStatus,music:musicModeRunning}));')
    assert result['rendered']['energy'] == .6
    assert len(result['rendered']['spectrum']) == 16
    assert result['rendered']['levelHistory'] == [.6]
    assert result['browser'] is False
    assert result['smart'] is None and result['music'] is False
    assert result['nodes']['waveformLabel']['textContent'] == 'CONTROLLER LEVEL HISTORY + BEAT'


def test_read_only_audio_clears_stale_bands_and_preserves_browser_priority():
    stale = {'active': False, 'level': 1, 'fft': [1] * 16}
    result = run_bridge('renderControllerAudioTelemetry('+json.dumps(stale)+');console.log(JSON.stringify(rendered));')
    assert result['energy'] == 0 and all(x == 0 for x in result['spectrum'])
    result = run_bridge('audioReactiveRunning=true;renderControllerAudioTelemetry('+json.dumps(STATUS['audio'])+');console.log(JSON.stringify(rendered));')
    assert result is None


def test_non_player_ui_streams_audio_instead_of_five_fps_polling():
    html = light_gui.render_html(player_enabled=False)
    assert "new EventSource('/api/audio/events')" in html
    assert 'requestAnimationFrame(' in html
    assert 'setInterval(pollControllerAudio, 200)' not in html
    assert 'setInterval(pollSmartDirector' not in html
    assert 'id="smartDirectorCard"' not in html


def test_audio_stream_coalesces_messages_to_latest_animation_frame():
    extra = '''
      let scheduled=null,scheduledCount=0;
      const requestAnimationFrame=fn=>{scheduled=fn;scheduledCount++;return 1;};
      class EventSource {constructor(url){this.url=url;this.listeners={};} addEventListener(name,fn){this.listeners[name]=fn;}}
      startControllerAudioStream();
      const emit=audio=>controllerAudioStream.listeners.audio({data:JSON.stringify({audio})});
      emit({active:true,level:.2,fft:[.1],receive_sequence:1});
      emit({active:true,level:.8,fft:[.7],receive_sequence:2});
      scheduled();
      console.log(JSON.stringify({energy:rendered.energy,scheduledCount,url:controllerAudioStream.url}));
    '''
    assert run_bridge(extra) == {'energy': .8, 'scheduledCount': 1, 'url': '/api/audio/events'}


def test_manual_director_status_preserves_fresh_audio_stream():
    extra = 'renderControllerAudioTelemetry(' + json.dumps(STATUS['audio']) + ');'
    extra += "renderControllerVisualization({running:false,selected_mode:'manual'});"
    extra += 'console.log(JSON.stringify({energy:rendered.energy,status:nodes.vizStatus.textContent}));'
    assert run_bridge(extra) == {'energy': .6, 'status': 'Controller mic · live audio'}


def test_director_audio_does_not_replace_newer_stream_measurement():
    extra = 'renderControllerAudioTelemetry(' + json.dumps(STATUS['audio']) + ');'
    older = {**STATUS, 'audio': {'active': False, 'level': 0, 'fft': [0] * 16}}
    extra += 'renderControllerVisualization(' + json.dumps(older) + ');'
    extra += 'console.log(JSON.stringify({energy:rendered.energy,status:nodes.vizStatus.textContent}));'
    result = run_bridge(extra)
    assert result['energy'] == .6
    assert 'unavailable' not in result['status'].lower()


def test_audio_reconnect_keeps_fresh_measurement_then_expires():
    extra = '''
      class EventSource {constructor(){this.listeners={};} addEventListener(name,fn){this.listeners[name]=fn;}}
      startControllerAudioStream();
      renderControllerAudioTelemetry(''' + json.dumps(STATUS['audio']) + ''');
      now+=100;controllerAudioStream.onerror();
      const fresh=rendered.energy;
      now+=3000;expireControllerAudioTelemetry();
      console.log(JSON.stringify({fresh,expired:rendered.energy,status:nodes.vizStatus.textContent}));
    '''
    result = run_bridge(extra)
    assert result['fresh'] == .6
    assert result['expired'] == 0
    assert 'unavailable' in result['status'].lower()


def test_expired_audio_cannot_reappear_from_a_queued_animation_frame():
    extra = '''
      let scheduled=null;
      const requestAnimationFrame=fn=>{scheduled=fn;return 1;};
      class EventSource {constructor(){this.listeners={};} addEventListener(name,fn){this.listeners[name]=fn;}}
      startControllerAudioStream();
      renderControllerAudioTelemetry(''' + json.dumps(STATUS['audio']) + ''');
      controllerAudioStream.listeners.audio({data:JSON.stringify({audio:{active:true,level:.8}})});
      now+=3000;expireControllerAudioTelemetry();
      scheduled();
      console.log(JSON.stringify({energy:rendered.energy}));
    '''
    assert run_bridge(extra) == {'energy': 0}


def test_vu_and_beat_remain_visible_when_lights_are_off():
    html = light_gui.render_html()
    code = html[html.index('function updateVu('):html.index('function averageBand(')]
    script = '''let peakLevel=0;const nodes={};
      const document={getElementById:id=>nodes[id]||(nodes[id]={style:{},classList:{toggle(name,on){nodes[id].on=on;},add(){},remove(){}}})};
      const getActiveColors=()=>({on:false,bri:0,c1:'#22d3ee',c2:'#fbbf24'});
      const setTimeout=()=>{};
    ''' + code + '''
      updateVu(.6,true,true);
      console.log(JSON.stringify({fill:nodes.vuFill.style.background,beat:nodes.beatLamp.on}));
    '''
    result = subprocess.run(['node', '-e', script], text=True, capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr
    measured = json.loads(result.stdout)
    assert 'linear-gradient' in measured['fill']
    assert measured['beat'] is True


def test_byte_scaled_browser_audio_has_a_bounded_energy_orb():
    html = light_gui.render_html()
    code = html[html.index('function updateVu('):html.index('function averageBand(')]
    script = '''let peakLevel=0;const nodes={};
      const document={getElementById:id=>nodes[id]||(nodes[id]={style:{},classList:{toggle(){},add(){},remove(){}}})};
      const getActiveColors=()=>({on:true,bri:255,c1:'#22d3ee',c2:'#fbbf24'});
      const setTimeout=()=>{};
    ''' + code + '''
      updateVu(100,false);
      console.log(JSON.stringify(nodes.energyOrb.style.boxShadow));
    '''
    result = subprocess.run(['node', '-e', script], text=True, capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == '0 0 50px #22d3ee'


def test_black_strip_colors_fall_back_to_visible_palette():
    html = light_gui.render_html()
    code = html[html.index('function getActiveColors('):html.index('function averageBand(')]
    script = '''let peakLevel=0;let currentStripState={on:true,bri:255,colors:[[0,0,0],[0,0,0],[0,0,0]]};
      const controllerVizActive=()=>false;const nodes={};
      const document={getElementById:id=>nodes[id]||(nodes[id]={style:{},classList:{toggle(name,on){nodes[id].on=on;},add(){},remove(){}}})};
      const setTimeout=()=>{};
    ''' + code + '''
      const black=getActiveColors();
      currentStripState={on:true,bri:255,colors:[[255,0,0],[0,255,0]]};
      const valid=getActiveColors();
      currentStripState={on:true,bri:255,colors:[[0,0,0],[0,0,0],[0,0,0]]};
      updateVu(.6,true,true);
      console.log(JSON.stringify({black,valid,fill:nodes.vuFill.style.background,width:nodes.vuFill.style.width}));
    '''
    result = subprocess.run(['node', '-e', script], text=True, capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr
    measured = json.loads(result.stdout)
    for key in ('c1', 'c2', 'c3'):
        assert measured['black'][key] != 'rgb(0, 0, 0)'
        assert measured['black'][key] != 'rgb(0,0,0)'
    assert measured['black']['c1'] == 'rgb(34, 211, 238)'
    assert measured['black']['c2'] == 'rgb(251, 191, 36)'
    assert measured['black']['c3'] == 'rgb(236, 72, 153)'
    assert measured['valid']['c1'] == 'rgb(255, 0, 0)'
    assert 'rgb(0,0,0)' not in measured['fill'].replace(' ', '')
    assert 'linear-gradient' in measured['fill']
    assert measured['width'] == '60%'
