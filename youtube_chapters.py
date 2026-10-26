#!/usr/bin/env python3
"""YouTube chapter resolution for long-form music mixes on the TV.

The Fire TV media session only exposes the mix title (e.g.
``80's Dance Party Mix - [Wukileaks], Wuki, null``), so one 48-minute video looks
like a single "track". YouTube videos carry authored chapters whose titles are
often ``Artist - Song``; resolving the video with yt-dlp lets the lighting layer
follow the songs inside a mix.

Signal chain (read-only, best effort, no WLED writes):

1. ``resolve_cached`` reads a small on-disk cache keyed by the normalized media
   description. Cache hits are instant; misses start at most ONE background
   daemon thread that searches/fetches with yt-dlp and writes the result (a
   negative entry on failure, so failures are retried after 6 h, not every poll).
2. ``enrich_observation`` is what the smart director calls every few seconds:
   it is a pure cache read, attaches ``video``/``chapters``/``current_chapter``/
   ``music_mix`` to a copy of ``media_session`` for YouTube apps only, and lets a
   refresh start only after the same description has been stable for >= 5 s
   (ads, previews and rapid channel surfing never trigger a search).
3. ``current_chapter`` maps ``media_session.position_s`` to the playing chapter.

Everything is injectable: ``runner(args, timeout) -> stdout`` replaces the yt-dlp
subprocess so tests never touch the network, and ``cache_path`` redirects the
cache file. Every public function returns safely: failures degrade to "no
chapters" and never raise.
"""

from __future__ import annotations

import copy
import json
import logging
import math
import os
import re
import shutil
import statistics
import subprocess
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any, Callable

import lightctl

logger = logging.getLogger("lightss.youtube_chapters")

#: Positive cache lifetime: a resolved video/chapter list is good for 30 days.
CACHE_TTL_S = 30 * 24 * 3600
#: Negative cache lifetime: failed lookups are retried after 6 hours.
NEGATIVE_TTL_S = 6 * 3600
#: How many search results yt-dlp returns per query.
SEARCH_RESULTS = 3
#: Foreground apps whose media sessions can be enriched with videos/chapters.
YOUTUBE_APPS = frozenset({
    "com.amazon.firetv.youtube",
    "com.google.android.youtube.tv",
    "com.google.android.youtube.tvmusic",
    "com.google.android.apps.youtube.music",
})
#: Cache file default (report-like data, not configuration); injectable.
CACHE_PATH = lightctl._CONFIG_ROOT / "youtube_chapters.json"
#: On-disk cache format version.
CACHE_VERSION = 1
#: Upper bound on cached descriptions and chapters kept per entry.
MAX_CACHE_ENTRIES = 200
MAX_ENRICH_CHAPTERS = 200
#: A description must be observed continuously this long before a search starts.
STABLE_AFTER_S = 5.0
#: Bound on the description-stability tracker.
MAX_STABILITY_ENTRIES = 8
#: Minimum title-match score accepting a search result.
TITLE_MATCH_THRESHOLD = 0.6
#: yt-dlp ``--print`` template for search rows (tab separated).
SEARCH_PRINT = "%(id)s\t%(duration)s\t%(channel)s\t%(title)s"
#: yt-dlp binary fallback when it is not on PATH.
YTDLP = "/home/linuxbrew/.linuxbrew/bin/yt-dlp"

Runner = Callable[[list[str], float], str]

# Clock injection keeps the cache/stability logic testable without sleeping.
_now = time.time
_monotonic = time.monotonic

_APOSTROPHES = "'\u2019\u02bc`"
_BRACKETED = re.compile(r"\[[^\]]*\]|\([^)]*\)|\{[^}]*\}")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_TRAILING_TAGS = re.compile(r"(?:\s*[\[\(【][^\]\)】]*[\]\)】])+\s*[.)]*\s*$")
_DASH_SPLIT = re.compile(r"\s+[-\u2013\u2014]\s+")


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def normalize_title(text: Any) -> str:
    """Lowercase, accent-folded, punctuation/bracket-free, space-collapsed text.

    Used both for the cache key and for fuzzy title matching, so formatting
    differences (``[Official Video]``, ``’`` vs ``'``, trailing punctuation)
    do not produce different identities.
    """
    if text is None:
        return ""
    value = unicodedata.normalize("NFKD", str(text))
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = _BRACKETED.sub(" ", value)
    value = value.lower()
    for char in _APOSTROPHES:
        value = value.replace(char, "")
    value = value.replace("&", " and ")
    value = _NON_ALNUM.sub(" ", value)
    return " ".join(value.split())


def _tokens(text: Any) -> set[str]:
    return set(normalize_title(text).split())


def split_media_description(description: Any) -> dict[str, str | None]:
    """Split Android's ``"Title, Artist, Album"`` media description heuristically.

    Android does not escape commas inside titles, so the raw middle parts are
    kept as part of the title: ``"Song, Part 2, Artist, Album"`` becomes
    ``{"title": "Song, Part 2", "artist": "Artist", "album": "Album"}``.
    Trailing empty/``"null"`` parts are dropped (Fire TV descriptions commonly
    end in ``", null"``).
    """
    result: dict[str, str | None] = {"title": "", "artist": None, "album": None}
    if not isinstance(description, str):
        return result
    parts = [part.strip() for part in description.split(",")]
    while parts and (not parts[-1] or parts[-1].lower() in {"null", "none"}):
        parts.pop()
    if not parts:
        return result
    if len(parts) == 1:
        result["title"] = parts[0]
    elif len(parts) == 2:
        result["title"] = parts[0]
        result["artist"] = parts[1] or None
    else:
        result["title"] = ", ".join(parts[:-2])
        result["artist"] = parts[-2] or None
        result["album"] = parts[-1] or None
    return result


def title_match_score(found_title: Any, description: Any) -> float:
    """Token-containment score 0..1 of a search-result title in the description.

    ``1.0`` means every token of the found title appears in the description;
    ``0.0`` means no token overlaps. Results scoring ``>= TITLE_MATCH_THRESHOLD``
    (0.6) are accepted as the playing video.
    """
    found = _tokens(found_title)
    described = _tokens(description)
    if not found or not described:
        return 0.0
    overlap = len(found & described)
    if not overlap:
        return 0.0
    return round(min(1.0, overlap / len(found)), 3)


# ---------------------------------------------------------------------------
# Chapters and songs
# ---------------------------------------------------------------------------

def _to_int(value: Any) -> int | None:
    try:
        if isinstance(value, bool):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def parse_chapters(payload: dict) -> list[dict[str, Any]]:
    """Normalize a yt-dlp JSON dict's ``chapters`` to start/end/title entries.

    ``end_s`` is the authored end time when present, otherwise the next
    chapter's start, otherwise the video duration (``None`` if unavailable).
    """
    if not isinstance(payload, dict):
        return []
    raw_chapters = payload.get("chapters")
    if not isinstance(raw_chapters, list):
        return []
    duration = _to_int(payload.get("duration"))
    if duration is None:
        duration = _to_int(payload.get("duration_s"))
    cleaned: list[tuple[int, dict]] = []
    for entry in raw_chapters:
        if not isinstance(entry, dict):
            continue
        start = _to_int(entry.get("start_time", entry.get("start_s")))
        if start is None or start < 0:
            continue
        cleaned.append((start, entry))
    chapters: list[dict[str, Any]] = []
    for position, (start, entry) in enumerate(cleaned):
        end = _to_int(entry.get("end_time", entry.get("end_s")))
        if end is None or end <= start:
            if position + 1 < len(cleaned):
                end = cleaned[position + 1][0]
            elif duration is not None and duration > start:
                end = duration
            else:
                end = None
        title = str(entry.get("title") or "").strip()
        chapters.append({"start_s": start, "end_s": end, "title": title})
    return chapters


def _strip_trailing_tags(title: str) -> str:
    previous = None
    while title and title != previous:
        previous = title
        title = _TRAILING_TAGS.sub("", title).strip()
        title = title.rstrip(" .)-\u2013\u2014").strip()
    return title


def song_from_chapter(title: Any) -> dict[str, str | None]:
    """Parse ``"Artist - Song [tag].)"`` into ``{"artist", "title"}``.

    Splits on the first spaced hyphen/en dash/em dash; trailing bracketed tags
    (``[Wukileak])``, ``[Official Video]``, ``(Lyrics)``) are removed first.
    Title-only chapters return ``artist=None``.
    """
    text = _strip_trailing_tags(str(title or "").strip())
    if not text:
        return {"artist": None, "title": ""}
    match = _DASH_SPLIT.search(text)
    if match is None:
        return {"artist": None, "title": text}
    artist = text[:match.start()].strip(" -\u2013\u2014")
    song = text[match.end():].strip(" -\u2013\u2014")
    if not artist or not song:
        return {"artist": None, "title": text}
    return {"artist": artist, "title": song}


def looks_like_music_mix(chapters: list[dict[str, Any]]) -> bool:
    """True when chapters look like a long-form music mix.

    Requires at least 3 chapters, >= 70% parsing as ``Artist - Song``, and a
    typical chapter length of 30..900 s (70% of chapter durations in range).
    """
    if not isinstance(chapters, (list, tuple)):
        return False
    entries = [entry for entry in chapters if isinstance(entry, dict)]
    if len(entries) < 3:
        return False
    parsed = [song_from_chapter(entry.get("title")) for entry in entries]
    with_artist = sum(1 for song in parsed if song.get("artist") and song.get("title"))
    if with_artist / len(entries) < 0.7:
        return False
    durations: list[int] = []
    for entry in entries:
        start = _to_int(entry.get("start_s"))
        end = _to_int(entry.get("end_s"))
        if start is not None and end is not None and end > start:
            durations.append(end - start)
    if not durations:
        return False
    typical = statistics.median(durations)
    if not 30 <= typical <= 900:
        return False
    in_range = sum(1 for duration in durations if 30 <= duration <= 900)
    return in_range / len(durations) >= 0.7


def current_chapter(
    chapters: list[dict[str, Any]], position_s: Any
) -> dict[str, Any] | None:
    """Map a playback position (seconds) to the current chapter.

    ``index`` is 0-based (matching ``chapters`` list indexing); ``track`` is
    ``"Artist — Song"`` when the chapter parses an artist, else the song title.
    Positions past the last chapter clamp to it; positions before the first
    chapter return ``None``.
    """
    if not isinstance(chapters, (list, tuple)) or not chapters:
        return None
    position = _to_float(position_s)
    if position is None:
        return None
    entries = [
        entry for entry in chapters
        if isinstance(entry, dict) and _to_int(entry.get("start_s")) is not None
    ]
    if not entries:
        return None
    total = len(entries)
    chosen: tuple[int, dict, int, int | None] | None = None
    for index, entry in enumerate(entries):
        start = _to_int(entry.get("start_s")) or 0
        end = _to_int(entry.get("end_s"))
        if end is None and index + 1 < total:
            end = _to_int(entries[index + 1].get("start_s"))
        if position < start:
            continue
        if end is not None and position >= end:
            continue
        chosen = (index, entry, start, end)
        break
    if chosen is None:
        index = total - 1
        entry = entries[index]
        start = _to_int(entry.get("start_s")) or 0
        if position < start:
            return None
        chosen = (index, entry, start, _to_int(entry.get("end_s")))
    index, entry, start, end = chosen
    song = song_from_chapter(entry.get("title"))
    artist = song.get("artist")
    title = str(song.get("title") or "")
    track = f"{artist} \u2014 {title}" if artist else title
    return {
        "index": index,
        "total": total,
        "start_s": start,
        "end_s": end,
        "title": title,
        "artist": artist,
        "track": track,
    }


# ---------------------------------------------------------------------------
# Cache (small JSON file, atomic 0600 write, in-memory with mtime check)
# ---------------------------------------------------------------------------

_cache_memory_lock = threading.RLock()
_cache_memory: dict[str, Any] = {
    "path": None,
    "mtime_ns": None,
    "data": {"version": CACHE_VERSION, "entries": {}},
}
_cache_write_lock = threading.Lock()


def _empty_cache() -> dict[str, Any]:
    return {"version": CACHE_VERSION, "entries": {}}


def _cache_file(cache_path: Path | str | None) -> Path:
    return Path(cache_path) if cache_path is not None else Path(CACHE_PATH)


def _read_cache_json(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _empty_cache()
    except OSError as exc:
        logger.debug("YouTube chapter cache unreadable at %s: %s", path, exc)
        return _empty_cache()
    try:
        data = json.loads(text)
    except ValueError as exc:
        logger.info("Ignoring invalid YouTube chapter cache %s: %s", path, exc)
        return _empty_cache()
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        return _empty_cache()
    return {"version": data.get("version", CACHE_VERSION), "entries": data["entries"]}


def _load_cache(path: Path) -> dict[str, Any]:
    """Read the cache file at most once per on-disk change (mtime checked)."""
    try:
        mtime_ns = os.stat(path).st_mtime_ns
    except OSError:
        mtime_ns = None
    with _cache_memory_lock:
        if _cache_memory["path"] == str(path) and _cache_memory["mtime_ns"] == mtime_ns:
            return _cache_memory["data"]
        data = _read_cache_json(path) if mtime_ns is not None else _empty_cache()
        _cache_memory.update(path=str(path), mtime_ns=mtime_ns, data=data)
        return data


def _atomic_write_cache(path: Path, data: Any) -> None:
    """Write the cache atomically (temp + fsync + os.replace) with mode 0600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True, default=str)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _cache_entry(cache_path: Path | str | None, key: str) -> dict[str, Any] | None:
    entries = _load_cache(_cache_file(cache_path)).get("entries") or {}
    entry = entries.get(key)
    return entry if isinstance(entry, dict) else None


def _entry_is_fresh(entry: dict[str, Any], now: float) -> bool:
    fetched_at = entry.get("fetched_at")
    if not isinstance(fetched_at, (int, float)):
        return False
    age = now - float(fetched_at)
    ttl = NEGATIVE_TTL_S if entry.get("status") == "negative" else CACHE_TTL_S
    return age <= ttl


def _store_entry(
    description: str, info: dict[str, Any] | None, cache_path: Path | str | None = None
) -> None:
    """Cache a positive result or a negative marker; failures are logged only."""
    key = normalize_title(description)
    if not key:
        return
    entry: dict[str, Any] = {
        "status": "ok" if info else "negative",
        "fetched_at": _now(),
        "description": description,
    }
    if info:
        clean = dict(info)
        clean["chapters"] = [
            dict(chapter)
            for chapter in (info.get("chapters") or [])
            if isinstance(chapter, dict)
        ][:MAX_ENRICH_CHAPTERS]
        entry["info"] = clean
    path = _cache_file(cache_path)
    with _cache_write_lock:
        data = _read_cache_json(path)  # fresh merge: other threads may have written
        entries = dict(data.get("entries") or {})
        entries[key] = entry
        if len(entries) > MAX_CACHE_ENTRIES:
            ordered = sorted(
                entries.items(),
                key=lambda item: float(item[1].get("fetched_at") or 0),
                reverse=True,
            )[:MAX_CACHE_ENTRIES]
            entries = dict(ordered)
        payload = {"version": CACHE_VERSION, "entries": entries}
        try:
            _atomic_write_cache(path, payload)
        except Exception as exc:  # persistence is best effort, never fatal
            logger.info("Could not write YouTube chapter cache %s: %s", path, exc)
            return
        try:
            mtime_ns = os.stat(path).st_mtime_ns
        except OSError:
            mtime_ns = None
        with _cache_memory_lock:
            _cache_memory.update(path=str(path), mtime_ns=mtime_ns, data=payload)


# ---------------------------------------------------------------------------
# yt-dlp search + fetch (sync; injectable runner)
# ---------------------------------------------------------------------------

def _binary() -> str:
    return shutil.which("yt-dlp") or YTDLP


def _run_ytdlp(args: list[str], timeout: float) -> str:
    """Default runner: call the yt-dlp binary and return stdout."""
    binary = _binary()
    if not binary:
        raise RuntimeError("yt-dlp is not installed")
    proc = subprocess.run(
        [binary, *args], capture_output=True, text=True, timeout=timeout, check=False
    )
    if proc.returncode != 0:
        logger.info(
            "yt-dlp exited with %s: %s", proc.returncode, (proc.stderr or "").strip()[:400]
        )
        return ""
    return proc.stdout or ""


def _parse_search_rows(stdout: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in (stdout or "").splitlines():
        fields = line.split("\t")
        if len(fields) < 2:
            continue
        video_id = fields[0].strip()
        if not video_id:
            continue
        duration = _to_int(_to_float(fields[1])) if fields[1].strip() else None
        channel = fields[2].strip() if len(fields) > 2 and fields[2].strip() else None
        title = "\t".join(fields[3:]).strip() if len(fields) > 3 else ""
        if not title:
            continue
        rows.append(
            {"video_id": video_id, "duration_s": duration, "channel": channel, "title": title}
        )
    return rows


def _pick_candidate(
    rows: list[dict[str, Any]], description: str
) -> dict[str, Any] | None:
    """Best search result by title match, channel overlap as tie-breaker."""
    description_tokens = _tokens(description)
    best: dict[str, Any] | None = None
    best_key: tuple[float, float] | None = None
    for row in rows:
        score = title_match_score(row.get("title"), description)
        if score < TITLE_MATCH_THRESHOLD:
            continue
        channel_tokens = _tokens(row.get("channel") or "")
        overlap = (
            len(channel_tokens & description_tokens) / len(channel_tokens)
            if channel_tokens
            else 0.0
        )
        key = (score, round(overlap, 3))
        if best_key is None or key > best_key:
            best, best_key = row, key
    return best


def fetch_for_description(
    description: str, *, runner: Runner | None = None, timeout: float = 60.0
) -> dict[str, Any] | None:
    """Search YouTube for the description and fetch the best match's chapters.

    Returns ``{"video_id","title","channel","duration_s","chapters","fetched_at"}``
    or ``None`` on any failure. ``runner`` replaces the yt-dlp subprocess.
    """
    try:
        if not isinstance(description, str) or not description.strip():
            return None
        parts = split_media_description(description)
        query = (parts.get("title") or "").strip() or description.strip()
        run = runner or _run_ytdlp
        search_stdout = run(
            [
                "--skip-download",
                "--no-warnings",
                "--flat-playlist",
                "--print",
                SEARCH_PRINT,
                f"ytsearch{SEARCH_RESULTS}:{query}",
            ],
            timeout,
        )
        candidate = _pick_candidate(_parse_search_rows(search_stdout), description)
        if candidate is None:
            logger.info("No YouTube search result matched %r", query)
            return None
        url = f"https://www.youtube.com/watch?v={candidate['video_id']}"
        dump_stdout = run(
            ["--skip-download", "--no-warnings", "--dump-single-json", url], timeout
        )
        if not dump_stdout or not dump_stdout.strip():
            logger.info("yt-dlp returned no video JSON for %s", candidate["video_id"])
            return None
        payload = json.loads(dump_stdout)
        if not isinstance(payload, dict):
            return None
        chapters = parse_chapters(payload)
        channel = payload.get("channel") or payload.get("uploader") or candidate.get("channel")
        duration = _to_int(payload.get("duration"))
        if duration is None:
            duration = candidate.get("duration_s")
        return {
            "video_id": str(payload.get("id") or candidate["video_id"]),
            "title": str(payload.get("title") or candidate.get("title") or query),
            "channel": str(channel) if channel else None,
            "duration_s": duration,
            "chapters": chapters[:MAX_ENRICH_CHAPTERS],
            "fetched_at": _now(),
        }
    except Exception as exc:
        logger.info("YouTube chapter fetch failed for %r: %s", description, exc)
        return None


# ---------------------------------------------------------------------------
# Background resolution (one in flight process-wide)
# ---------------------------------------------------------------------------

_inflight_lock = threading.Lock()
_inflight: set[str] = set()


def _background_worker(
    description: str, runner: Runner | None, cache_path: Path | str | None
) -> None:
    key = normalize_title(description)
    try:
        try:
            info = fetch_for_description(description, runner=runner)
        except Exception:
            logger.info(
                "Background YouTube chapter resolution failed for %r",
                description,
                exc_info=True,
            )
            info = None
        try:
            _store_entry(description, info, cache_path=cache_path)
        except Exception:
            logger.info(
                "Could not cache YouTube chapter result for %r",
                description,
                exc_info=True,
            )
    finally:
        with _inflight_lock:
            _inflight.discard(key)


def _start_background_resolve(
    description: str, *, runner: Runner | None = None, cache_path: Path | str | None = None
) -> bool:
    """Start one background resolve; False when one is already running."""
    key = normalize_title(description)
    if not key:
        return False
    with _inflight_lock:
        if _inflight:  # at most one yt-dlp search/fetch in flight process-wide
            return False
        _inflight.add(key)
    thread = threading.Thread(
        target=_background_worker,
        args=(description, runner, cache_path),
        name="lightss-youtube-chapters",
        daemon=True,
    )
    try:
        thread.start()
    except Exception as exc:
        logger.info("Could not start YouTube chapter resolver: %s", exc)
        with _inflight_lock:
            _inflight.discard(key)
        return False
    return True


def resolve_cached(
    description: str,
    *,
    refresh: bool = True,
    runner: Runner | None = None,
    cache_path: Path | str | None = None,
) -> dict[str, Any] | None:
    """Return cached chapter info for a description, or ``None``.

    Cache-only: a fresh positive entry is returned; a fresh negative entry or a
    missing/stale entry returns ``None``. With ``refresh=True`` a miss starts at
    most one background resolver (never blocks, never raises).
    """
    try:
        if not isinstance(description, str) or not description.strip():
            return None
        key = normalize_title(description)
        if not key:
            return None
        entry = _cache_entry(cache_path, key)
        if entry is not None and _entry_is_fresh(entry, _now()):
            if entry.get("status") == "ok" and isinstance(entry.get("info"), dict):
                return copy.deepcopy(entry["info"])
            return None
        if refresh:
            _start_background_resolve(
                description, runner=runner, cache_path=cache_path
            )
        return None
    except Exception:
        logger.debug("YouTube chapter cache read failed", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Description stability (ads/previews must not trigger searches)
# ---------------------------------------------------------------------------

_stability_lock = threading.Lock()
_stability_entries: dict[str, float] = {}
_last_description: str | None = None


def _mark_seen(description: str) -> bool:
    """Remember first-seen time for the current continuous streak.

    Returns True only when the same description has been observed continuously
    for at least ``STABLE_AFTER_S`` seconds. Any description change starts a new
    streak, so rapid churn (ads, previews, channel surfing) never resolves.
    """
    global _last_description
    key = normalize_title(description)
    if not key:
        return False
    seen_at = _monotonic()
    with _stability_lock:
        if key != _last_description:
            _last_description = key
            _stability_entries[key] = seen_at
        first_seen = _stability_entries.get(key)
        if first_seen is None:
            first_seen = seen_at
            _stability_entries[key] = first_seen
        if len(_stability_entries) > MAX_STABILITY_ENTRIES:
            oldest = min(_stability_entries, key=lambda name: _stability_entries[name])
            _stability_entries.pop(oldest, None)
        return seen_at - first_seen >= STABLE_AFTER_S


# ---------------------------------------------------------------------------
# Observation enrichment (what the smart director calls every few seconds)
# ---------------------------------------------------------------------------

def enrich_observation(
    observation: dict,
    *,
    refresh: bool = True,
    runner: Runner | None = None,
    cache_path: Path | str | None = None,
) -> dict:
    """Attach resolved YouTube chapter information to a media observation.

    Only YouTube foreground apps with a media-session description are touched.
    Cache hits are attached immediately; on a miss this is a pure cache read
    unless the same description has been stable for >= 5 s, in which case one
    background resolver may start. A copy of ``media_session`` gains::

        video          {video_id, title, channel, duration_s}
        chapters       [{start_s, end_s, title}, ...]   (max 200)
        current_chapter{index, total, start_s, end_s, title, artist, track} | None
        music_mix      bool

    Unknown/unavailable data leaves the observation unchanged. Never raises.
    """
    try:
        if not isinstance(observation, dict):
            return observation
        if observation.get("foreground_app") not in YOUTUBE_APPS:
            return observation
        media_session = observation.get("media_session")
        if not isinstance(media_session, dict):
            return observation
        description = media_session.get("description")
        if not isinstance(description, str) or not description.strip():
            return observation
        info = resolve_cached(
            description, refresh=False, runner=runner, cache_path=cache_path
        )
        if info is None:
            # Pure cache read until the description proves stable.
            if refresh and _mark_seen(description):
                resolve_cached(
                    description, refresh=True, runner=runner, cache_path=cache_path
                )
            return observation
        enriched = copy.deepcopy(observation)
        session = dict(enriched.get("media_session") or {})
        chapters = [
            dict(chapter)
            for chapter in (info.get("chapters") or [])
            if isinstance(chapter, dict)
        ][:MAX_ENRICH_CHAPTERS]
        session["video"] = {
            "video_id": info.get("video_id"),
            "title": info.get("title"),
            "channel": info.get("channel"),
            "duration_s": info.get("duration_s"),
        }
        session["chapters"] = chapters
        session["current_chapter"] = current_chapter(chapters, media_session.get("position_s"))
        session["music_mix"] = looks_like_music_mix(chapters)
        enriched["media_session"] = session
        return enriched
    except Exception:
        logger.debug("YouTube chapter enrichment failed", exc_info=True)
        return observation


__all__ = [
    "CACHE_PATH",
    "CACHE_TTL_S",
    "NEGATIVE_TTL_S",
    "SEARCH_RESULTS",
    "YOUTUBE_APPS",
    "normalize_title",
    "split_media_description",
    "title_match_score",
    "parse_chapters",
    "song_from_chapter",
    "looks_like_music_mix",
    "current_chapter",
    "resolve_cached",
    "fetch_for_description",
    "enrich_observation",
]
