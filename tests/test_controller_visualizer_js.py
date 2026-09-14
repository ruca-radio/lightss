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
