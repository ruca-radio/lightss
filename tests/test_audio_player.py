from __future__ import annotations

import base64
import struct

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
    status = audio_player.player_status(source="apple_music")
    assert status["source"] == "apple_music"
    assert status["connected"] is False
    assert "developer token" in status["message"].lower()


def test_unknown_source_fails_closed():
    status = audio_player.player_status(source="spotify")
    assert status["connected"] is False
    assert "unknown" in status["message"].lower()


def test_apple_login_session_creates_code_url_and_qr():
    audio_player.reset_apple_auth()
    session = audio_player.create_apple_login_session("http://10.27.27.96:8123")
    assert session["authorized"] is False
    assert session["code"]
    assert session["session"]
    assert session["login_url"].startswith("http://10.27.27.96:8123/apple-login?session=")
    assert session["session"] in session["login_url"]
    uri = session["qr_png"]
    assert uri.startswith("data:image/png;base64,")
    raw = base64.b64decode(uri.split(",", 1)[1])
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", raw[16:24])
    assert width == height
    assert width >= 21


def test_public_base_url_rewrites_loopback_to_lan():
    lan = audio_player.lan_ipv4()
    url = audio_player.public_base_url("http", "127.0.0.1:8123")
    if lan and not str(lan).startswith("127."):
        assert url == f"http://{lan}:8123"
        assert "127.0.0.1" not in url
    else:
        assert url == "http://127.0.0.1:8123"
    assert audio_player.public_base_url("http", "10.27.27.96:8123") == "http://10.27.27.96:8123"


def test_apple_login_complete_authorizes_and_connects_status():
    audio_player.reset_apple_auth()
    created = audio_player.create_apple_login_session("http://127.0.0.1:8123")
    result = audio_player.complete_apple_login(created["session"], music_user_token="mut-secret")
    assert result["authorized"] is True
    polled = audio_player.apple_login_status(created["session"])
    assert polled["authorized"] is True
    assert polled["music_user_token"] == "mut-secret"
    status = audio_player.player_status({"apple_developer_token": "dev"}, source="apple_music")
    assert status["connected"] is True
    assert status["authorized"] is True


def test_apple_login_rejects_unknown_session():
    audio_player.reset_apple_auth()
    result = audio_player.complete_apple_login("missing", music_user_token="x")
    assert result["ok"] is False
    assert result["authorized"] is False


def test_search_apple_catalog_maps_songs():
    transport = FakeTransport(
        responses=[
            {
                "results": {
                    "songs": {
                        "data": [
                            {
                                "id": "1440801598",
                                "attributes": {
                                    "name": "Breathe (Whethan Remix)",
                                    "artistName": "Yeat",
                                    "albumName": "Lyfe",
                                    "artwork": {"url": "https://is1-ssl.mzstatic.com/image/{w}x{h}bb.jpg"},
                                },
                            }
                        ]
                    }
                }
            }
        ]
    )
    result = audio_player.search_library(
        {"apple_developer_token": "dev"},
        source="apple_music",
        query="breathe yeat",
        transport=transport,
    )
    assert result["ok"] is True
    assert result["items"][0]["title"] == "Breathe (Whethan Remix)"
    assert result["items"][0]["artist"] == "Yeat"
    assert result["items"][0]["id"] == "1440801598"
    assert "api.music.apple.com" in transport.calls[0]["url"]
    assert "Authorization" in (transport.calls[0]["headers"] or {})


def test_search_youtube_maps_queue_items():
    transport = FakeTransport(
        responses=[
            {
                "player": {
                    "queue": {
                        "items": [
                            {"title": "Breathe (Whethan Remix)", "author": "Yeat", "videoId": "abc123", "thumbnails": [{"url": "https://i.ytimg.com/vi/abc123/hqdefault.jpg"}]}
                        ]
                    }
                }
            }
        ]
    )
    result = audio_player.search_library(
        {"youtube_host": "http://127.0.0.1:9863"},
        source="youtube_music",
        query="breathe",
        transport=transport,
    )
    assert result["ok"] is True
    assert result["items"][0]["id"] == "abc123"
    assert result["items"][0]["title"].startswith("Breathe")
    assert transport.calls[0]["url"].endswith("/api/v1/state")


def test_youtube_playlists_map_companion_list():
    transport = FakeTransport(responses=[[{"id": "PL1", "title": "Late Night"}]])
    result = audio_player.list_playlists(
        {"youtube_host": "http://10.27.27.96:9863", "youtube_token": "tok"},
        source="youtube_music",
        transport=transport,
    )
    assert result["ok"] is True
    assert result["playlists"][0]["id"] == "PL1"
    assert result["playlists"][0]["title"] == "Late Night"
    assert transport.calls[0]["url"].endswith("/api/v1/playlists")
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer tok"


def test_apple_playlists_use_music_user_token():
    audio_player.reset_apple_auth()
    audio_player.store_apple_user_token("mut-1")
    transport = FakeTransport(
        responses=[
            {
                "data": [
                    {"id": "p.123", "attributes": {"name": "Focus", "artwork": {"url": "https://x/{w}x{h}.jpg"}}}
                ]
            }
        ]
    )
    result = audio_player.list_playlists(
        {"apple_developer_token": "dev"},
        source="apple_music",
        transport=transport,
    )
    assert result["ok"] is True
    assert result["playlists"][0]["title"] == "Focus"
    assert transport.calls[0]["headers"]["Music-User-Token"] == "mut-1"


def test_play_item_youtube_sends_change_video():
    transport = FakeTransport(responses=[{}])
    result = audio_player.player_command(
        {"youtube_host": "http://127.0.0.1:9863"},
        source="youtube_music",
        command="playItem",
        transport=transport,
        data={"videoId": "abc123"},
    )
    assert result["ok"] is True
    assert transport.calls[0]["url"].endswith("/api/v1/command")
    assert transport.calls[0]["body"] == {"command": "changeVideo", "data": {"videoId": "abc123"}}


def test_play_item_apple_returns_client_play_payload():
    audio_player.reset_apple_auth()
    audio_player.store_apple_user_token("mut")
    result = audio_player.player_command(
        {"apple_developer_token": "dev"},
        source="apple_music",
        command="playItem",
        data={"songId": "1440801598"},
    )
    assert result["ok"] is True
    assert result["client_play"]["song"] == "1440801598"


def test_youtube_status_includes_artwork():
    transport = FakeTransport(
        responses=[
            {
                "player": {"trackState": 1},
                "video": {
                    "title": "Breathe",
                    "author": "Yeat",
                    "thumbnails": [{"url": "https://i.ytimg.com/vi/x/hqdefault.jpg"}],
                },
            }
        ]
    )
    status = audio_player.player_status(
        {"youtube_host": "http://127.0.0.1:9863"},
        source="youtube_music",
        transport=transport,
    )
    assert status["artwork"] == "https://i.ytimg.com/vi/x/hqdefault.jpg"


def test_apple_user_token_from_config_authorizes_without_session():
    audio_player.reset_apple_auth()
    settings = {"apple_developer_token": "dev", "apple_user_token": "mut-config"}
    status = audio_player.player_status(settings, source="apple_music")
    assert status["connected"] is True
    assert status["authorized"] is True
    transport = FakeTransport(responses=[{"data": []}])
    result = audio_player.list_playlists(settings, source="apple_music", transport=transport)
    assert result["ok"] is True
    assert transport.calls[0]["headers"]["Music-User-Token"] == "mut-config"


def test_apple_search_honors_custom_api_base_and_origin():
    audio_player.reset_apple_auth()
    transport = FakeTransport(responses=[{"results": {}}])
    settings = {
        "apple_developer_token": "dev",
        "apple_api_base": "https://amp-api.music.apple.com/v1",
        "apple_origin": "https://music.apple.com",
    }
    result = audio_player.search_library(settings, source="apple_music", query="breathe", transport=transport)
    assert result["ok"] is True
    assert transport.calls[0]["url"].startswith("https://amp-api.music.apple.com/v1/catalog/us/search")
    assert transport.calls[0]["headers"]["Origin"] == "https://music.apple.com"


def test_apple_settings_from_config_passes_through_new_keys():
    config = {
        "audio_player": {
            "apple_developer_token": "dev",
            "apple_user_token": "mut",
            "apple_api_base": "https://amp-api.music.apple.com/v1",
            "apple_origin": "https://music.apple.com",
        }
    }
    settings = audio_player.settings_from_config(config)
    assert settings["apple_user_token"] == "mut"
    assert settings["apple_api_base"] == "https://amp-api.music.apple.com/v1"
    assert settings["apple_origin"] == "https://music.apple.com"
