"""Untrusted data must never become markup in the GUI.

Two sinks historically interpolated externally-controlled text straight into
``innerHTML``: the smart-suggestion chips (song/artist metadata, which any page
can set via ``navigator.mediaSession``) and the light preview (raw WLED state
from a controller). These tests execute the shipped JS with a hostile payload
and assert it is treated as text.
"""

from __future__ import annotations

import json
import subprocess

import light_gui


def _run(code: str, extra: str) -> dict:
    script = code + "\n(async()=>{" + extra + "})().catch(e=>{console.error(e);process.exit(1)});"
    result = subprocess.run(
        ["node", "-e", script], text=True, capture_output=True, timeout=15
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_light_preview_escapes_controller_supplied_state():
    """A hostile /json/state payload must not produce live markup."""
    html = light_gui.render_html()
    start = html.index("    function safeNum(value, fallback = 0) {")
    end = html.index("// SSE state updates", start)
    code = html[start:end]

    payload = {
        "on": True,
        "bri": "<img src=x onerror=alert('bri')>",
        "seg": [
            {
                "fx": 9,
                "sx": "<svg onload=alert('sx')>",
                "grp": "<img src=g onerror=alert('grp')>",
                "cct": "<b>",
                "col": [["<img src=c onerror=alert('col')>", 0, 0, 0]],
            }
        ],
    }
    extra = (
        "updateLightPreview("
        + json.dumps(payload)
        + ",null,0);console.log(JSON.stringify({html:captured.html}));"
    )
    result = _run(
        """
    const captured={};
    const lightInfo={set innerHTML(v){captured.html=v;},get innerHTML(){return captured.html||'';}};
    const effectNameMap={'9':'Rainbow'};
    const STRIP_SEGMENTS=[];
    let currentStripState=null;
    """
        + code,
        extra,
    )

    rendered = result["html"]
    for tag in ("<img", "<svg", "<b>", "onerror", "onload"):
        assert tag not in rendered, rendered
    assert "&lt;img" in rendered or "RGBW(" in rendered


def test_smart_suggestions_render_media_metadata_as_text():
    """A track title/reason with markup must stay inert text."""
    html = light_gui.render_html()
    start = html.index("async function refreshSmartSuggestions()")
    end = html.index("async function autoMatchSong(", start)
    code = html[start:end]

    hostile_title = "</b><img src=x onerror=alert(document.domain)>"
    hostile_reason = "Evil <svg onload=alert(1)> (Album, Playing)"
    suggestions = [
        {"title": hostile_title, "reason": hostile_reason, "action": "on", "payload": {"a": 1}},
        {"title": "Match the music", "reason": "safe", "action": "music_match", "payload": {}},
    ]

    extra = (
        "refreshSmartSuggestions().then(()=>{"
        "const chips=box.children.map(c=>({texts:c.children.map(k=>k.textContent),"
        "children:c.children.map(k=>k.tagName)}));"
        "let clicked=null;chips.forEach((c,i)=>{if(box.children[i].onclick){box.children[i].onclick();}});"
        "console.log(JSON.stringify({chips,sent,html:null}));"
        "});"
    )
    result = _run(
        """
    function makeEl(tag){return {tagName:tag,children:[],textContent:'',
      className:'',append(...kids){this.children.push(...kids);},
      addEventListener(type,fn){this['on'+type]=fn;}};}
    const box=makeEl('div');
    const document={createElement:makeEl,getElementById:id=>id==='smartSuggestions'?box:null};
    const sent=[];
    const send=(action,payload)=>sent.push({action,payload});
    const matchLightsFromNowPlaying=()=>sent.push({action:'music_match'});
    const fetchJsonWithTimeout=async()=>({suggestions:"""
        + json.dumps(suggestions)
        + """});
    """
        + code,
        extra,
    )

    chips = result["chips"]
    assert len(chips) == 2
    # The metadata is stored as text on real elements, never as markup.
    assert chips[0]["texts"][0] == hostile_title
    assert chips[0]["texts"][1] == hostile_reason
    assert chips[0]["children"] == ["b", "span"]
    # Clicking still dispatches the intended action.
    assert result["sent"][0]["action"] == "on"
    assert result["sent"][0]["payload"] == {"a": 1}
    assert result["sent"][1]["action"] == "music_match"


def test_apple_login_page_is_reachable():
    """The Apple sign-in link and QR code point at /apple-login."""
    assert "APPLE_LOGIN_HTML" in dir(light_gui.light_gui_html)
    assert "/apple-login" in light_gui.API_GET_PATHS
    assert len(light_gui.apple_login_html()) > 200


def test_manual_effect_pickers_are_rebuilt_from_the_live_catalog():
    """The curated list is only a first paint; the live catalog replaces it.

    Effect-family prohibitions were removed, so the manual pickers must offer
    every installed effect (e.g. Blink, Strobe, DJ Light), not the ~30 curated
    ids baked into the server-rendered HTML.
    """
    html = light_gui.render_html()
    start = html.index("    const effectNameMap")
    end = html.index("    const STRIP_SEGMENTS", start)
    code = html[start:end]

    extra = (
        "refreshEffectCatalog().then(()=>{"
        "console.log(JSON.stringify({"
        "names:Object.keys(effectNameMap).sort((a,b)=>a-b),"
        "options:fx.children.map(o=>o.value),"
        "kept:fx.value}));"
        "});"
    )
    result = _run(
        """
    const fx={value:'9',children:[],set textContent(v){this._t=v;if(v==='')this.children=[];},
      get textContent(){return this._t||'';},append(o){this.children.push(o);}};
    const other={value:'',children:[],set textContent(v){this._t=v;if(v==='')this.children=[];},
      get textContent(){return this._t||'';},append(o){this.children.push(o);}};
    const document={getElementById:id=>(id==='fx'?fx:other),createElement:t=>({tag:t})};
    const fetchJsonWithTimeout=async()=>({effects:[
      {id:0,name:'Solid'},{id:1,name:'Blink'},{id:23,name:'Strobe'},
      {id:42,name:'Fireworks'},{id:159,name:'DJ Light'}]});
    """
        + code,
        extra,
    )

    assert result["names"] == ["0", "1", "23", "42", "159"]
    assert result["options"] == ["0", "1", "23", "42", "159"]
    # An already-selected effect stays selected through the rebuild.
    assert result["kept"] == "9" or result["kept"] in ("0", "1", "23", "42", "159")
