import math

import desktop_bridge


def test_registry_lifecycle_and_per_provider_snapshots():
    registry = desktop_bridge.DesktopBridgeRegistry(clock=lambda: 10.0)
    assert registry.active() is False
    registry.activate()
    assert registry.active() is True
    assert registry.snapshot("youtube_music")["connected"] is False

    published = registry.publish(
        "youtube_music",
        {"playing": True, "ended": False, "position": 4, "duration": 20, "title": "Track"},
    )
    assert published["connected"] is True
    assert registry.active() is True
    assert registry.snapshot("apple_music")["connected"] is False

    registry.close()
    assert registry.active() is False
    assert registry.drain() == []


def test_commands_are_validated_fifo_and_reject_when_full():
    registry = desktop_bridge.DesktopBridgeRegistry(max_commands=2)
    assert registry.command("youtube_music", "seek", {"position": 12.5})["ok"]
    assert registry.command("apple_music", "volume", {"volume": 0.4})["ok"]
    full = registry.command("youtube_music", "play")
    assert full == {"ok": False, "error": "command queue is full"}
    assert registry.drain() == [
        {"source": "youtube_music", "command": "seek", "data": {"position": 12.5}},
        {"source": "apple_music", "command": "volume", "data": {"volume": 0.4}},
    ]


def test_command_contract_supports_delegated_catalog_and_rejects_bad_data():
    registry = desktop_bridge.DesktopBridgeRegistry()
    accepted = [
        ("play", None), ("pause", {}), ("toggle", None), ("next", None),
        ("previous", None), ("library", None),
        ("search", {"query": "Boards of Canada"}),
        ("play_id", {"id": "PL_x-9:abc", "kind": "playlist"}),
    ]
    for command, data in accepted:
        assert registry.command("youtube_music", command, data)["ok"] is True
    for source, command, data in [
        ("youtube", "play", None),
        ("youtube_music", "eval", {"javascript": "alert(1)"}),
        ("youtube_music", "seek", {"position": -1}),
        ("youtube_music", "volume", {"volume": 2}),
        ("youtube_music", "search", {"query": " "}),
        ("youtube_music", "play_id", {"id": "../bad", "kind": "song"}),
        ("youtube_music", "play_id", {"id": "good", "kind": "video"}),
    ]:
        assert registry.command(source, command, data)["ok"] is False


def test_publish_rejects_unknown_or_unsafe_fields_urls_and_ids():
    registry = desktop_bridge.DesktopBridgeRegistry()
    assert registry.publish("youtube_music", {"position": math.nan})["ok"] is False
    assert registry.publish("youtube_music", {"script": "document.cookie"})["ok"] is False
    assert registry.publish("youtube_music", {"track_id": "../../passwd"})["ok"] is False
    assert registry.publish("youtube_music", {"url": "http://127.0.0.1:8123/api/settings"})["ok"] is False
    assert registry.publish("youtube_music", {"url": "https://music.youtube.com/watch?v=abc"})["connected"]
    assert registry.publish("apple_music", {"url": "https://music.apple.com/us/album/x/1"})["connected"]


def test_snapshot_marks_timing_stale_without_fabricating_progress():
    now = [100.0]
    registry = desktop_bridge.DesktopBridgeRegistry(stale_after=5, clock=lambda: now[0])
    registry.publish("apple_music", {"playing": True, "position": 9, "duration": 30})
    now[0] = 106.0
    snapshot = registry.snapshot("apple_music")
    assert snapshot["connected"] is True
    assert snapshot["stale"] is True
    assert snapshot["playing"] is False
    assert snapshot["ended"] is False
    assert snapshot["position"] is None
    assert snapshot["duration"] is None


def test_ended_event_remains_sequenced_after_later_playing_observations():
    registry = desktop_bridge.DesktopBridgeRegistry()
    registry.activate()
    registry.publish("youtube_music", {"playing": True, "ended": False, "track_id": "track-a", "position": 9})
    baseline = registry.snapshot("youtube_music")["ended_sequence"]
    registry.publish("youtube_music", {"playing": False, "ended": True, "track_id": "track-a", "position": 10})
    registry.publish("youtube_music", {"playing": True, "ended": False, "track_id": "track-b", "position": 1})
    snapshot = registry.snapshot("youtube_music")
    assert snapshot["track_id"] == "track-b"
    assert snapshot["playing"] is True
    assert snapshot["ended"] is False
    assert snapshot["last_ended_track_id"] == "track-a"
    assert snapshot["ended_sequence"] == baseline + 1


def test_navigation_reset_clears_observation_and_end_lifecycle():
    registry = desktop_bridge.DesktopBridgeRegistry()
    registry.activate()
    registry.publish("apple_music", {"ended": True, "track_id": "old-track"})
    registry.reset("apple_music")
    snapshot = registry.snapshot("apple_music")
    assert snapshot["connected"] is False and snapshot["stale"] is True
    assert snapshot["last_ended_track_id"] is None
    assert snapshot["ended_sequence"] == 0
