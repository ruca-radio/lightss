import unittest

import light_gui
import light_gui_html


def _section(html, start_marker, end_marker):
    return html[html.index(start_marker):html.index(end_marker)]


class GuiHtmlAuditTests(unittest.TestCase):
    """Regression tests for the audited light_gui_html.py JS surface."""

    def test_update_light_preview_guards_null_strip_state(self):
        # currentStripState is null on the first update; reading .ledInfo off
        # it used to throw into the SSE catch and falsely show Disconnected.
        html = light_gui.render_html()

        self.assertIn(
            "ledInfo: ledInfo || (currentStripState && currentStripState.ledInfo) || null,",
            html,
        )

    def test_creative_visualizer_controls_do_not_stop_music_mode(self):
        # send() stops the music-mode beat loop; the creative controls must
        # post through sendBeatUpdate like the beat loop itself does.
        html = light_gui.render_html()
        boost = _section(html, "function creativeBoostBin", "function creativeSpectrumMap")
        spectrum = _section(html, "function creativeSpectrumMap", "function creativeEnergyPulse")
        pulse = _section(html, "function creativeEnergyPulse", "function creativeEvolve")

        self.assertIn("sendBeatUpdate({", boost)
        self.assertNotIn("send(", boost)
        self.assertIn("sendBeatUpdate(payload);", spectrum)
        self.assertNotIn("send(", spectrum)
        self.assertIn("sendBeatUpdate({", pulse)
        self.assertNotIn("send(", pulse)

    def test_stop_audio_reactive_stops_mood_pipeline(self):
        html = light_gui.render_html()
        body = _section(html, "function stopAudioReactive", "function closeMicSession")

        self.assertIn("setMoodSessionRunning(false);", body)
        self.assertIn("stopMoodRecorder();", body)

    def test_start_music_mode_catch_releases_mic(self):
        html = light_gui.render_html()
        body = _section(html, "async function startMusicMode", "function stopMusicMode")
        catch_body = body[body.index("catch (err)"):]  # includes finally; fine

        self.assertIn("stopAudioReactive();", catch_body)

    def test_fleet_shrink_resets_target_selector(self):
        # After a fleet shrink the selector must not keep offering dead
        # controllers — it resets to just the "all" option.
        html = light_gui.render_html()
        body = _section(html, "function applyFleetInfo", "// Resolve the state blob")

        shrink_idx = body.index("fleetInfo.targets.length <= 1")
        reset_idx = body.index("sel.innerHTML = '';")
        self.assertLess(shrink_idx, reset_idx)
        self.assertIn("opt.value = 'all';", body)

    def test_autonomous_status_applies_computed_color(self):
        html = light_gui.render_html()

        self.assertIn("autoSt.style.color = color;", html)

    def test_channel_target_segment_is_passed_to_preview(self):
        html = light_gui.render_html()

        self.assertIn("segId = Number(mapping[currentTarget][1]) || 0;", html)
        self.assertIn("function updateLightPreview(st, ledInfo, segId = 0)", html)
        self.assertIn("segs[segId] || segs[0]", html)
        self.assertEqual(html.count("updateLightPreview(st, ledCapabilities, picked.segId)"), 2)

    def test_main_page_requests_have_timeouts(self):
        html = light_gui.render_html()

        self.assertIn("fetchJsonWithTimeout('/api/ai'", html)
        self.assertIn("fetchJsonWithTimeout(`/api/ai/jobs/", html)
        self.assertIn("fetchJsonWithTimeout('/api/ai_vision'", html)
        self.assertIn("fetchJsonWithTimeout('/api/mood/control'", html)
        self.assertIn("fetchJsonWithTimeout('/api/suggestions'", html)

    def test_audio_player_controls_are_visible(self):
        html = light_gui.render_html()
        for marker in (
            "Audio Player",
            "playerSource",
            "youtube_music",
            "apple_music",
            "playerCommand('playPause')",
            "/api/player",
        ):
            self.assertIn(marker, html)

    def test_player_stage_sits_above_tabs_with_library_and_apple_qr(self):
        html = light_gui.render_html()
        stage = html.index('id="playerStage"')
        library = html.index('id="playerLibrary"')
        tabs = html.index('id="tab-live"')
        self.assertLess(stage, tabs)
        self.assertLess(library, tabs)
        self.assertLess(html.index('id="ledStrip"'), tabs)
        for marker in (
            "appleQr",
            "qr_png",
            "/api/player/apple/session",
            "/api/player/search",
            "/api/player/playlists",
            "ensurePlayerAnalyser",
            "createMediaElementSource",
            "Sign in with Apple",
        ):
            self.assertIn(marker, html)
        live_right = html[html.index('id="tab-live"'):]
        self.assertEqual(live_right.count("<h2>Audio Player</h2>"), 0)

    def test_apple_login_page_uses_musickit(self):
        html = light_gui_html.APPLE_LOGIN_HTML
        self.assertIn("musickit", html.lower())
        self.assertIn("Apple ID", html)
        self.assertIn("/api/player/apple/complete", html)

    def test_player_disable_toggle_wires_settings_card_and_polling(self):
        html = light_gui.render_html()
        card = _section(html, "<h2>Player connections</h2>", 'id="playerSettingsMsg"')
        self.assertIn('id="setPlayerEnabled"', card)

        save = _section(html, "async function savePlayerSettings", "async function saveAudioSettings")
        self.assertIn("enabled:", save)
        self.assertIn("setPlayerEnabled", save)

        load = _section(html, "async function loadSettings()", "async function saveAiSettings")
        self.assertIn("applyPlayerEnabled(player.enabled);", load)

        poll = _section(html, "async function refreshPlayerStatus()", "async function playerCommand")
        self.assertIn("if (!playerEnabled) return null;", poll)

        # Deck hides and the 4s poller is gated behind applyPlayerEnabled.
        self.assertIn("document.querySelector('.player-deck')", html)
        self.assertIn("playerPollTimer = setInterval(refreshPlayerStatus, 4000);", html)
        self.assertIn("if (!playerEnabled) return;", html)

    def test_dynamic_scene_controls_are_visible(self):
        html = light_gui.render_html()

        for marker in (
            "Dynamic AI Scenes",
            "dynEngine",
            "dynComposition",
            "Generated mode is not a stock WLED effect",
            "Apply Dynamic Scene",
            "send('dynamic_scene'",
        ):
            self.assertIn(marker, html)

    def test_realtime_and_feedback_controls_are_visible(self):
        html = light_gui.render_html()

        for marker in (
            "Realtime Direct Mode",
            "send('realtime_start'",
            "send('realtime_stop'",
            "refreshRealtimeStatus()",
            "rtColors",
            "ember_rise",
            "parseColorList(",
            "That worked",
            "look_feedback",
            "look_memory_summary",
            "Show memory summary",
            "design_look",
            "Design unique look",
        ):
            self.assertIn(marker, html)

    def test_look_agent_controls_are_visible(self):
        html = light_gui.render_html()

        for marker in (
            "Look Agents",
            "agentColoristModel",
            "agentMotionModel",
            "agentCriticModel",
            "design_look",
            "Design unique look",
        ):
            self.assertIn(marker, html)

    def test_tv_page_pollers_have_timeouts(self):
        tv = light_gui_html.TV_AMBIENT_HTML

        self.assertIn("async function fetchJsonWithTimeout", tv)
        self.assertIn("fetchJsonWithTimeout('/api/state')", tv)
        self.assertIn("fetchJsonWithTimeout('/api/music-director')", tv)
        self.assertIn("fetchJsonWithTimeout('/api/tv-trivia')", tv)


SHARED_ELEMENT_IDS = (
    "musicTitle",
    "musicArtist",
    "musicGenre",
    "albumArt",
    "nowPlaying",
    "ledStrip",
    "lightInfo",
    "vizStatus",
    "waveformCanvas",
    "musicModeState",
    "musicModeBtn",
    "musicModeStopBtn",
    "micPipelineState",
    "songSourceState",
    "nextMatchState",
    "autoStatus",
    "smartSuggestions",
    "modelResponsesPane",
    "modelResponses",
    "visualizerHero",
)


class PlayerDisabledRenderTests(unittest.TestCase):
    """When audio_player.enabled is false the main page is the pre-player UI."""

    def test_disabled_render_omits_player_markup(self):
        html = light_gui.render_html(player_enabled=False)
        for marker in (
            'id="playerStage"',
            'id="playerLibrary"',
            'class="player-deck"',
            'id="appleAuthPanel"',
            'id="playerSearchInput"',
            'id="playerSource"',
        ):
            self.assertNotIn(marker, html)

    def test_disabled_render_restores_legacy_markup(self):
        html = light_gui.render_html(player_enabled=False)
        for marker in (
            'id="visualizerHero"',
            "Now Playing",
            'id="modelResponsesPane"',
            'id="musicModeState"',
            'id="musicTitle"',
            'id="albumArt"',
            'id="nowPlaying"',
            'id="musicModeBtn"',
        ):
            self.assertIn(marker, html)

    def test_disabled_render_keeps_minimal_player_settings_card(self):
        html = light_gui.render_html(player_enabled=False)
        self.assertIn("Onboard audio player", html)
        self.assertIn('id="setPlayerEnabled"', html)
        self.assertIn("savePlayerSettings(this)", html)
        self.assertNotIn('id="setYoutubeHost"', html)
        self.assertNotIn('id="setAppleDevToken"', html)

    def test_enabled_render_keeps_player_markup(self):
        html = light_gui.render_html()
        for marker in (
            'id="playerStage"',
            'id="playerLibrary"',
            'class="player-deck"',
            'id="appleAuthPanel"',
            'id="playerSearchInput"',
            'id="playerSource"',
        ):
            self.assertIn(marker, html)

    def test_shared_ids_present_in_both_renders(self):
        for enabled in (True, False):
            html = light_gui.render_html(player_enabled=enabled)
            for element_id in SHARED_ELEMENT_IDS:
                self.assertIn(
                    f'id="{element_id}"',
                    html,
                    f"{element_id} missing from player_enabled={enabled} render",
                )


if __name__ == "__main__":
    unittest.main()
