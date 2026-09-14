from __future__ import annotations

import light_gui_html


def rendered(player_enabled=True):
    return light_gui_html.render_main_html(player_enabled=player_enabled)


def section(html, start, end):
    return html[html.index(start):html.index(end, html.index(start))]


def test_smart_lighting_card_is_visible_near_tv_observer_and_responsive():
    html = rendered()
    observer = html.index('id="tvObservationStatus"')
    card = html.index('id="smartDirectorCard"')
    tabs = html.index('<div class="tab-bar">')
    assert observer < card < tabs
    for marker in (
        'Smart lighting',
        'id="smartDirectorEnabled"',
        'id="smartDirectorMode"',
        '<option value="auto">Auto</option>',
        '<option value="music">Music</option>',
        '<option value="tv">TV</option>',
        '<option value="manual">Manual</option>',
        'id="smartDirectorTheme"',
        '<option value="warm">Warm</option>',
        '<option value="neutral">Neutral</option>',
        '<option value="blue">Blue</option>',
        'id="smartDirectorMusicBrightness"',
        'id="smartDirectorDayBrightness"',
        'id="smartDirectorEveningBrightness"',
        'id="smartDirectorNightBrightness"',
        'applySmartDirectorSettings(this)',
        'id="smartDirectorMicStatus"',
        'id="smartDirectorRendererStatus"',
        'id="smartDirectorDecisionStatus"',
    ):
        assert marker in html
    assert '.smart-director-grid' in html
    assert '@media (max-width: 640px)' in html


def test_apply_posts_only_displayed_defaults_keys_and_converts_percent_to_fraction():
    html = rendered()
    body = section(html, 'function readSmartDirectorForm()', 'function populateSmartDirectorSettings')
    assert "enabled: !!document.getElementById('smartDirectorEnabled').checked" in body
    assert "mode: document.getElementById('smartDirectorMode').value" in body
    assert "tv_theme: document.getElementById('smartDirectorTheme').value" in body
    for key, element in (
        ('music_brightness', 'smartDirectorMusicBrightness'),
        ('day_brightness', 'smartDirectorDayBrightness'),
        ('evening_brightness', 'smartDirectorEveningBrightness'),
        ('night_brightness', 'smartDirectorNightBrightness'),
    ):
        assert f"{key}: smartDirectorFraction('{element}')" in body
    for forbidden in ('day_start:', 'evening_start:', 'night_start:', 'timezone:', 'debounce_s:'):
        assert forbidden not in body

    apply_body = section(html, 'async function applySmartDirectorSettings', 'async function setSmartDirectorMode')
    assert "postJson('/api/smart-director', readSmartDirectorForm())" in apply_body


def test_live_polling_is_fast_but_idle_throttled_and_preserves_dirty_form():
    html = rendered()
    poll_body = section(html, 'async function pollSmartDirector()', 'async function loadSmartDirector')
    assert 'if (smartDirectorBusy) return;' in poll_body
    assert 'renderSmartDirectorResponse(data, true);' in poll_body
    assert 'smartDirectorBusy = false;' in poll_body
    render_body = section(html, 'function renderSmartDirectorResponse', 'async function pollSmartDirector')
    assert 'if (allowSettings && !smartDirectorDirty)' in render_body
    assert 'setInterval(pollSmartDirector, 100)' in html
    assert 'performance.now() - controllerVizAt < 2000' in poll_body
    assert 'markSmartDirectorDirty()' in html


def test_primary_music_mode_enables_controller_mic_director_without_browser_capture():
    html = rendered()
    body = section(html, 'async function startMusicMode', 'function startBrowserMusicMode')
    gate = body.index('if (!useBrowserMic)')
    post = body.index("setSmartDirectorMode({enabled: true, mode: 'music'})")
    browser = body.index('await startAudioReactive();')
    assert gate < post < browser
    assert 'return;' in body[post:browser]
    assert 'options && options.browserMic === true' in body
    assert 'function startBrowserMusicMode' in html
    assert 'Advanced browser mic' in html


def test_local_music_cleanup_does_not_disable_smart_director_but_explicit_stop_does():
    html = rendered()
    local_stop = section(html, 'function stopMusicMode()', 'let playbackClock')
    assert '/api/smart-director' not in local_stop
    assert 'setSmartDirectorMode' not in local_stop

    smart_stop = section(html, 'async function stopSmartMusicMode()', 'async function startMusicMode')
    assert "setSmartDirectorMode({enabled: false})" in smart_stop
    assert 'stopMusicMode();' in smart_stop
    assert 'onclick="stopSmartMusicMode()"' in html


def test_status_renders_controller_mic_renderer_and_decision_fields():
    html = rendered()
    body = section(html, 'function renderSmartDirectorStatus', 'function renderSmartDirectorResponse')
    assert 'status.audio || {}' in body
    assert 'status.renderer || {}' in body
    assert 'status.intelligence || {}' in body
    assert "setText('smartDirectorMicStatus'" in body
    assert "setText('smartDirectorRendererStatus'" in body
    assert "setText('smartDirectorDecisionStatus'" in body
    assert 'status.last_error' in body
    assert 'renderer.last_error' in body
    assert 'renderer.beat_count' in body
    assert 'renderer.bpm' in body


def test_tv_observation_copy_explains_smart_lighting_use():
    html = rendered()
    assert 'This switch only permits observation; Smart lighting uses TV context when enabled.' in html


def test_card_and_contract_render_with_player_disabled_too():
    html = rendered(player_enabled=False)
    assert 'id="smartDirectorCard"' in html
    assert "fetchJsonWithTimeout('/api/smart-director'" in html
    assert "postJson('/api/smart-director'" in html


def test_mobile_brightness_captions_stay_on_one_line_and_legacy_music_is_clear():
    html = rendered()
    for label in ('Music', 'Day', 'Evening', 'Night'):
        assert f'<span class="smart-brightness-label">{label} ' in html
    assert '.smart-brightness-label { display: block; white-space: nowrap;' in html
    assert 'Legacy AI music' in html
    assert 'Start browser mic beat-reactive + music matching' not in html


def test_smart_status_exposes_startup_errors_and_writes_invalidate_old_polls():
    html=rendered()
    status=section(html,'function renderSmartDirectorStatus','function renderSmartDirectorResponse')
    assert 'status.startup_error' in status
    poll=section(html,'async function pollSmartDirector()','async function loadSmartDirector')
    assert 'revision === smartDirectorRevision' in poll
    apply=section(html,'async function applySmartDirectorSettings','async function setSmartDirectorMode')
    assert 'smartDirectorWriting = true' in apply and 'smartDirectorWriting = false' in apply
