"""Tests for music_director.py — mood matching, director thread, registry."""

from __future__ import annotations

import json
import time
import unittest
from unittest import mock
from unittest.mock import patch

import atmospheres
import music_director
import music_recognizer
import shows
from music_director import match_mood


# ---------------------------------------------------------------------------
# Fakes / helpers
# ---------------------------------------------------------------------------

class FakeFleet:
    """Duck-typed fleet: channels() + post_state() recording every call."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.posts: list[tuple[dict, str]] = []
        self._channels = {
            "far-left": ("left", 1),
            "middle-left": ("left", 0),
            "middle-right": ("right", 1),
            "far-right": ("right", 0),
        }

    def channels(self) -> dict:
        return dict(self._channels)

    def post_state(self, payload: dict, target: str = "all") -> dict:
        if self.fail:
            raise RuntimeError("controller unreachable")
        self.posts.append((payload, target))
        return {target: {"ok": True}}


def seg_posts(fleet_: FakeFleet) -> int:
    """Count posts carrying segment payloads (excludes transition pre-posts)."""
    return sum(1 for payload, _t in fleet_.posts if "seg" in payload)


def wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def track(artist: str, title: str, genre: str = "") -> dict:
    return {"artist": artist, "title": title, "genre": genre, "source": "mpris"}


# ---------------------------------------------------------------------------
# match_mood
# ---------------------------------------------------------------------------

class MatchMoodTests(unittest.TestCase):
    def test_edm_family(self):
        for text in ("edm festival mix", "Tech House set", "techno bunker",
                     "trance anthem", "dubstep wobble"):
            self.assertIs(match_mood(text), music_director.MOOD_LOOKS[0][1], text)

    def test_hiphop_family(self):
        for text in ("hip-hop classics", "Rap God", "trap mix", "drill beat"):
            self.assertIs(match_mood(text), music_director.MOOD_LOOKS[1][1], text)

    def test_rock_family(self):
        for text in ("classic rock", "Heavy Metal thunder", "punk riot"):
            self.assertIs(match_mood(text), music_director.MOOD_LOOKS[2][1], text)

    def test_pop_family(self):
        for text in ("pop hits", "K-POP stars", "disco fever"):
            self.assertIs(match_mood(text), music_director.MOOD_LOOKS[3][1], text)

    def test_rnb_soul_funk_family(self):
        for text in ("r&b slow jams", "Soul train", "FUNK night"):
            self.assertIs(match_mood(text), music_director.MOOD_LOOKS[4][1], text)

    def test_calm_family(self):
        for text in ("Jazz lounge", "Acoustic session", "CLASSICAL piano",
                     "ambient drones", "Chill beats", "lofi study"):
            self.assertIs(match_mood(text), music_director.MOOD_LOOKS[5][1], text)

    def test_latin_family(self):
        for text in ("latin party", "Reggaeton hit", "salsa night"):
            self.assertIs(match_mood(text), music_director.MOOD_LOOKS[6][1], text)

    def test_fallback_for_unmatched_text(self):
        self.assertIs(match_mood("qzx unknown artist - untitled"),
                      music_director.FALLBACK_LOOK)

    def test_case_insensitive(self):
        self.assertIs(match_mood("EDM BANGER"), match_mood("edm banger"))
        self.assertIs(match_mood("HeAvY MeTaL"), music_director.MOOD_LOOKS[2][1])

    def test_fallback_is_last_and_keywordless(self):
        self.assertEqual(music_director.MOOD_LOOKS[-1][0], ())
        self.assertIs(music_director.MOOD_LOOKS[-1][1], music_director.FALLBACK_LOOK)


# ---------------------------------------------------------------------------
# MOOD_LOOKS structural validity
# ---------------------------------------------------------------------------

# ♪ audio-reactive effect ids on AudioReactive WLED 16.0.1.
AUDIO_FX_IDS = {68, 132, 135, 136, 137, 139, 143, 144, 145, 155, 156, 157,
                158, 159, 175, 185}


class LookValidityTests(unittest.TestCase):
    def test_every_look_passes_shows_validation(self):
        for keywords, look in music_director.MOOD_LOOKS:
            steps = shows.validate_show({
                "steps": [{"look": look, "duration_s": 1}],
            })
            self.assertEqual(len(steps), 1, f"look for {keywords}")

    def test_atmosphere_looks_reference_real_atmospheres(self):
        for _keywords, look in music_director.MOOD_LOOKS:
            if "atmosphere" in look:
                self.assertIn(look["atmosphere"], atmospheres.ATMOSPHERES)

    def test_every_wall_look_uses_audio_reactive_fx(self):
        for keywords, look in music_director.MOOD_LOOKS:
            if "wall_mode" not in look:
                continue
            for key in ("fx", "fx_left", "fx_right"):
                if key in look:
                    self.assertIn(look[key], AUDIO_FX_IDS,
                                  f"{keywords}:{key}={look[key]} not ♪")

    def test_default_idle_atmosphere_exists(self):
        self.assertIn("candlelit", atmospheres.ATMOSPHERES)


# ---------------------------------------------------------------------------
# MusicDirector thread
# ---------------------------------------------------------------------------

class DirectorThreadTests(unittest.TestCase):
    def _start(self, fleet_: FakeFleet, mpris, poll_s: float = 0.05):
        patcher = mock.patch.object(music_recognizer, "now_playing_mpris", mpris)
        patcher.start()
        self.addCleanup(patcher.stop)
        director = music_director.MusicDirector(fleet_, poll_s=poll_s)
        director.start()
        self.addCleanup(lambda: (director.stop(), director.join(timeout=3)))
        return director

    def test_daemon_thread(self):
        director = music_director.MusicDirector(FakeFleet())
        self.assertTrue(director.daemon)

    def test_track_change_applies_exactly_one_look(self):
        fleet_ = FakeFleet()
        state = {"track": track("Darude", "Sandstorm", "edm")}
        self._start(fleet_, lambda: state["track"])
        # span across two controllers = 2 segment posts for one look
        self.assertTrue(wait_for(lambda: seg_posts(fleet_) == 2))

    def test_same_track_repeated_polls_apply_nothing_new(self):
        fleet_ = FakeFleet()
        self._start(fleet_, lambda: track("Darude", "Sandstorm", "edm"))
        self.assertTrue(wait_for(lambda: seg_posts(fleet_) == 2))
        time.sleep(0.25)  # several poll cycles at poll_s=0.05
        self.assertEqual(seg_posts(fleet_), 2)

    def test_new_track_applies_one_more_look(self):
        fleet_ = FakeFleet()
        state = {"track": track("Darude", "Sandstorm", "edm")}
        self._start(fleet_, lambda: state["track"])
        self.assertTrue(wait_for(lambda: seg_posts(fleet_) == 2))
        state["track"] = track("Metallica", "One", "metal")
        self.assertTrue(wait_for(lambda: seg_posts(fleet_) == 4))

    def test_pause_applies_idle_atmosphere_once(self):
        fleet_ = FakeFleet()
        state = {"track": track("Darude", "Sandstorm", "edm")}
        self._start(fleet_, lambda: state["track"])
        self.assertTrue(wait_for(lambda: seg_posts(fleet_) == 2))
        state["track"] = None  # music stopped
        # candlelit idle = one more wall_span = 2 more segment posts
        self.assertTrue(wait_for(lambda: seg_posts(fleet_) == 4))
        time.sleep(0.25)  # stays idle: no reapplication every cycle
        self.assertEqual(seg_posts(fleet_), 4)

    def test_resume_after_idle_applies_look_again(self):
        fleet_ = FakeFleet()
        state = {"track": track("Darude", "Sandstorm", "edm")}
        director = self._start(fleet_, lambda: state["track"])
        self.assertTrue(wait_for(lambda: seg_posts(fleet_) == 2))
        state["track"] = None
        self.assertTrue(wait_for(lambda: director.current_track is None))
        self.assertTrue(wait_for(lambda: seg_posts(fleet_) == 4))
        state["track"] = track("Darude", "Sandstorm", "edm")  # same song again
        self.assertTrue(wait_for(lambda: seg_posts(fleet_) == 6))

    def test_status_fields_track_and_mood(self):
        fleet_ = FakeFleet()
        director = self._start(fleet_, lambda: track("Darude", "Sandstorm", "edm"))
        self.assertTrue(wait_for(lambda: director.current_mood is not None))
        self.assertEqual(director.current_track, "Darude — Sandstorm")
        self.assertEqual(director.current_mood, "edm")

    def test_bad_post_is_swallowed_and_thread_survives(self):
        fleet_ = FakeFleet(fail=True)
        director = self._start(fleet_, lambda: track("Darude", "Sandstorm", "edm"))
        time.sleep(0.2)
        self.assertTrue(director.is_alive())
        self.assertTrue(director.is_running())

    def test_mpris_exception_is_treated_as_idle(self):
        fleet_ = FakeFleet()

        def boom():
            raise RuntimeError("playerctl exploded")

        director = self._start(fleet_, boom)
        # two failed polls -> idle strikes -> candlelit applied once
        self.assertTrue(wait_for(lambda: seg_posts(fleet_) == 2))
        self.assertTrue(director.is_running())

    def test_stop_is_cooperative_during_long_poll(self):
        fleet_ = FakeFleet()
        patcher = mock.patch.object(music_recognizer, "now_playing_mpris",
                                    lambda: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        director = music_director.MusicDirector(fleet_, poll_s=60.0)
        director.start()
        self.assertTrue(wait_for(lambda: director.is_alive()))
        director.stop()
        director.join(timeout=3)
        self.assertFalse(director.is_alive(), "stop() must interrupt the poll sleep")


# ---------------------------------------------------------------------------
# Module-level registry
# ---------------------------------------------------------------------------

class DirectorRegistryTests(unittest.TestCase):
    def tearDown(self):
        try:
            music_director.stop_director()
        except Exception:
            pass

    def test_status_when_idle(self):
        music_director.stop_director()
        status = music_director.director_status()
        self.assertEqual(status, {"running": False, "track": None, "mood": None})

    def test_stop_without_start_is_a_message(self):
        music_director.stop_director()
        self.assertEqual(music_director.stop_director(), "No music director is running.")

    def test_start_status_stop_cycle(self):
        fleet_ = FakeFleet()
        with mock.patch.object(music_recognizer, "now_playing_mpris",
                               lambda: track("Darude", "Sandstorm", "edm")):
            message = music_director.start_director(fleet_, poll_s=0.05)
            self.assertIsInstance(message, str)
            self.assertTrue(wait_for(lambda: seg_posts(fleet_) == 2))
            status = music_director.director_status()
            self.assertTrue(status["running"])
            self.assertEqual(status["track"], "Darude — Sandstorm")
            self.assertEqual(status["mood"], "edm")
            stop_message = music_director.stop_director()
            self.assertEqual(stop_message, "Music director stopped.")
            self.assertTrue(wait_for(
                lambda: not music_director.director_status()["running"]))

    def test_starting_replaces_running_director(self):
        fleet_ = FakeFleet()
        with mock.patch.object(music_recognizer, "now_playing_mpris",
                               lambda: None):
            music_director.start_director(fleet_, poll_s=0.05)
            first = music_director._current_director
            music_director.start_director(fleet_, poll_s=0.05)
            second = music_director._current_director
            self.assertIsNot(first, second)
            self.assertTrue(wait_for(lambda: not first.is_alive()))
            self.assertTrue(second.is_running())


if __name__ == "__main__":
    unittest.main()


class AiMoodClassifyTests(unittest.TestCase):
    SETTINGS = {"base_url": "https://example.test/v1", "model": "m", "api_key_env": "NO_SUCH_KEY"}

    def _mock_response(self, text):
        return {"choices": [{"message": {"content": text}}]}

    def test_label_returned(self):
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = \
                json.dumps(self._mock_response("edm")).encode()
            assert music_director.classify_track_ai("NGHTMRE", "FEELING GUD", self.SETTINGS) == "edm"

    def test_label_extracted_from_noisy_answer(self):
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = \
                json.dumps(self._mock_response("This is Hip-Hop.")).encode()
            assert music_director.classify_track_ai("Lil Durk", "All My Life", self.SETTINGS) == "hip-hop"

    def test_failure_returns_none(self):
        with patch("urllib.request.urlopen", side_effect=OSError("down")):
            assert music_director.classify_track_ai("A", "B", self.SETTINGS) is None

    def test_missing_base_url_returns_none(self):
        """A settings dict without base_url must not raise KeyError out of classify."""
        assert music_director.classify_track_ai("A", "B", {"model": "m"}) is None

    def test_ai_cache_is_capped(self):
        fleet_ = FakeFleet()
        director = music_director.MusicDirector(fleet_, poll_s=60, ai_settings=self.SETTINGS)
        director._ai_cache.update({(f"artist{i}", f"title{i}"): None for i in range(600)})
        with patch.object(music_director, "classify_track_ai", return_value=None):
            director._handle_track({"artist": "Brand New", "title": "Track"})
        assert len(director._ai_cache) <= 513

    def test_director_upgrades_default_mood_via_ai(self):
        fleet_ = FakeFleet()
        director = music_director.MusicDirector(fleet_, poll_s=60, ai_settings=self.SETTINGS)
        with patch.object(music_director, "classify_track_ai", return_value="edm") as mock_classify:
            director._handle_track({"artist": "NGHTMRE", "title": "FEELING GUD"})
            assert director.current_mood == "edm"
            # cached: second handle of same key must not re-classify
            director._handle_track({"artist": "NGHTMRE", "title": "FEELING GUD"})
            assert mock_classify.call_count == 1
            fx_ids = {seg["fx"] for payload, _t in fleet_.posts if "seg" in payload for seg in payload["seg"]}
            assert fx_ids == {139}  # EDM look = GEQ

    def test_director_without_ai_settings_keeps_default(self):
        fleet_ = FakeFleet()
        director = music_director.MusicDirector(fleet_, poll_s=60)
        director._handle_track({"artist": "NGHTMRE", "title": "FEELING GUD"})
        assert director.current_mood == "default"


class AiShowComposerTests(unittest.TestCase):
    SETTINGS = {"base_url": "https://example.test/v1", "model": "m", "api_key_env": "NO_SUCH_KEY"}

    def test_composer_runs_chat_and_respects_generation(self):
        fleet_ = FakeFleet()
        director = music_director.MusicDirector(fleet_, poll_s=60, ai_settings=self.SETTINGS)
        with patch("ai_chat.run_chat", return_value={"text": "fire show", "log": ["start_show"], "rounds": 2}) as mock_chat:
            director._handle_track({"artist": "Sullivan King", "title": "Rumble"})
            director._composer_thread.join(timeout=5)
            assert mock_chat.call_count == 1
            prompt = mock_chat.call_args[0][1]
            assert "Rumble" in prompt and "Sullivan King" in prompt
            assert mock_chat.call_args.kwargs["timeout"] == 15.0

    def test_composer_skipped_when_one_already_running(self):
        fleet_ = FakeFleet()
        director = music_director.MusicDirector(fleet_, poll_s=60, ai_settings=self.SETTINGS)

        def slow_chat(*args, **kwargs):
            time.sleep(0.3)
            return {"text": "x", "log": [], "rounds": 1}

        with patch("ai_chat.run_chat", side_effect=slow_chat) as mock_chat:
            director._handle_track({"artist": "A", "title": "One"})
            director._handle_track({"artist": "B", "title": "Two"})  # composer busy -> skipped
            if director._composer_thread:
                director._composer_thread.join(timeout=5)
            assert mock_chat.call_count == 1

    def test_stale_generation_discards_result(self):
        fleet_ = FakeFleet()
        director = music_director.MusicDirector(fleet_, poll_s=60, ai_settings=self.SETTINGS)
        with patch("ai_chat.run_chat", return_value={"text": "x", "log": [], "rounds": 1}):
            director._compose_ai_show("A", "Old", generation=999)  # != current generation
        # no crash, nothing applied beyond the initial heuristic (which never ran)

    def test_stale_show_stops_show_and_reapplies_current_look(self):
        """A show composed for a stale generation must be undone, not left live."""
        fleet_ = FakeFleet()
        director = music_director.MusicDirector(fleet_, poll_s=60)  # no auto-composer
        director._handle_track({"artist": "Darude", "title": "Sandstorm", "genre": "edm"})
        seg_before = seg_posts(fleet_)
        generation = director._generation

        def fake_chat(*args, **kwargs):
            director._generation += 1  # track moved on while the chat was running
            return {"text": "x", "log": ["start_show"], "rounds": 1}

        with patch("ai_chat.run_chat", side_effect=fake_chat), \
                patch.object(music_director.shows, "stop_show", return_value="stopped") as mock_stop:
            director._compose_ai_show("Darude", "Sandstorm", generation=generation)
        assert mock_stop.call_count == 1
        # the current track's heuristic look is re-applied (EDM span = 2 seg posts)
        assert seg_posts(fleet_) == seg_before + 2

    def test_track_change_stops_running_show(self):
        fleet_ = FakeFleet()
        director = music_director.MusicDirector(fleet_, poll_s=60)
        with patch.object(music_director.shows, "stop_show", return_value="stopped") as mock_stop:
            director._handle_track({"artist": "A", "title": "One"})
            assert mock_stop.call_count == 1
