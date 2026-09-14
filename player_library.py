"""Durable, provider-neutral local playlists and playback queue."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

MAX_PLAYLISTS = 100
MAX_PLAYLIST_ITEMS = 1_000
MAX_QUEUE_ITEMS = 500
MAX_NAME_LENGTH = 160
MAX_TEXT_LENGTH = 1_000
QUEUE_TRACKS_ONLY_MESSAGE = "Queue requires individual tracks; open album in provider and add tracks."

_SOURCES = {"youtube_music", "apple_music"}
_KINDS = {"song", "album", "playlist", "artist"}
_PROVIDER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")


class Library:
    """SQLite-backed local library with atomic ordered-list mutations."""

    def __init__(self, path: str | os.PathLike[str] | None = None):
        if path is None:
            data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
            path = data_home / "lightss" / "player-library.sqlite3"
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS playlists (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    position INTEGER NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS playlist_items (
                    playlist_id TEXT NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    item_json TEXT NOT NULL,
                    PRIMARY KEY (playlist_id, position)
                );
                CREATE TABLE IF NOT EXISTS queue_items (
                    position INTEGER PRIMARY KEY,
                    item_json TEXT NOT NULL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def snapshot(self) -> dict[str, Any]:
        with self._lock, self._connect() as db:
            playlists = []
            for playlist_id, name in db.execute("SELECT id, name FROM playlists ORDER BY position"):
                items = [
                    json.loads(row[0])
                    for row in db.execute(
                        "SELECT item_json FROM playlist_items WHERE playlist_id = ? ORDER BY position",
                        (playlist_id,),
                    )
                ]
                playlists.append({"id": playlist_id, "name": name, "items": items})
            queue = [json.loads(row[0]) for row in db.execute("SELECT item_json FROM queue_items ORDER BY position")]
        return {"ok": True, "playlists": playlists, "queue": queue}

    def handle(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        try:
            if not isinstance(payload, dict):
                raise ValueError("payload must be an object")
            action = str(payload.get("action") or "").strip()
            handler = getattr(self, f"_action_{action}", None)
            if handler is None or action.startswith("_"):
                raise ValueError(f"unknown library action {action!r}")
            with self._lock, self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                handler(db, payload)
                db.commit()
            result = self.snapshot()
            result["message"] = f"Library action {action} completed."
            return result
        except (ValueError, sqlite3.Error) as exc:
            result = self.snapshot()
            result.update(ok=False, message=str(exc))
            return result

    @staticmethod
    def _name(value: Any) -> str:
        name = str(value or "").strip()
        if not name:
            raise ValueError("playlist name is required")
        if len(name) > MAX_NAME_LENGTH:
            raise ValueError("playlist name is too long")
        return name

    @staticmethod
    def _index(value: Any, *, size: int, insert: bool = False) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("index must be an integer")
        upper = size if insert else size - 1
        if value < 0 or value > upper:
            raise ValueError("index is out of bounds")
        return value

    @staticmethod
    def _item(value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            raise ValueError("item must be an object")
        source = str(value.get("source") or "").strip()
        provider_id = str(value.get("provider_id") or "").strip()
        kind = str(value.get("kind") or "song").strip().lower()
        if source not in _SOURCES:
            raise ValueError("unsupported item source")
        if not _PROVIDER_ID.fullmatch(provider_id):
            raise ValueError("invalid provider_id")
        if kind not in _KINDS:
            raise ValueError("unsupported item kind")
        result = {"source": source, "provider_id": provider_id, "kind": kind}
        for key in ("title", "artist", "album", "artwork"):
            text = str(value.get(key) or "").strip()
            if len(text) > MAX_TEXT_LENGTH:
                raise ValueError(f"item {key} is too long")
            result[key] = text
        return result

    @staticmethod
    def _playlist_id(db: sqlite3.Connection, payload: dict[str, Any]) -> str:
        playlist_id = str(payload.get("playlist_id") or "").strip()
        if not playlist_id or db.execute("SELECT 1 FROM playlists WHERE id = ?", (playlist_id,)).fetchone() is None:
            raise ValueError("unknown playlist")
        return playlist_id

    @staticmethod
    def _read_items(db: sqlite3.Connection, table: str, playlist_id: str | None = None) -> list[str]:
        if playlist_id is None:
            return [row[0] for row in db.execute(f"SELECT item_json FROM {table} ORDER BY position")]
        return [row[0] for row in db.execute("SELECT item_json FROM playlist_items WHERE playlist_id = ? ORDER BY position", (playlist_id,))]

    @staticmethod
    def _write_items(db: sqlite3.Connection, table: str, items: list[str], playlist_id: str | None = None) -> None:
        if playlist_id is None:
            db.execute(f"DELETE FROM {table}")
            db.executemany(f"INSERT INTO {table}(position, item_json) VALUES (?, ?)", enumerate(items))
        else:
            db.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (playlist_id,))
            db.executemany(
                "INSERT INTO playlist_items(playlist_id, position, item_json) VALUES (?, ?, ?)",
                ((playlist_id, index, item) for index, item in enumerate(items)),
            )

    def _action_create(self, db: sqlite3.Connection, payload: dict[str, Any]) -> None:
        count = db.execute("SELECT COUNT(*) FROM playlists").fetchone()[0]
        if count >= MAX_PLAYLISTS:
            raise ValueError("playlist limit reached")
        db.execute("INSERT INTO playlists(id, name, position) VALUES (?, ?, ?)", (uuid.uuid4().hex, self._name(payload.get("name")), count))

    def _action_rename(self, db: sqlite3.Connection, payload: dict[str, Any]) -> None:
        playlist_id = self._playlist_id(db, payload)
        db.execute("UPDATE playlists SET name = ? WHERE id = ?", (self._name(payload.get("name")), playlist_id))

    def _action_delete(self, db: sqlite3.Connection, payload: dict[str, Any]) -> None:
        playlist_id = self._playlist_id(db, payload)
        position = db.execute("SELECT position FROM playlists WHERE id = ?", (playlist_id,)).fetchone()[0]
        db.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
        db.execute("UPDATE playlists SET position = position - 1 WHERE position > ?", (position,))

    def _action_add(self, db: sqlite3.Connection, payload: dict[str, Any]) -> None:
        playlist_id = self._playlist_id(db, payload)
        items = self._read_items(db, "playlist_items", playlist_id)
        if len(items) >= MAX_PLAYLIST_ITEMS:
            raise ValueError("playlist item limit reached")
        index = len(items) if "index" not in payload else self._index(payload["index"], size=len(items), insert=True)
        items.insert(index, json.dumps(self._item(payload.get("item")), separators=(",", ":")))
        self._write_items(db, "playlist_items", items, playlist_id)

    def _action_remove(self, db: sqlite3.Connection, payload: dict[str, Any]) -> None:
        playlist_id = self._playlist_id(db, payload)
        items = self._read_items(db, "playlist_items", playlist_id)
        items.pop(self._index(payload.get("index"), size=len(items)))
        self._write_items(db, "playlist_items", items, playlist_id)

    def _action_reorder(self, db: sqlite3.Connection, payload: dict[str, Any]) -> None:
        playlist_id = self._playlist_id(db, payload)
        items = self._read_items(db, "playlist_items", playlist_id)
        source = self._index(payload.get("from_index"), size=len(items))
        target = self._index(payload.get("to_index"), size=len(items))
        items.insert(target, items.pop(source))
        self._write_items(db, "playlist_items", items, playlist_id)

    def _action_queue_add(self, db: sqlite3.Connection, payload: dict[str, Any]) -> None:
        items = self._read_items(db, "queue_items")
        if len(items) >= MAX_QUEUE_ITEMS:
            raise ValueError("queue item limit reached")
        item = self._item(payload.get("item"))
        if item["kind"] != "song":
            raise ValueError(QUEUE_TRACKS_ONLY_MESSAGE)
        index = len(items) if "index" not in payload else self._index(payload["index"], size=len(items), insert=True)
        items.insert(index, json.dumps(item, separators=(",", ":")))
        self._write_items(db, "queue_items", items)

    def _action_queue_remove(self, db: sqlite3.Connection, payload: dict[str, Any]) -> None:
        items = self._read_items(db, "queue_items")
        items.pop(self._index(payload.get("index"), size=len(items)))
        self._write_items(db, "queue_items", items)

    def _action_queue_reorder(self, db: sqlite3.Connection, payload: dict[str, Any]) -> None:
        items = self._read_items(db, "queue_items")
        source = self._index(payload.get("from_index"), size=len(items))
        target = self._index(payload.get("to_index"), size=len(items))
        items.insert(target, items.pop(source))
        self._write_items(db, "queue_items", items)

    def _action_queue_from_playlist(self, db: sqlite3.Connection, payload: dict[str, Any]) -> None:
        playlist_id = self._playlist_id(db, payload)
        items = self._read_items(db, "playlist_items", playlist_id)
        if len(items) > MAX_QUEUE_ITEMS:
            raise ValueError("queue item limit reached")
        if any(json.loads(item).get("kind") != "song" for item in items):
            raise ValueError(QUEUE_TRACKS_ONLY_MESSAGE)
        self._write_items(db, "queue_items", items)

    @staticmethod
    def _action_queue_clear(db: sqlite3.Connection, payload: dict[str, Any]) -> None:
        db.execute("DELETE FROM queue_items")
