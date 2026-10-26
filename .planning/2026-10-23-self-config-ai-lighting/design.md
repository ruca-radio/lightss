# Self-config boot + AI per-strip lighting + TV taming — design

Date: 2026-10-23 · Status: approved by direct user request (implement now)

## Outcome (observable)

1. **Boot self-config.** On GUI start, before automation takes the wall, the 4 vertical
   strips are lit one at a time (others off, distinct static colors), each strip's
   mapping and current programming are read from the controllers, a report is
   persisted, and lines are served/streamed for articulation. End state restores the
   pre-boot look, then configured automation starts.
2. **AI per-strip control.** AI can set all four strips, any subset, or each strip
   independently, with effect + palette + colors + speed/intensity, both on demand
   (chat/MCP `strips` tool) and autonomously per song (Smart Director chapters).
3. **TV taming.** When the Fire TV plays video (esp. YouTube videos), the wall goes
   calm/steady and low; when music plays, the AI music choreography runs. This is the
   existing Smart Director `auto` mode, enabled and auto-started at boot.

## Decisions

- Single wall owner at a time. Startup ordering: HTTP bind → boot sequence → automation.
  Boot stops `realtime`/`shows`, snapshots state, animates, restores, then starts the
  configured automation (Smart Director `auto`, else legacy Music Director).
- Boot report is not config: `~/.config/lightsctl/boot_report.json` (atomic, 0600).
  Config section `boot_sequence` = `{enabled, dwell_s, brightness}` read via
  `light_gui.boot_sequence_config()`.
- The boot report feeds every AI request as an authoritative context block
  (`ai_context_text`) so the AI always knows the verified layout and programming.
- Per-strip autonomy uses **native WLED audio-reactive effects** per segment
  (`columns.per_strip`), so each strip keeps hardware beat sync with its own effect,
  palette colors, speed and intensity. Wall-wide DDP choreography stays the default.
- TV taming reuses `smart_director._tv()`: solid theme color at time-of-day brightness
  (day .30 / evening .15 / night .05) — calm by design. No new taming code unless live
  verification shows a distraction.

## Interfaces

### New `boot_sequence.py`
```
DEFAULTS = {"enabled": True, "dwell_s": 1.1, "brightness": 160}
validate_config(raw) -> dict
class BootSequence(threading.Thread)
  __init__(fleet, config=None, *, sleep=time.sleep, observe_fn=None,
           report_path=None, clock=time.monotonic)
  stop(); status() -> dict
start_boot(fleet, config=None, **kw) -> BootSequence | None
stop_boot() -> str
boot_status() -> dict
latest_report() -> dict | None
summary_text(report) -> str        # <= 8 compact lines
context_text() -> str              # block for AI context, "" when no report
```
GET `/api/boot` → `{ok, enabled, running, phase: idle|running|done|error, started_at,
finished_at, step: {index,total,channel,text}|null, lines: [str], report: obj|null,
error}`. POST `/api/boot` `{enabled?: bool, run?: bool}` — `run` allowed only when no
director owns the wall.

### `columns.py`
`_seg_entry` accepts `col` | `colors` | `color` (hex string, flat RGB, or list of RGB).
`per_strip` passes those through; no other behavior change.

### `mcp_light.py` `strips` tool
`assignments[]` gains `col`/`colors` (per-strip colors), `bri` (segment brightness
0-255), `on` (bool). `channels` mode gains `colors` for the shared look. Validation:
channel names from `fleet.channels()`, colors 0-255 ints, effects via existing
`_check_fx_allowed`. Unused columns untouched.

### `music_chapters.py` + `smart_director.py`
Chapter JSON may include optional `strips` (2–4 entries):
`{channel, effect, colors, native_speed?, native_intensity?}` →
normalized `{engine:"native", strips:[...]}`. Each effect must be in the supplied
`native_effects` list, channels from `previous["channels"]`. Smart Director applies it
with `columns.per_strip` (own effect/colors/speed/intensity per strip, hardware beat
sync), stores it in `_native_look`, and reports it in status. Optional `note`
(≤120 chars) is surfaced as `chapters.note`. Chapter request context gains
`channels` + `wall_order`.

### `light_gui.py` (Lead-owned)
- `boot_sequence_config()`, `_music_director_start_kwargs` untouched.
- `main()`: after HTTP bind start boot thread (if enabled, not dry-run); on completion
  start automation once (`auto_start_smart_director(state)` if enabled, else legacy).
  Also auto-start Smart Director when config enables it (missing today).
- `/api/boot` GET/POST in path sets + handlers; `ai_context_text` appends
  `boot_sequence.context_text()`.
- Boot lines also mirrored to the GUI log.

### `light_gui_html.py`
Self-config card: enabled checkbox, Run now button, phase/step text, last boot lines;
polls `/api/boot` (2 s idle, 800 ms while running) and pushes each new line into the
model-response marquee (`appendModelResponse('boot', line)`) without innerHTML.

## Safety / limits
- Static colors only, dwell ≥0.8 s, whole run bounded (~10 s), no flashing.
- Skip on `--dry-run`; skip when `boot_sequence.enabled` false; per-controller failures
  recorded, never fatal; restore pre-boot state in `finally`.
- Manual re-run refused while a director owns the wall (no silent ownership theft).
- Config switch to Smart Director `auto` + legacy Music Director off is written with a
  timestamped backup; revert instructions kept in the final report.

## Verification
- Unit: boot sequence with fake fleet (order, exclusivity, restore, report, persistence,
  stop, offline controller); columns colors; MCP tool schema validation; chapter strips
  normalize/apply; smart director status; UI JS unit; endpoint tests.
- Live: restart GUI → `/api/boot` shows 4 lines and report; log shows sequence; then
  Smart Director auto starts; with YouTube video → mode `tv` (calm), with music →
  `music` + chapters; `strips` chat action can address one/any/all.
- Full pytest suite (known env-only failures in test_full_music_ui.py excluded).

---

## Addendum 2026-10-23 (afternoon): YouTube chapters as song identity

Live finding: the user plays **long-form YouTube music mixes** on the Fire TV. The
media session only reports the mix title (e.g. `80's Dance Party Mix - [Wukileaks]`),
so the AI gets one "track" per 48-minute video and cannot follow the songs inside it.
Verified against the live TV: the video has **21 authored chapters** with song titles
(e.g. `Prince - 1999 [Wukileak].)` at 0–171 s), and the session carries a stale
`PlaybackState position` plus `updated` elapsed time; device uptime extrapolation
reproduced the true position within ~1 s (validated with a pause/read/resume on
`DBuyeSmPXd0`, 549.6 s actual).

### Added signal chain (read-only, best effort)
1. `firetv.parse_media_session`/`observe`: capture `position_ms`, `speed`,
   `updated` and compute `media_session.position_s` from the device uptime
   (`(position + (uptime - updated) * speed) / 1000`), paused uses position as-is.
2. New `youtube_chapters.py`: resolve the playing video (yt-dlp title search +
   conservative title match), fetch `chapters`, cache on disk
   (`~/.config/lightsctl/youtube_chapters.json`, TTL 30 d, negative 6 h), parse
   song titles (`Artist - Song`), map `position_s` → current chapter, and expose
   `enrich_observation()` (YouTube apps only, never blocks: cache read + background
   fetch, one in flight).
3. `smart_director`: enrich each observation; when chapters look like a music mix
   (`>=3` artist-song chapters), treat it as music even if the classifier says TV;
   key AI chapters on the **current song** instead of the mix title, and pass
   `youtube: {video, current, upcoming, progress_s, music_mix}` into the chapter
   request so the model designs per song.
4. Failure is safe: no yt-dlp / no match / blocked fetch → no chapters, current
   behavior (mix title, classifier decides, TV tames if it classifies TV).

Scope: no new dependencies beyond the already-installed `yt-dlp` binary; no WLED
writes from the chapter module; no UI changes required (status shows the song key).

---

## Addendum 2026-10-23 (afternoon 2): WLED effect/palette knowledge

Source: https://github.com/wled/WLED/wiki/List-of-effects-and-palettes (Mirror:
`wled_reference.py`, 118 effects + 56 palettes with descriptions and the meaning of
the intensity slider).

Critical caveat encoded in the module: the wiki IDs are historical. Effect order
(and ids) changes between WLED releases/builds, so the reference is keyed by
**name** and merged with the live controller catalog (`/json/eff`, `/json/pal`).
A small alias table maps 0.14 renames (`Scan Dual`→`Dual Scan`, `Running Dual`→
`Running 2`, special palettes `* Color 1`, `* Colors 1&2`, `* Color Gradient`,
`* Colors Only`, `Analogous`→`Analoguous`).

Wiring:
- `mcp_light`: new `wled_reference` tool (kind=effect|palette, query, limit) returns
  live ids + wiki description + intensity meaning. Core tool for AI chat.
- `show_library.candidates`: chapter effect/palette candidates carry description
  and intensity so the chapter model picks knowingly (bounded list, no prompt bloat).
- `music_chapters.SYSTEM`: tells the model to use those fields.
- `light_gui.ai_action_reference`: documents the tool for non-tool-calling fallback.

Newer/renamed effects without a wiki entry simply return `description: null`; the
tool never invents ids or descriptions.

---

## Addendum 2026-10-24: local playback is a first-class AI-lighting input

Live finding: with the Fire TV asleep or on its launcher (`awake:false`,
`foreground_app: com.amazon.tv.launcher`), AI lighting stayed on the steady TV look
while music played on this computer (Slipmat over MPRIS). Smart Director observed
only the Fire TV, so the auto decision could never see local playback; switching to
the legacy Music Director worked because *it* polls MPRIS. That asymmetry is the
"light controller doesn't work when I play out of this machine" report.

Fix (`smart_director.py`):
- Local now-playing (`music_recognizer.now_playing()` — desktop player first, then
  MPRIS) is merged as an observation when the TV is **not** actively playing media.
  The synthesized observation carries `activity_hint: music`, a `local:<player>`
  package and `Artist — Title` as the media description, so classification, the
  auto decision order and the per-song chapter key work unchanged.
- TV video/music that is actively playing still wins (taming is preserved).
- A confirmed local song no longer falls back to steady TV just because the
  controller microphone is quiet (`silence_hold_s` gate skipped when local playback
  is known), so headphones/low volume do not flip the wall to TV.
- Chapter requests get `track` (player/title/artist/album/genre) in their context;
  `/api/smart-director` status exposes `local_playback`.
- `smart_director.start()` wires the provider by default; injected `observe_fn`
  observers are untouched (tests and alternative backends keep ownership).

Verification: live `_observe()` returned the merged `local:Slipmat` music
observation; `activity_intelligence` classified it `music 1.0`; a simulated auto
step entered music after the debounce with chapter key
`Nitty Gritty Dirt Band — Will the Circle Be Unbroken`; full suite 1708 passed.
