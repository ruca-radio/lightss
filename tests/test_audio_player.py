from __future__ import annotations

import audio_player


class FakeTransport:
    def __init__(self, responses=None, errors=None):
        self.responses = list(responses or [])
        self.errors = list(errors or [])
        self.calls = []

    def request(self, method, url, body=None, headers=None, timeout=3.0):
        self.calls.append({"method": method, "url": url, "body": body, "headers": headers})
        if self.errors:
            raise self.errors.pop(0)
        if not self.responses:
            return {"ok": True}
        return self.responses.pop(0)


def test_youtube_status_maps_companion_state():
    transport = FakeTransport(
        responses=[
            {
                "player": {"trackState": 1, "videoProgress": 12.5, "volume": 80},
                "video": {"title": "Breathe (Whethan Remix)", "author": "Yeat", "album": "Lyfe"},
            }
        ]
    )
    status = audio_player.player_status(
        {"youtube_host": "http://127.0.0.1:9863"},
        source="youtube_music",
        transport=transport,
    )
    assert status["source"] == "youtube_music"
    assert status["connected"] is True
    assert status["playing"] is True
    assert status["title"] == "Breathe (Whethan Remix)"
    assert status["artist"] == "Yeat"
    assert transport.calls[0]["url"].endswith("/api/v1/state")


def test_youtube_command_posts_companion_command():
    transport = FakeTransport(responses=[{}])
    result = audio_player.player_command(
        {"youtube_host": "http://10.27.27.96:9863", "youtube_token": "tok"},
        source="youtube_music",
        command="playPause",
        transport=transport,
    )
    assert result["ok"] is True
    assert transport.calls[0]["method"] == "POST"
    assert transport.calls[0]["url"].endswith("/api/v1/command")
    assert transport.calls[0]["body"] == {"command": "playPause"}
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer tok"


def test_apple_music_reports_not_configured():
    status = audio_player.player_status({}, source="apple_music")
    assert status["source"] == "apple_music"
    assert status["connected"] is False
    assert "developer token" in status["message"].lower()


def test_unknown_source_fails_closed():
    status = audio_player.player_status({}, source="spotify")
    assert status["connected"] is False
    assert "unknown" in status["message"].lower()
