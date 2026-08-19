#!/usr/bin/env python3
"""Integrated audio player backends for the Lightss controller.

YouTube Music talks to the local Youtopia companion server.
Apple Music is a first-class source in the controller, but playback
requires a MusicKit developer token that is not assumed to exist.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

SOURCES = ("youtube_music", "apple_music")
COMMANDS = ("play", "pause", "playPause", "next", "previous")
DEFAULT_YOUTUBE_HOST = "http://127.0.0.1:9863"


class UrlTransport:
    def request(self, method: str, url: str, body: dict | None = None, headers: dict | None = None, timeout: float = 3.0) -> dict:
        payload = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, data=payload, headers=headers or {}, method=method)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}


def _host(settings: dict | None, key: str, default: str) -> str:
    value = str((settings or {}).get(key) or default).strip().rstrip("/")
    parsed = urlparse(value if "://" in value else f"http://{value}")
    return f"{parsed.scheme}://{parsed.netloc}"


def _auth_headers(token: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _youtube_status(settings: dict | None, transport: Any) -> dict[str, Any]:
    host = _host(settings, "youtube_host", DEFAULT_YOUTUBE_HOST)
    token = str((settings or {}).get("youtube_token") or "").strip() or None
    try:
        data = transport.request("GET", f"{host}/api/v1/state", headers=_auth_headers(token))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {
            "source": "youtube_music",
            "connected": False,
            "playing": False,
            "title": "",
            "artist": "",
            "album": "",
            "message": f"Youtopia companion unreachable: {exc}",
        }
    player = data.get("player") if isinstance(data.get("player"), dict) else {}
    video = data.get("video") if isinstance(data.get("video"), dict) else {}
    title = str(video.get("title") or data.get("title") or "").strip()
    artist = str(video.get("author") or video.get("artist") or data.get("artist") or "").strip()
    return {
        "source": "youtube_music",
        "connected": True,
        "playing": int(player.get("trackState") or 0) == 1,
        "title": title,
        "artist": artist,
        "album": str(video.get("album") or ""),
        "progress": player.get("videoProgress"),
        "volume": player.get("volume"),
        "message": "YouTube Music via Youtopia",
    }


def _youtube_command(settings: dict | None, command: str, transport: Any) -> dict[str, Any]:
    host = _host(settings, "youtube_host", DEFAULT_YOUTUBE_HOST)
    token = str((settings or {}).get("youtube_token") or "").strip() or None
    try:
        transport.request(
            "POST",
            f"{host}/api/v1/command",
            body={"command": command},
            headers=_auth_headers(token),
        )
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "source": "youtube_music", "command": command, "message": str(exc)}
    return {"ok": True, "source": "youtube_music", "command": command, "message": f"Sent {command} to Youtopia"}


def _apple_status(settings: dict | None) -> dict[str, Any]:
    token = str((settings or {}).get("apple_developer_token") or "").strip()
    if not token:
        return {
            "source": "apple_music",
            "connected": False,
            "playing": False,
            "title": "",
            "artist": "",
            "album": "",
            "message": "Apple Music needs a MusicKit developer token in settings.",
        }
    return {
        "source": "apple_music",
        "connected": False,
        "playing": False,
        "title": "",
        "artist": "",
        "album": "",
        "message": "Apple Music token is set; MusicKit session is not authorized yet.",
    }


def player_status(settings: dict | None = None, source: str = "youtube_music", transport: Any | None = None) -> dict[str, Any]:
    source = (source or "youtube_music").strip().lower()
    if source == "youtube_music":
        return _youtube_status(settings, transport or UrlTransport())
    if source == "apple_music":
        return _apple_status(settings)
    return {
        "source": source,
        "connected": False,
        "playing": False,
        "title": "",
        "artist": "",
        "album": "",
        "message": f"Unknown audio source {source!r}",
    }


def player_command(
    settings: dict | None,
    source: str,
    command: str,
    transport: Any | None = None,
) -> dict[str, Any]:
    source = (source or "youtube_music").strip().lower()
    command = (command or "").strip()
    if command not in COMMANDS:
        return {"ok": False, "source": source, "command": command, "message": f"Unknown command {command!r}"}
    if source == "youtube_music":
        return _youtube_command(settings, command, transport or UrlTransport())
    if source == "apple_music":
        return {"ok": False, "source": source, "command": command, "message": "Apple Music playback is not authorized yet."}
    return {"ok": False, "source": source, "command": command, "message": f"Unknown audio source {source!r}"}


def settings_from_config(config: dict | None) -> dict[str, str]:
    block = (config or {}).get("audio_player")
    if not isinstance(block, dict):
        return {}
    return {
        key: str(block[key]).strip()
        for key in ("youtube_host", "youtube_token", "apple_developer_token")
        if block.get(key)
    }
