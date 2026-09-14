"""Exercise rendered JavaScript, with network and DOM boundaries stubbed."""
import json
import shutil
import subprocess

import pytest
import light_gui


def run_cycle(extra):
    if not shutil.which('node'):
        pytest.skip('node unavailable')
    html = light_gui.render_html()
    body = html[html.index('async function runMusicRecognitionCycle'):html.index('async function startAudioReactive')]
    script = '''
      let musicModeRunning=true, musicRecognitionBusy=false, musicMetadataKey='',
          musicMetadataActive=false, musicModeGeneration=1;
      const status={textContent:''};
      const document={getElementById:()=>({textContent:''})};
      const setText=()=>{};
      let recognized=0, matched=0, metadata=null;
      async function refreshNowPlaying(){return metadata;}
      async function recognizeSongOnce(){recognized++; return {title:'Mic song'};}
      async function matchLightsFromRecognizedSong(song){matched++; return {ok:true,now_playing:song};}
      async function matchLightsFromNowPlaying(){return {ok:false};}
    ''' + body + '\n(async()=>{' + extra + '\n})().catch(e=>{console.error(e);process.exit(1)});'
    result = subprocess.run(['node','-e',script],capture_output=True,text=True,timeout=10)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_automatic_cycle_never_calls_shazam_without_metadata():
    assert run_cycle('await runMusicRecognitionCycle(false); console.log(JSON.stringify({recognized,matched}));') == {'recognized':0,'matched':0}


def test_metadata_same_track_does_not_repeat_ai_matching():
    assert run_cycle("metadata={title:'Song',artist:'Artist'}; await runMusicRecognitionCycle(false); await runMusicRecognitionCycle(false); console.log(JSON.stringify({recognized,matched}));") == {'recognized':0,'matched':1}


def test_manual_identify_remains_available():
    assert run_cycle('await runMusicRecognitionCycle(true); console.log(JSON.stringify({recognized,matched}));') == {'recognized':1,'matched':1}


def test_music_start_does_not_force_duplicate_identification():
    html = light_gui.render_html()
    start = html[html.index('async function startMusicMode'):html.index('function stopMusicMode')]
    assert 'runMusicRecognitionCycle(true)' not in start


def test_clock_ui_does_not_label_detection_time_as_position():
    html = light_gui.render_html()
    assert 'function renderPlaybackClock' in html
    assert 'Position unknown' in html
    assert 'id="songClockState"' in html


def test_library_rows_keep_remote_values_out_of_inline_javascript():
    html = light_gui.render_html()
    body = html[html.index('function renderLibraryRows'):html.index('async function searchPlayerLibrary')]
    script = '''
      class Element {constructor(){this.children=[];} appendChild(e){this.children.push(e);} append(...e){this.children.push(...e);} set innerHTML(x){this.children=[];this.html=x;} }
      const document={createElement:()=>new Element()};
      const el=new Element(); let played=null;
      function playLibraryItem(kind,id){played=[kind,id];}
    ''' + body + '''
      const id="x');globalThis.pwned=true;//";
      const title='" onmouseover="bad()';
      renderLibraryRows(el,[{id,title,artist:'<b>untrusted</b>'}],'song');
      if(el.children.length!==1) throw Error('must build DOM rows, not inline JS');
      el.children[0].onclick();
      if(played[1]!==id || globalThis.pwned) throw Error('unsafe identifier handling');
      if(el.children[0].children[1].children[0].textContent!==title) throw Error('title changed');
    '''
    result=subprocess.run(['node','-e',script],capture_output=True,text=True,timeout=10)
    assert result.returncode==0, result.stderr


def test_youtube_mode_does_not_hijack_an_inactive_audio_element():
    html=light_gui.render_html()
    body=html[html.index('async function ensurePlayerAnalyser'):html.index('async function applyDynamicScene')]
    script="const currentPlayerSource=()=> 'youtube_music'; const document={querySelector:()=>({paused:true})}; " + body + "ensurePlayerAnalyser().then(value=>{if(value!==false)process.exit(1)}).catch(e=>{console.error(e);process.exit(1)});"
    result=subprocess.run(['node','-e',script],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
