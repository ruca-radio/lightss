#!/usr/bin/env python3
"""TV Trivia — ticker facts about whoever is on the screen.

The /tv ambient page shows a now-playing ticker; this module feeds it
short trivia lines about the current artist. It answers immediately and
never blocks the web request: on a cache miss it hands the lookup to a
daemon background thread that asks the chat provider for 6-8 ticker
lines (facts, history, tour/recent-activity notes) and files them in a
disk cache at ~/.config/lightss/tv_trivia_cache.json, keyed by
artist.lower() with a 30-day TTL. The first request for an artist
therefore returns an empty list; the trivia lands on the next poll.

The entry point is trivia_payload(track, settings): track is a
"Artist — Title" string (em dash separator) or None, settings is the
same {"base_url", "model", "api_key_env"} shape light_gui.ai_settings()
returns. It always returns {"ok", "artist", "items"} and never raises —
a trivia ticker must never take the ambient page down. Stdlib only.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from pathlib import Path

logger = logging.getLogger("tv_trivia")

# Disk cache: {artist_key: {"fetched": epoch, "items": [str, ...]}}.
CACHE_PATH = Path.home() / ".config" / "lightss" / "tv_trivia_cache.json"
CACHE_TTL_S = 30 * 24 * 3600  # 30 days

# One generation thread at a time; cache guard against duplicate lookups.
_cache_lock = threading.Lock()
_in_flight: set[str] = set()

# Negative caching: after a failed attempt, don't respawn a provider call
# (which holds ai_chat.provider_lock up to 30 s) on every UI poll.
_last_attempt: dict[str, float] = {}
RETRY_COOLDOWN_S = 5 * 60


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.info("TV trivia: cache write failed: %s", exc)


def _cached_items(artist_key: str) -> list[str] | None:
    """Return cached items for the artist, or None on miss/expiry."""
    entry = _load_cache().get(artist_key)
    if not isinstance(entry, dict):
        return None
    try:
        fetched = float(entry.get("fetched") or 0)
    except (TypeError, ValueError):
        return None  # corrupt fetched value counts as expired, so we regenerate
    if time.time() - fetched > CACHE_TTL_S:
        return None
    items = entry.get("items")
    if isinstance(items, list):
        return [str(item) for item in items]
    return None


def _generate_items(artist: str, settings: dict, timeout: float = 30.0) -> list[str]:
    """Ask the chat provider for 6-8 ticker lines about the artist.

    Returns [] on any failure (caller caches nothing and retries later).
    Mirrors music_director.classify_track_ai's HTTP pattern.
    """
    import os

    url = f"{str(settings['base_url']).rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get(str(settings.get("api_key_env") or "").lstrip("$"), "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    body = {
        "model": settings.get("model"),
        "messages": [
            {"role": "system", "content": (
                "You write ticker lines for a music TV ambient display. Reply with ONLY "
                "a JSON array of 6-8 short strings about the artist: facts, history, "
                "tour or recent-activity notes. Each line under 90 characters, plain "
                "text, no emoji (a ♪ prefix is fine). No markdown, no commentary."
            )},
            {"role": "user", "content": artist},
        ],
        "max_tokens": 800,  # thinking models burn tokens on reasoning first
        # note: no temperature — some models only allow their default
    }
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        import ai_chat  # local import keeps the module importable without it

        with ai_chat.provider_lock:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read())
        # 200-with-error-body, empty choices, or a null message all land here.
        text = (
            ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        ).strip()
    except Exception as exc:
        logger.info("TV trivia: generation failed for %r: %s", artist, exc)
        return []
    # Models love wrapping JSON in ```json fences — strip them if present.
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        items = json.loads(text)
    except Exception:
        logger.info("TV trivia: unparseable reply for %r: %r", artist, text[:120])
        return []
    if not isinstance(items, list):
        return []
    return [str(item).strip() for item in items if str(item).strip()][:8]


def _background_generate(artist: str, artist_key: str, settings: dict) -> None:
    """Daemon worker: generate trivia, file it, release the in-flight slot."""
    try:
        items = _generate_items(artist, settings)
        if items:
            with _cache_lock:
                cache = _load_cache()
                cache[artist_key] = {"fetched": time.time(), "items": items}
                _save_cache(cache)
            logger.info("TV trivia: cached %d lines for %r", len(items), artist)
    except Exception as exc:  # a trivia thread must never die loudly
        logger.info("TV trivia: background generation failed for %r: %s", artist, exc)
    finally:
        with _cache_lock:
            _in_flight.discard(artist_key)


def trivia_payload(track: str | None, settings: dict) -> dict:
    """Return the trivia payload for the TV ambient page. Never raises.

    track: "Artist — Title" (em dash separator) or None. On a cache hit the
    cached items come back; on a miss a daemon thread is spawned to generate
    them and this call returns items=[] immediately (non-blocking).
    """
    try:
        artist = None
        if track:
            parts = str(track).split("—", 1)
            if len(parts) == 2:
                artist = parts[0].strip() or None
        if artist is None:
            return {"ok": True, "artist": None, "items": []}
        artist_key = artist.lower()
        items = _cached_items(artist_key)
        if items is not None:
            return {"ok": True, "artist": artist, "items": items}
        with _cache_lock:
            if artist_key not in _in_flight:
                if time.time() - _last_attempt.get(artist_key, 0.0) < RETRY_COOLDOWN_S:
                    return {"ok": True, "artist": artist, "items": []}
                _last_attempt[artist_key] = time.time()
                _in_flight.add(artist_key)
                threading.Thread(
                    target=_background_generate,
                    args=(artist, artist_key, settings),
                    daemon=True,
                    name="lightss-tv-trivia",
                ).start()
        return {"ok": True, "artist": artist, "items": []}
    except Exception as exc:  # contract: NEVER raises
        logger.info("TV trivia: payload failed: %s", exc)
        return {"ok": False, "artist": None, "items": []}
