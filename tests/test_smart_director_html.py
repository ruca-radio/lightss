from __future__ import annotations

import light_gui_html


def rendered(player_enabled=True):
    return light_gui_html.render_main_html(player_enabled=player_enabled)


def section(html, start, end):
    return html[html.index(start):html.index(end, html.index(start))]


def test_removed_smart_lighting_card_is_not_visible():
    for player_enabled in (True, False):
        html = rendered(player_enabled)
        assert 'id="smartDirectorCard"' not in html
        assert 'Smart lighting' not in html
        assert 'id="musicDirectorEnabled"' in html


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
    assert 'setInterval(pollSmartDirector, 100)' not in html
    assert 'performance.now() - controllerVizAt < 2000' in poll_body
    assert 'markSmartDirectorDirty()' in html


def test_primary_music_mode_enables_controller_mic_director_without_browser_capture():
    html = rendered()
    body = section(html, 'async function startMusicMode', 'function startBrowserMusicMode')
    gate = body.index('if (!useBrowserMic)')
    post = body.index("postJson('/api/music-director', {enabled: true})")
    browser = body.index('await startAudioReactive();')
    assert gate < post < browser
    assert 'return;' in body[post:browser]
    assert 'options && options.browserMic === true' in body
    assert 'function startBrowserMusicMode' in html
    assert 'id="musicModeBtn"' in html


def test_local_music_cleanup_does_not_disable_smart_director_but_explicit_stop_does():
    html = rendered()
    local_stop = section(html, 'function stopMusicMode()', 'let playbackClock')
    assert '/api/smart-director' not in local_stop
    assert 'setSmartDirectorMode' not in local_stop

    smart_stop = section(html, 'async function stopSmartMusicMode()', 'async function startMusicMode')
    assert "postJson('/api/music-director', {enabled: false})" in smart_stop
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


def test_tv_observation_does_not_promise_removed_smart_lighting():
    html = rendered()
    assert 'id="tvObservationStatus"' in html
    assert 'Smart lighting' not in html


def test_card_and_contract_render_with_player_disabled_too():
    html = rendered(player_enabled=False)
    assert 'id="smartDirectorCard"' not in html
    assert "fetchJsonWithTimeout('/api/smart-director'" in html
    assert "postJson('/api/smart-director'" in html


def test_music_director_is_not_labeled_legacy_or_smart_lighting():
    html = rendered()
    assert 'Legacy AI music' not in html
    assert 'Smart lighting' not in html
    assert 'Music Director' in html


def test_smart_status_exposes_startup_errors_and_writes_invalidate_old_polls():
    html=rendered()
    status=section(html,'function renderSmartDirectorStatus','function renderSmartDirectorResponse')
    assert 'status.startup_error' in status
    poll=section(html,'async function pollSmartDirector()','async function loadSmartDirector')
    assert 'revision === smartDirectorRevision' in poll
    apply=section(html,'async function applySmartDirectorSettings','async function setSmartDirectorMode')
    assert 'smartDirectorWriting = true' in apply and 'smartDirectorWriting = false' in apply
