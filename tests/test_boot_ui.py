"""Contracts for the Self-config boot card and its marquee wiring.

The card polls ``/api/boot`` and pushes each new boot line into the existing
model-response marquee exactly once.  These tests execute the shipped
JavaScript against a fake DOM, so the dedup/render behavior is covered without
a browser.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import pytest

import light_gui_html


def html(player_enabled: bool = False) -> str:
    return light_gui_html.render_main_html(player_enabled=player_enabled)


def js_slice(page: str, start: str, end: str) -> str:
    return page[page.index(start):page.index(end, page.index(start))]


def run_node(code: str, extra: str, prelude: str = "") -> dict:
    script = (
        prelude
        + "\n"
        + code
        + "\n(async()=>{"
        + extra
        + "})().catch(e=>{console.error(e);process.exit(1)});"
    )
    result = subprocess.run(
        ["node", "-e", script],
        text=True,
        capture_output=True,
        timeout=15,
        env={**os.environ, "HOME": "/tmp/lightss-ui-test-home"},
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


FAKE_DOM_PRELUDE = """
class Element {
  constructor() { this.children = []; this.className = ''; this.checked = false; this.disabled = false; this._text = ''; }
  appendChild(child) { this.children.push(child); }
  set textContent(value) { this.children = []; this._text = String(value); }
  get textContent() { return this._text; }
}
const nodes = {};
const document = {
  hidden: false,
  getElementById: id => nodes[id] || (nodes[id] = new Element()),
  createElement: () => new Element(),
};
const setText = (id, value) => { document.getElementById(id).textContent = value; };
const displayed = [];
const appendModelResponse = (kind, text) => displayed.push({kind, text});
"""


@pytest.mark.parametrize("player_enabled", [False, True])
def test_self_config_card_uses_existing_card_markup_and_ids(player_enabled):
    page = html(player_enabled)
    assert 'class="card boot-card" id="bootCard"' in page
    for marker in (
        'id="bootEnabled"',
        'id="bootRun"',
        'id="bootStatus"',
        'id="bootLines"',
        'id="bootPhase"',
        'id="aiLightingEnabled"',
        'id="aiLightingStatus"',
        'AI lighting (auto)',
        'class="mode-pill" id="bootPhase"',
        'class="mode-header"',
        'class="mode-actions"',
        'class="boot-toggle"',
        'onchange="toggleBootEnabled(this.checked)"',
        'onchange="toggleAiLighting(this.checked)"',
        'onclick="runBootNow()"',
    ):
        assert marker in page, marker
    # The AI lighting control must not resurrect the removed Smart lighting panel.
    assert 'id="smartDirectorCard"' not in page
    assert 'id="smartDirector' not in page
    assert "Smart lighting" not in page
    # The boot lines container ships empty: server text only ever arrives via
    # textContent after /api/boot responds.
    assert '<div class="boot-lines" id="bootLines" role="list" aria-live="polite"></div>' in page
    # Sits with the automation controls, before the tabs.
    boot = page.index('id="bootCard"')
    assert page.index('id="tab-live"') > boot
    assert page.index('class="target-bar"') > boot
    assert page.index('id="musicDirectorEnabled"') > boot


def test_boot_script_polls_posts_and_feeds_the_marquee():
    page = html(False)
    code = js_slice(page, "// --- Self-config boot sequence", "async function wallMode")
    for marker in (
        "fetchJsonWithTimeout('/api/boot'",
        "postJson('/api/boot', {enabled: !!enabled})",
        "postJson('/api/boot', {run: true})",
        "appendModelResponse('boot', line)",
        "appendModelResponse('boot', text)",
        "bootLastLineCount",
        "bootLastPhase",
        "document.hidden",
        "setTimeout(pollBoot, delay)",
        "document.createElement('div')",
        "row.className = 'boot-line'",
        "row.textContent = String(line)",
        "pill.className = phase === 'running' ? 'mode-pill running' : 'mode-pill'",
        "loadAiLighting();",
    ):
        assert marker in code, marker
    # 2 s normally, 800 ms while the sequence runs.
    assert "? 800 : 2000" in code
    # Server text is rendered as text, never as markup.
    assert "innerHTML" not in code
    # Polling starts from the existing init sequence alongside the other loaders.
    init = page[page.index("    // Init"):]
    assert "loadBootStatus();" in init
    assert init.index("loadMusicDirectorState();") < init.index("loadBootStatus();")


def test_boot_render_dedups_new_lines_and_appends_final_status_once():
    page = html(False)
    code = js_slice(page, "// --- Self-config boot sequence", "function scheduleBootPoll")
    running = {
        "ok": True,
        "enabled": True,
        "running": True,
        "phase": "running",
        "started_at": "2026-10-23T03:00:00",
        "finished_at": None,
        "step": {"index": 1, "total": 4, "channel": "left", "text": "Reading left strip"},
        "lines": ["Boot: left strip lit", "Boot: left strip mapped"],
        "report": None,
        "error": None,
    }
    advanced = {
        **running,
        "step": {"index": 2, "total": 4, "channel": "right", "text": "Reading right strip"},
        "lines": running["lines"] + ["Boot: right strip lit"],
    }
    done = {
        **advanced,
        "running": False,
        "phase": "done",
        "finished_at": "2026-10-23T03:00:05",
        "step": {"index": 4, "total": 4, "channel": "top", "text": "Restored"},
    }
    result = run_node(
        code,
        f"""
      renderBootStatus({json.dumps(running)});
      const afterFirst = displayed.length;
      const runningStatus = nodes.bootStatus.textContent;
      renderBootStatus({json.dumps(running)});
      const afterRepeat = displayed.length;
      renderBootStatus({json.dumps(advanced)});
      const afterNewLine = displayed.length;
      renderBootStatus({json.dumps(done)});
      renderBootStatus({json.dumps(done)});
      console.log(JSON.stringify({{afterFirst, afterRepeat, afterNewLine,
        afterDone: displayed.length, kinds: displayed.map(e => e.kind),
        lines: nodes.bootLines.children.map(row => row.textContent),
        classes: nodes.bootLines.children.map(row => row.className),
        runningStatus,
        status: nodes.bootStatus.textContent, phase: nodes.bootPhase.textContent,
        pill: nodes.bootPhase.className, checked: nodes.bootEnabled.checked}}));
    """,
        FAKE_DOM_PRELUDE,
    )
    # Two initial lines, then only the genuinely new line, then the final
    # status line exactly once (the repeated done poll must not append again).
    assert result["afterFirst"] == 2
    assert result["afterRepeat"] == 2
    assert result["afterNewLine"] == 3
    assert result["afterDone"] == 4
    assert result["kinds"] == ["boot", "boot", "boot", "boot"]
    assert result["lines"] == advanced["lines"]
    assert all(name == "boot-line" for name in result["classes"])
    assert "Step 1/4 · left · Reading left strip" in result["runningStatus"]
    assert result["phase"] == "Done"
    assert result["pill"] == "mode-pill"
    assert result["checked"] is True
    assert "Done" in result["status"] and "4 strips verified" in result["status"]


def test_run_now_reports_refusal_in_status_and_toggle_reverts_on_error():
    page = html(False)
    code = js_slice(page, "async function toggleBootEnabled", "async function wallMode")
    prelude = FAKE_DOM_PRELUDE + """
    const posts = [];
    const postJson = async (url, body) => { posts.push({url, body}); return {ok: false, error: 'Smart Director owns the wall.'}; };
    const scheduleBootPoll = () => {};
    """
    result = run_node(
        code,
        """
      await toggleBootEnabled(true);
      const toggled = {status: nodes.bootStatus.textContent, checked: nodes.bootEnabled.checked};
      await runBootNow();
      console.log(JSON.stringify({posts, toggled,
        runStatus: nodes.bootStatus.textContent, disabled: nodes.bootRun.disabled}));
    """,
        prelude,
    )
    assert result["posts"] == [
        {"url": "/api/boot", "body": {"enabled": True}},
        {"url": "/api/boot", "body": {"run": True}},
    ]
    assert result["toggled"] == {
        "status": "Could not save: Smart Director owns the wall.",
        "checked": False,
    }
    assert result["runStatus"] == "Cannot run now: Smart Director owns the wall."
    assert result["disabled"] is False


def test_ai_lighting_toggle_reads_settings_and_posts_auto_mode():
    page = html(False)
    code = js_slice(page, "// --- AI lighting (auto) toggle", "async function wallMode")
    for marker in (
        "fetchJsonWithTimeout('/api/smart-director'",
        "postJson('/api/smart-director', {enabled: !!enabled, mode: 'auto'})",
        "settings.enabled",
        "status.running",
        "status.startup_error",
        "'Active · '",
    ):
        assert marker in code, marker
    assert "innerHTML" not in code
    init = page[page.index("    // Init"):]
    assert "loadAiLighting();" in init


def test_ai_lighting_render_and_toggle_revert_on_refusal():
    page = html(False)
    code = js_slice(page, "// --- AI lighting (auto) toggle", "async function wallMode")
    prelude = FAKE_DOM_PRELUDE + """
    const posts = [];
    const fetches = [];
    let responses = [
      {ok: false, error: 'Smart Director owns the wall.'},
      {ok: true, settings: {enabled: true}, status: {running: true, mode: 'auto'}},
    ];
    const postJson = async (url, body) => { posts.push({url, body}); return responses.shift(); };
    const fetchJsonWithTimeout = async (url) => {
      fetches.push(url);
      return {ok: true, settings: {enabled: true}, status: {running: true, mode: 'auto'}};
    };
    """
    result = run_node(
        code,
        """
      renderAiLighting({settings: {enabled: true}, status: {running: true, mode: 'tv'}});
      const active = {status: nodes.aiLightingStatus.textContent, checked: nodes.aiLightingEnabled.checked};
      renderAiLighting({settings: {enabled: false}, status: {running: false, startup_error: 'controller offline'}});
      const errored = {status: nodes.aiLightingStatus.textContent, checked: nodes.aiLightingEnabled.checked};
      await toggleAiLighting(true);
      const refused = {status: nodes.aiLightingStatus.textContent, checked: nodes.aiLightingEnabled.checked};
      await toggleAiLighting(true);
      const accepted = {status: nodes.aiLightingStatus.textContent, checked: nodes.aiLightingEnabled.checked};
      await loadAiLighting();
      const loaded = {status: nodes.aiLightingStatus.textContent, checked: nodes.aiLightingEnabled.checked};
      console.log(JSON.stringify({active, errored, refused, accepted, loaded, posts, fetches}));
    """,
        prelude,
    )
    assert result["active"] == {"status": "Active · tv", "checked": True}
    assert result["errored"] == {"status": "Off · controller offline", "checked": False}
    assert result["refused"] == {
        "status": "Could not update: Smart Director owns the wall.",
        "checked": False,
    }
    assert result["accepted"] == {"status": "Active · auto", "checked": True}
    assert result["loaded"] == {"status": "Active · auto", "checked": True}
    assert result["fetches"] == ["/api/smart-director"]
    assert result["posts"] == [
        {"url": "/api/smart-director", "body": {"enabled": True, "mode": "auto"}},
        {"url": "/api/smart-director", "body": {"enabled": True, "mode": "auto"}},
    ]


def test_music_director_toggle_reports_ai_lighting_handover():
    """The explicit Music Director switch takes over from AI lighting and says so."""
    page = html(False)
    code = js_slice(page, "async function toggleMusicDirector", "// --- Self-config boot sequence")
    prelude = FAKE_DOM_PRELUDE + """
    const posts = [];
    const polls = [];
    const messages = [];
    let aiLightingLoads = 0;
    const musicDirectorMsg = (text, kind) => messages.push({text, kind});
    const setMusicDirectorPolling = enabled => polls.push(enabled);
    const loadAiLighting = () => { aiLightingLoads++; };
    let response = {ok: true, enabled: true, switched_from: 'ai_lighting'};
    const postJson = async (url, body) => { posts.push({url, body}); return response; };
    """
    result = run_node(
        code,
        """
      document.getElementById('musicDirectorEnabled').checked = true;
      await toggleMusicDirector(true);
      const switched = {
        messages: messages.slice(),
        polls: polls.slice(),
        posts: posts.slice(),
        aiLightingLoads,
        checked: nodes.musicDirectorEnabled.checked,
      };
      response = {ok: true, enabled: true};
      await toggleMusicDirector(true);
      console.log(JSON.stringify({
        switched,
        plain: {messages: messages.slice(-1), aiLightingLoads, polls: polls.slice()},
      }));
    """,
        prelude,
    )
    assert result["switched"] == {
        "messages": [
            {"text": "Saving...", "kind": ""},
            {"text": "Music mode on · AI lighting turned off", "kind": "ok"},
        ],
        "polls": [True],
        "posts": [{"url": "/api/music-director", "body": {"enabled": True}}],
        "aiLightingLoads": 1,
        "checked": True,
    }
    assert result["plain"] == {
        "messages": [{"text": "Music mode on", "kind": "ok"}],
        "aiLightingLoads": 1,
        "polls": [True, True],
    }


def test_start_music_mode_reports_ai_lighting_handover():
    page = html(False)
    body = js_slice(page, "async function startMusicMode", "function startBrowserMusicMode")
    assert "data.switched_from === 'ai_lighting'" in body
    assert "AI lighting (auto) was turned off" in body
    assert "postJson('/api/music-director', {enabled: true})" in body
    assert "loadAiLighting()" in body


@pytest.mark.parametrize("player_enabled", [False, True])
def test_rendered_main_page_scripts_parse(player_enabled):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to validate generated JavaScript")

    page = html(player_enabled)
    scripts = [
        script
        for script in re.findall(r"<script\b[^>]*>(.*?)</script>", page, re.DOTALL)
        if script.strip()
    ]
    assert scripts, "The main page must contain executable JavaScript"
    for index, script in enumerate(scripts):
        result = subprocess.run(
            [node, "--check"],
            input=script,
            text=True,
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 0, f"Script {index}: {result.stderr}"
