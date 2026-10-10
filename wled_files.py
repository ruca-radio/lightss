#!/usr/bin/env python3
"""Author WLED 16 custom palettes from the AI/GUI/MCP surfaces.

WLED 16.0 added first-class custom palettes: a device can hold many
(``info.cpalmax`` — 129 on ESP32, 10 on ESP8266), they are addressed by real
palette ids, and any effect that uses a palette can be driven by one.

There is no bespoke "create palette" JSON endpoint — the WLED UI itself saves
palettes by uploading ``paletteN.json`` through the file manager
(``data/edit.htm`` -> ``uploadFile`` -> ``POST /upload``). The firmware reloads
them **immediately** after such an upload, with no reboot:

    wled_server.cpp: handleUpload()  ->
        if (filename.indexOf("palette") >= 0 && filename.indexOf(".json") >= 0)
            loadCustomPalettes();

That is the mechanism this module uses, so a palette the AI invents is on the
wall seconds later and survives reboots.

Palette file format (``{"palette":[pos,r,g,b, ...]}``, positions 0-255):

    {"palette":[0,255,33,4, 43,255,68,25, 255,87,255,65]}

Palette ids are version dependent: on 16.0+ custom palettes live in 72..200 and
are allocated *downward* (the first custom palette is 200, the next 199), while
before 16.0 they count down from 255.
"""

from __future__ import annotations

import mimetypes
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Iterable, Sequence

WLED_PALETTE_SLOT_LIMIT = 129  # ESP32 ceiling; ESP8266 is 10 (info.cpalmax)
PALETTE_ID_V16_FIRST = 200
PALETTE_ID_V16_LAST = 72
LEGACY_PALETTE_ID_FIRST = 255
MAX_PALETTE_STOPS = 16
MIN_PALETTE_STOPS = 2


class WledFileError(RuntimeError):
    """A WLED filesystem operation failed."""


# ---------------------------------------------------------------------------
# Palette model
# ---------------------------------------------------------------------------

def normalize_color(value: Any) -> tuple[int, int, int]:
    """Accept '#RRGGBB'/'RRGGBB'/RRGGBBWW hex or an [r,g,b(,w)] sequence."""
    if isinstance(value, str):
        text = value.strip().lstrip("#")
        if len(text) not in (6, 8):
            raise ValueError(f"Palette color must be RRGGBB or RRGGBBWW, got {value!r}.")
        try:
            channels = [int(text[i:i + 2], 16) for i in range(0, 6, 2)]
        except ValueError:
            raise ValueError(f"Invalid palette color {value!r}.") from None
        return channels[0], channels[1], channels[2]
    if isinstance(value, (list, tuple)) and 3 <= len(value) <= 4:
        try:
            channels = [int(channel) for channel in value[:3]]
        except (TypeError, ValueError):
            raise ValueError(f"Palette color must be numeric, got {value!r}.") from None
        if any(not 0 <= channel <= 255 for channel in channels):
            raise ValueError(f"Palette color channels must be 0-255, got {value!r}.")
        return channels[0], channels[1], channels[2]
    raise ValueError(f"Palette color must be RRGGBB or [r,g,b], got {value!r}.")


def _spread_positions(count: int) -> list[int]:
    """Evenly spaced 0-255 stop positions, first exactly 0 and last exactly 255."""
    if count == 1:
        return [0]
    step = 255 / (count - 1)
    return [round(index * step) for index in range(count)]


def normalize_stops(stops: Iterable[Any]) -> list[tuple[int, int, int, int]]:
    """Normalize palette input to ascending (pos, r, g, b) tuples.

    Accepts:
      * colors only — ``["#ff0000", "#0000ff"]`` (positions spread evenly)
      * explicit stops — ``[[0, 255, 0, 0], [255, 0, 0, 255]]``

    Positions must be unique and inside 0-255. WLED interpolates between stops,
    so 2-16 stops is the useful range.
    """
    items = list(stops)
    if not items:
        raise ValueError("A palette needs at least two color stops.")
    explicit = all(
        isinstance(item, (list, tuple)) and len(item) == 4 and all(
            isinstance(part, (int, float)) and not isinstance(part, bool) for part in item
        )
        for item in items
    )
    normalized: list[tuple[int, int, int, int]] = []
    if explicit:
        for item in items:
            position = int(item[0])
            if not 0 <= position <= 255:
                raise ValueError(f"Palette stop positions must be 0-255, got {position}.")
            red, green, blue = normalize_color([item[1], item[2], item[3]])
            normalized.append((position, red, green, blue))
    else:
        colors = [normalize_color(item) for item in items]
        positions = _spread_positions(len(colors))
        normalized = [
            (positions[index], red, green, blue)
            for index, (red, green, blue) in enumerate(colors)
        ]
    if len(normalized) < MIN_PALETTE_STOPS:
        raise ValueError(f"A palette needs at least {MIN_PALETTE_STOPS} color stops.")
    if len(normalized) > MAX_PALETTE_STOPS:
        raise ValueError(f"A palette takes at most {MAX_PALETTE_STOPS} color stops.")
    seen: set[int] = set()
    for position, *_rest in normalized:
        if position in seen:
            raise ValueError(f"Duplicate palette stop position {position}.")
        seen.add(position)
    normalized.sort(key=lambda stop: stop[0])
    return normalized


def palette_file(stops: Sequence[tuple[int, int, int, int]]) -> bytes:
    """Serialize stops to the exact ``paletteN.json`` bytes WLED expects."""
    flat: list[int] = []
    for position, red, green, blue in stops:
        flat.extend((position, red, green, blue))
    body = "{\"palette\":[" + ",".join(str(value) for value in flat) + "]}"
    return body.encode("utf-8")


def parse_palette_file(raw: Any) -> list[tuple[int, int, int, int]]:
    """Read back a ``{"palette":[...]}`` body.

    The firmware accepts two shapes (``colors.cpp: loadCustomPalettes``):

      * ``[pos, "RRGGBB", pos, "RRGGBB", ...]`` — what the WLED 16 UI writes
      * ``[pos, r, g, b, pos, r, g, b, ...]``   — the shape documented in the API docs

    Both are parsed here; this module writes the documented integer form.
    """
    import json

    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    try:
        data = json.loads(str(raw))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Palette file is not valid JSON: {exc}") from exc
    values = data.get("palette") if isinstance(data, dict) else data
    if not isinstance(values, list) or len(values) < 4:
        raise ValueError("Palette file must contain at least two stops in 'palette'.")

    stops: list[tuple[int, int, int, int]] = []
    if len(values) % 2 == 0 and all(
        isinstance(values[index], str) for index in range(1, len(values), 2)
    ):
        for index in range(0, len(values), 2):
            try:
                position = int(values[index])
            except (TypeError, ValueError):
                raise ValueError(f"Palette stop position {values[index]!r} is not a number.") from None
            red, green, blue = normalize_color(values[index + 1])
            stops.append((position, red, green, blue))
    elif len(values) % 4 == 0:
        try:
            numbers = [int(value) for value in values]
        except (TypeError, ValueError):
            raise ValueError("Palette values must be numbers or RRGGBB strings.") from None
        stops = [
            (numbers[index], numbers[index + 1], numbers[index + 2], numbers[index + 3])
            for index in range(0, len(numbers), 4)
        ]
    else:
        raise ValueError(
            "Palette file must pair each position with a colour "
            "(pos,r,g,b or pos,\"RRGGBB\")."
        )
    for position, red, green, blue in stops:
        if not 0 <= position <= 255:
            raise ValueError(f"Palette stop position {position} is outside 0-255.")
        for channel in (red, green, blue):
            if not 0 <= channel <= 255:
                raise ValueError("Palette colour channels must be 0-255.")
    return stops


# ---------------------------------------------------------------------------
# Version-dependent palette ids
# ---------------------------------------------------------------------------

def _version_tuple(version: Any) -> tuple[int, int]:
    match = re.match(r"\s*(\d+)\.(\d+)", str(version or ""))
    if not match:
        return (0, 0)
    return int(match.group(1)), int(match.group(2))


def palette_id_for_slot(slot: int, version: Any = None) -> int:
    """Palette id for ``palette<slot>.json`` on this firmware version."""
    if slot < 0:
        raise ValueError("Palette slot must be zero or greater.")
    if _version_tuple(version) >= (0, 16):
        palette_id = PALETTE_ID_V16_FIRST - slot
        if palette_id < PALETTE_ID_V16_LAST:
            raise ValueError(
                f"Slot {slot} exceeds the WLED 16 custom palette range "
                f"({PALETTE_ID_V16_FIRST} down to {PALETTE_ID_V16_LAST})."
            )
        return palette_id
    palette_id = LEGACY_PALETTE_ID_FIRST - slot
    if palette_id < 1:
        raise ValueError(f"Slot {slot} is outside the pre-16 custom palette range.")
    return palette_id


def palette_slot_filename(slot: int) -> str:
    if slot < 0:
        raise ValueError("Palette slot must be zero or greater.")
    return f"palette{slot}.json"


# ---------------------------------------------------------------------------
# WLED file manager transport
# ---------------------------------------------------------------------------

def _multipart_body(
    filename: str, content: bytes, boundary: str
) -> bytes:
    """Single-file multipart/form-data body, matching WLED's ``handleUpload``.

    The firmware reads the filename from the part header, so it must be the
    bare name (``palette0.json``); WLED prepends the leading slash itself.
    """
    safe_name = str(filename).replace('"', "").replace("\\", "")
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{safe_name}"\r\n'
        f"Content-Type: {mimetypes.guess_type(safe_name)[0] or 'application/octet-stream'}\r\n"
        "\r\n"
    ).encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
    return head + content + tail


def _request(
    host: str,
    path: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    method: str = "GET",
    timeout: float = 8.0,
) -> bytes:
    url = f"{str(host).rstrip('/')}{path}"
    request = urllib.request.Request(
        url,
        data=data,
        headers=headers or {},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read(256).decode("utf-8", errors="replace").strip()
        except Exception:
            pass
        finally:
            exc.close()
        raise WledFileError(
            f"WLED {method} {path} failed with HTTP {exc.code}"
            + (f": {detail}" if detail else "")
        ) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise WledFileError(f"Could not reach {url}: {exc}") from exc


def upload_file(
    host: str, filename: str, content: bytes, *, timeout: float = 8.0
) -> bytes:
    """Upload one file via ``POST /upload`` (the same path the WLED UI uses)."""
    boundary = f"----lightss{uuid.uuid4().hex}"
    body = _multipart_body(filename, content, boundary)
    return _request(
        host,
        "/upload",
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
        method="POST",
        timeout=timeout,
    )


def delete_file(host: str, filename: str, *, timeout: float = 8.0) -> bytes:
    """Delete one file via the ``/edit`` file manager."""
    query = urllib.parse.urlencode({"func": "delete", "path": str(filename)})
    return _request(host, f"/edit?{query}", timeout=timeout)


def read_file(host: str, filename: str, *, timeout: float = 8.0) -> str:
    """Read one file back via the ``/edit`` file manager (empty when absent).

    A missing file is a normal answer here (slot probing relies on it), so the
    file manager's 404 is reported as an empty body rather than an error.
    """
    query = urllib.parse.urlencode({"func": "edit", "path": str(filename)})
    try:
        return _request(host, f"/edit?{query}", timeout=timeout).decode(
            "utf-8", errors="replace"
        )
    except WledFileError as exc:
        if "HTTP 404" in str(exc):
            return ""
        raise


def file_exists(host: str, filename: str, *, timeout: float = 8.0) -> bool:
    """True when the device already holds that file."""
    try:
        body = read_file(host, filename, timeout=timeout)
    except WledFileError:
        return False
    return bool(body.strip())


# ---------------------------------------------------------------------------
# High-level: author a palette on one or more controllers
# ---------------------------------------------------------------------------

def write_palette(
    host: str,
    stops: Sequence[tuple[int, int, int, int]],
    *,
    slot: int,
    timeout: float = 8.0,
) -> dict[str, Any]:
    """Write ``palette<slot>.json``; WLED reloads its palettes immediately."""
    filename = palette_slot_filename(slot)
    payload = palette_file(stops)
    response = upload_file(host, filename, payload, timeout=timeout)
    return {
        "host": host,
        "file": filename,
        "bytes": len(payload),
        "response": response.decode("utf-8", errors="replace").strip(),
    }


def next_free_slot(host: str, *, reported_count: int | None = None,
                   limit: int = WLED_PALETTE_SLOT_LIMIT) -> int:
    """Lowest slot whose ``paletteN.json`` is absent.

    ``info.cpalcount`` counts used slots downward from the highest, so it is a
    good starting guess; the device is still probed so gaps cannot collide.
    """
    start = 0
    if isinstance(reported_count, int) and reported_count > 0:
        start = min(reported_count, max(0, limit - 1))
    for slot in range(start, limit):
        if not file_exists(host, palette_slot_filename(slot)):
            return slot
    for slot in range(0, start):
        if not file_exists(host, palette_slot_filename(slot)):
            return slot
    raise WledFileError(f"No free custom palette slot on {host} (limit {limit}).")
