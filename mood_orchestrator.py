from __future__ import annotations

import json
import os

_CONFIG_DIR = os.path.expanduser("~/.config/lightss")
_SONG_CACHE_PATH = os.path.join(_CONFIG_DIR, "song_moods.json")


class SongCache:
    """Persistent cache of song key -> last WLED mood payload."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or _SONG_CACHE_PATH
        self._data = self._load()

    def _load(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        return {}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)

    def get(self, key: str) -> dict | None:
        return self._data.get(key)

    def set(self, key: str, payload: dict) -> None:
        self._data[key] = payload
        self._save()
