import json
import os
import tempfile

import pytest

import mood_orchestrator


class TestSongCache:
    def test_get_missing_returns_none(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmp:
            monkeypatch.setattr(mood_orchestrator, "_CONFIG_DIR", tmp)
            cache = mood_orchestrator.SongCache()
            assert cache.get("missing") is None

    def test_set_and_get_roundtrip(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmp:
            monkeypatch.setattr(mood_orchestrator, "_CONFIG_DIR", tmp)
            cache = mood_orchestrator.SongCache()
            cache.set("artist||title||album", {"bri": 180, "seg": [{"fx": 9}]})
            assert cache.get("artist||title||album") == {"bri": 180, "seg": [{"fx": 9}]}
