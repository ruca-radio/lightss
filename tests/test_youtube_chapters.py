"""Tests for youtube_chapters.py — no network, no real yt-dlp process.

Every yt-dlp interaction goes through an injected ``runner`` (or a patched
``fetch_for_description``); the clock is injectable so the 5 s stability gate and
cache TTLs are exercised instantly.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import youtube_chapters

LIVE_DESCRIPTION = "80's Dance Party Mix - [Wukileaks], Wuki, null"
LIVE_KEY = "80s dance party mix wuki null"

MIX_CHAPTERS = [
    {"start_s": 0, "end_s": 171, "title": "Prince - 1999 [Wukileak].)"},
    {"start_s": 171, "end_s": 345, "title": "Talking Heads \u2013 Once in a Lifetime"},
    {"start_s": 345, "end_s": 520, "title": "Michael Jackson - Billie Jean"},
    {"start_s": 520, "end_s": 700, "title": "Madonna - Like a Prayer"},
]


def live_info(chapters=None) -> dict:
    return {
        "video_id": "DBuyeSmPXd0",
        "title": "80's Dance Party Mix - [Wukileaks]",
        "channel": "Wuki",
        "duration_s": 2883,
        "chapters": copy.deepcopy(MIX_CHAPTERS if chapters is None else chapters),
        "fetched_at": youtube_chapters._now(),
    }


def youtube_observation(
    description: str = LIVE_DESCRIPTION,
    position_s=200.0,
    app: str = "com.amazon.firetv.youtube",
) -> dict:
    return {
        "connected": True,
        "awake": True,
        "foreground_app": app,
        "activity_hint": "unknown",
        "media_session": {
            "package": app,
            "active": True,
            "state": 3,
            "description": description,
            "position_s": position_s,
        },
    }


def wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.002)
    return False


class YouTubeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="lightss-yt-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cache = os.path.join(self.tmp, "youtube_chapters.json")
        self._reset_state()
        self._clock = [0.0]
        self._original_monotonic = youtube_chapters._monotonic
        youtube_chapters._monotonic = lambda: self._clock[0]
        self.addCleanup(self._restore_state)

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _reset_state() -> None:
        with youtube_chapters._stability_lock:
            youtube_chapters._stability_entries.clear()
            youtube_chapters._last_description = None
        with youtube_chapters._inflight_lock:
            youtube_chapters._inflight.clear()
        with youtube_chapters._cache_memory_lock:
            youtube_chapters._cache_memory.update(
                path=None, mtime_ns=None, data=youtube_chapters._empty_cache()
            )

    def _restore_state(self) -> None:
        youtube_chapters._monotonic = self._original_monotonic
        self._reset_state()

    def advance(self, seconds: float) -> None:
        self._clock[0] += seconds

    def seed(self, description: str = LIVE_DESCRIPTION, info: dict | None = None) -> None:
        youtube_chapters._store_entry(
            description,
            copy.deepcopy(live_info() if info is None else info),
            cache_path=self.cache,
        )

    def write_cache(self, entries: dict) -> None:
        youtube_chapters._atomic_write_cache(
            Path(self.cache), {"version": 1, "entries": entries}
        )

    def cache_entries(self) -> dict:
        return youtube_chapters._read_cache_json(Path(self.cache))["entries"]


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

class TextHelperTests(unittest.TestCase):
    def test_normalize_title(self):
        self.assertEqual(
            youtube_chapters.normalize_title("80's Dance Party Mix - [Wukileaks]"),
            "80s dance party mix",
        )
        self.assertEqual(youtube_chapters.normalize_title("Caf\u00e9 del Mar (Live)"), "cafe del mar")
        self.assertEqual(youtube_chapters.normalize_title("  A&B  "), "a and b")
        self.assertEqual(youtube_chapters.normalize_title(None), "")

    def test_split_live_media_description(self):
        parts = youtube_chapters.split_media_description(LIVE_DESCRIPTION)
        self.assertEqual(parts["title"], "80's Dance Party Mix - [Wukileaks]")
        self.assertEqual(parts["artist"], "Wuki")
        self.assertIsNone(parts["album"])

    def test_split_keeps_commas_in_title(self):
        parts = youtube_chapters.split_media_description("Song, Part 2, Artist, Album")
        self.assertEqual(parts["title"], "Song, Part 2")
        self.assertEqual(parts["artist"], "Artist")
        self.assertEqual(parts["album"], "Album")

    def test_split_drops_trailing_empty_and_null_parts(self):
        self.assertEqual(
            youtube_chapters.split_media_description("Title, Artist, null"),
            {"title": "Title", "artist": "Artist", "album": None},
        )
        self.assertEqual(
            youtube_chapters.split_media_description("Title, null, null, null"),
            {"title": "Title", "artist": None, "album": None},
        )
        self.assertEqual(
            youtube_chapters.split_media_description("null, null"),
            {"title": "", "artist": None, "album": None},
        )

    def test_title_match_score_accepts_the_mix_and_rejects_music_videos(self):
        self.assertGreaterEqual(
            youtube_chapters.title_match_score(
                "80's Dance Party Mix - [Wukileaks] [Official Video]", LIVE_DESCRIPTION
            ),
            0.6,
        )
        self.assertLess(
            youtube_chapters.title_match_score("Prince - 1999", LIVE_DESCRIPTION), 0.6
        )
        self.assertLess(
            youtube_chapters.title_match_score(
                "Best Podcast Episode 42", LIVE_DESCRIPTION
            ),
            0.6,
        )
        self.assertEqual(youtube_chapters.title_match_score("", LIVE_DESCRIPTION), 0.0)
        self.assertEqual(youtube_chapters.title_match_score("Mix", ""), 0.0)


# ---------------------------------------------------------------------------
# Chapter parsing / song parsing / music-mix heuristic
# ---------------------------------------------------------------------------

class ChapterParsingTests(unittest.TestCase):
    def test_end_fills_from_next_start_then_duration(self):
        payload = {
            "duration": 2883,
            "chapters": [
                {"start_time": 0, "end_time": 171, "title": "Prince - 1999"},
                {"start_time": 171, "title": "Talking Heads - Once in a Lifetime"},
            ],
        }
        chapters = youtube_chapters.parse_chapters(payload)
        self.assertEqual(
            chapters,
            [
                {"start_s": 0, "end_s": 171, "title": "Prince - 1999"},
                {"start_s": 171, "end_s": 2883, "title": "Talking Heads - Once in a Lifetime"},
            ],
        )

    def test_authored_end_is_kept(self):
        chapters = youtube_chapters.parse_chapters(
            {"duration": 1000, "chapters": [{"start_time": 10, "end_time": 90, "title": "A - B"}]}
        )
        self.assertEqual(chapters[0]["end_s"], 90)

    def test_last_chapter_without_duration_has_no_end(self):
        chapters = youtube_chapters.parse_chapters(
            {"chapters": [{"start_time": 5, "title": "A - B"}]}
        )
        self.assertEqual(chapters, [{"start_s": 5, "end_s": None, "title": "A - B"}])

    def test_malformed_payloads_are_ignored(self):
        self.assertEqual(youtube_chapters.parse_chapters(None), [])
        self.assertEqual(youtube_chapters.parse_chapters({}), [])
        self.assertEqual(youtube_chapters.parse_chapters({"chapters": "nope"}), [])
        self.assertEqual(
            youtube_chapters.parse_chapters({"chapters": [{"title": "no start"}, None]}), []
        )

    def test_song_from_chapter_strips_tags_and_splits(self):
        self.assertEqual(
            youtube_chapters.song_from_chapter("Prince - 1999 [Wukileak].)"),
            {"artist": "Prince", "title": "1999"},
        )
        self.assertEqual(
            youtube_chapters.song_from_chapter("Talking Heads \u2013 Once in a Lifetime"),
            {"artist": "Talking Heads", "title": "Once in a Lifetime"},
        )
        self.assertEqual(
            youtube_chapters.song_from_chapter("Michael Jackson - Billie Jean [Official Video]"),
            {"artist": "Michael Jackson", "title": "Billie Jean"},
        )
        self.assertEqual(
            youtube_chapters.song_from_chapter("Intro"), {"artist": None, "title": "Intro"}
        )
        self.assertEqual(
            youtube_chapters.song_from_chapter(""), {"artist": None, "title": ""}
        )


class MusicMixTests(unittest.TestCase):
    def test_true_for_artist_song_chapters(self):
        self.assertTrue(youtube_chapters.looks_like_music_mix(MIX_CHAPTERS))

    def test_false_without_artist_songs(self):
        chapters = [
            {"start_s": 0, "end_s": 300, "title": "Intro"},
            {"start_s": 300, "end_s": 600, "title": "The Story"},
            {"start_s": 600, "end_s": 900, "title": "Conclusion"},
        ]
        self.assertFalse(youtube_chapters.looks_like_music_mix(chapters))

    def test_false_for_too_few_chapters(self):
        self.assertFalse(youtube_chapters.looks_like_music_mix(MIX_CHAPTERS[:2]))
        self.assertFalse(youtube_chapters.looks_like_music_mix([]))

    def test_false_for_long_tv_style_chapters(self):
        chapters = [
            {"start_s": 0, "end_s": 1200, "title": "Part 1 - Beginning"},
            {"start_s": 1200, "end_s": 2400, "title": "Part 2 - Middle"},
            {"start_s": 2400, "end_s": 3600, "title": "Part 3 - End"},
        ]
        self.assertFalse(youtube_chapters.looks_like_music_mix(chapters))


class CurrentChapterTests(unittest.TestCase):
    def test_first_chapter_and_track(self):
        current = youtube_chapters.current_chapter(MIX_CHAPTERS, 0)
        self.assertEqual(current["index"], 0)
        self.assertEqual(current["total"], 4)
        self.assertEqual(current["start_s"], 0)
        self.assertEqual(current["end_s"], 171)
        self.assertEqual(current["artist"], "Prince")
        self.assertEqual(current["title"], "1999")
        self.assertEqual(current["track"], "Prince \u2014 1999")

    def test_middle_chapter_maps_by_position(self):
        current = youtube_chapters.current_chapter(MIX_CHAPTERS, 200)
        self.assertEqual(current["index"], 1)
        self.assertEqual(current["title"], "Once in a Lifetime")
        self.assertEqual(current["artist"], "Talking Heads")
        self.assertEqual(current["track"], "Talking Heads \u2014 Once in a Lifetime")

    def test_last_chapter_and_past_end_clamps(self):
        self.assertEqual(youtube_chapters.current_chapter(MIX_CHAPTERS, 520)["index"], 3)
        self.assertEqual(youtube_chapters.current_chapter(MIX_CHAPTERS, 699)["index"], 3)
        self.assertEqual(youtube_chapters.current_chapter(MIX_CHAPTERS, 5000)["index"], 3)

    def test_position_before_first_start_returns_none(self):
        chapters = [{"start_s": 50, "end_s": 100, "title": "A - B"}]
        self.assertIsNone(youtube_chapters.current_chapter(chapters, 10))

    def test_missing_end_times_use_the_next_start(self):
        chapters = [{"start_s": 0, "title": "A - B"}, {"start_s": 100, "title": "C - D"}]
        self.assertEqual(youtube_chapters.current_chapter(chapters, 50)["index"], 0)
        self.assertEqual(youtube_chapters.current_chapter(chapters, 150)["index"], 1)

    def test_invalid_inputs(self):
        self.assertIsNone(youtube_chapters.current_chapter([], 10))
        self.assertIsNone(youtube_chapters.current_chapter(MIX_CHAPTERS, None))
        self.assertIsNone(youtube_chapters.current_chapter(MIX_CHAPTERS, "nope"))
        self.assertIsNone(youtube_chapters.current_chapter(MIX_CHAPTERS, float("nan")))


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

class CacheTests(YouTubeTestCase):
    def test_constants(self):
        self.assertEqual(youtube_chapters.CACHE_TTL_S, 30 * 24 * 3600)
        self.assertEqual(youtube_chapters.NEGATIVE_TTL_S, 6 * 3600)
        self.assertEqual(youtube_chapters.SEARCH_RESULTS, 3)
        self.assertIn("com.amazon.firetv.youtube", youtube_chapters.YOUTUBE_APPS)
        self.assertIn("com.google.android.youtube.tv", youtube_chapters.YOUTUBE_APPS)
        self.assertIn("com.google.android.apps.youtube.music", youtube_chapters.YOUTUBE_APPS)

    def test_roundtrip_and_file_mode(self):
        info = live_info()
        self.seed(info=info)
        self.assertEqual(stat.S_IMODE(os.stat(self.cache).st_mode), 0o600)
        cached = youtube_chapters.resolve_cached(
            LIVE_DESCRIPTION, refresh=False, cache_path=self.cache
        )
        self.assertEqual(cached, info)
        self.assertEqual(list(self.cache_entries()), [LIVE_KEY])

    def test_key_is_normalized_description(self):
        self.seed()
        variant = "80'S DANCE PARTY MIX - [WUKILEAKS],   WUKI, NULL"
        self.assertIsNotNone(
            youtube_chapters.resolve_cached(variant, refresh=False, cache_path=self.cache)
        )

    def test_positive_entry_expires(self):
        now = youtube_chapters._now()
        self.write_cache(
            {
                LIVE_KEY: {
                    "status": "ok",
                    "fetched_at": now - youtube_chapters.CACHE_TTL_S - 10,
                    "info": live_info(),
                }
            }
        )
        self.assertIsNone(
            youtube_chapters.resolve_cached(
                LIVE_DESCRIPTION, refresh=False, cache_path=self.cache
            )
        )
        with mock.patch.object(
            youtube_chapters, "_start_background_resolve", return_value=True
        ) as start:
            self.assertIsNone(
                youtube_chapters.resolve_cached(
                    LIVE_DESCRIPTION, refresh=True, cache_path=self.cache
                )
            )
        start.assert_called_once()

    def test_fresh_negative_entry_blocks_refresh(self):
        now = youtube_chapters._now()
        self.write_cache(
            {
                LIVE_KEY: {
                    "status": "negative",
                    "fetched_at": now - 60,
                    "description": LIVE_DESCRIPTION,
                }
            }
        )
        with mock.patch.object(
            youtube_chapters, "_start_background_resolve", return_value=True
        ) as start:
            self.assertIsNone(
                youtube_chapters.resolve_cached(
                    LIVE_DESCRIPTION, refresh=True, cache_path=self.cache
                )
            )
        start.assert_not_called()
        self.assertIsNone(youtube_chapters.resolve_cached(
            LIVE_DESCRIPTION, refresh=False, cache_path=self.cache
        ))

    def test_stale_negative_entry_retries_after_six_hours(self):
        now = youtube_chapters._now()
        self.write_cache(
            {
                LIVE_KEY: {
                    "status": "negative",
                    "fetched_at": now - youtube_chapters.NEGATIVE_TTL_S - 1,
                    "description": LIVE_DESCRIPTION,
                }
            }
        )
        with mock.patch.object(
            youtube_chapters, "_start_background_resolve", return_value=True
        ) as start:
            self.assertIsNone(
                youtube_chapters.resolve_cached(
                    LIVE_DESCRIPTION, refresh=True, cache_path=self.cache
                )
            )
        start.assert_called_once()

    def test_refresh_false_never_starts_a_thread(self):
        with mock.patch.object(
            youtube_chapters, "_start_background_resolve", return_value=True
        ) as start:
            self.assertIsNone(
                youtube_chapters.resolve_cached(
                    LIVE_DESCRIPTION, refresh=False, cache_path=self.cache
                )
            )
        start.assert_not_called()
        self.assertFalse(youtube_chapters._inflight)

    def test_missing_cache_file_is_safe(self):
        self.assertIsNone(
            youtube_chapters.resolve_cached(
                LIVE_DESCRIPTION, refresh=False, cache_path=self.cache
            )
        )

    def test_cache_file_is_read_once_per_on_disk_change(self):
        youtube_chapters._atomic_write_cache(
            Path(self.cache),
            {"version": 1, "entries": {LIVE_KEY: {
                "status": "ok", "fetched_at": youtube_chapters._now(), "info": live_info()}}},
        )
        real_reader = youtube_chapters._read_cache_json
        calls: list[Path] = []

        def counting_reader(path):
            calls.append(path)
            return real_reader(path)

        with mock.patch.object(youtube_chapters, "_read_cache_json", side_effect=counting_reader):
            first = youtube_chapters.resolve_cached(
                LIVE_DESCRIPTION, refresh=False, cache_path=self.cache
            )
            second = youtube_chapters.resolve_cached(
                LIVE_DESCRIPTION, refresh=False, cache_path=self.cache
            )
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(len(calls), 1)
        # A changed file (new mtime) is re-read exactly once.
        now_ns = time.time_ns()
        os.utime(self.cache, ns=(now_ns, now_ns + 1_000_000))
        with mock.patch.object(youtube_chapters, "_read_cache_json", side_effect=counting_reader):
            third = youtube_chapters.resolve_cached(
                LIVE_DESCRIPTION, refresh=False, cache_path=self.cache
            )
        self.assertIsNotNone(third)
        self.assertEqual(len(calls), 2)


# ---------------------------------------------------------------------------
# fetch_for_description with an injected runner
# ---------------------------------------------------------------------------

SEARCH_STDOUT = "\n".join(
    [
        "wrongid\t180\tRandom Channel\tPrince - 1999 (Official Video)",
        "DBuyeSmPXd0\t2883\tWuki\t80's Dance Party Mix - [Wukileaks] [Official Video]",
        "otherid\t4000\tOther Channel\tSome Other Mix",
    ]
)

DUMP_STDOUT = json.dumps(
    {
        "id": "DBuyeSmPXd0",
        "title": "80's Dance Party Mix - [Wukileaks]",
        "channel": "Wuki",
        "duration": 2883,
        "chapters": [
            {"start_time": 0, "end_time": 171, "title": "Prince - 1999 [Wukileak].)"},
            {"start_time": 171, "end_time": 345, "title": "Talking Heads - Once in a Lifetime"},
        ],
    }
)


class FetchTests(unittest.TestCase):
    def test_fetch_searches_then_fetches_chapters(self):
        calls: list[list[str]] = []

        def runner(args, timeout):
            calls.append(list(args))
            self.assertEqual(timeout, 60.0)
            if any(arg.startswith("ytsearch") for arg in args):
                return SEARCH_STDOUT
            return DUMP_STDOUT

        info = youtube_chapters.fetch_for_description(LIVE_DESCRIPTION, runner=runner)
        self.assertEqual(info["video_id"], "DBuyeSmPXd0")
        self.assertEqual(info["channel"], "Wuki")
        self.assertEqual(info["duration_s"], 2883)
        self.assertEqual(len(info["chapters"]), 2)
        self.assertEqual(info["chapters"][0]["title"], "Prince - 1999 [Wukileak].)")
        self.assertIsInstance(info["fetched_at"], float)
        self.assertEqual(len(calls), 2)
        self.assertIn(f"ytsearch{youtube_chapters.SEARCH_RESULTS}:", calls[0][-1])
        self.assertIn("--flat-playlist", calls[0])
        self.assertIn("--print", calls[0])
        self.assertIn("--dump-single-json", calls[1])
        self.assertTrue(calls[1][-1].endswith("watch?v=DBuyeSmPXd0"))

    def test_fetch_rejects_unrelated_results(self):
        calls: list[list[str]] = []

        def runner(args, timeout):
            calls.append(list(args))
            return "wrongid\t180\tRandom Channel\tPrince - 1999 (Official Video)"

        self.assertIsNone(
            youtube_chapters.fetch_for_description(LIVE_DESCRIPTION, runner=runner)
        )
        self.assertEqual(len(calls), 1)  # no dump fetch for a rejected search

    def test_fetch_failures_return_none(self):
        def raising(args, timeout):
            raise RuntimeError("yt-dlp is unavailable")

        self.assertIsNone(
            youtube_chapters.fetch_for_description(LIVE_DESCRIPTION, runner=raising)
        )
        self.assertIsNone(
            youtube_chapters.fetch_for_description(
                LIVE_DESCRIPTION, runner=lambda args, timeout: "{not json"
            )
        )
        self.assertIsNone(youtube_chapters.fetch_for_description(""))
        self.assertIsNone(youtube_chapters.fetch_for_description(None))

    def test_default_runner_without_binary_returns_none(self):
        with mock.patch.object(youtube_chapters, "_binary", return_value=""):
            self.assertIsNone(youtube_chapters.fetch_for_description(LIVE_DESCRIPTION))


# ---------------------------------------------------------------------------
# Background resolution
# ---------------------------------------------------------------------------

class BackgroundTests(YouTubeTestCase):
    def test_background_resolve_caches_with_injected_fetcher(self):
        fetched: list[str] = []
        stored: list[dict] = []

        def fake_fetch(description, **kwargs):
            fetched.append(description)
            info = live_info()
            stored.append(info)
            return copy.deepcopy(info)

        with mock.patch.object(youtube_chapters, "fetch_for_description", side_effect=fake_fetch):
            self.assertTrue(
                youtube_chapters._start_background_resolve(
                    LIVE_DESCRIPTION, cache_path=self.cache
                )
            )
            self.assertTrue(wait_for(lambda: not youtube_chapters._inflight))
        self.assertEqual(fetched, [LIVE_DESCRIPTION])
        self.assertEqual(
            youtube_chapters.resolve_cached(
                LIVE_DESCRIPTION, refresh=False, cache_path=self.cache
            ),
            stored[0],
        )

    def test_only_one_resolve_in_flight_process_wide(self):
        release = threading.Event()
        entered = threading.Event()
        fetched: list[str] = []

        def blocking_fetch(description, **kwargs):
            fetched.append(description)
            entered.set()
            release.wait(5)
            return copy.deepcopy(live_info())

        with mock.patch.object(
            youtube_chapters, "fetch_for_description", side_effect=blocking_fetch
        ):
            self.assertTrue(
                youtube_chapters._start_background_resolve(
                    LIVE_DESCRIPTION, cache_path=self.cache
                )
            )
            self.assertTrue(entered.wait(2))
            self.assertFalse(
                youtube_chapters._start_background_resolve(
                    "Another Mix - Artist", cache_path=self.cache
                )
            )
            self.assertEqual(len(fetched), 1)
            release.set()
            self.assertTrue(wait_for(lambda: not youtube_chapters._inflight))
            self.assertTrue(
                youtube_chapters._start_background_resolve(
                    "Another Mix - Artist", cache_path=self.cache
                )
            )
            self.assertTrue(wait_for(lambda: not youtube_chapters._inflight))

    def test_background_failure_is_cached_negative(self):
        with mock.patch.object(
            youtube_chapters, "fetch_for_description", side_effect=RuntimeError("blocked")
        ):
            self.assertTrue(
                youtube_chapters._start_background_resolve(
                    LIVE_DESCRIPTION, cache_path=self.cache
                )
            )
            self.assertTrue(wait_for(lambda: not youtube_chapters._inflight))
        entries = self.cache_entries()
        self.assertEqual(entries[LIVE_KEY]["status"], "negative")
        self.assertIsNone(
            youtube_chapters.resolve_cached(
                LIVE_DESCRIPTION, refresh=False, cache_path=self.cache
            )
        )


# ---------------------------------------------------------------------------
# Observation enrichment
# ---------------------------------------------------------------------------

class EnrichTests(YouTubeTestCase):
    def test_attaches_fields_for_youtube_with_cache_hit(self):
        self.seed()
        observation = youtube_observation(position_s=200)
        before = copy.deepcopy(observation)
        enriched = youtube_chapters.enrich_observation(
            observation, cache_path=self.cache
        )
        self.assertIsNot(enriched, observation)
        self.assertEqual(observation, before)  # caller's dict untouched
        session = enriched["media_session"]
        self.assertEqual(
            session["video"],
            {
                "video_id": "DBuyeSmPXd0",
                "title": "80's Dance Party Mix - [Wukileaks]",
                "channel": "Wuki",
                "duration_s": 2883,
            },
        )
        self.assertEqual(len(session["chapters"]), 4)
        self.assertEqual(session["current_chapter"]["index"], 1)
        self.assertEqual(session["current_chapter"]["track"], "Talking Heads \u2014 Once in a Lifetime")
        self.assertTrue(session["music_mix"])
        # smart_director slices upcoming chapters with this 0-based index.
        self.assertEqual(
            session["chapters"][session["current_chapter"]["index"]]["start_s"], 171
        )
        self.assertFalse(youtube_chapters._inflight)

    def test_cached_item_attaches_immediately_without_stability_window(self):
        self.seed()
        enriched = youtube_chapters.enrich_observation(
            youtube_observation(), cache_path=self.cache
        )
        self.assertIn("video", enriched["media_session"])
        self.assertEqual(enriched["media_session"]["current_chapter"]["index"], 1)
        self.assertFalse(youtube_chapters._inflight)

    def test_non_youtube_apps_are_untouched(self):
        self.seed()
        observation = youtube_observation(app="com.netflix.ninja")
        self.assertIs(
            youtube_chapters.enrich_observation(observation, cache_path=self.cache),
            observation,
        )

    def test_missing_session_or_description_is_untouched(self):
        self.seed()
        for observation in (
            {"foreground_app": "com.amazon.firetv.youtube"},
            {"foreground_app": "com.amazon.firetv.youtube", "media_session": {}},
            {"foreground_app": "com.amazon.firetv.youtube",
             "media_session": {"description": "   "}},
            {"foreground_app": "com.amazon.firetv.youtube", "media_session": None},
            "not-a-dict",
        ):
            self.assertIs(
                youtube_chapters.enrich_observation(observation, cache_path=self.cache),
                observation,
            )

    def test_chapters_are_capped_at_200(self):
        chapters = [
            {"start_s": index * 60, "end_s": (index + 1) * 60,
             "title": f"Artist {index} - Song {index}"}
            for index in range(250)
        ]
        self.seed(info=live_info(chapters))
        enriched = youtube_chapters.enrich_observation(
            youtube_observation(position_s=10), cache_path=self.cache
        )
        self.assertEqual(len(enriched["media_session"]["chapters"]), 200)

    def test_missing_position_still_attaches_video(self):
        self.seed()
        observation = youtube_observation()
        del observation["media_session"]["position_s"]
        enriched = youtube_chapters.enrich_observation(observation, cache_path=self.cache)
        session = enriched["media_session"]
        self.assertIsNone(session["current_chapter"])
        self.assertTrue(session["music_mix"])

    def test_youtube_music_package_is_recognized(self):
        self.seed()
        enriched = youtube_chapters.enrich_observation(
            youtube_observation(app="com.google.android.youtube.tvmusic"),
            cache_path=self.cache,
        )
        self.assertIn("video", enriched["media_session"])

    def test_cache_miss_is_cheap_and_leaves_observation_unchanged(self):
        def unexpected(*args, **kwargs):
            raise AssertionError("no fetch should happen before the stability window")

        observation = youtube_observation()
        with mock.patch.object(
            youtube_chapters, "fetch_for_description", side_effect=unexpected
        ):
            self.assertIs(
                youtube_chapters.enrich_observation(observation, cache_path=self.cache),
                observation,
            )
        self.assertFalse(youtube_chapters._inflight)

    def test_description_churn_never_spawns(self):
        fetched: list[str] = []

        def fake_fetch(description, **kwargs):
            fetched.append(description)
            return copy.deepcopy(live_info())

        with mock.patch.object(
            youtube_chapters, "fetch_for_description", side_effect=fake_fetch
        ):
            youtube_chapters.enrich_observation(
                youtube_observation("Mix A - Artist", position_s=1),
                cache_path=self.cache,
            )
            self.advance(1)
            youtube_chapters.enrich_observation(
                youtube_observation("Ad break", position_s=1), cache_path=self.cache
            )
            self.advance(1)
            youtube_chapters.enrich_observation(
                youtube_observation("Mix A - Artist", position_s=2),
                cache_path=self.cache,
            )
            self.advance(2)
            youtube_chapters.enrich_observation(
                youtube_observation("Mix A - Artist", position_s=3),
                cache_path=self.cache,
            )
        self.assertEqual(fetched, [])
        self.assertFalse(youtube_chapters._inflight)

    def test_stable_description_spawns_at_most_one(self):
        release = threading.Event()
        entered = threading.Event()
        fetched: list[str] = []

        def blocking_fetch(description, **kwargs):
            fetched.append(description)
            entered.set()
            release.wait(5)
            return copy.deepcopy(live_info())

        observation = youtube_observation()
        with mock.patch.object(
            youtube_chapters, "fetch_for_description", side_effect=blocking_fetch
        ):
            youtube_chapters.enrich_observation(observation, cache_path=self.cache)
            self.advance(2)
            youtube_chapters.enrich_observation(observation, cache_path=self.cache)
            self.advance(3)  # t = 5 s: stable
            youtube_chapters.enrich_observation(observation, cache_path=self.cache)
            self.assertTrue(entered.wait(2))
            self.advance(1)
            youtube_chapters.enrich_observation(observation, cache_path=self.cache)
            self.assertEqual(len(fetched), 1)
            release.set()
            self.assertTrue(wait_for(lambda: not youtube_chapters._inflight))
        self.assertEqual(len(fetched), 1)

    def test_short_ad_description_is_safe(self):
        def fake_fetch(description, **kwargs):
            return None

        observation = youtube_observation(description="Ad", position_s=0)
        with mock.patch.object(
            youtube_chapters, "fetch_for_description", side_effect=fake_fetch
        ):
            self.assertIs(
                youtube_chapters.enrich_observation(observation, cache_path=self.cache),
                observation,
            )
            self.assertFalse(youtube_chapters._inflight)
            self.advance(10)  # a stable ad may resolve and cache negative
            self.assertIs(
                youtube_chapters.enrich_observation(observation, cache_path=self.cache),
                observation,
            )
            self.assertTrue(wait_for(lambda: not youtube_chapters._inflight))
        self.assertIs(
            youtube_chapters.enrich_observation(observation, cache_path=self.cache),
            observation,
        )

    def test_enrichment_never_raises_on_unreadable_cache(self):
        observation = youtube_observation()
        self.assertIs(
            youtube_chapters.enrich_observation(
                observation, cache_path=self.tmp, refresh=False
            ),
            observation,
        )
        self.assertIs(
            youtube_chapters.enrich_observation(
                observation, cache_path=self.tmp, refresh=True
            ),
            observation,
        )


if __name__ == "__main__":
    unittest.main()
