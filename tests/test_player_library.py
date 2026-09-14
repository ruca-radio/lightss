from __future__ import annotations

import sqlite3

import pytest

from player_library import Library


ITEM_A = {
    "source": "youtube_music",
    "provider_id": "dQw4w9WgXcQ",
    "kind": "song",
    "title": "Never Gonna Give You Up",
    "artist": "Rick Astley",
}
ITEM_B = {
    "source": "apple_music",
    "provider_id": "1440801598",
    "kind": "album",
    "title": "Album",
}
ITEM_C = {**ITEM_B, "provider_id": "203709340", "kind": "song", "title": "Second song"}


def test_playlist_crud_and_order_survive_reopen(tmp_path):
    path = tmp_path / "library.sqlite3"
    library = Library(path)
    created = library.handle({"action": "create", "name": "Road trip"})
    playlist_id = created["playlists"][0]["id"]

    library.handle({"action": "add", "playlist_id": playlist_id, "item": ITEM_A})
    library.handle({"action": "add", "playlist_id": playlist_id, "item": ITEM_B, "index": 0})
    library.handle({"action": "reorder", "playlist_id": playlist_id, "from_index": 0, "to_index": 1})
    renamed = library.handle({"action": "rename", "playlist_id": playlist_id, "name": "Night drive"})

    assert renamed["ok"] is True
    assert renamed["playlists"][0]["name"] == "Night drive"
    assert [item["provider_id"] for item in Library(path).snapshot()["playlists"][0]["items"]] == [
        ITEM_A["provider_id"],
        ITEM_B["provider_id"],
    ]
    removed = library.handle({"action": "remove", "playlist_id": playlist_id, "index": 0})
    assert [item["provider_id"] for item in removed["playlists"][0]["items"]] == [ITEM_B["provider_id"]]
    assert library.handle({"action": "delete", "playlist_id": playlist_id})["playlists"] == []


def test_queue_crud_is_ordered_and_durable(tmp_path):
    path = tmp_path / "library.sqlite3"
    library = Library(path)
    library.handle({"action": "queue_add", "item": ITEM_A})
    result = library.handle({"action": "queue_add", "item": ITEM_C, "index": 0})
    assert [item["provider_id"] for item in result["queue"]] == [ITEM_C["provider_id"], ITEM_A["provider_id"]]
    assert Library(path).handle({"action": "queue_remove", "index": 1})["queue"] == [
        {**ITEM_C, "artist": "", "album": "", "artwork": ""}
    ]
    assert library.handle({"action": "queue_clear"})["queue"] == []


def test_queue_reorder_is_transactional_and_durable(tmp_path):
    path = tmp_path / "library.sqlite3"
    library = Library(path)
    library.handle({"action": "queue_add", "item": ITEM_A})
    library.handle({"action": "queue_add", "item": ITEM_C})
    result = library.handle({"action": "queue_reorder", "from_index": 1, "to_index": 0})
    assert result["ok"] is True
    assert [item["provider_id"] for item in Library(path).snapshot()["queue"]] == [ITEM_C["provider_id"], ITEM_A["provider_id"]]
    before = result["queue"]
    rejected = library.handle({"action": "queue_reorder", "from_index": 0, "to_index": 3})
    assert rejected["ok"] is False
    assert rejected["queue"] == before


def test_queue_from_playlist_atomically_replaces_queue_and_survives_reopen(tmp_path):
    path = tmp_path / "library.sqlite3"
    library = Library(path)
    playlist_id = library.handle({"action": "create", "name": "Set"})["playlists"][0]["id"]
    library.handle({"action": "add", "playlist_id": playlist_id, "item": ITEM_A})
    library.handle({"action": "add", "playlist_id": playlist_id, "item": ITEM_C})
    library.handle({"action": "queue_add", "item": ITEM_C})

    result = Library(path).handle({"action": "queue_from_playlist", "playlist_id": playlist_id})

    assert result["ok"] is True
    assert [item["provider_id"] for item in Library(path).snapshot()["queue"]] == [ITEM_A["provider_id"], ITEM_C["provider_id"]]


def test_queue_from_playlist_preserves_prior_queue_when_playlist_exceeds_limit(tmp_path, monkeypatch):
    import player_library

    path = tmp_path / "library.sqlite3"
    library = Library(path)
    playlist_id = library.handle({"action": "create", "name": "Set"})["playlists"][0]["id"]
    library.handle({"action": "add", "playlist_id": playlist_id, "item": ITEM_A})
    library.handle({"action": "add", "playlist_id": playlist_id, "item": ITEM_C})
    library.handle({"action": "queue_add", "item": ITEM_A})
    before = library.snapshot()["queue"]
    monkeypatch.setattr(player_library, "MAX_QUEUE_ITEMS", 1)

    result = Library(path).handle({"action": "queue_from_playlist", "playlist_id": playlist_id})

    assert result["ok"] is False
    assert "limit" in result["message"]
    assert result["queue"] == before
    assert Library(path).snapshot()["queue"] == before


def test_queue_add_rejects_collection_bookmark_without_mutating_queue(tmp_path):
    library = Library(tmp_path / "library.sqlite3")
    library.handle({"action": "queue_add", "item": ITEM_A})
    before = library.snapshot()["queue"]

    result = library.handle({"action": "queue_add", "item": ITEM_B})

    assert result["ok"] is False
    assert result["message"] == "Queue requires individual tracks; open album in provider and add tracks."
    assert result["queue"] == before


def test_queue_from_mixed_playlist_preserves_prior_queue(tmp_path):
    path = tmp_path / "library.sqlite3"
    library = Library(path)
    playlist_id = library.handle({"action": "create", "name": "Mixed bookmarks"})["playlists"][0]["id"]
    library.handle({"action": "add", "playlist_id": playlist_id, "item": ITEM_A})
    library.handle({"action": "add", "playlist_id": playlist_id, "item": ITEM_B})
    library.handle({"action": "queue_add", "item": ITEM_C})
    before = library.snapshot()["queue"]

    result = Library(path).handle({"action": "queue_from_playlist", "playlist_id": playlist_id})

    assert result["ok"] is False
    assert result["message"] == "Queue requires individual tracks; open album in provider and add tracks."
    assert result["queue"] == before
    assert Library(path).snapshot()["queue"] == before


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "create", "name": "   "},
        {"action": "queue_add", "item": {**ITEM_A, "source": "spotify"}},
        {"action": "queue_add", "item": {**ITEM_A, "provider_id": "../bad"}},
        {"action": "queue_add", "item": {**ITEM_A, "kind": "station"}},
        {"action": "queue_remove", "index": 0},
        {"action": "not_real"},
    ],
)
def test_invalid_actions_fail_without_mutating_state(tmp_path, payload):
    library = Library(tmp_path / "library.sqlite3")
    before = library.snapshot()
    result = library.handle(payload)
    assert result["ok"] is False
    assert result["playlists"] == before["playlists"]
    assert result["queue"] == before["queue"]


def test_failed_order_operation_rolls_back_positions(tmp_path):
    library = Library(tmp_path / "library.sqlite3")
    playlist_id = library.handle({"action": "create", "name": "P"})["playlists"][0]["id"]
    library.handle({"action": "add", "playlist_id": playlist_id, "item": ITEM_A})
    before = library.snapshot()
    result = library.handle({"action": "reorder", "playlist_id": playlist_id, "from_index": 0, "to_index": 4})
    assert result["ok"] is False
    assert library.snapshot()["playlists"] == before["playlists"]


def test_list_bounds_are_enforced(tmp_path, monkeypatch):
    import player_library

    monkeypatch.setattr(player_library, "MAX_PLAYLIST_ITEMS", 1)
    monkeypatch.setattr(player_library, "MAX_QUEUE_ITEMS", 1)
    library = Library(tmp_path / "library.sqlite3")
    playlist_id = library.handle({"action": "create", "name": "P"})["playlists"][0]["id"]
    assert library.handle({"action": "add", "playlist_id": playlist_id, "item": ITEM_A})["ok"] is True
    assert library.handle({"action": "add", "playlist_id": playlist_id, "item": ITEM_B})["ok"] is False
    assert library.handle({"action": "queue_add", "item": ITEM_A})["ok"] is True
    assert library.handle({"action": "queue_add", "item": ITEM_B})["ok"] is False


def test_schema_uses_foreign_keys_and_unique_order_positions(tmp_path):
    path = tmp_path / "library.sqlite3"
    Library(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA foreign_key_list(playlist_items)").fetchall()
        indexes = connection.execute("PRAGMA index_list(playlist_items)").fetchall()
    assert any(row[2] for row in indexes)


def test_deleting_first_playlist_compacts_remaining_order(tmp_path):
    library = Library(tmp_path / "library.sqlite3")
    first = library.handle({"action": "create", "name": "First"})["playlists"][0]["id"]
    library.handle({"action": "create", "name": "Second"})
    library.handle({"action": "create", "name": "Third"})
    result = library.handle({"action": "delete", "playlist_id": first})
    assert result["ok"] is True
    assert [playlist["name"] for playlist in result["playlists"]] == ["Second", "Third"]
