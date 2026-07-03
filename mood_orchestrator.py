from __future__ import annotations

import json
import os
from typing import Any

import lightctl

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


class TransitionSmoother:
    """Make WLED payload changes gentle and non-jarring."""

    MIN_TRANSITION_MS = 1200
    AMBIENT_TRANSITION_MS = 4000
    AMBIENT_TO_MOOD_MS = 2500
    BRIGHTNESS_JUMP_THRESHOLD = 80
    INTERMEDIATE_STEP_MS = 800

    def smooth(
        self,
        payload: lightctl.WledPayload,
        current_state: dict[str, Any] | None,
        source: str,
    ) -> list[lightctl.WledPayload]:
        current_state = current_state or {}
        current_bri = current_state.get("bri", 128)
        target_bri = payload.get("bri", current_bri)

        if source == "ambient":
            min_tt = self.AMBIENT_TRANSITION_MS
        elif source == "ambient_to_mood":
            min_tt = self.AMBIENT_TO_MOOD_MS
        else:
            min_tt = self.MIN_TRANSITION_MS

        tt = payload.get("tt", 0)
        if tt < min_tt:
            payload = dict(payload)
            payload["tt"] = min_tt

        # If brightness jump is too large, split into two posts.
        if abs(target_bri - current_bri) > 2 * self.BRIGHTNESS_JUMP_THRESHOLD:
            direction = 1 if target_bri > current_bri else -1
            intermediate_bri = current_bri + direction * self.BRIGHTNESS_JUMP_THRESHOLD
            intermediate = dict(payload)
            intermediate["bri"] = lightctl.clamp_byte(intermediate_bri)
            intermediate["tt"] = self.INTERMEDIATE_STEP_MS
            final = dict(payload)
            final["bri"] = lightctl.clamp_byte(target_bri)
            return [intermediate, final]

        return [payload]
