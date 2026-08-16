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
            "That worked",
            "look_feedback",
            "look_memory_summary",
            "Show memory summary",
        ):
            self.assertIn(marker, html)

    def test_tv_page_pollers_have_timeouts(self):
        tv = light_gui_html.TV_AMBIENT_HTML

        self.assertIn("async function fetchJsonWithTimeout", tv)
        self.assertIn("fetchJsonWithTimeout('/api/state')", tv)
        self.assertIn("fetchJsonWithTimeout('/api/music-director')", tv)
        self.assertIn("fetchJsonWithTimeout('/api/tv-trivia')", tv)


if __name__ == "__main__":
    unittest.main()
