# Bedroom LED Controller

Controller for a small fleet of Wi-Fi WLED controllers driving the bedroom wall
columns. The verified installation is four vertical 2.0 m BTF-LIGHTING WS2811
FCOB RGB columns spaced 32 inches apart, with non-uniform calibrated addressable
pixel counts. LED/pixel 0 and controller/data input are at the bottom of each
strip (bottom-up orientation). One addressable pixel/logical LED equals one
5-LED physical Smart IC segment/block. Two WLED 16.x controllers drive two
columns each:

- **Controller `left`** — `http://10.27.27.110`
  - Segment 0 → channel **far-left** (GPIO 16, 34 addressable pixels)
  - Segment 1 → channel **middle-left** (GPIO 2, 48 addressable pixels)
- **Controller `right`** — `http://10.27.27.112`
  - Segment 0 → channel **far-right** (GPIO 2, 40 addressable pixels)
  - Segment 1 → channel **middle-right** (GPIO 16, 47 addressable pixels)

Physical wall order (left → right): `far-left`, `middle-left`, `middle-right`,
`far-right`. The LED buses use physical **BRG** color order (WLED color order
enum 2); that mapping lives only at the WLED bus layer, so API colors on every
surface remain semantic RGB.

Every surface (CLI, GUI, tray, MCP) can target `all` controllers (default), a
single controller, or a single channel/segment.

## Opinionated dynamic scenes

The `dynamic_scene` action/tool is a high-level lighting director for creative
or vague requests (for example, "dreamy calm ocean" or "tasteful party motion").
By default `engine="generated"` paints exact-length static pixel frames for each
strip using real wall order, live segment ids, calibrated pixel counts,
orientation, and bottom-origin direction. Composition modes (`unison`,
`independent`, `pairs`, `center_vs_outer`, `left_vs_right`, `alternating`,
`random_groups`) decide whether strips match, differ, or form groups. Optional
`engine="effect"` preserves the stock safe-effect behavior. Dynamic scenes
reassert configured segment start/stop bounds in payloads but never mutate WLED
hardware configuration or create/delete extra segments. Use explicit
actions (`color`, `effect`, `brightness`, wall modes, etc.) when a request gives
exact values.

Lightss also keeps a small local look memory in
`~/.config/lightss/look_memory.json`. Dynamic scenes record compact summaries
(controllers, segment ids/bounds, brightnesses, frame lengths — not full pixel
frames), and the `look_feedback` action/MCP tool can mark the last look as liked
or disliked with notes/tags. Future AI context includes this summary so the
director can repeat what worked and avoid known issues.

## Realtime direct-control renderer

For direct AI-directed visuals, Lightss includes a bounded local DDP renderer
(`realtime_start`, `realtime_stop`, `realtime_status`). The AI selects only safe
parameters — shader (`red_rocks`, `aurora_flow`, `bass_bloom`,
`liquid_gradient`, `center_wave`, `vertical_scan`), mood, composition mode,
intensity, FPS, finite duration, and seed. The local renderer derives calibrated
DDP offsets from topology (including right-controller middle-right at offset 0
and far-right at offset 47), streams RGB8 DDP packets to port 4048, caps FPS at
40 and duration at 15 minutes, and never accepts raw pixels or shader code.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## WLED Topology Migration

`scripts/migrate_wled_topology.py` migrates the two live controllers to the
verified topology described above: .110 uses starts/stops 0..34 and 34..82;
.112 uses starts/stops 0..47 and 47..87 (BRG color order). The utility is backup-first — every run saves `/json/cfg`,
`/json/state`, `/json/info`, `/json/eff`, and `/json/pal` for both hosts
before anything else — and dry-run is the default; writes require `--apply`.

Preview the migration (dry-run + backup, no writes):

```bash
backup_dir="/tmp/lightss-wled-backups/$(date +%Y%m%d-%H%M%S)"
python scripts/migrate_wled_topology.py --backup-dir "$backup_dir"
python scripts/migrate_wled_topology.py --backup-dir "$backup_dir" --apply
```

Roll back from a backup directory:

```bash
python scripts/migrate_wled_topology.py --restore "$backup_dir"
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENAI_API_KEY` | API key for AI features in the GUI | *(required for AI)* |
| `LIGHT_AI_MODEL` | OpenAI model override for AI prompts | `gpt-5.2` |
| `LIGHT_HOST` | WLED controller URL for MCP server | `http://10.27.27.110` |
| `LIGHT_HOSTS` | Comma-separated WLED controller URLs defining the fleet (names `light-1`, `light-2`, …; no channel aliases) | config `controllers` / built-in two-controller default |
| `LIGHT_AUDIO_SOURCE` | Audio capture source: `monitor`, `mic`, or a device name | `monitor` |
| `LD_LIBRARY_PATH` | May be needed for `sounddevice` / PortAudio | `/home/linuxbrew/.linuxbrew/lib` |

## Commands

```bash
./lightctl on
./lightctl off
./lightctl bri 200
./lightctl color 255 0 0 0
./lightctl color 0 0 255 0
./lightctl color 255 100 0 255
./lightctl fx 9 --speed 128
./lightctl scene party
./lightctl temp 3000           # Kelvin temperature (2000-6500)
./lightctl save-scene cozy
./lightctl delete-scene cozy
./lightctl schedule add 08:00 on
./lightctl schedule add 23:00 scene --scene-name night
./lightctl schedule list
./lightctl schedule remove 0
./lightctl hex #ff6600              # Hex color (6 or 8 digit)
./lightctl random                   # Random built-in scene
./lightctl fade-off 30              # Fade to off over 30 minutes
./lightctl preset 5                 # Load WLED preset ID 1-250
./lightctl cycle --interval 60      # Auto-rotate scenes every 60s
./lightctl sunrise --minutes 30     # Gradual wake-up simulation
./lightctl info                     # Read WLED device info
./lightctl segments                 # List controllers, channels, and segment IDs
```

Target one controller or channel, and address a specific segment:

```bash
./lightctl --target right fx 9              # Only the 'right' controller
./lightctl --target middle-left color 255 0 0 0   # Only the middle-left channel
./lightctl --target right --segment 1 fx 46 # Segment 1 on the 'right' controller
```

Valid targets: `all` (default), a controller name (`right`, `left`), or a
channel name (`far-left`, `middle-left`, `middle-right`, `far-right`). Channel targets
automatically address the matching WLED segment id.

Wall-wide composer modes across all four columns:

```bash
./lightctl wall span --fx 9               # Same effect on all four columns
./lightctl wall mirror --fx 9             # Left pair mirrors the right pair
./lightctl wall chase --fx 28             # Staggered chase along the wall
./lightctl wall versus --fx-left 9 --fx-right 46   # Left vs right duel
```

Use a single controller host directly (single-client escape hatch, bypasses the
fleet):

```bash
./lightctl --host 10.27.27.110 on
```

Dry-run without sending to the light:

```bash
./lightctl --dry-run color 255 0 0 0
```

Smooth transitions (0–65535 ms):

```bash
./lightctl --transition 500 scene warm
```

## Mode 1: Audio Reactive

Mode 1 listens to an audio source, detects room-music energy spikes, and
changes LED color, brightness, effect, and effect speed on beats.

```bash
./lightctl mode1
```

Stop it with `Ctrl+C`.

The default audio source is the **system monitor** — the PulseAudio/PipeWire
monitor of the default output (`audio_source: "monitor"` in `config.json`), so
it reacts to music playing on this machine without a microphone. Set
`audio_source` to `"mic"` (or a device name) to use a microphone instead;
`LIGHT_AUDIO_SOURCE` overrides the config value. Track metadata comes from
MPRIS (`playerctl`, filtered to browser players like Chrome/Chromium/Firefox),
with Shazam as fallback when MPRIS yields nothing.

## Browser GUI

Start the local GUI:

```bash
./light-gui
```

Then open:

```text
http://127.0.0.1:8123/
```

The GUI includes:
- **Live state** display auto-updated via SSE (`/api/events`), aggregated per controller
- **Target selector** — All / controllers / individual channels
- **Wall panel** with the four wall modes (span, mirror, chase, left-vs-right)
- Power, brightness, RGBW colors, hex color input, effect control
- **Transition** slider for smooth fades
- **Temperature** slider and presets (Warm 2700K, Daylight 5000K, Cool 6500K)
- Scene buttons plus **Save/Delete custom scenes** and **Random** button
- **Preset** loader (WLED presets 1-250)
- **Cycle** control for auto-rotating scenes
- **Sunrise** wake-up simulation
- **Fade Timer** for gradual dim-to-off
- **Schedule** management with automatic execution
- Mode 1 start/stop with browser microphone support
- AI prompt box with now-playing detection
- Browser Music Mode with shared mic capture for beat detection, Shazam matching,
  and mood orchestration
- Webcam room analysis for AI-selected ambient lighting
- **📺 TV** toggle and **↗** cast button for Fire TV integration (see below)

GUI Mode 1 uses the browser microphone permission on `localhost`, so it can react
to room music even when Python cannot see a system audio device. Effects are
restricted to safe non-strobe options, including Breathe, Colorloop, Rainbow,
Fade, Chase, Chase Rainbow, Rainbow Runner, Colorwaves, Pacifica, Flow, Drift,
Swirl, and similar smooth movement effects.

The AI prompt box uses `OPENAI_API_KEY` from the GUI server environment. Optional
model override:

```bash
LIGHT_AI_MODEL=gpt-5.2 ./light-gui
```

If installed as a package, the same entry point is available as:

```bash
lightss
```

The AI receives the system map: power, brightness, full RGBW color space, hex
colors, safe non-strobe effects, effect speed, scenes, random scenes, WLED presets,
temperature, transitions, sunrise simulation, scene cycling, fade timers, and
browser microphone beat mode. It can also use a webcam snapshot as room context.
It returns a visible response plus operation confirmations in the GUI.

The AI also reads desktop now-playing metadata when available through MPRIS
media sessions, or `playerctl` if it is installed. Use **Detect Song** in the GUI
to confirm what the AI can see before applying a prompt.

## Fire TV

The platform can drive a Fire TV (via ADB at `10.27.27.207:5555`) so the TV
complements the light visuals instead of competing with them. On first use the
TV shows a one-time **Allow USB debugging** authorization prompt — accept it on
the TV to finish pairing.

TV control is gated behind the **📺 TV** toggle in the GUI header. When the
toggle is off, the platform will not touch the TV at all — use this when the
TV is busy playing music. The **↗** button next to it casts the ambient
visuals page (`/tv`, a fullscreen wall-mirror view) to the TV.

The AI/MCP server exposes matching tools: `tv_status`, `tv_wake`, `tv_sleep`,
and `tv_open_url`. Action tools raise an error while the toggle is off.

`config.json` holds the `firetv` settings:

```json
{
  "firetv": {"enabled": true, "host": "10.27.27.207:5555"}
}
```

For casting to work, the GUI must be reachable from the TV — start it bound to
all interfaces instead of the default loopback:

```bash
./light-gui --listen 0.0.0.0
```

## MCP Server

Run the MCP stdio server:

```bash
./mcp-light
```

The server controls the whole fleet by default. Useful environment overrides:

```bash
LIGHT_HOST=http://10.27.27.110 ./mcp-light                    # single controller
LIGHT_HOSTS=http://10.27.27.110,http://10.27.27.112 ./mcp-light   # custom fleet
```

Every control tool accepts an optional `target` argument (`all` by default, or
a controller/channel name) and, where meaningful, an optional `segment` id.

Tools exposed to MCP clients:

- `light_on`
- `light_off`
- `get_state`
- `get_info`
- `set_brightness`
- `set_color`
- `set_hex_color`
- `set_temperature`
- `set_effect`
- `set_scene`
- `save_scene`
- `delete_scene`
- `list_scenes`
- `random_scene`
- `load_preset`
- `fade_off`
- `start_sunrise`
- `start_audio_reactive`
- `stop_audio_reactive`
- `recognize_music`
- `match_lights_to_song`
- `restart_controller`
- `list_controllers`
- `list_segments`
- `wall_mode` (mode: `span` | `mirror` | `chase` | `versus`, with `fx`/`pal` or
  `fx_left`/`fx_right`/`pal_left`/`pal_right`)
- `tv_status`
- `tv_wake`
- `tv_sleep`
- `tv_open_url`

## Package Entry Points

When installed from `pyproject.toml`, these console scripts are available:

- `lightss` / `light-gui` — start the browser GUI
- `lightctl` — CLI controller
- `mcp-light` — MCP stdio server
- `light-tray` — desktop tray GUI

## Configuration

Scenes, schedules, and settings are persisted in `~/.config/lightss/`:

- `scenes.json` — saved custom scenes
- `schedule.json` — timed automation entries
- `config.json` — general configuration (controllers, defaults, etc.)
- `song_moods.json` — cached AI-generated song moods

The controller fleet is defined in `config.json` under `controllers` (written
on first run if absent). Each controller lists its host and a map of WLED
segment id → segment config (channel name, GPIO pin, pixel count):

```json
{
  "controllers": [
    {"name": "left",  "host": "http://10.27.27.110", "segments": {"0": {"channel": "far-left", "gpio": 16, "pixels": 34, "start": 0, "stop": 34}, "1": {"channel": "middle-left", "gpio": 2, "pixels": 48, "start": 34, "stop": 82}}},
    {"name": "right", "host": "http://10.27.27.112", "segments": {"0": {"channel": "far-right", "gpio": 2, "pixels": 40, "start": 47, "stop": 87}, "1": {"channel": "middle-right", "gpio": 16, "pixels": 47, "start": 0, "stop": 47}}}
  ]
}
```

Resolution order: config file `controllers` → `LIGHT_HOSTS` env (comma-separated
hosts named `light-1`, `light-2`, … with no channel aliases) → the built-in
two-controller default above. A plain channel-name string per segment (e.g.
`"0": "far-left"`) still works for custom fleets without GPIO/pixel data.

An optional `installation` block records the physical wall geometry (defaults
shown match the verified installation):

```json
{
  "installation": {
    "wall_order": ["far-left", "middle-left", "middle-right", "far-right"],
    "spacing_inches": 32.0,
    "orientation": "vertical",
    "pixel_zero": "bottom",
    "column_length_m": 2.0,
    "pixels_per_meter": 20,
    "visible_leds_per_meter": 720,
    "color_order": "BRG",
    "addressable_pixel_physical_leds": 5
  }
}
```

`config.json` also holds `audio_source` for music/beat capture: `"monitor"`
(default, system output monitor), `"mic"`, or a device name; `LIGHT_AUDIO_SOURCE`
overrides it. The legacy `mic_device` setting still applies when `audio_source`
is `"mic"`.
