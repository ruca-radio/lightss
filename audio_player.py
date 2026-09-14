#!/usr/bin/env python3
"""Integrated audio player backends for the Lightss controller.

YouTube Music talks to the local Youtopia companion server.
Apple Music uses a MusicKit developer token plus an Apple ID login
session (QR on the controller, authorize on a phone).
"""

from __future__ import annotations

import base64
import io
import json
import secrets
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any
from urllib.parse import urlparse

SOURCES = ("youtube_music", "apple_music")
COMMANDS = ("play", "pause", "playPause", "next", "previous", "playItem", "search", "library", "seek", "volume")
DEFAULT_YOUTUBE_HOST = "http://127.0.0.1:9863"
APPLE_CATALOG = "https://api.music.apple.com/v1"
DEFAULT_STOREFRONT = "us"
SESSION_TTL_S = 600

_apple_sessions: dict[str, dict[str, Any]] = {}
_apple_user_token: str | None = None
_apple_now_playing: dict[str, Any] = {}


def _desktop_registry() -> Any | None:
    """Return the active desktop registry without making it a hard dependency."""
    try:
        from desktop_bridge import registry

        return registry if registry.active() else None
    except (ImportError, AttributeError, RuntimeError):
        return None


def _desktop_play_item(data: dict | None) -> dict[str, str] | None:
    values = data if isinstance(data, dict) else {}
    candidates = (
        ("videoId", "song"), ("songId", "song"), ("albumId", "album"),
        ("playlistId", "playlist"), ("artistId", "artist"),
    )
    for key, kind in candidates:
        value = str(values.get(key) or "").strip()
        if value:
            return {"id": value, "kind": kind}
    value = str(values.get("id") or "").strip()
    kind = str(values.get("kind") or "song").strip().lower()
    return {"id": value, "kind": kind} if value else None


class UrlTransport:
    def request(self, method: str, url: str, body: dict | None = None, headers: dict | None = None, timeout: float = 3.0) -> Any:
        payload = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, data=payload, headers=headers or {}, method=method)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8"))
        return data


def _host(settings: dict | None, key: str, default: str) -> str:
    value = str((settings or {}).get(key) or default).strip().rstrip("/")
    parsed = urlparse(value if "://" in value else f"http://{value}")
    return f"{parsed.scheme}://{parsed.netloc}"


def _auth_headers(token: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _apple_headers(settings: dict | None, user_token: str | None = None) -> dict[str, str]:
    token = str((settings or {}).get("apple_developer_token") or "").strip()
    headers = _auth_headers(token or None)
    if user_token:
        headers["Music-User-Token"] = user_token
    origin = str((settings or {}).get("apple_origin") or "").strip()
    if origin:
        headers["Origin"] = origin
    return headers


def _apple_base(settings: dict | None) -> str:
    base = str((settings or {}).get("apple_api_base") or APPLE_CATALOG).strip().rstrip("/")
    return base or APPLE_CATALOG


def _effective_user_token(settings: dict | None) -> str | None:
    return _apple_user_token or str((settings or {}).get("apple_user_token") or "").strip() or None


def _artwork_url(value: Any, size: int = 240) -> str:
    if isinstance(value, str) and value:
        return value.replace("{w}", str(size)).replace("{h}", str(size))
    if isinstance(value, list) and value:
        last = value[-1] if isinstance(value[-1], dict) else {}
        return str(last.get("url") or last.get("url") or "")
    if isinstance(value, dict):
        if value.get("url"):
            return _artwork_url(value.get("url"), size)
        thumbs = value.get("thumbnails")
        if isinstance(thumbs, list):
            return _artwork_url(thumbs, size)
    return ""


def _as_list(data: Any) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "items", "playlists"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0", "::1", "[::1]"}


def lan_ipv4() -> str | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()
    if not ip or ip.startswith("127."):
        return None
    return ip


def public_base_url(proto: str, host: str) -> str:
    proto = (proto or "http").split(",")[0].strip() or "http"
    host = (host or "127.0.0.1:8123").split(",")[0].strip() or "127.0.0.1:8123"
    hostname, sep, port = host.rpartition(":")
    if host.startswith("[") and "]" in host:
        hostname = host[1:host.index("]")]
        rest = host[host.index("]") + 1:]
        port = rest[1:] if rest.startswith(":") else ""
        sep = ":" if port else ""
    elif not sep:
        hostname, port = host, ""
    if hostname.lower() in _LOOPBACK_HOSTS:
        lan = lan_ipv4()
        if lan:
            host = f"{lan}:{port}" if port else lan
    return f"{proto}://{host}"


def qr_png_data_uri(payload: str) -> str:
    import segno

    buff = io.BytesIO()
    segno.make(payload, error="m").save(
        buff,
        kind="png",
        scale=6,
        border=4,
        dark="#111111",
        light="#ffffff",
    )
    return "data:image/png;base64," + base64.b64encode(buff.getvalue()).decode("ascii")


def qr_svg(payload: str) -> str:
    return qr_png_data_uri(payload)


def reset_apple_auth() -> None:
    global _apple_user_token, _apple_now_playing
    _apple_sessions.clear()
    _apple_user_token = None
    _apple_now_playing = {}


def store_apple_user_token(token: str | None) -> None:
    global _apple_user_token
    _apple_user_token = str(token or "").strip() or None


def apple_user_token() -> str | None:
    return _apple_user_token


def store_apple_now_playing(payload: dict | None) -> dict[str, Any]:
    global _apple_now_playing
    data = payload if isinstance(payload, dict) else {}
    _apple_now_playing = {
        "title": str(data.get("title") or ""),
        "artist": str(data.get("artist") or ""),
        "album": str(data.get("album") or ""),
        "artwork": _artwork_url(data.get("artwork") or data.get("artworkUrl")),
        "playing": bool(data.get("playing")),
    }
    return dict(_apple_now_playing)


def _prune_sessions(now: float | None = None) -> None:
    now = time.time() if now is None else now
    expired = [key for key, rec in _apple_sessions.items() if now - float(rec.get("created") or 0) > SESSION_TTL_S]
    for key in expired:
        _apple_sessions.pop(key, None)


def create_apple_login_session(base_url: str) -> dict[str, Any]:
    _prune_sessions()
    session_id = secrets.token_urlsafe(16)
    code = secrets.token_hex(3).upper()
    login_url = f"{str(base_url or '').rstrip('/')}/apple-login?session={session_id}"
    rec = {
        "session": session_id,
        "code": code,
        "login_url": login_url,
        "authorized": False,
        "created": time.time(),
        "music_user_token": None,
    }
    _apple_sessions[session_id] = rec
    return {
        "ok": True,
        "session": session_id,
        "code": code,
        "login_url": login_url,
        "authorized": False,
        "qr_png": qr_png_data_uri(login_url),
        "expires_in": SESSION_TTL_S,
    }


def apple_login_status(session_id: str) -> dict[str, Any]:
    _prune_sessions()
    rec = _apple_sessions.get(str(session_id or ""))
    if not rec:
        return {"ok": False, "authorized": False, "message": "Unknown or expired Apple login session."}
    payload = {
        "ok": True,
        "session": rec["session"],
        "code": rec["code"],
        "login_url": rec["login_url"],
        "authorized": bool(rec.get("authorized")),
        "qr_png": qr_png_data_uri(rec["login_url"]),
    }
    if rec.get("authorized") and rec.get("music_user_token"):
        payload["music_user_token"] = rec["music_user_token"]
    return payload


def complete_apple_login(session_id: str, music_user_token: str) -> dict[str, Any]:
    _prune_sessions()
    rec = _apple_sessions.get(str(session_id or ""))
    token = str(music_user_token or "").strip()
    if not rec:
        return {"ok": False, "authorized": False, "message": "Unknown or expired Apple login session."}
    if not token:
        return {"ok": False, "authorized": False, "message": "Music user token is required."}
    rec["authorized"] = True
    rec["music_user_token"] = token
    store_apple_user_token(token)
    return {"ok": True, "authorized": True, "session": rec["session"], "message": "Apple ID connected."}


def _youtube_artwork(video: dict) -> str:
    thumbs = video.get("thumbnails") or video.get("thumbnail")
    return _artwork_url(thumbs)


def _youtube_status(settings: dict | None, transport: Any) -> dict[str, Any]:
    host = _host(settings, "youtube_host", DEFAULT_YOUTUBE_HOST)
    token = str((settings or {}).get("youtube_token") or "").strip() or None
    try:
        data = transport.request("GET", f"{host}/api/v1/state", headers=_auth_headers(token))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, TypeError):
        return {
            "source": "youtube_music",
            "connected": False,
            "playing": False,
            "title": "",
            "artist": "",
            "album": "",
            "artwork": "",
            "message": "YouTube Music is not connected. Start Youtopia or set the host in Player connections.",
        }
    data = data if isinstance(data, dict) else {}
    player = data.get("player") if isinstance(data.get("player"), dict) else {}
    video = data.get("video") if isinstance(data.get("video"), dict) else {}
    title = str(video.get("title") or data.get("title") or "").strip()
    artist = str(video.get("author") or video.get("artist") or data.get("artist") or "").strip()
    try:
        playing = int(player.get("trackState") or 0) == 1
    except (TypeError, ValueError):
        playing = False  # a junk trackState must not raise on this hot path
    return {
        "source": "youtube_music",
        "connected": True,
        "playing": playing,
        "title": title,
        "artist": artist,
        "album": str(video.get("album") or ""),
        "artwork": _youtube_artwork(video),
        "progress": player.get("videoProgress"),
        "volume": player.get("volume"),
        "message": "YouTube Music via Youtopia",
    }


def _youtube_command(settings: dict | None, command: str, transport: Any, data: dict | None = None) -> dict[str, Any]:
    host = _host(settings, "youtube_host", DEFAULT_YOUTUBE_HOST)
    token = str((settings or {}).get("youtube_token") or "").strip() or None
    body: dict[str, Any]
    if command == "playItem":
        payload = {key: value for key, value in (data or {}).items() if key in ("videoId", "playlistId") and value}
        if not payload:
            return {"ok": False, "source": "youtube_music", "command": command, "message": "playItem needs videoId or playlistId"}
        body = {"command": "changeVideo", "data": payload}
    else:
        body = {"command": command}
    try:
        transport.request(
            "POST",
            f"{host}/api/v1/command",
            body=body,
            headers=_auth_headers(token),
        )
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, TypeError) as exc:
        return {"ok": False, "source": "youtube_music", "command": command, "message": str(exc)}
    return {"ok": True, "source": "youtube_music", "command": command, "message": f"Sent {command} to Youtopia"}


def _apple_status(settings: dict | None) -> dict[str, Any]:
    token = str((settings or {}).get("apple_developer_token") or "").strip()
    now = dict(_apple_now_playing)
    if not token:
        return {
            "source": "apple_music",
            "connected": False,
            "authorized": False,
            "playing": False,
            "title": "",
            "artist": "",
            "album": "",
            "artwork": "",
            "message": "Apple Music needs a MusicKit developer token in settings.",
        }
    authorized = bool(_effective_user_token(settings))
    if not authorized:
        return {
            "source": "apple_music",
            "connected": False,
            "authorized": False,
            "playing": False,
            "title": "",
            "artist": "",
            "album": "",
            "artwork": "",
            "message": "Apple Music token is set; MusicKit session is not authorized yet.",
        }
    return {
        "source": "apple_music",
        "connected": True,
        "authorized": True,
        "playing": bool(now.get("playing")),
        "title": str(now.get("title") or ""),
        "artist": str(now.get("artist") or ""),
        "album": str(now.get("album") or ""),
        "artwork": str(now.get("artwork") or ""),
        "message": "Apple Music signed in",
    }


def player_status(settings: dict | None = None, source: str = "youtube_music", transport: Any | None = None) -> dict[str, Any]:
    source = (source or "youtube_music").strip().lower()
    if transport is None and (registry := _desktop_registry()) is not None and source in SOURCES:
        status = registry.snapshot(source)
        status = dict(status) if isinstance(status, dict) else {}
        status.setdefault("source", source)
        status["desktop"] = True
        status["backend"] = "desktop"
        status.setdefault("connected", False)
        status.setdefault("playing", False)
        status.setdefault("title", "")
        status.setdefault("artist", "")
        status.setdefault("album", "")
        status.setdefault("artwork", "")
        for field in ("title", "artist", "album", "artwork"):
            status[field] = str(status[field] or "")
        status.setdefault("message", f"{source.replace('_', ' ').title()} desktop provider")
        if "position" in status:
            status.setdefault("progress", status["position"])
            status.setdefault("position_s", status["position"])
        if "duration" in status:
            status.setdefault("duration_s", status["duration"])
        return status
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
        "artwork": "",
        "message": f"Unknown audio source {source!r}",
    }


def player_command(
    settings: dict | None,
    source: str,
    command: str,
    transport: Any | None = None,
    data: dict | None = None,
) -> dict[str, Any]:
    source = (source or "youtube_music").strip().lower()
    command = (command or "").strip()
    if command not in COMMANDS:
        return {"ok": False, "source": source, "command": command, "message": f"Unknown command {command!r}"}
    if transport is None and (registry := _desktop_registry()) is not None and source in SOURCES:
        desktop_command = {"playPause": "toggle", "playItem": "play_id"}.get(command, command)
        desktop_data = data
        if command == "playItem":
            desktop_data = _desktop_play_item(data)
            if desktop_data is None:
                return {"ok": False, "source": source, "command": command, "message": "playItem needs a provider ID"}
        return registry.command(source, desktop_command, desktop_data)
    if source == "youtube_music":
        return _youtube_command(settings, command, transport or UrlTransport(), data)
    if source == "apple_music":
        if not str((settings or {}).get("apple_developer_token") or "").strip():
            return {"ok": False, "source": source, "command": command, "message": "Apple Music needs a MusicKit developer token in settings."}
        if not _effective_user_token(settings):
            return {"ok": False, "source": source, "command": command, "message": "Apple Music playback is not authorized yet."}
        if command == "playItem":
            values = data or {}
            generic_id = str(values.get("id") or "").strip()
            kind = str(values.get("kind") or "song").strip().lower()
            song_id = str(values.get("songId") or (generic_id if kind == "song" else "")).strip()
            album_id = str(values.get("albumId") or (generic_id if kind == "album" else "")).strip()
            playlist_id = str(values.get("playlistId") or (generic_id if kind == "playlist" else "")).strip()
            artist_id = str(values.get("artistId") or (generic_id if kind == "artist" else "")).strip()
            client_play = {}
            if song_id:
                client_play["song"] = song_id
            if album_id:
                client_play["album"] = album_id
            if playlist_id:
                client_play["playlist"] = playlist_id
            if artist_id:
                client_play["artist"] = artist_id
            if not client_play:
                return {"ok": False, "source": source, "command": command, "message": "playItem needs songId or playlistId"}
            return {
                "ok": True,
                "source": source,
                "command": command,
                "client": True,
                "client_play": client_play,
                "message": "Play this item in MusicKit",
            }
        return {
            "ok": True,
            "source": source,
            "command": command,
            "client": True,
            "message": f"Handle {command} in MusicKit",
        }
    return {"ok": False, "source": source, "command": command, "message": f"Unknown audio source {source!r}"}


def _map_apple_item(item: dict, kind: str = "song") -> dict[str, str]:
    attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    return {
        "id": str(item.get("id") or ""),
        "title": str(attrs.get("name") or item.get("title") or ""),
        "artist": str(attrs.get("artistName") or attrs.get("curatorName") or ""),
        "album": str(attrs.get("albumName") or ""),
        "artwork": _artwork_url(attrs.get("artwork")),
        "kind": kind,
    }


def _map_youtube_item(item: dict, kind: str = "song") -> dict[str, str]:
    if not isinstance(item, dict):
        return {"id": "", "title": "", "artist": "", "album": "", "artwork": "", "kind": kind}
    video_id = str(item.get("videoId") or item.get("id") or "")
    return {
        "id": video_id,
        "title": str(item.get("title") or ""),
        "artist": str(item.get("author") or item.get("artist") or ""),
        "album": str(item.get("album") or ""),
        "artwork": _youtube_artwork(item),
        "kind": kind,
    }


def _filter_query(items: list[dict[str, str]], query: str) -> list[dict[str, str]]:
    needle = (query or "").strip().lower()
    if not needle:
        return items
    matched = [
        item
        for item in items
        if needle in " ".join([item.get("title") or "", item.get("artist") or "", item.get("album") or ""]).lower()
    ]
    return matched


def search_library(
    settings: dict | None,
    source: str,
    query: str,
    transport: Any | None = None,
) -> dict[str, Any]:
    source = (source or "youtube_music").strip().lower()
    query = str(query or "").strip()
    if transport is None and (registry := _desktop_registry()) is not None and source in SOURCES:
        result = registry.command(source, "search", {"query": query})
        ok = bool(isinstance(result, dict) and result.get("ok"))
        return {
            "ok": ok, "source": source, "query": query, "items": [], "delegated": True,
            "message": (
                f"Search opened in the {source.replace('_', ' ').title()} provider page."
                if ok else str((result or {}).get("error") or "Provider search could not be opened.")
            ),
        }
    transport = transport or UrlTransport()
    if source == "apple_music":
        token = str((settings or {}).get("apple_developer_token") or "").strip()
        if not token:
            return {"ok": False, "source": source, "query": query, "items": [], "message": "Apple Music needs a MusicKit developer token in settings."}
        storefront = str((settings or {}).get("apple_storefront") or DEFAULT_STOREFRONT).strip() or DEFAULT_STOREFRONT
        url = (
            f"{_apple_base(settings)}/catalog/{urllib.parse.quote(storefront)}/search"
            f"?term={urllib.parse.quote(query)}&types=songs,artists,playlists,albums&limit=25"
        )
        try:
            data = transport.request("GET", url, headers=_apple_headers(settings, _effective_user_token(settings)))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, TypeError) as exc:
            return {"ok": False, "source": source, "query": query, "items": [], "message": str(exc)}
        results = data.get("results") if isinstance(data, dict) else {}
        items = []
        for kind, key in (("song", "songs"), ("artist", "artists"), ("playlist", "playlists"), ("album", "albums")):
            block = results.get(key) if isinstance(results, dict) else None
            rows = (block or {}).get("data") if isinstance(block, dict) else []
            for row in rows or []:
                if isinstance(row, dict):
                    items.append(_map_apple_item(row, kind))
        return {"ok": True, "source": source, "query": query, "items": items}
    if source == "youtube_music":
        host = _host(settings, "youtube_host", DEFAULT_YOUTUBE_HOST)
        token = str((settings or {}).get("youtube_token") or "").strip() or None
        try:
            data = transport.request("GET", f"{host}/api/v1/state", headers=_auth_headers(token))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, TypeError):
            return {
                "ok": False,
                "source": source,
                "query": query,
                "items": [],
                "message": "YouTube Music is not connected. Start Youtopia or set the host in Player connections.",
            }
        data = data if isinstance(data, dict) else {}
        player = data.get("player") if isinstance(data.get("player"), dict) else {}
        queue = player.get("queue") if isinstance(player.get("queue"), dict) else {}
        raw_items = []
        for key in ("items", "automixItems"):
            raw_items.extend(_as_list(queue.get(key)))
        video = data.get("video") if isinstance(data.get("video"), dict) else {}
        if video.get("title") or video.get("id"):
            raw_items.insert(0, video)
        items = _filter_query([_map_youtube_item(item) for item in raw_items if isinstance(item, dict)], query)
        items = [item for item in items if item.get("id") or item.get("title")]
        return {"ok": True, "source": source, "query": query, "items": items, "message": "YouTube Music queue and now playing"}
    return {"ok": False, "source": source, "query": query, "items": [], "message": f"Unknown audio source {source!r}"}


def list_playlists(
    settings: dict | None,
    source: str,
    transport: Any | None = None,
) -> dict[str, Any]:
    source = (source or "youtube_music").strip().lower()
    if transport is None and _desktop_registry() is not None and source in SOURCES:
        return {
            "ok": True, "source": source, "playlists": [], "delegated": True,
            "message": f"Account playlists are available in the {source.replace('_', ' ').title()} provider page.",
        }
    transport = transport or UrlTransport()
    if source == "youtube_music":
        host = _host(settings, "youtube_host", DEFAULT_YOUTUBE_HOST)
        token = str((settings or {}).get("youtube_token") or "").strip() or None
        try:
            data = transport.request("GET", f"{host}/api/v1/playlists", headers=_auth_headers(token))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, TypeError):
            return {
                "ok": False,
                "source": source,
                "playlists": [],
                "message": "YouTube Music is not connected. Start Youtopia or set the host in Player connections.",
            }
        playlists = []
        for row in _as_list(data):
            if isinstance(row, dict):
                playlists.append(
                    {
                        "id": str(row.get("id") or ""),
                        "title": str(row.get("title") or row.get("name") or ""),
                        "artwork": _artwork_url(row.get("artwork") or row.get("thumbnails")),
                    }
                )
        return {"ok": True, "source": source, "playlists": playlists}
    if source == "apple_music":
        token = str((settings or {}).get("apple_developer_token") or "").strip()
        if not token:
            return {"ok": False, "source": source, "playlists": [], "message": "Apple Music needs a MusicKit developer token in settings."}
        user_token = _effective_user_token(settings)
        if not user_token:
            return {"ok": False, "source": source, "playlists": [], "message": "Sign in with Apple ID to load library playlists."}
        try:
            data = transport.request(
                "GET",
                f"{_apple_base(settings)}/me/library/playlists?limit=25",
                headers=_apple_headers(settings, user_token),
            )
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, TypeError) as exc:
            return {"ok": False, "source": source, "playlists": [], "message": str(exc)}
        playlists = []
        for row in _as_list(data if not isinstance(data, dict) else data.get("data") or data):
            if isinstance(row, dict):
                mapped = _map_apple_item(row, "playlist")
                playlists.append({"id": mapped["id"], "title": mapped["title"], "artwork": mapped["artwork"]})
        return {"ok": True, "source": source, "playlists": playlists}
    return {"ok": False, "source": source, "playlists": [], "message": f"Unknown audio source {source!r}"}


def settings_from_config(config: dict | None) -> dict[str, str]:
    block = (config or {}).get("audio_player")
    if not isinstance(block, dict):
        return {}
    settings = {
        key: str(block[key]).strip()
        for key in ("youtube_host", "youtube_token", "apple_developer_token", "apple_storefront", "apple_user_token", "apple_api_base", "apple_origin")
        if block.get(key)
    }
    if "enabled" in block:
        settings["enabled"] = "false" if str(block["enabled"]).strip().lower() in ("false", "0", "no", "off") else "true"
    return settings
