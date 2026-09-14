import json
import shutil
import subprocess

import pytest
import light_gui


def test_calibration_and_local_library_render():
    html = light_gui.render_html()
    for ident in ('calibrationDialog', 'calibrationApply', 'localPlaylistSelect', 'localQueue', 'calibrationCamera'):
        assert f'id="{ident}"' in html
    assert 'onclick="openCalibration()"' in html
    assert 'Account library' in html


def run_js(extra):
    import desktop_ui
    if not shutil.which('node'):
        pytest.skip('node unavailable')
    setup = '''
    const nodes=new Map();
    class El {constructor(){this.children=[];this.value='';this.disabled=false;this.open=false;this.style={};}
      append(...xs){this.children.push(...xs);} appendChild(x){this.children.push(x);} replaceChildren(...xs){this.children=xs;}
      showModal(){this.open=true;} close(){this.open=false;} set textContent(x){this.text=x;} get textContent(){return this.text;}}
    const document={getElementById:id=>{if(!nodes.has(id)) nodes.set(id,new El()); return nodes.get(id);},createElement:()=>new El()};
    const setText=(id,t)=>document.getElementById(id).textContent=t;
    const currentPlayerSource=()=> 'youtube_music';
    let requests=[];
    async function postJson(url,data){requests.push({url,data});return {ok:true,token:'review',can_apply:true,probes:{left:{led_count:82,segments:[{id:0,start:0,stop:34}],buses:[]}},proposed:{controllers:[]},playlists:[],queue:[]};}
    const fetchJsonWithTimeout=async()=>({ok:true,playlists:[],queue:[]});
    const confirm=()=>true; const lastPlayerStatus={};
    const refreshPlayerStatus=async()=>{};
    '''
    result=subprocess.run(['node','-e',setup+desktop_ui.SCRIPT+'\n(async()=>{'+extra+'})().catch(e=>{console.error(e);process.exit(1)});'],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    return json.loads(result.stdout)


def test_calibrate_button_only_scans_until_reviewed_apply():
    data=run_js("await openCalibration(); console.log(JSON.stringify({actions:requests.map(x=>x.data.action),disabled:document.getElementById('calibrationApply').disabled}));")
    assert data=={'actions':['scan'],'disabled':False}


def test_apply_sends_only_reviewed_token():
    data=run_js("await openCalibration(); await applyCalibration(); console.log(JSON.stringify(requests[1]));")
    assert data=={'url':'/api/calibration','data':{'action':'apply','token':'review'}}


def test_queue_does_not_advance_on_pause():
    data=run_js("localLibrary={playlists:[],queue:[{source:'youtube_music',provider_id:'abcdefghijk',kind:'song'},{source:'youtube_music',provider_id:'12345678901',kind:'song'}]}; await playLocalQueue(0); await maybeAdvanceLocalQueue({source:'youtube_music',track_id:'abcdefghijk',playing:true,ended:false}); await maybeAdvanceLocalQueue({source:'youtube_music',track_id:'abcdefghijk',playing:false,ended:false}); console.log(JSON.stringify(requests.filter(x=>x.url==='/api/player').length));")
    assert data==1


def test_queue_advances_only_after_matching_track_ends():
    data=run_js("localLibrary={playlists:[],queue:[{source:'youtube_music',provider_id:'abcdefghijk',kind:'song'},{source:'youtube_music',provider_id:'12345678901',kind:'song'}]}; await playLocalQueue(0); await maybeAdvanceLocalQueue({source:'youtube_music',track_id:'abcdefghijk',playing:true,ended:false}); await maybeAdvanceLocalQueue({source:'youtube_music',track_id:'abcdefghijk',playing:false,ended:true}); console.log(JSON.stringify(requests.filter(x=>x.url==='/api/player').length));")
    assert data==2


def run_apple(extra):
    html = light_gui.render_html()
    body = html[html.index('async function handleAppleClientCommand'):html.index('async function syncAppleNowPlaying')]
    script = "let queues=[],plays=0; const configureMusicKit=async()=>({setQueue:async q=>queues.push(q),play:async()=>plays++}); const ensurePlayerAnalyser=async()=>{}; let musicModeRunning=true; const startMusicMode=()=>{};const syncAppleNowPlaying=async()=>{};const setText=()=>{};" + body + "(async()=>{" + extra + "})().catch(e=>{console.error(e);process.exit(1)});"
    result=subprocess.run(['node','-e',script],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    return json.loads(result.stdout)


def test_apple_album_sets_album_queue_before_playing():
    result=run_apple("await handleAppleClientCommand('playItem',{id:'123',kind:'album'});console.log(JSON.stringify({queues,plays}));")
    assert result=={'queues':[{'album':'123'}],'plays':1}


def test_apple_artist_does_not_play_previous_queue():
    result=run_apple("const result=await handleAppleClientCommand('playItem',{artist:'123'});console.log(JSON.stringify({ok:result.ok,queues,plays}));")
    assert result=={'ok':False,'queues':[],'plays':0}


def test_playlist_play_replaces_queue_in_one_transaction():
    data=run_js("localLibrary={playlists:[{id:'p1',name:'Set',items:[{source:'youtube_music',provider_id:'abc',kind:'song'}]}],queue:[]};document.getElementById('localPlaylistSelect').value='p1';await playSavedPlaylist();console.log(JSON.stringify(requests.filter(x=>x.url==='/api/player/library').map(x=>x.data))); ")
    assert data==[{'action':'queue_from_playlist','playlist_id':'p1'}]


def test_desktop_transport_renders_seek_and_volume():
    html=light_gui.render_html()
    assert 'id="playerSeek"' in html
    assert 'id="playerVolume"' in html


def test_queue_uses_retained_completion_when_poll_misses_ended_frame():
    data=run_js("localLibrary={playlists:[],queue:[{source:'youtube_music',provider_id:'abcdefghijk',kind:'song'},{source:'youtube_music',provider_id:'12345678901',kind:'song'}]};await playLocalQueue(0);await maybeAdvanceLocalQueue({source:'youtube_music',track_id:'abcdefghijk',playing:true,ended_sequence:3});await maybeAdvanceLocalQueue({source:'youtube_music',track_id:'provider-autoplay-next',playing:true,ended:false,last_ended_track_id:'abcdefghijk',ended_sequence:4});console.log(JSON.stringify(requests.filter(x=>x.url==='/api/player').length));")
    assert data==2


def test_transport_next_follows_local_queue_when_active():
    html=light_gui.render_html()
    command=html[html.index('async function playerCommand'):html.index('async function onPlayerSourceChange')]
    data=run_js(command + "let moved=null;localQueueRun={index:2};playLocalQueue=async i=>{moved=i;};await playerCommand('next');console.log(JSON.stringify(moved));")
    assert data==3
