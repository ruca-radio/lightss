# Mood Orchestrator Design

## Goal

Give the browser GUI continuous, intelligent, non-jarring mood lighting that follows whatever song the webcam microphone hears.

When music is playing, the AI reads the recognized song and generates a matching WLED mood (colors, effect, palette, speed, brightness). When music stops or cannot be identified, the lights gently fall back to a warm ambient scene. Beat detection from the browser mic adds a layered pulse on top without changing the base mood.

## Scope

- Browser GUI only. No CLI or MCP changes.
- Uses the existing webcam mic via the browser.
- Reuses the existing OpenAI wiring, Shazam recognition, WLED client, and browser beat visualizer.
- Adds one new Python module and small edits to the GUI server/HTML/JS.

## Architecture

### New module: `mood_orchestrator.py`

| Component | Responsibility |
|-----------|----------------|
| `SongCache` | Persist recognized song metadata and the last AI-generated mood payload to `~/.config/lightss/song_moods.json`. Provides consistent moods across restarts and avoids repeated LLM calls for the same song. |
| `AudioSampleBuffer` | Accept raw WAV bytes from the browser, enforce a minimum cooldown between Shazam attempts, and route eligible samples to recognition. |
| `MoodGenerator` | Build a focused prompt from song metadata + device snapshot, call the existing `call_openai_for_plan`, and return a validated WLED payload. |
| `TransitionSmoother` | Ensure every mood change is gentle: minimum transition times, brightness ramp guards, and effect-change guards. |
| `MoodSession` | State machine that owns the whole loop: `IDLE` → `LISTENING` → `RECOGNIZED` → `AMBIENT`. Tracks last song, silence timer, recognition cooldown, and current mood. |

### Edits to existing files

- `light_gui.py`: add `/api/mood/sample` and `/api/mood/control` endpoints; instantiate `MoodSession` in `GuiState`; expose mood status through `/api/events`.
- `light_gui_html.py`: add a `MediaRecorder` loop that uploads 5-second WAV chunks while Music Mode is running; show mood state in the Music Mode card.
- Tests: new `tests/test_mood_orchestrator.py` covering `SongCache`, `TransitionSmoother`, and `MoodSession` state transitions.

## Data Flow

1. User clicks **Start Full Music Mode**.
2. Browser starts the existing `AnalyserNode` beat visualizer and a `MediaRecorder` loop on the same mic stream.
3. Every 5 seconds the browser POSTs a WAV chunk to `/api/mood/sample`.
4. `MoodSession` receives the sample and checks cooldown (default 20 seconds). If cooldown passed, it runs Shazam on the bytes.
5. On a match, `MoodSession` builds a song key (`artist||title||album`).
   - If the key exists in `SongCache`, load the cached mood.
   - Otherwise, `MoodGenerator` calls OpenAI for a new mood.
6. The mood payload passes through `TransitionSmoother`, then is posted to WLED.
7. If no match is found for `AMBIENT_TIMEOUT` (default 60 seconds), `MoodSession` transitions to `AMBIENT` and applies a warm, dim scene.
8. When a new song is later recognized, it transitions back to `RECOGNIZED` with a smooth fade.
9. The existing SSE stream (`/api/events`) carries the current mood state to the GUI.

## State Machine

```
IDLE       -> LISTENING  : user starts Music Mode
LISTENING  -> RECOGNIZED : Shazam match + mood applied
LISTENING  -> AMBIENT    : silence/no-match for AMBIENT_TIMEOUT
RECOGNIZED -> AMBIENT    : silence/no-match for AMBIENT_TIMEOUT
RECOGNIZED -> RECOGNIZED : new/different song recognized
AMBIENT    -> RECOGNIZED : new song recognized
ANY        -> IDLE       : user stops Music Mode
```

A "silence" condition is met when the browser has not uploaded audio above a minimum RMS threshold for the timeout period, or when multiple consecutive Shazam attempts return no match.

## Transition Smoothing Rules

To guarantee non-jarring changes:

1. **Minimum transition time**
   - Any mood change: at least `1200ms` (`transition_ms` / WLED `tt`).
   - Ambient fallback: `4000ms`.
   - Ambient → song mood: `2500ms`.

2. **Brightness ramp guard**
   - If the target brightness is more than 80 points away from the current brightness, split the change into two posts: an intermediate brightness over `800ms`, then the final target over the remaining transition.

3. **Effect change guard**
   - Cache the chosen effect for the duration of a song; do not switch effects mid-song.
   - When switching songs, prefer effects that share color-slot behavior or use WLED’s transition to cross-fade.

4. **Beat overlay is additive only**
   - Browser beat detection modifies brightness ±20% and speed ±15% on top of the current mood.
   - It never overrides base colors, effect, or palette.

5. **No flash on failure**
   - Failed recognition, Shazam errors, or OpenAI errors do not change the lights.
   - Errors are logged and surfaced in the GUI status text only.

## API Endpoints

### `POST /api/mood/sample`

Request body:
```json
{
  "audio_b64": "<base64 WAV bytes>"
}
```

Response:
```json
{
  "ok": true,
  "state": "listening|recognized|ambient",
  "song": {"title": "...", "artist": "..."},
  "message": "..."
}
```

This endpoint is fire-and-forget from the browser’s perspective; the actual state change arrives via SSE.

### `POST /api/mood/control`

Request body:
```json
{
  "command": "start|stop|status"
}
```

Response:
```json
{
  "ok": true,
  "running": true,
  "state": "listening|recognized|ambient",
  "song": {...},
  "message": "..."
}
```

`start` is idempotent; `stop` resets the session to `IDLE`.

## Browser Changes

- Add `MediaRecorder` capture inside `startMusicMode()` using `mimeType: audio/wav` or a PCM WAV encoder fallback.
- Send 5-second chunks to `/api/mood/sample` while `musicModeRunning` is true.
- Stop the recorder and clear its interval in `stopMusicMode()`.
- Update the Music Mode status readouts (`micPipelineState`, `songSourceState`, `nextMatchState`, `autoStatus`) with the mood state received from SSE.
- Keep the existing beat visualizer and `/api/action` `beat` posts unchanged.

## Error Handling

- Browser mic permission denied: show a clear message and stop Music Mode; do not retry automatically.
- Shazam unavailable: log once, continue beat detection, stay in `LISTENING`.
- OpenAI error or timeout: keep the previous mood. Do not fall back to ambient unless the silence timer also fires.
- WLED offline: log and retry the last payload when the controller comes back; the SSE stream already reports offline status.
- Corrupt cache file: reset to an empty cache and log a warning.

## Testing

- `tests/test_mood_orchestrator.py`:
  - `SongCache` saves and loads moods by song key.
  - `TransitionSmoother` applies minimum transition times and brightness ramp guards.
  - `MoodSession` transitions through the state machine on sample/match/silence/stop.
  - Recognition cooldown prevents back-to-back Shazam calls.
- Existing tests must still pass (`pytest`).

## Non-Goals

- No CLI commands for mood mode (GUI only).
- No MCP tools added.
- No user-trained preference model in this iteration; caching gives consistency.
- No lyrics or audio-feature analysis (tempo/BPM/key) — Shazam metadata + LLM only.

## Open Questions

None remaining. The user confirmed continuous auto mode, browser mic upload, pure AI generation, layered beat reaction, ambient fallback, and non-jarring transitions.
