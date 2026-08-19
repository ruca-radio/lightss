# WLED Fleet + Four-Column Segment Control — Design

Date: 2026-08-01
Status: Approved by user (design); implementation in progress

## Context

The lightss platform (`/home/rucaradio/lightss`) targets a single WLED controller via
`DEFAULT_HOST = "http://10.27.27.110"` (`lightctl.py:20`). The physical installation is:

- **Controller `right`** = http://10.27.27.110 — GLEDOPTO GL-C-310WL, WLED 0.15.4, 187 effects.
  - Bus 1: GPIO2, start 0, len 50, type 22 (WS2811) → segment id 0 = channel **right**
  - Bus 2: GPIO16, start 50, len 50 → segment id 1 = channel **right-center**
- **Controller `left`** = http://10.27.27.112 — GL-C-310WL, WLED 16.0.1, 220 effects.
  - Bus 1: GPIO2, start 0, len 50 → segment id 0 = channel **left-center**
  - Bus 2: GPIO16, start 50, len 50 → segment id 1 = channel **left**

Wall order (physical, left→right): `["left", "left-center", "right-center", "right"]`.
Both controllers are Audio Reactive (PDM mic) and on WLED sync group 1 (recv).

Verified live via `/json/info`, `/json/cfg`, `/json/state` on 2026-08-01.

## Goals

1. Control both controllers from every surface: target `all` (default), one controller, or one channel.
2. Full per-segment (per-column) control: effect, palette, colors, speed, intensity, on/off, reverse, mirror.
3. "Crazy" layer: wall-wide composers (span, mirror, chase, left-vs-right) across all four columns.
4. Controllers defined in config, not hardcoded.

Non-goals: WLED device-side bus configuration (already correct), UDP sync orchestration,
proxy daemon architecture.

## Music source (same-machine playback)

Music plays on this machine via Apple Music in the browser (Chrome/Chromium). This simplifies
analysis on two axes:

- **Track metadata without Shazam**: Chrome exposes web-player metadata over MPRIS
  (Media Session API). Read now-playing via `playerctl` (subprocess, stdlib — no new deps;
  degrade gracefully if playerctl absent). ShazamIO remains as fallback when MPRIS yields nothing.
- **System audio instead of a mic**: capture the PulseAudio/PipeWire monitor source
  (`pactl list short sources` → `@DEFAULT_MONITOR@` / `*.monitor`) for the beat/reactive path.
  Config key `audio_source` in config.json: `"monitor"` (new default) | `"mic"` | device name;
  `LIGHT_AUDIO_SOURCE` env override. Existing `mic_device` config keeps working when
  `audio_source` is `"mic"`.
- The mood orchestrator is source-agnostic: it consumes whatever `music_recognizer` yields.

### `music_recognizer.py` / `mood_orchestrator.py` (MODIFIED)

- `music_recognizer.py`: new `now_playing_mpris() -> dict | None` (playerctl metadata:
  title/artist/album, player filtered to chrome/chromium/firefox when ambiguous);
  `recognize()` tries MPRIS first, Shazam fallback. New `resolve_audio_source(cfg) -> str`
  implementing the monitor/mic selection above for the capture path.
- `mood_orchestrator.py`: `MoodSession` must accept either a `LightClient` or a `LightFleet`
  (duck-typed `post_state(payload)` — fleet's `target` defaults to `"all"`, so it is a drop-in).

## Config schema

`~/.config/lightss/config.json` gains (written on first run if absent):

```json
{
  "controllers": [
    {"name": "right", "host": "http://10.27.27.110", "segments": {"0": "right", "1": "right-center"}},
    {"name": "left",  "host": "http://10.27.27.112", "segments": {"0": "left-center", "1": "left"}}
  ]
}
```

Resolution order: config file `controllers` → `LIGHT_HOSTS` env (comma-separated hosts, names
`light-1`, `light-2`, no channel aliases) → built-in default (the two controllers above).
`DEFAULT_HOST` remains as a back-compat alias = first configured controller's host.

## Module contracts (the parallelization boundary — code against these exactly)

### `fleet.py` (NEW)

```python
from __future__ import annotations
import lightctl  # top-level import is fine; lightctl must NOT import fleet at top level

WALL_ORDER: list[str] = ["left", "left-center", "right-center", "right"]
DEFAULT_TARGET = "all"

@dataclass
class ControllerConfig:
    name: str
    host: str
    segments: dict[int, str]  # segment id -> channel name

def load_controllers(config: dict | None = None) -> list[ControllerConfig]: ...

class LightFleet:
    def __init__(self, clients: dict[str, lightctl.LightClient],
                 controllers: list[ControllerConfig] | None = None): ...
    @classmethod
    def from_config(cls, *, dry_run: bool = False, timeout: float = 10.0) -> "LightFleet": ...
    def names(self) -> list[str]: ...                      # controller names
    def channels(self) -> dict[str, tuple[str, int]]: ...  # channel -> (controller_name, seg_id)
    def resolve(self, target: str) -> list[tuple[str, int | None]]:
        """target: 'all' | controller name | channel name.
        Returns [(controller_name, seg_id_or_None)]. Raises ValueError on unknown target."""
    def post_state(self, payload: dict, target: str = "all") -> dict[str, dict]:
        """Fan out. If target is a channel, inject seg id into payload's seg entries.
        Returns {controller_name: {"ok": True, "response": ...} | {"ok": False, "error": str}}.
        One dead controller must NOT raise or block the others."""
    def get_state(self, target: str = "all") -> dict[str, dict]: ...
    def get_fleet_snapshot(self) -> dict: ...  # per-controller labeled snapshots for AI
    def effect_ids(self, controller: str) -> set[int] | None:
        """Live effect id set from /json/eff (indices), cached; None on failure."""
```

### `lightctl.py` (MODIFIED — keep all existing public APIs back-compatible)

- `SegPayload` TypedDict gains optional `id`, `start`, `stop`, `len`.
- Builders `effect_payload`, `color_payload`, `palette_payload` (and any other seg builders)
  gain keyword `seg_id: int | None = None`; when set, the emitted `seg` entry includes `"id": seg_id`.
  Default behavior (no seg_id) must be byte-identical to today.
- New `segment_payload(segments: list[dict]) -> WledPayload` — multi-segment array, validated.
- `merge_payloads` merges per segment `id` (entries with an id merge into the same id;
  id-less entries merge into seg[0], preserving current behavior).
- New `playlist_payload(playlist_id: int) -> WledPayload` (fixes latent AttributeError from
  `light_gui.py:1018,1181`).
- Effect validation: `SAFE_EFFECTS` stays as the offline fallback. New
  `validate_effect(fx: int, allowed: set[int] | None = None) -> int` — if `allowed` (live device
  fx id set) is provided, accept any id in it except `BLOCKED_EFFECTS`; otherwise fall back to
  `SAFE_EFFECTS`. `BLOCKED_EFFECTS: set[int]` — keep empty for now (constant + hook only).
- `load_config()` unchanged in signature; controllers are read from its result by fleet.py.
- CLI: new global flags `--target TARGET` (default `all`) and `--segment N`; new commands
  `segments` (list controllers/channels/segments) and `wall MODE` (span|mirror|chase|versus)
  delegating to `columns.py`. `fleet` imported lazily inside `main()`/`cmd_*` functions only
  (no top-level `import fleet` — fleet.py imports lightctl).
- In fleet mode the CLI builds a `LightFleet` and routes existing commands through
  `fleet.post_state(payload, target=args.target)`; `--host` still forces a single `LightClient`
  (back-compat escape hatch).

### `columns.py` (NEW) — the crazy layer

All functions take a `fleet.LightFleet` and return its `post_state` result dict:

```python
def wall_span(fleet, fx: int, pal: int | None = None, **seg_opts) -> dict: ...
    # Same fx/pal on all 4 channels (per-controller 2-seg payloads), one call per controller.
def mirror(fleet, fx: int, pal: int | None = None, **seg_opts) -> dict: ...
    # Left pair mirrors right pair: set mi=True on the left controller's segments.
def chase(fleet, fx: int, pal: int | None = None, **seg_opts) -> dict: ...
    # Same fx on all channels with staggered effect offsets (seg "of") along WALL_ORDER.
def left_vs_right(fleet, fx_left: int, fx_right: int,
                  pal_left: int | None = None, pal_right: int | None = None, **seg_opts) -> dict: ...
def set_channel(fleet, channel: str, **seg_opts) -> dict: ...
    # Thin wrapper: fleet.post_state(lightctl.segment_payload([{...seg_opts}]), target=channel)
```

### `actions.py` (MODIFIED)

New entries in `AI_ACTIONS`/`CLIENT_ACTIONS` (exact names, all take optional `target`):
`wall_span`, `wall_mirror`, `wall_chase`, `wall_versus`, `set_channel` (params: channel, fx?, pal?, col?).
Existing actions unchanged.

### `mcp_light.py` (MODIFIED)

- Server builds one `LightFleet` (env `LIGHT_HOSTS` override still honored via fleet loader).
- Every existing tool gains optional `target` (string, default `"all"`) and, where meaningful,
  `segment` (int) args.
- New tools: `list_controllers`, `list_segments`, `wall_mode` (mode: span|mirror|chase|versus,
  params fx/pal/fx_left/fx_right/pal_left/pal_right).

### `light_gui.py` + `light_gui_html.py` (MODIFIED)

- `main()` builds a `LightFleet` (`--host` still forces single-client mode; `--target` flag added).
- `payload_for_ai_action` / `payload_for_action` route through the fleet with a target; AI plan
  application accepts optional `target` and per-channel `segments` entries.
- `/api/state` returns `{controller_name: state}` aggregated; SSE cache broadcasts fleet state.
- HTML UI: target selector (All / Right / Left / each channel) + a "Wall" panel with the four
  wall modes. Frontend talks only to `/api/*`, as today.
- Webcam/mood paths unchanged except fan-out via fleet (target=all).

### `light_tray.py` (MODIFIED)

- `LightWorker` wraps a `LightFleet`; tray menu gains a target submenu
  (All / controllers / channels) applied to quick actions. `--target` flag.

### `youtopia_bridge.py` + `youtopia_daemon.py` (MODIFIED)

- JSON payload gains optional `target` (default `all`); a `LightFleet` replaces the single client,
  `host` field still honored as single-controller override.

### Tests

- New `tests/test_fleet.py`: target resolution, fan-out success/partial-failure, channel seg
  injection, snapshot shape. Use `lightctl.LightClient(dry_run=True)` instances and FakeClient
  patterns from `tests/test_lightctl.py`.
- New `tests/test_columns.py`: each wall mode builds correct per-controller multi-seg payloads
  (assert via a recording fake fleet).
- Extend `tests/test_lightctl.py` / `tests/test_new_features.py`: `seg_id` builders,
  `segment_payload`, per-id `merge_payloads`, `playlist_payload`, `validate_effect` with/without
  live set. Existing tests must keep passing unmodified where possible; hardcoded-IP assertions
  may be updated to the new defaults.
- Runner: `python -m pytest tests/ -q` (see pyproject.toml). Ruff available.

## Error handling

- Any single controller failure → per-controller error entry in the result dict + warning on
  stderr; never an exception that aborts a broadcast.
- Unknown target/channel → `ValueError` with the list of valid targets.
- Effect id not in a target device's live list → clamp/reject with a clear message naming the
  controller and its firmware's effect count (187 vs 220 differ!).

## Conventions

- `from __future__ import annotations`, stdlib-only networking (urllib), section banner comments,
  TypedDict payloads, `clamp_byte` for 0-255 values.
- Do not add dependencies. Do not reformat unrelated code.

---

# Addendum 2026-08-01: AI-complete control — zones, shows, per-LED

Goal: AI has complete control over the WLED fleet for complex, highly technical light shows.

## Physical model
- Each channel = one vertical bar, 2.0 m, 50 addressable WS2811 IC units (~4 cm/LED).
- Channels (physical order): far-left, middle-left (.112, segs 1/0), middle-right, far-right (.110, segs 1/0).
- Orientation: config key `led_orientation` = "up" (LED 0 at bottom, default) | "down".
  Zone math must respect it ("top" maps to high LED indices when "up").

## lightctl.py — zone/geometry helpers (contract)
- `LEDS_PER_COLUMN = 50`, `COLUMN_LENGTH_M = 2.0`
- `zone_bounds(zone: str, length: int = LEDS_PER_COLUMN, orientation: str = "up") -> tuple[int, int]`
  accepting "top|middle|bottom half|third|quarter" (+ "all").
- `zone_payload(zones: list[dict], length: int = LEDS_PER_COLUMN, orientation: str = "up") -> WledPayload`
  — each zone: {"zone"|"start"/"stop", optional "id", plus any seg fields (fx/pal/col/sx/ix/rev/mi/on...)}.
  Emits seg entries with explicit start/stop; entries with "id" update that segment, id-less append new.
- `leds_payload(led_ranges: list, seg_id: int | None = None) -> WledPayload`
  — ranges: ["RRGGBB", ...] (from LED 0) or [[start, stop, "RRGGBB"], ...]; emits seg "i" array.

## shows.py (new) — show sequencer (contract)
- Show model: {"name"?, "loop"?: bool, "steps": [{"look": {...}, "duration_s": float, "transition_s"?: float}]}
  Look forms: {"atmosphere": name} | {"wall_mode": "span|mirror|chase|versus", ...kwargs}
  | {"payload": {...}, "target"?: str}
- `validate_show(show: dict) -> list[dict]` — normalized steps; ValueError with clear message otherwise.
- `class ShowRunner(threading.Thread)` — `ShowRunner(fleet, steps, loop=False)`, daemon thread,
  `stop()` cooperative, `is_running()`; posts through fleet.post_state (udpn.nn already suppresses sync).
- Module-level registry: `start_show(fleet, show) -> str`, `stop_show() -> str`, `show_status() -> dict`.

## mcp_light.py — new tools (contract)
- `set_zone` {channel (required), zone, fx?, pal?, col? (hex string), sx?, ix?, rev?, mi?} — one zone of one column.
- `set_segment_bounds` {target, id, start, stop, grp?, spc?, of?, rev?, mi?}
- `delete_segment` {target, id}
- `set_leds` {target, segment?, leds: ["RRGGBB",...] | [[start,stop,"RRGGBB"],...]} — per-LED frames (freezes fx).
- `start_show` {show}, `stop_show` {}, `show_status` {} — via shows.py registry.

## light_gui.py — TOOL_CHAT_SYSTEM_PROMPT only
Add the physical model (2 m bars, 50 IC LEDs ≈ 4 cm, orientation), zone tools
("carve columns into zones with set_zone/set_segment_bounds/delete_segment"),
per-LED frames (set_leds; freezes the running effect), and show sequencer
(start_show with timed steps; use for multi-part light shows). gravity guidance:
fire/plasma rises (rev=false when orientation up), rain/waterfall falls (rev=true).

## Notes
- Custom palettes were descoped by the user.
- Existing behavior unchanged; all 268 tests stay green.
