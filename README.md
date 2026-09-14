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
orientation, and bottom-origin direction. Pass `colors` (hex or RGB stops) and a
`seed` to build a unique palette instead of the stock mood colors. Composition
modes (`unison`, `independent`, `pairs`, `center_vs_outer`, `left_vs_right`,
`alternating`, `random_groups`) decide whether strips match, differ, or form
groups. Optional `engine="effect"` preserves the stock safe-effect behavior.
Dynamic scenes reassert configured segment start/stop bounds in payloads but
never mutate WLED hardware configuration or create/delete extra segments. Use
explicit actions (`color`, `effect`, `brightness`, wall modes, etc.) when a
request gives exact values.

Lightss also keeps a small local look memory in
`~/.config/lightss/look_memory.json`. Dynamic scenes record compact summaries
(controllers, segment ids/bounds, brightnesses, frame lengths — not full pixel
frames), and the `look_feedback` action/MCP tool can mark the last look as liked
or disliked with notes/tags. Future AI context includes this summary so the
director can repeat what worked and avoid known issues.

## Realtime direct-control renderer

For direct AI-directed visuals, Lightss includes a bounded local DDP renderer
(`realtime_start`, `realtime_stop`, `realtime_status`). The AI can build a unique
look from shader (`auto`, `red_rocks`, `aurora_flow`, `bass_bloom`,
`liquid_gradient`, `center_wave`, `vertical_scan`, `ember_rise`, `tide_pull`,
`comet_fall`, `dusk_bloom`, `magma_column`, `twin_helix`, `ribbon_drift`), mood,
custom color stops, composition mode, intensity, FPS, finite duration, and seed.
The local renderer derives calibrated DDP offsets from topology (including
right-controller middle-right at offset 0 and far-right at offset 47), streams
RGB8 DDP packets to port 4048, caps FPS at 40 and duration at 15 minutes, and
never accepts raw pixels or shader code.

## Design look

The `design_look` MCP/chat tool invents a unique wall recipe (shader, palette,
composition, intensity) via optional specialist models (colorist/motion/critic)
or local `color_lab` when agents are off. Pass a prompt plus optional
mood/energy/motion, colors, and seed. Set `run=true` to start the bounded
realtime DDP renderer with that look (`fps` 1–40, `duration_s` 0.1–900).

## Read-only TV observation

The **Observe TV (read-only)** switch is independent of **TV control**. It reads
Fire TV power, foreground app, and the active foreground media-session summary
without sending keypresses, opening apps, or changing the lights. Use **Refresh
TV status** for a new snapshot. The separate Smart lighting controller polls this
read-only context while enabled.

Configuration keeps the permissions separate:

```json
{"firetv": {"host": "10.27.27.207:5555", "enabled": false, "observation_enabled": true}}
```

`GET /api/tv-observation` returns the observation. `POST /api/tv-observation`
with `{"enabled": true}` or `{"enabled": false}` changes only observation
permission. The observation switch defaults to off and does not unlock TV
wake/sleep, URL launching, or playback controls.

Known music apps require a playing foreground session for a music hint. YouTube
is mixed-content: even a song-like title is reported as ambiguous, not proof of
music listening. Unavailable power stays unknown rather than being treated as
off. Metadata is display text, not AI instructions, and is not saved to history.

Observation permission alone does not activate automatic lighting. Enable the
separate **Smart lighting** controller to use the context below.

## Smart music and TV lighting

**Auto** uses the WLED controller's room microphone, not this computer's audio
output or a browser microphone. The first configured controller must have WLED
AudioReactive enabled and audio sync **Send** on `239.0.0.1:11988`. The receiver
joins that multicast group on the controller-facing interface and accepts audio
only from that configured controller. Other controllers can receive its audio too.

- **Music:** a continuous 30-fps DDP renderer uses live FFT bands, energy and beats
  to vary motion, color distribution and brightness across the wall. It does not
  randomly cycle WLED effect presets. Track context selects a bounded palette and
  composition; optional AI classification can also suggest motion, speed and
  intensity. It is cached and runs off the frame loop, without lighting tools.
  Audio remains local; only media metadata reaches the configured classifier
  when needed.
  - **Live controls:** Music brightness, Motion, Speed, Intensity and Color apply
    after a short drag debounce without stopping or restarting the show. Mode
    and TV schedule changes still use **Apply smart lighting**. Spectrum-bar
    clicks now trigger individual, repeatable frequency accents on the active
    DDP stream; they do not replace the chosen motion. Each of the 16 bands has
    a 0–3× gain control plus Reset EQ. Spectrum / Energy Pulse / Evolve remain
    available without competing legacy beat writes.
  - **Motion:** Auto follows sustained audio energy/frequency changes (or the
    AI's song-level motion recommendation); Flow paints ribbons, Punch creates
    localized bass blooms, Chase sends peaks along the physical columns,
    Spectrum paints frequency cells, Comet paints a head and trailing tail,
    and Ripple expands rings from the center.
    Selecting a motion explicitly overrides the AI recommendation.
  - **Speed** (0.25–4×) and **Intensity** (0–100%) adjust the musical recipe;
    the renderer status shows the effective speed after any AI bias. **Color**
    increases saturation of the selected colors without inventing intermediate
    rainbow hues or recoloring intentional white/black accents. These controls also work during brief
    metadata gaps without discarding the last song palette.
  - **Music brightness** now supports 0–100%, rather than a hidden 65% ceiling.
    The existing RGB channel cap remains 210,
    and frame-to-frame output is smoothed. TV/manual colors and controller
    power, wiring, microphone and firmware settings are unchanged. The preview
    displays successfully sent DDP pixels, not a fabricated WLED effect.
  - **AI live control:** `music_show` status/tune/accent adjusts the existing
    stream, including palette, grouping and EQ. Tuning is session-only; explicit
    UI settings override matching AI adjustments. Native WLED effects, raw JSON,
    per-strip tools and manual shows remain available for an explicit handoff.
    HTTP clients use `GET/POST /api/music-show`; for example
    `{"action":"accent","band":3,"strength":1}` or
    `{"action":"tune","motion":"comet","speed":2,"colors":[[210,30,0],[0,150,210]]}`.
  - **Now playing and responses:** the header uses active local-player or
    foreground-TV track metadata. Model Responses retains readable full text
    and distinguishes automated decision summaries from model replies.
- **TV:** a steady warm, neutral or blue theme. Defaults are 30% during the day
  (07:00–18:00), 15% in the evening (18:00–22:00), and 5% overnight, in
  `America/Detroit`. Settings are editable and brightness changes fade gradually.
- **Auto:** known video content stays steady even with a musical soundtrack.
  Confident music waits for sustained confirmation; ambiguous content falls back
  to steady lighting. Metadata classification is not infallible: explicit Music
  and TV modes are available when YouTube does not identify its content clearly.
- **Manual:** stops automatic output. Manual lighting commands also suspend the
  smart renderer; select Auto to resume. Silence or a stale microphone settles
  the show rather than producing random flashes.

`GET /api/smart-director` exposes settings and live microphone/renderer/decision
status. `POST /api/smart-director` accepts partial settings, for example
`{"enabled":true,"mode":"auto"}`. The setting persists across app restarts.
TV observation and TV control remain separate permissions; automatic lighting
does not require permission to wake, pause, or otherwise control the TV.

## Setup

```bash
uv sync
./light-desktop
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
| `LIGHT_COLORIST_MODEL` | Optional specialist model for palette design | *(off — local color_lab)* |
| `LIGHT_MOTION_MODEL` | Optional specialist model for shader/composition | *(off — local color_lab)* |
| `LIGHT_CRITIC_MODEL` | Optional specialist model that only tightens safety | *(off — local critic)* |
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
`audio_source` to `"mic"` (or a device name) to use a microphone instead, or
`"wled_mic"` to listen to a WLED controller's GPIO mic over UDP (WLED-SR audio
sync, port 11988); `LIGHT_AUDIO_SOURCE` overrides the config value. Track metadata comes from
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
- `strips` (any 1–4 strip combo with one look, or different effects per strip)
- `design_look` (unique look via colorist/motion/critic models or local fallback; `run=true` starts realtime)
- `tv_status`
- `tv_wake`
- `tv_sleep`
- `tv_open_url`

## Package Entry Points

When installed from `pyproject.toml`, these console scripts are available:

- `lightss` / `lightss-desktop` / `light-desktop` — standalone desktop application
- `light-gui` — browser/server interface (legacy entry point preserved)
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
(default, system output monitor), `"mic"`, `"wled_mic"` (WLED controller GPIO
mic via WLED-SR UDP audio sync), or a device name; `LIGHT_AUDIO_SOURCE`
overrides it. The legacy `mic_device` setting still applies when `audio_source`
is `"mic"`.

## Change-driven recognition and observer clock

Music Mode uploads short audio windows for **local** spectral-change detection.
It calls Shazam initially, then only after a sustained likely track change or
silence followed by music. Stable audio reuses the identified track. A failed
identification gets at most one delayed retry per detected change; the existing
transport retry limit still applies. Automatic metadata polling never falls back
to a second browser Shazam loop. Manual **Identify** remains available.

This is a heuristic, not perfect fingerprint matching: strong section changes
can trigger a recheck, while similar songs or seamless mixes may be missed.
Continuous beat rendering does not call an LLM. Cached mood plans are reused.

`GET /api/playback-clock` observes external MPRIS playback without controlling it.
It reports position and duration when the player exposes them, and a local clock
advances between observations. For room-mic audio without a verified alignment,
`position_s` stays `null`; `observed_for_s` is time since identification, **not**
time into the song. The UI and AI context preserve this distinction. External
clock context expires after 15 seconds without refresh and is cleared when the
player disappears; sustained room silence clears the previous room observation. No lyrics
service or music database is queried by this implementation.

## Standalone desktop (0.2.2)

Run `./light-desktop` from the checkout, or `lightss` after installation. Python
3.14 and PySide6 6.10+ are required (`uv sync` installs the locked dependencies).
The app owns its loopback backend and shows Lightss, YouTube Music and Apple Music
in one desktop window. No Youtopia companion process is required in desktop mode.
On Linux with Chrome/Chromium installed and X11/XWayland available, provider tabs
now embed **full Chrome**, including its native phone-passkey QR interface. The local
Lightss control page stays in Qt. The window title identifies the active engine.
Chrome 152 is the verified runtime; update Chrome if native startup fails.
Sign in directly in the provider tabs. Each provider has its own Lightss-owned Chrome
profile; your personal browser and older Qt sessions are not imported. The private
browser control connection uses inherited pipes, not a listening debugging port.
Provider pages have no privileged access to the local control API.

- Search opens the selected service's real catalog UI, including tracks, artists,
  albums and playlists. Account playlist creation/editing stays in that provider UI.
- Lightss transport supports play/pause, previous/next, seeking and volume. Navigation
  requests are reported as queued, not as proof that audio has started.
- **My playlists & queue** stores local playlists in
  `$XDG_DATA_HOME/lightss/player-library.sqlite3` (default `~/.local/share/lightss/`).
  Create/rename/delete, add by current track or service URL, remove and reorder items.
  Local lists are separate from account playlists. Album/artist/playlist bookmarks
  open in the provider; sequential queues require individual tracks. Replacing the
  queue from a saved playlist is atomic. Editing a queue stops automatic sequencing.
- Track observations and playback position are collected locally. Completed-track
  events are retained so the slower UI poll can advance a local queue; pause is not
  mistaken for completion. No model call is needed for this bookkeeping.

Authenticated playback depends on the provider account/subscription and the shipped
selected browser codec/DRM support. Public provider-page loading is verified; authenticated
login/playback, popup-based authorization and provider-specific autoplay must still be
verified with an account. Provider controls can change with the upstream web UI.
A queued navigation alone is **not** a verified successful playback result.

### Native passkey sign-in

Run `./light-desktop` (automatic engine selection), or force native mode:

```bash
QT_QPA_PLATFORM=xcb ./light-desktop --provider-engine=chrome
```

In the provider sign-in flow, select **another device / phone or tablet**, then scan
Chrome's QR prompt. Keep Bluetooth enabled on both devices for proximity verification.
Native QR rendering has been verified inside both provider tabs using a controlled
WebAuthn test origin, including tab switching, resize and cancellation. This is not a
claim that Google/Apple account authentication or protected playback has been completed.
The private control channel marks Chrome as automation-controlled; provider policies
can still reject a session. No identity/browser-detection spoofing is applied.

- Native tabs need a current Chrome/Chromium installation and an X11 display. XWayland
  is used when available under Wayland. Native Wayland foreign-window embedding is not
  supported. If the title says **Qt (limited passkeys)**, phone QR is not available.
- `./light-desktop --provider-engine=qt` retains the older limited browser. Its native
  security-key/PIN dialogs remain available, but it does not gain phone QR transport.
- Chrome profiles live under the Qt application data directory in
  `providers/chrome/youtube_music` and `providers/chrome/apple_music`. They are private,
  persistent, and separate. Existing Qt profiles are preserved. Close an older Lightss
  instance before opening the same profiles in another instance.
- Browser I/O and native-window discovery run off the UI thread. Navigation invalidates
  stale observations. A lost player session fails visibly rather than silently looping.
- Literal local-address requests are blocked; browser local-network/loopback permissions
  are also denied to cover resolved private addresses. CORS, TLS verification and the
  browser sandbox remain enabled. Authentication material is not sent to the model.
- Apple's authentication CORS failures are separate from native QR support. Do not disable
  browser security to hide them. Beacon, WebGPU, ARIA and audio deprecation warnings do
  not alone establish an authentication failure.

For a reproducible, account-free native check from the source checkout:

```bash
xvfb-run -a --server-args='-screen 0 1400x950x24' .venv/bin/python \
  tests/native_chrome_smoke.py --output-dir /tmp/lightss-native-check
```

It uses temporary browser/config profiles, fake controllers, a local WebAuthn fixture,
muted test audio and test-only exact-origin exceptions. It checks the real player-to-clock
path and browser cleanup, and saves QR screenshots for visual review. It does not scan
for personal browser data, authenticate an account, or contact physical controllers.

## Reviewed device calibration

Use **Calibrate LEDs** from the main tabs. **Scan** reads every configured controller's
buses/GPIOs, declared LED count and segment bounds, and displays the proposed platform
configuration. **Apply reviewed topology** requires a recent single-use scan token,
fresh matching hardware, unchanged settings, and a complete valid fleet. It preserves
unknown settings, creates a unique backup and atomically writes the local topology.
Offline/invalid controllers never silently disappear from the proposal. The app reloads
the controller fleet after a successful apply.

**Identify briefly** is an explicit, low-brightness one-segment marker that restores the
previous state. Active realtime/frozen outputs must be stopped first. **Enable camera**
asks for permission; **Analyze one frame** sends only that snapshot to the vision model
and returns advisory geometry observations. It does not apply settings or invoke the
lighting planner. Camera capture stops when the dialog closes. No continuous vision
or lyrics requests run. Controller declarations/photos cannot prove electrical wiring,
physical pixel count or pixel-zero direction; GPIO/bus/power settings are never guessed.

Mood generation now validates against the same live controller catalogs shown to the
AI, rather than falling back to the offline example list. Failed generations do not
cache ambient fallback as a successful song mood.
