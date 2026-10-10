from __future__ import annotations

import sys
from types import SimpleNamespace

import audio_player


class Registry:
    def __init__(self):
        self.calls = []

    def active(self):
        return True

    def snapshot(self, source):
        self.calls.append(("snapshot", source, None))
        return {"source": source, "connected": True, "stale": False, "playing": True, "position": 12.0, "duration": 90.0, "title": "Song"}

    def command(self, source, command, data=None):
        self.calls.append(("command", source, command, data))
        return {"ok": True, "queued": True, "source": source, "command": command}


def install_registry(monkeypatch, registry):
    monkeypatch.setitem(sys.modules, "desktop_bridge", SimpleNamespace(registry=registry))


def test_desktop_status_uses_observed_provider_snapshot(monkeypatch):
    registry = Registry()
    install_registry(monkeypatch, registry)
    result = audio_player.player_status(source="youtube_music")
    assert result["desktop"] is True and result["backend"] == "desktop"
    assert result["title"] == "Song"
    assert result["position"] == 12.0
    assert registry.calls == [("snapshot", "youtube_music", None)]


def test_desktop_empty_snapshot_normalizes_text_for_legacy_clients(monkeypatch):
    registry = Registry()
    registry.snapshot = lambda source: {
        "source": source, "connected": False, "playing": False,
        "title": None, "artist": None, "album": None,
    }
    install_registry(monkeypatch, registry)
    result = audio_player.player_status(source="apple_music")
    assert result["title"] == result["artist"] == result["album"] == ""


def test_desktop_transport_maps_legacy_command_names_and_album_id(monkeypatch):
    registry = Registry()
    install_registry(monkeypatch, registry)
    assert audio_player.player_command({}, "apple_music", "playPause")["ok"] is True
    assert audio_player.player_command({}, "apple_music", "playItem", data={"albumId": "l.ab-12"})["ok"] is True
    assert registry.calls == [
        ("command", "apple_music", "toggle", None),
        ("command", "apple_music", "play_id", {"id": "l.ab-12", "kind": "album"}),
    ]


def test_desktop_search_and_account_library_are_honestly_delegated(monkeypatch):
    registry = Registry()
    install_registry(monkeypatch, registry)
    search = audio_player.search_library({}, "youtube_music", "ambient")
    playlists = audio_player.list_playlists({}, "apple_music")
    assert search["ok"] is True and search["delegated"] is True and search["items"] == []
    assert "provider" in search["message"].lower()
    assert playlists["ok"] is True and playlists["delegated"] is True and playlists["playlists"] == []
    assert registry.calls == [
        ("command", "youtube_music", "search", {"query": "ambient"}),
    ]


def test_explicit_transport_preserves_legacy_youtopia_integration(monkeypatch):
    registry = Registry()
    install_registry(monkeypatch, registry)

    class Transport:
        def __init__(self):
            self.calls = []

        def request(self, method, url, body=None, headers=None, timeout=3.0):
            self.calls.append(url)
            return {"player": {"trackState": 0}, "video": {"title": "Legacy"}}

    transport = Transport()
    result = audio_player.player_status(source="youtube_music", transport=transport)
    assert result["title"] == "Legacy"
    assert transport.calls and registry.calls == []


def test_apple_catalog_search_includes_and_maps_artists(monkeypatch):
    monkeypatch.setitem(sys.modules, "desktop_bridge", SimpleNamespace(registry=SimpleNamespace(active=lambda: False)))

    class Transport:
        def __init__(self):
            self.url = ""

        def request(self, method, url, body=None, headers=None, timeout=3.0):
            self.url = url
            return {"results": {"artists": {"data": [{"id": "203", "attributes": {"name": "Artist"}}]}}}

    transport = Transport()
    result = audio_player.search_library({"apple_developer_token": "dev"}, "apple_music", "Artist", transport)
    assert "types=songs,artists,playlists,albums" in transport.url
    assert result["items"] == [{"id": "203", "title": "Artist", "artist": "", "album": "", "artwork": "", "kind": "artist"}]


def test_legacy_musickit_album_result_is_not_treated_as_a_song(monkeypatch):
    monkeypatch.setitem(sys.modules, "desktop_bridge", SimpleNamespace(registry=SimpleNamespace(active=lambda: False)))
    audio_player.reset_apple_auth()
    audio_player.store_apple_user_token("mut")
    result = audio_player.player_command(
        {"apple_developer_token": "dev"},
        "apple_music",
        "playItem",
        data={"id": "l.album-1", "kind": "album"},
    )
    assert result["client_play"] == {"album": "l.album-1"}
