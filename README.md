# Bedroom LED Controller

Simple controller for the Wi-Fi LED strip at `10.27.27.110`.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENAI_API_KEY` | API key for AI features in the GUI | *(required for AI)* |
| `LIGHT_AI_MODEL` | OpenAI model override for AI prompts | `gpt-5.2` |
| `LIGHT_HOST` | WLED controller URL for MCP server | `http://10.27.27.110` |
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
```

Use a different controller host:

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

Mode 1 listens to the default microphone, detects room-music energy spikes, and
changes LED color, brightness, effect, and effect speed on beats.

```bash
./lightctl mode1
```

Stop it with `Ctrl+C`.

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
- **Live state** display auto-updated via SSE (`/api/events`)
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

The AI receives the system map: power, brightness, full RGBW color space, hex
colors, safe non-strobe effects, effect speed, scenes, random scenes, WLED presets,
temperature, transitions, sunrise simulation, scene cycling, fade timers, and
browser microphone beat mode. It returns a visible response plus operation
confirmations in the GUI.

The AI also reads desktop now-playing metadata when available through MPRIS
media sessions, or `playerctl` if it is installed. Use **Detect Song** in the GUI
to confirm what the AI can see before applying a prompt.

## MCP Server

Run the MCP stdio server:

```bash
./mcp-light
```

Useful environment override:

```bash
LIGHT_HOST=http://10.27.27.110 ./mcp-light
```

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

## Configuration

Scenes, schedules, and settings are persisted in `~/.config/lightss/`:

- `scenes.json` — saved custom scenes
- `schedule.json` — timed automation entries
- `config.json` — general configuration (host, defaults, etc.)
