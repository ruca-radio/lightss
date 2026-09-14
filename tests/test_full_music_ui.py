"""Full music UI contracts exercised against the JavaScript shipped in render_html()."""
import json
import os
import subprocess
import textwrap

import light_gui


def html():
    return light_gui.render_html()


def js_slice(start, end):
    page = html()
    return page[page.index(start):page.index(end, page.index(start))]


def run_node(code, extra, prelude=""):
    script = prelude + "\n" + code + "\n(async()=>{" + extra + "})().catch(e=>{console.error(e);process.exit(1)});"
    result = subprocess.run(["node", "-e", script], text=True, capture_output=True,
                            timeout=10, env={**os.environ, "HOME": "/tmp/lightss-ui-test-home"})
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_top_identity_replaces_logo_without_removing_player_title_contract():
    page = html()
    assert 'class="wled-logo"' not in page
    assert 'id="musicIdentityTitle"' in page
    assert 'id="musicIdentityArtist"' in page
    assert 'id="musicIdentitySource"' in page
    assert 'id="musicTitle"' in page
    assert 'id="musicArtist"' in page


def test_full_model_responses_are_safe_persistent_history_with_clear_and_errors():
    page = html()
    assert "localStorage.setItem('light_model_responses'" in page
    assert "content.textContent = text" in page
    assert "appendModelResponse('error'" in page
    assert "localStorage.removeItem('light_model_responses')" in page
    assert "marquee-content" not in js_slice("function clearModelResponses", "let currentTarget")


def test_tv_identity_requires_active_playing_foreground_session_and_parses_null_tail():
    code = js_slice("function parseMediaIdentity", "function renderControllerVisualization")
    prelude = """
    const nodes={}; const document={getElementById:id=>nodes[id]||(nodes[id]={textContent:''})};
    const setText=(id,text)=>document.getElementById(id).textContent=text;
    let lastPlayerStatus=null;
    """
    valid = {"foreground_app": "tv.music", "media_session": {
        "package": "tv.music", "active": True, "state": 3,
        "description": "Title <safe>, Artist & Co, null"}}
    stale = {**valid, "media_session": {**valid["media_session"], "state": 2}}
    result = run_node(code, f"""
      updateMusicIdentity({json.dumps(valid)});
      const playing={{title:nodes.musicIdentityTitle.textContent,artist:nodes.musicIdentityArtist.textContent,source:nodes.musicIdentitySource.textContent}};
      updateMusicIdentity({json.dumps(stale)});
      console.log(JSON.stringify({{playing,stale:nodes.musicIdentityTitle.textContent}}));
    """, prelude)
    assert result["playing"] == {"title": "Title <safe>", "artist": "Artist & Co", "source": "TV · tv.music"}
    assert result["stale"] == "Nothing playing"


def test_spectrum_click_uses_latest_render_and_repeated_controller_accents_are_not_debounced():
    code = js_slice("function drawSpectrum", "function drawControllerLevelHistory")
    code += js_slice("async function creativeBoostBin", "function creativeSpectrumMap")
    prelude = """
    const posts=[]; let controllerVizStatus={mode:'music',renderer:{running:true}};
    const controllerVizActive=()=>true; const postJson=async(path,body)=>(posts.push({path,body}),{ok:true,status:{mode:'music'}});
    const setText=()=>{}; const flashOrb=()=>{}; const sendBeatUpdate=()=>{}; const getActiveColors=()=>({on:false,bri:0});
    const ctx={clearRect(){},fillRect(){}}; const handlers={};
    const canvas={width:160,height:40,getContext:()=>ctx,getBoundingClientRect:()=>({left:0,width:160}),addEventListener:(n,f)=>handlers[n]=f};
    const document={getElementById:id=>id==='spectrumCanvas'?canvas:null}; let frequencyData=[];
    """
    result = run_node(code, """
      drawSpectrum({spectrum:Array(4).fill(10)});
      drawSpectrum({spectrum:Array(16).fill(20)});
      await handlers.click({clientX:159}); await handlers.click({clientX:159});
      console.log(JSON.stringify(posts));
    """, prelude)
    assert result == [
        {"path": "/api/music-show", "body": {"action": "accent", "band": 15, "strength": 1}},
        {"path": "/api/music-show", "body": {"action": "accent", "band": 15, "strength": 1}},
    ]


def test_full_range_controls_motions_and_editable_band_gains_are_shipped():
    page = html()
    assert 'id="smartDirectorMusicBrightness" type="range" min="0" max="100"' in page
    assert 'id="smartDirectorSpeed" type="range" min="25" max="400"' in page
    for motion in ("auto", "flow", "punch", "chase", "spectrum", "comet", "ripple"):
        assert f'<option value="{motion}">' in page
    assert page.count('class="band-gain"') == 16
    assert "action:'tune',band_gains:entry.gains" in page
    assert 'id="resetBandGains"' in page
    assert "populateEffectiveShowStatus(status)" in page
    assert "renderer.motion" in page
    assert "status.brightness_percent" in page
    assert "status.show_overrides" in page
    assert 'id="smartDirectorEffectiveShow"' in page
    effective = js_slice("function populateEffectiveShowStatus", "function smartDecisionSummary")
    assert "smartDirectorSpeed'" not in effective
    assert "smartDirectorMusicBrightness'" not in effective
    assert "smartDirectorIntensity'" not in effective
    assert "smartDirectorColorfulness'" not in effective


def test_automated_decision_history_deduplicates_identical_polls_and_keeps_errors_distinct():
    code = js_slice("function populateEffectiveShowStatus", "function renderSmartDirectorResponse")
    prelude = """
    const entries=[]; const appendModelResponse=(kind,text)=>entries.push({kind,text});
    const setText=()=>{}; const renderControllerVisualization=()=>{}; const liveShowPending={};
    const document={getElementById:()=>null,querySelectorAll:()=>[]};
    """
    status = {"running": True, "selected_mode": "auto", "mode": "music",
              "reason": "Foreground music is playing", "audio": {}, "renderer": {}}
    errored = {**status, "last_error": "controller stopped"}
    result = run_node(code, f"""
      renderSmartDirectorStatus({json.dumps(status)}); renderSmartDirectorStatus({json.dumps(status)});
      renderSmartDirectorStatus({json.dumps(errored)});
      console.log(JSON.stringify(entries));
    """, prelude)
    assert len(result) == 2
    assert "Auto → Music" in result[0]["text"]
    assert result[1] == {"kind": "error", "text": "Smart lighting error: controller stopped"}


def test_explicit_music_mode_logs_stable_classifier_recipe_not_frame_noise():
    code = js_slice("function populateEffectiveShowStatus", "function renderSmartDirectorResponse")
    prelude = """
    const entries=[]; const appendModelResponse=(kind,text)=>entries.push({kind,text});
    const setText=()=>{}; const renderControllerVisualization=()=>{}; const liveShowPending={};
    const document={getElementById:()=>null,querySelectorAll:()=>[]};
    """
    status = {"running": True, "selected_mode": "music", "mode": "music",
              "reason": "Music identified", "brightness_percent": 65,
              "tv": {"media_session": {"description": "Track, Artist, null"}},
              "audio": {}, "renderer": {"sent_frames": 1},
              "intelligence": {"kind": "music", "reason": "Music identified",
                               "colors": [[1, 2, 3], [4, 5, 6]],
                               "composition_mode": "center_vs_outer"}}
    later = {**status, "renderer": {"sent_frames": 999, "bpm": 143}}
    result = run_node(code, f"""
      renderSmartDirectorStatus({json.dumps(status)}); renderSmartDirectorStatus({json.dumps(later)});
      console.log(JSON.stringify(entries));
    """, prelude)
    assert len(result) == 1
    assert "Automated recipe" in result[0]["text"]
    assert "center_vs_outer" in result[0]["text"]


def test_ai_placeholder_describes_live_music_control_not_rainbow_effects():
    page = html()
    assert "Try: make the live music show" in page
    assert "Try: slow rainbow chase" not in page


def test_tv_identity_rejects_disconnected_state_and_preserves_commas_in_title():
    code = js_slice("function parseMediaIdentity", "function renderControllerVisualization")
    prelude = """
    const nodes={}; const document={getElementById:id=>nodes[id]||(nodes[id]={textContent:''})};
    const setText=(id,text)=>document.getElementById(id).textContent=text;
    let lastPlayerStatus=null;
    """
    tv = {"connected": True, "awake": True, "foreground_app": "tv.music", "media_session": {
        "package": "tv.music", "active": True, "state": 3,
        "description": "Song, Live Version, Artist, Album"}}
    result = run_node(code, f"""
      updateMusicIdentity({json.dumps(tv)}); const title=nodes.musicIdentityTitle.textContent;
      updateMusicIdentity({json.dumps({**tv, 'connected': False})});
      console.log(JSON.stringify({{title,cleared:nodes.musicIdentityTitle.textContent}}));
    """, prelude)
    assert result == {"title": "Song, Live Version", "cleared": "Nothing playing"}


def test_response_persistence_is_best_effort_and_dom_history_is_bounded():
    page = html()
    code = js_slice("function clearModelResponses", "let currentTarget")
    assert "try { localStorage.removeItem" in code
    assert "try { localStorage.setItem" in code
    assert "while (pane.children.length > 50)" in code
    player_catch = page[page.index("async function refreshPlayerStatus"):page.index("async function playerCommand")]
    assert "lastPlayerStatus = null" in player_catch


def test_band_gain_writes_serialize_and_latest_response_wins_without_poll_overwrite():
    code = js_slice("function readBandGains", "function controllerVizActive")
    prelude = """
    const inputs=Array.from({length:16},()=>({value:'1'}));
    const nodes={}; const document={querySelectorAll:()=>inputs,getElementById:id=>nodes[id]||(nodes[id]={textContent:''})};
    const setText=(id,text)=>document.getElementById(id).textContent=text;
    const requests=[]; const rendered=[]; let smartDirectorRevision=0;
    const postJson=(path,body)=>new Promise(resolve=>requests.push({path,body,resolve}));
    const renderSmartDirectorStatus=status=>rendered.push(status.value);
    """
    result = run_node(code, """
      inputs[0].value='2'; const first=tuneBandGains();
      inputs[0].value='3'; const second=tuneBandGains();
      await Promise.resolve(); const before=requests.map(r=>r.body.band_gains[0]);
      requests[0].resolve({ok:true,status:{value:2}}); await first; await Promise.resolve();
      const after=requests.map(r=>r.body.band_gains[0]);
      requests[1].resolve({ok:true,status:{value:3}}); await second;
      console.log(JSON.stringify({before,after,rendered,pending:bandGainWritePending,writing:bandGainWriting}));
    """, prelude)
    assert result == {"before": [2], "after": [2, 3], "rendered": [2, 3], "pending": 0, "writing": False}


def test_music_identity_css_contains_long_mobile_metadata():
    page = html()
    css = page[page.index('<style>'):page.index('</style>')]
    assert ".music-identity" in css
    assert "min-width:0" in css
    assert "overflow-wrap:anywhere" in css
    probe = textwrap.dedent("""
        import json, os
        from playwright.sync_api import sync_playwright
        body = '<div class="music-identity"><h1>' + ('X' * 180) + '</h1><p>' + ('Y' * 180) + '</p><span class="music-identity-source">' + ('Z' * 180) + '</span></div>'
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path='/usr/bin/google-chrome')
            page = browser.new_page(viewport={"width":320,"height":700})
            page.set_content(os.environ['SHIPPED_STYLE'] + body)
            print(json.dumps(page.evaluate('({inner:innerWidth,scroll:document.documentElement.scrollWidth})')))
            browser.close()
    """)
    env = {**os.environ, "HOME": "/tmp/lightss-ui-test-home",
           "PYTHONPATH": "/home/rucaradio/.local/lib/python3.14/site-packages",
           "SHIPPED_STYLE": css + "</style>"}
    result = subprocess.run(["/home/linuxbrew/.linuxbrew/bin/python3", "-c", probe],
                            text=True, capture_output=True, timeout=30, env=env)
    assert result.returncode == 0, result.stderr
    measured = json.loads(result.stdout)
    assert measured["scroll"] == measured["inner"] == 320


def test_clear_keeps_current_recipe_suppressed_but_allows_changed_recipe():
    responses = js_slice("function clearModelResponses", "let currentTarget")
    decision = js_slice("function populateEffectiveShowStatus", "function renderSmartDirectorResponse")
    text = "Automated recipe · Track, Artist, null · flow · Music identified"
    prelude = f"""
    const saved=[{{kind:'recipe',text:{json.dumps(text)},at:'now'}}];
    const localStorage={{getItem:()=>JSON.stringify(saved),setItem(){{}},removeItem(){{}}}};
    const pane={{children:[],scrollHeight:1,appendChild(x){{this.children.push(x)}},removeChild(){{this.children.shift()}},get firstElementChild(){{return this.children[0]}},set textContent(v){{this.children=[]}}}};
    const document={{getElementById:()=>pane,createElement:()=>({{className:'',textContent:'',appendChild(){{}}}})}};
    const liveShowPending={{}};const setText=()=>{{}};const renderControllerVisualization=()=>{{}};
    let bandGainWriting=false,bandGainWritePending=0,bandGainDirty=false;
    """
    status = {"selected_mode": "music", "mode": "music", "reason": "Music identified",
              "tv": {"media_session": {"description": "Track, Artist, null"}},
              "audio": {}, "renderer": {"running": False},
              "intelligence": {"kind": "music", "reason": "Music identified",
                               "composition_mode": "flow"}}
    changed = {**status, "intelligence": {**status["intelligence"], "composition_mode": "chase"}}
    result = run_node(responses + decision, f"""
      loadModelResponses(); const seeded=smartDecisionSummary.lastKey;
      clearModelResponses(); renderSmartDirectorStatus({json.dumps(status)}); const afterSame=pane.children.length;
      renderSmartDirectorStatus({json.dumps(changed)});
      console.log(JSON.stringify({{seeded,afterSame,afterChanged:pane.children.length}}));
    """, prelude)
    assert result == {"seeded": "recipe|" + text, "afterSame": 0, "afterChanged": 1}


def test_google_chrome_executes_shipped_response_history_safely():
    code = js_slice("function clearModelResponses", "let currentTarget")
    probe = textwrap.dedent("""
        import json, os
        from playwright.sync_api import sync_playwright
        code = os.environ['SHIPPED_JS']
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path='/usr/bin/google-chrome')
            page = browser.new_page()
            page.route('https://lightss.test/', lambda route: route.fulfill(
                content_type='text/html', body='<div id="modelResponses"></div>'))
            page.goto('https://lightss.test/')
            page.add_script_tag(content=code)
            page.evaluate("appendModelResponse('ai', '<img src=x onerror=alert(1)> full response')")
            result = page.eval_on_selector('#modelResponses', 'e => ({text:e.textContent,html:e.innerHTML})')
            result['saved'] = page.evaluate("JSON.parse(localStorage.getItem('light_model_responses')).length")
            print(json.dumps(result))
            browser.close()
    """)
    env = {**os.environ, "HOME": "/tmp/lightss-ui-test-home",
           "PYTHONPATH": "/home/rucaradio/.local/lib/python3.14/site-packages",
           "SHIPPED_JS": code}
    result = subprocess.run(["/home/linuxbrew/.linuxbrew/bin/python3", "-c", probe],
                            text=True, capture_output=True, timeout=30, env=env)
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    assert actual["saved"] == 1
    assert "<img src=x" in actual["text"]
    assert "<img src=x" not in actual["html"]
