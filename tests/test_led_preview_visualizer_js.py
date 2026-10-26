"""The strip preview must render actual controller pixels from the Peek feed.

Priority: local DDP renderer preview → live Peek pixels → synthetic state
approximation. Peek pixels are sliced per channel in wall order so the
visualizer represents the current color *and* pattern of the real LEDs.
"""

import json
import subprocess

import light_gui

LEFT_FAR = [[10, 0, 0]] * 34
LEFT_MIDDLE = [[0, 10, 0]] * 47
RIGHT_MIDDLE = [[0, 0, 10]] * 47
RIGHT_FAR = [[10, 10, 0]] * 40

CHANNELS = {
    "far-left": ["left", 0],
    "middle-left": ["left", 1],
    "middle-right": ["right", 0],
    "far-right": ["right", 1],
}

STATE_MAP = {
    "left": {"on": True, "seg": [{"id": 0, "start": 0, "stop": 34}, {"id": 1, "start": 34, "stop": 81}]},
    "right": {"on": True, "seg": [{"id": 0, "start": 0, "stop": 47}, {"id": 1, "start": 47, "stop": 87}]},
}


def run_client_js(extra):
    html = light_gui.render_html()
    assert "// --- Live LED pixel preview (controller Peek)" in html
    code = html[
        html.index("// --- Live LED pixel preview (controller Peek)") : html.index(
            "function updateLightPreview("
        )
    ]
    stubs = """
    let now=1000;const performance={now:()=>now};
    const nodes={};
    const document={hidden:false,getElementById:id=>nodes[id]||(nodes[id]={textContent:'',innerHTML:''})};
    let currentTarget='all';
    let fleetInfo={fleet:true,targets:['all'],channels:%s};
    const mkChild=()=>({style:{}});
    const children=Array.from({length:12},mkChild);
    const stripClasses=new Set(['off']);
    const ledStrip={children,classList:{add:c=>stripClasses.add(c),remove:c=>stripClasses.delete(c)},get classListOff(){return stripClasses.has('off');}};
    let ddpPixels=[];
    const controllerPreviewColors=()=>ddpPixels.slice();
    let currentStripState=null;
    let renderCalls=0;const renderSegment=()=>{renderCalls++;return [7,8,9];};
    const rgbwToCss=(r,g,b)=>`rgb(${r},${g},${b})`;
    const rafQueue=[];const requestAnimationFrame=fn=>{rafQueue.push(fn);return rafQueue.length;};
    let fetchCalls=0,fetchResult=null,fetchError=null;
    const fetchJsonWithTimeout=async(url,opts,timeout)=>{fetchCalls++;if(fetchError)throw fetchError;return fetchResult;};
    const safeNum=(value,fallback=0)=>{const n=Number(value);return Number.isFinite(n)?n:fallback;};
    """ % json.dumps(CHANNELS)
    script = stubs + code + "\n" + extra
    result = subprocess.run(["node", "-e", script], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def peek_payload():
    return {
        "ok": True,
        "fleet": True,
        "channels": CHANNELS,
        "controllers": {
            "left": {"active": True, "status": "live", "colors": LEFT_FAR + LEFT_MIDDLE + [[1, 2, "x"]]},
            "right": {"active": True, "status": "live", "colors": RIGHT_MIDDLE + RIGHT_FAR},
        },
    }


def test_all_target_concatenates_channels_in_wall_order_and_drops_bad_pixels():
    extra = (
        "rebuildChannelRanges("
        + json.dumps(STATE_MAP)
        + ");applyLedPreview("
        + json.dumps(peek_payload())
        + ");"
        + """console.log(JSON.stringify({pixels:livePeekColors(), target:currentTarget}));"""
    )
    result = run_client_js(extra)
    expected = LEFT_FAR + LEFT_MIDDLE + RIGHT_MIDDLE + RIGHT_FAR
    assert result["pixels"] == expected


def test_channel_target_slices_only_that_strip():
    extra = (
        "rebuildChannelRanges("
        + json.dumps(STATE_MAP)
        + ");applyLedPreview("
        + json.dumps(peek_payload())
        + ");currentTarget='far-right';"
        "console.log(JSON.stringify(livePeekColors()));"
    )
    assert run_client_js(extra) == RIGHT_FAR


def test_inner_and_outer_group_targets_map_to_channel_pairs():
    extra = (
        "rebuildChannelRanges("
        + json.dumps(STATE_MAP)
        + ");applyLedPreview("
        + json.dumps(peek_payload())
        + ");"
        "currentTarget='inner';const inner=livePeekColors();"
        "currentTarget='outer';const outer=livePeekColors();"
        "console.log(JSON.stringify({inner,outer}));"
    )
    result = run_client_js(extra)
    assert result["inner"] == LEFT_MIDDLE + RIGHT_MIDDLE
    assert result["outer"] == LEFT_FAR + RIGHT_FAR


def test_controller_target_uses_its_channels_in_wall_order():
    extra = (
        "rebuildChannelRanges("
        + json.dumps(STATE_MAP)
        + ");applyLedPreview("
        + json.dumps(peek_payload())
        + ");currentTarget='right';"
        "console.log(JSON.stringify(livePeekColors()));"
    )
    assert run_client_js(extra) == RIGHT_MIDDLE + RIGHT_FAR


def test_all_target_without_channel_map_concatenates_whole_buffers():
    payload = peek_payload()
    payload["channels"] = {}
    extra = (
        "fleetInfo.channels={};rebuildChannelRanges({});applyLedPreview("
        + json.dumps(payload)
        + ");"
        "console.log(JSON.stringify(livePeekColors()));"
    )
    assert run_client_js(extra) == LEFT_FAR + LEFT_MIDDLE + RIGHT_MIDDLE + RIGHT_FAR


def test_unknown_segment_bounds_show_each_controller_once_not_per_channel():
    # Channels exist but no state bounds arrived yet: each controller's whole
    # buffer must appear once, not duplicated per channel.
    payload = peek_payload()
    payload["channels"] = CHANNELS
    extra = (
        "fleetInfo.channels="
        + json.dumps(CHANNELS)
        + ";rebuildChannelRanges({});applyLedPreview("
        + json.dumps(payload)
        + ");"
        "console.log(JSON.stringify(livePeekColors()));"
    )
    assert run_client_js(extra) == LEFT_FAR + LEFT_MIDDLE + RIGHT_MIDDLE + RIGHT_FAR


def test_stale_peek_falls_back_to_synthetic_state_rendering():
    extra = (
        "rebuildChannelRanges("
        + json.dumps(STATE_MAP)
        + ");applyLedPreview("
        + json.dumps(peek_payload())
        + ");now+=3000;"
        "currentStripState={on:true,bri:255,colors:[[1,2,3,0]],segments:12,seg:{fx:0},sx:128,ix:128,ledInfo:null};"
        "updateStripFrame(0);"
        "console.log(JSON.stringify({pixels:livePeekColors(),renderCalls,first:children[0].style.backgroundColor,off:stripClasses.has('off')}));"
    )
    result = run_client_js(extra)
    assert result["pixels"] == []
    assert result["renderCalls"] == 12
    assert result["first"] == "rgb(7,8,9)"
    assert result["off"] is False


def test_local_ddp_renderer_preview_wins_over_peek_pixels():
    extra = (
        "rebuildChannelRanges("
        + json.dumps(STATE_MAP)
        + ");applyLedPreview("
        + json.dumps(peek_payload())
        + ");ddpPixels=[[1,2,3]];"
        "updateStripFrame(0);"
        "console.log(JSON.stringify({first:children[0].style.backgroundColor,second:children[1].style.backgroundColor}));"
    )
    result = run_client_js(extra)
    assert result["first"] == "rgb(1,2,3)"
    assert result["second"] == "rgb(1,2,3)"


def test_update_strip_frame_paints_actual_peek_pixels():
    extra = (
        "rebuildChannelRanges("
        + json.dumps(STATE_MAP)
        + ");applyLedPreview("
        + json.dumps(peek_payload())
        + ");updateStripFrame(0);"
        "console.log(JSON.stringify({first:children[0].style.backgroundColor,"
        "mid:children[6].style.backgroundColor,last:children[11].style.backgroundColor,"
        "glow:children[0].style.boxShadow,off:stripClasses.has('off')}));"
    )
    result = run_client_js(extra)
    assert result["off"] is False
    assert result["first"] == "rgb(10,0,0)"       # far-left red zone
    assert result["mid"] == "rgb(0,0,10)"         # middle-right blue zone
    assert result["last"] == "rgb(10,10,0)"       # far-right yellow zone
    assert result["glow"] == "0 0 6px rgb(10,0,0)"


def test_poll_skips_hidden_tabs_and_clears_feed_on_error():
    extra = (
        "fetchResult="
        + json.dumps(peek_payload())
        + ";await pollLedPreview();const fresh=livePeekColors().length;"
        "document.hidden=true;await pollLedPreview();const hiddenCalls=fetchCalls;"
        "document.hidden=false;fetchError=new Error('down');await pollLedPreview();"
        "console.log(JSON.stringify({fresh,hiddenCalls,afterError:livePeekColors().length}));"
    )
    result = run_client_js("(async()=>{" + extra + "})()")
    assert result["fresh"] == 168
    assert result["hiddenCalls"] == 1
    assert result["afterError"] == 0


def test_inactive_controller_is_never_rendered_as_live():
    payload = peek_payload()
    payload["controllers"]["right"] = {"active": False, "status": "stale"}
    extra = (
        "rebuildChannelRanges("
        + json.dumps(STATE_MAP)
        + ");applyLedPreview("
        + json.dumps(payload)
        + ");"
        "console.log(JSON.stringify(livePeekColors()));"
    )
    assert run_client_js(extra) == LEFT_FAR + LEFT_MIDDLE
