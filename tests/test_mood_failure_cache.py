from __future__ import annotations

from mood_orchestrator import MoodSession, SongCache


SONG = {"artist": "Artist", "title": "Track", "album": "Album"}


class Client:
    def __init__(self):
        self.posts = []

    def get_state(self):
        return {"on": True, "bri": 60}

    def post_state(self, payload):
        self.posts.append(dict(payload))


def test_failed_generation_applies_ambient_without_caching_it(tmp_path):
    cache = SongCache(str(tmp_path / "moods.json"))

    def fail(_song):
        raise RuntimeError("Effect 56 is unavailable in the live safe catalog")

    session = MoodSession(Client(), lambda _audio: SONG, fail, cache=cache)
    session.start()
    status = session._handle_recognition(SONG)

    assert cache.get(session._song_key(SONG)) is None
    assert status["generation_status"] == "failed"
    assert "Effect 56" in status["last_generation_error"]
    assert status["last_cache_hit"] is False


def test_same_track_is_coalesced_after_generation_failure(tmp_path):
    calls = []

    def fail(song):
        calls.append(song)
        raise RuntimeError("temporary generation failure")

    session = MoodSession(Client(), lambda _audio: SONG, fail, cache=SongCache(str(tmp_path / "moods.json")))
    session.start()
    session._handle_recognition(SONG)
    status = session._handle_recognition(dict(SONG))

    assert len(calls) == 1
    assert status["generation_status"] == "failed"
    assert status["last_generation_error"] == "temporary generation failure"


def test_new_session_retries_track_that_failed_without_cache_entry(tmp_path):
    cache = SongCache(str(tmp_path / "moods.json"))
    failed = MoodSession(Client(), lambda _audio: SONG, lambda _song: (_ for _ in ()).throw(RuntimeError("first failed")), cache=cache)
    failed.start()
    failed._handle_recognition(SONG)

    calls = []
    retry = MoodSession(Client(), lambda _audio: SONG, lambda song: calls.append(song) or {"on": True, "bri": 123}, cache=cache)
    retry.start()
    status = retry._handle_recognition(SONG)

    assert len(calls) == 1
    assert status["generation_status"] == "generated"
    assert status["last_generation_error"] == ""
    assert cache.get(retry._song_key(SONG))["bri"] == 123
