"""Tests for tv_trivia.py and the /api/tv-trivia endpoint wiring.

The disk cache path is always redirected to tmp_path so the real cache at
~/.config/lightss/tv_trivia_cache.json is never touched.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

import light_gui
import music_director
import tv_trivia


SETTINGS = {"base_url": "https://example.test/v1", "model": "test-model", "api_key_env": "UNSET_KEY"}


@pytest.fixture()
def cache_path(tmp_path, monkeypatch):
    """Redirect the trivia disk cache into tmp_path."""
    path = tmp_path / "tv_trivia_cache.json"
    monkeypatch.setattr(tv_trivia, "CACHE_PATH", path)
    return path


@pytest.fixture(autouse=True)
def clear_attempt_state():
    """Keep the retry-cooldown bookkeeping isolated between tests."""
    tv_trivia._last_attempt.clear()
    tv_trivia._in_flight.clear()
    yield
    tv_trivia._last_attempt.clear()
    tv_trivia._in_flight.clear()


def write_cache(path, entries: dict) -> None:
    path.write_text(json.dumps(entries), encoding="utf-8")


def fresh_entry(items: list[str]) -> dict:
    return {"fetched": time.time(), "items": items}


def drain_in_flight(timeout: float = 3.0) -> None:
    """Wait for any background generation threads to finish."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not tv_trivia._in_flight:
            return
        time.sleep(0.02)
    raise AssertionError("background trivia thread did not finish")


class TestTrackParsing:
    def test_none_track_returns_empty_payload(self, cache_path):
        result = tv_trivia.trivia_payload(None, SETTINGS)
        assert result == {"ok": True, "artist": None, "items": []}

    def test_unparseable_track_returns_empty_payload(self, cache_path):
        for track in ("", "No separator here", "— Title only"):
            result = tv_trivia.trivia_payload(track, SETTINGS)
            assert result == {"ok": True, "artist": None, "items": []}, track

    def test_artist_is_split_on_em_dash(self, cache_path, monkeypatch):
        monkeypatch.setattr(tv_trivia, "_generate_items", lambda *a, **k: [])
        result = tv_trivia.trivia_payload("  Radiohead  —  Creep ", SETTINGS)
        assert result["ok"] is True
        assert result["artist"] == "Radiohead"
        drain_in_flight()


class TestCacheHit:
    def test_cache_hit_returns_cached_items(self, cache_path, monkeypatch):
        items = ["♪ Formed in Abingdon in 1985", "OK Computer topped the UK charts"]
        write_cache(cache_path, {"radiohead": fresh_entry(items)})

        def fail_loudly(*args, **kwargs):
            raise AssertionError("HTTP must not be attempted on a cache hit")

        monkeypatch.setattr(tv_trivia.urllib.request, "urlopen", fail_loudly)
        result = tv_trivia.trivia_payload("Radiohead — Creep", SETTINGS)
        assert result == {"ok": True, "artist": "Radiohead", "items": items}

    def test_cache_is_keyed_by_lowercase_artist(self, cache_path, monkeypatch):
        write_cache(cache_path, {"radiohead": fresh_entry(["line"])})

        def fail_loudly(*args, **kwargs):
            raise AssertionError("HTTP must not be attempted on a cache hit")

        monkeypatch.setattr(tv_trivia.urllib.request, "urlopen", fail_loudly)
        result = tv_trivia.trivia_payload("RADIOHEAD — Creep", SETTINGS)
        assert result["items"] == ["line"]
        assert result["artist"] == "RADIOHEAD"

    def test_expired_entry_is_a_miss(self, cache_path, monkeypatch):
        stale = {"fetched": time.time() - 31 * 24 * 3600, "items": ["old"]}
        write_cache(cache_path, {"radiohead": stale})
        monkeypatch.setattr(tv_trivia, "_generate_items", lambda *a, **k: [])
        result = tv_trivia.trivia_payload("Radiohead — Creep", SETTINGS)
        assert result["ok"] is True
        assert result["items"] == []
        drain_in_flight()


class TestCacheMiss:
    def test_miss_returns_empty_immediately(self, cache_path, monkeypatch):
        def slow_generate(*args, **kwargs):
            time.sleep(0.5)
            return []

        monkeypatch.setattr(tv_trivia, "_generate_items", slow_generate)
        start = time.time()
        result = tv_trivia.trivia_payload("Radiohead — Creep", SETTINGS)
        elapsed = time.time() - start
        assert result == {"ok": True, "artist": "Radiohead", "items": []}
        assert elapsed < 0.3, "cache miss must not block on generation"
        drain_in_flight()

    def test_miss_spawns_daemon_thread_that_fills_cache(self, cache_path, monkeypatch):
        generated = ["♪ one", "two", "three"]
        monkeypatch.setattr(tv_trivia, "_generate_items", lambda *a, **k: generated)

        result = tv_trivia.trivia_payload("Radiohead — Creep", SETTINGS)
        assert result["items"] == []
        drain_in_flight()

        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        assert cache["radiohead"]["items"] == generated
        assert time.time() - cache["radiohead"]["fetched"] < 60

        threads = [t for t in threading.enumerate() if t.name == "lightss-tv-trivia"]
        assert all(t.daemon for t in threads)

        # Second call is now a cache hit.
        result = tv_trivia.trivia_payload("Radiohead — Creep", SETTINGS)
        assert result["items"] == generated

    def test_failed_generation_caches_nothing(self, cache_path, monkeypatch):
        monkeypatch.setattr(tv_trivia, "_generate_items", lambda *a, **k: [])
        result = tv_trivia.trivia_payload("Radiohead — Creep", SETTINGS)
        assert result["items"] == []
        drain_in_flight()
        assert not cache_path.exists() or "radiohead" not in json.loads(
            cache_path.read_text(encoding="utf-8")
        )

    def test_payload_never_raises(self, cache_path, monkeypatch):
        monkeypatch.setattr(tv_trivia, "_cached_items", lambda *a, **k: None)
        monkeypatch.setattr(tv_trivia, "_load_cache", lambda: 1 / 0)
        monkeypatch.setattr(tv_trivia, "_generate_items", lambda *a, **k: [])
        result = tv_trivia.trivia_payload("Radiohead — Creep", SETTINGS)
        assert result["ok"] is True
        assert result["items"] == []
        drain_in_flight()


class TestGenerateItems:
    def test_generate_items_posts_chat_completion(self, cache_path, monkeypatch):
        captured = {}
        lines = ["♪ fact one", "fact two"]

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps(
                    {"choices": [{"message": {"content": json.dumps(lines)}}]}
                ).encode("utf-8")

        def fake_urlopen(request, timeout=0):
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeResponse()

        monkeypatch.setattr(tv_trivia.urllib.request, "urlopen", fake_urlopen)
        items = tv_trivia._generate_items("Radiohead", SETTINGS)

        assert items == lines
        assert captured["url"] == "https://example.test/v1/chat/completions"
        assert captured["body"]["model"] == "test-model"
        assert captured["body"]["messages"][-1]["content"] == "Radiohead"

    def test_generate_items_strips_markdown_fences(self, cache_path, monkeypatch):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps(
                    {"choices": [{"message": {"content": '```json\n["a", "b"]\n```'}}]}
                ).encode("utf-8")

        monkeypatch.setattr(
            tv_trivia.urllib.request, "urlopen", lambda *a, **k: FakeResponse()
        )
        assert tv_trivia._generate_items("Radiohead", SETTINGS) == ["a", "b"]

    def test_generate_items_returns_empty_on_http_error(self, cache_path, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(tv_trivia.urllib.request, "urlopen", boom)
        assert tv_trivia._generate_items("Radiohead", SETTINGS) == []

    @pytest.mark.parametrize("payload", [
        {"error": {"message": "bad api key"}},  # 200 with an error body
        {"choices": []},  # empty choices
        {"choices": [{"message": None}]},  # null message
    ])
    def test_generate_items_returns_empty_on_wrong_shape(self, cache_path, monkeypatch, payload):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps(payload).encode("utf-8")

        monkeypatch.setattr(
            tv_trivia.urllib.request, "urlopen", lambda *a, **k: FakeResponse()
        )
        assert tv_trivia._generate_items("Radiohead", SETTINGS) == []


class TestCorruptCache:
    def test_corrupt_fetched_counts_as_expired_and_regenerates(self, cache_path, monkeypatch):
        write_cache(cache_path, {"radiohead": {"fetched": "not-a-number", "items": ["old"]}})
        monkeypatch.setattr(tv_trivia, "_generate_items", lambda *a, **k: ["fresh"])

        result = tv_trivia.trivia_payload("Radiohead — Creep", SETTINGS)
        assert result["items"] == []
        drain_in_flight()

        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        assert cache["radiohead"]["items"] == ["fresh"]


class TestRetryCooldown:
    def test_failed_attempt_is_not_retried_within_cooldown(self, cache_path, monkeypatch):
        calls = []
        monkeypatch.setattr(tv_trivia, "_generate_items", lambda *a, **k: (calls.append(1), [])[1])

        tv_trivia.trivia_payload("Radiohead — Creep", SETTINGS)
        drain_in_flight()
        tv_trivia.trivia_payload("Radiohead — Creep", SETTINGS)
        drain_in_flight()

        assert len(calls) == 1, "provider outage must not spawn a thread per poll"

    def test_retry_allowed_after_cooldown(self, cache_path, monkeypatch):
        calls = []
        monkeypatch.setattr(tv_trivia, "_generate_items", lambda *a, **k: (calls.append(1), [])[1])

        tv_trivia.trivia_payload("Radiohead — Creep", SETTINGS)
        drain_in_flight()
        tv_trivia._last_attempt["radiohead"] = time.time() - tv_trivia.RETRY_COOLDOWN_S - 1
        tv_trivia.trivia_payload("Radiohead — Creep", SETTINGS)
        drain_in_flight()

        assert len(calls) == 2


class TestLightGuiIntegration:
    def test_light_gui_exposes_tv_trivia_payload(self):
        assert callable(light_gui.tv_trivia_payload)

    def test_tv_trivia_route_is_registered(self):
        route_sets = [
            value
            for name, value in vars(light_gui).items()
            if ("ROUTES" in name or "PATHS" in name)
            and isinstance(value, (list, tuple, set, frozenset))
        ]
        assert route_sets, "light_gui has no route list/set to register against"
        assert any("/api/tv-trivia" in routes for routes in route_sets)
        assert "/api/tv-trivia" in light_gui.API_GET_PATHS

    def test_light_gui_payload_delegates_to_tv_trivia(self, monkeypatch):
        sentinel = {"ok": True, "artist": "Radiohead", "items": ["line"]}
        seen = {}

        def fake_trivia_payload(track, settings):
            seen["track"] = track
            return sentinel

        monkeypatch.setattr(tv_trivia, "trivia_payload", fake_trivia_payload)
        monkeypatch.setattr(
            music_director, "director_status",
            lambda: {"running": True, "track": "Radiohead — Creep", "mood": None},
        )
        assert light_gui.tv_trivia_payload() == sentinel
        assert seen["track"] == "Radiohead — Creep"

    def test_light_gui_payload_degrades_when_tv_trivia_missing(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "tv_trivia":
                raise ImportError("no module named tv_trivia")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        result = light_gui.tv_trivia_payload()
        assert result == {"ok": True, "artist": None, "items": []}
