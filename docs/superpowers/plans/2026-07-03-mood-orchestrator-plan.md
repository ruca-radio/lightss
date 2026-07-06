# Mood Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a browser-mic-driven mood orchestrator to the GUI that continuously recognizes songs, AI-generates smooth WLED moods, and falls back to ambient light when music stops.

**Architecture:** A new `mood_orchestrator.py` module contains a stateful `MoodSession`, `SongCache`, `TransitionSmoother`, and `AudioSampleBuffer`. The browser uploads 5-second WAV chunks to `/api/mood/sample`; the server decides when to Shazam, when to ask the LLM, and when to dim to ambient. Beat detection stays in the browser and only tweaks brightness/speed on top of the base mood.

**Tech Stack:** Python 3.14, existing `lightctl.py`, `music_recognizer.py`, browser Web Audio API / `MediaRecorder` fallback, OpenAI Responses API (already wired in `light_gui.py`).

## Global Constraints

- GUI only — no CLI or MCP changes.
- Use the existing webcam mic through the browser.
- Transitions must be non-jarring: minimum 1200 ms, ambient fallback 4000 ms, brightness ramp guard for jumps > 80 points.
- Beat overlay is additive only (±20% brightness, ±15% speed); never overrides base colors/effect.
- Errors must not flash the lights; log and surface in GUI status.
- Existing tests must still pass (`pytest`).
- Python type hints follow existing codebase style.

---

## File Map

| File | Responsibility |
|------|----------------|
| `mood_orchestrator.py` (new) | `SongCache`, `TransitionSmoother`, `AudioSampleBuffer`, `MoodSession`. Pure Python, testable, no GUI/HTML knowledge. |
| `light_gui.py` (modify) | Add `/api/mood/sample` and `/api/mood/control` endpoints; wire `MoodSession` into `GuiState`; include mood status in SSE. |
| `light_gui_html.py` (modify) | Capture 5-second WAV chunks from browser mic and upload; display mood state from SSE. |
| `tests/test_mood_orchestrator.py` (new) | Unit tests for cache, smoother, and state machine. |

---

## Task 1: SongCache persistence

**Files:**
- Create: `mood_orchestrator.py`
- Test: `tests/test_mood_orchestrator.py`

**Interfaces:**
- Produces: `class SongCache` with `get(key: str) -> dict | None`, `set(key: str, payload: dict) -> None`, `_load() -> dict`, `_save(data: dict) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mood_orchestrator.py
import json
import os
import tempfile

import pytest

import mood_orchestrator


class TestSongCache:
    def test_get_missing_returns_none(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmp:
            monkeypatch.setattr(mood_orchestrator, "_CONFIG_DIR", tmp)
            cache = mood_orchestrator.SongCache()
            assert cache.get("missing") is None

    def test_set_and_get_roundtrip(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmp:
            monkeypatch.setattr(mood_orchestrator, "_CONFIG_DIR", tmp)
            cache = mood_orchestrator.SongCache()
            cache.set("artist||title||album", {"bri": 180, "seg": [{"fx": 9}]})
            assert cache.get("artist||title||album") == {"bri": 180, "seg": [{"fx": 9}]}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mood_orchestrator.py::TestSongCache -v`
Expected: `ModuleNotFoundError: No module named 'mood_orchestrator'` or `AttributeError` on `SongCache`.

- [ ] **Step 3: Write minimal implementation**

```python
# mood_orchestrator.py
from __future__ import annotations

import json
import os

_CONFIG_DIR = os.path.expanduser("~/.config/lightss")
_SONG_CACHE_PATH = os.path.join(_CONFIG_DIR, "song_moods.json")


class SongCache:
    """Persistent cache of song key -> last WLED mood payload."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or _SONG_CACHE_PATH
        self._data = self._load()

    def _load(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        return {}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)

    def get(self, key: str) -> dict | None:
        return self._data.get(key)

    def set(self, key: str, payload: dict) -> None:
        self._data[key] = payload
        self._save()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_mood_orchestrator.py::TestSongCache -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/test_mood_orchestrator.py mood_orchestrator.py
git commit -m "feat: add SongCache for persistent song moods"
```

---

## Task 2: TransitionSmoother

**Files:**
- Modify: `mood_orchestrator.py`
- Test: `tests/test_mood_orchestrator.py`

**Interfaces:**
- Consumes: WLED payload dicts, current WLED state dict.
- Produces: `class TransitionSmoother` with `smooth(payload, current_state, source) -> list[WledPayload]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mood_orchestrator.py
class TestTransitionSmoother:
    def test_adds_minimum_transition_for_mood_change(self):
        smoother = mood_orchestrator.TransitionSmoother()
        result = smoother.smooth({"bri": 200}, {"bri": 100}, "recognized")
        assert result == [{"bri": 200, "tt": 1200}]

    def test_ambient_fallback_uses_longer_transition(self):
        smoother = mood_orchestrator.TransitionSmoother()
        result = smoother.smooth({"bri": 60}, {"bri": 200}, "ambient")
        assert result == [{"bri": 60, "tt": 4000}]

    def test_splits_large_brightness_jump(self):
        smoother = mood_orchestrator.TransitionSmoother()
        result = smoother.smooth({"bri": 250}, {"bri": 10}, "recognized")
        assert len(result) == 2
        assert result[0]["bri"] == 90  # 10 + 80
        assert result[0]["tt"] == 800
        assert result[1]["bri"] == 250
        assert result[1]["tt"] == 1200

    def test_preserves_existing_longer_transition(self):
        smoother = mood_orchestrator.TransitionSmoother()
        result = smoother.smooth({"bri": 200, "tt": 3000}, {"bri": 100}, "recognized")
        assert result == [{"bri": 200, "tt": 3000}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mood_orchestrator.py::TestTransitionSmoother -v`
Expected: `AttributeError` on `TransitionSmoother`.

- [ ] **Step 3: Write minimal implementation**

```python
# mood_orchestrator.py
from typing import Any

import lightctl


class TransitionSmoother:
    """Make WLED payload changes gentle and non-jarring."""

    MIN_TRANSITION_MS = 1200
    AMBIENT_TRANSITION_MS = 4000
    AMBIENT_TO_MOOD_MS = 2500
    BRIGHTNESS_JUMP_THRESHOLD = 80
    INTERMEDIATE_STEP_MS = 800

    def smooth(
        self,
        payload: lightctl.WledPayload,
        current_state: dict[str, Any] | None,
        source: str,
    ) -> list[lightctl.WledPayload]:
        current_state = current_state or {}
        current_bri = current_state.get("bri", 128)
        target_bri = payload.get("bri", current_bri)

        if source == "ambient":
            min_tt = self.AMBIENT_TRANSITION_MS
        elif source == "ambient_to_mood":
            min_tt = self.AMBIENT_TO_MOOD_MS
        else:
            min_tt = self.MIN_TRANSITION_MS

        tt = payload.get("tt", 0)
        if tt < min_tt:
            payload = dict(payload)
            payload["tt"] = min_tt

        # If brightness jump is too large, split into two posts.
        if abs(target_bri - current_bri) > self.BRIGHTNESS_JUMP_THRESHOLD:
            direction = 1 if target_bri > current_bri else -1
            intermediate_bri = current_bri + direction * self.BRIGHTNESS_JUMP_THRESHOLD
            intermediate = dict(payload)
            intermediate["bri"] = lightctl.clamp_byte(intermediate_bri)
            intermediate["tt"] = self.INTERMEDIATE_STEP_MS
            final = dict(payload)
            final["bri"] = lightctl.clamp_byte(target_bri)
            return [intermediate, final]

        return [payload]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_mood_orchestrator.py::TestTransitionSmoother -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/test_mood_orchestrator.py mood_orchestrator.py
git commit -m "feat: add TransitionSmoother for gentle WLED transitions"
```

---

## Task 3: AudioSampleBuffer cooldown

**Files:**
- Modify: `mood_orchestrator.py`
- Test: `tests/test_mood_orchestrator.py`

**Interfaces:**
- Produces: `class AudioSampleBuffer` with `maybe_recognize(audio_bytes: bytes) -> dict | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mood_orchestrator.py
class TestAudioSampleBuffer:
    def test_runs_recognizer_on_first_sample(self):
        calls = []
        def fake_recognize(data: bytes) -> dict:
            calls.append(data)
            return {"title": "Song"}

        buf = mood_orchestrator.AudioSampleBuffer(recognize_fn=fake_recognize, cooldown_seconds=0)
        result = buf.maybe_recognize(b"audio")
        assert result == {"title": "Song"}
        assert calls == [b"audio"]

    def test_respects_cooldown(self):
        calls = []
        def fake_recognize(data: bytes) -> dict:
            calls.append(data)
            return {"title": "Song"}

        import time
        buf = mood_orchestrator.AudioSampleBuffer(recognize_fn=fake_recognize, cooldown_seconds=10)
        assert buf.maybe_recognize(b"audio1") == {"title": "Song"}
        assert buf.maybe_recognize(b"audio2") is None
        assert len(calls) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mood_orchestrator.py::TestAudioSampleBuffer -v`
Expected: `AttributeError` on `AudioSampleBuffer`.

- [ ] **Step 3: Write minimal implementation**

```python
# mood_orchestrator.py
import time
from typing import Callable


class AudioSampleBuffer:
    """Throttle incoming audio samples and route them to recognition."""

    def __init__(
        self,
        recognize_fn: Callable[[bytes], dict[str, str] | None],
        cooldown_seconds: float = 20.0,
    ) -> None:
        self.recognize_fn = recognize_fn
        self.cooldown_seconds = cooldown_seconds
        self._last_attempt: float = 0.0

    def maybe_recognize(self, audio_bytes: bytes) -> dict[str, str] | None:
        now = time.monotonic()
        if now - self._last_attempt < self.cooldown_seconds:
            return None
        self._last_attempt = now
        try:
            return self.recognize_fn(audio_bytes)
        except Exception:
            return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_mood_orchestrator.py::TestAudioSampleBuffer -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/test_mood_orchestrator.py mood_orchestrator.py
git commit -m "feat: add AudioSampleBuffer with recognition cooldown"
```

---

## Task 4: MoodSession state machine

**Files:**
- Modify: `mood_orchestrator.py`
- Test: `tests/test_mood_orchestrator.py`

**Interfaces:**
- Consumes: `LightClient`, `recognize_fn(bytes)`, `generate_fn(song_dict)`.
- Produces: `class MoodSession` with `start()`, `stop()`, `sample(audio_bytes)`, `status()`, and states `IDLE`, `LISTENING`, `RECOGNIZED`, `AMBIENT`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mood_orchestrator.py
class TestMoodSession:
    def test_start_stop_lifecycle(self):
        client = lightctl.LightClient("http://example.com", dry_run=True)
        session = mood_orchestrator.MoodSession(
            client=client,
            recognize_fn=lambda data: None,
            generate_fn=lambda song: {"bri": 100},
        )
        session.start()
        assert session.status()["state"] == "listening"
        session.stop()
        assert session.status()["state"] == "idle"

    def test_recognized_song_applies_mood(self):
        client = lightctl.LightClient("http://example.com", dry_run=True)
        session = mood_orchestrator.MoodSession(
            client=client,
            recognize_fn=lambda data: {"title": "Song", "artist": "Artist", "album": "Album"},
            generate_fn=lambda song: {"bri": 222},
            recognize_cooldown=0,
            ambient_timeout=10,
        )
        session.start()
        session.sample(b"audio")
        status = session.status()
        assert status["state"] == "recognized"
        assert status["song"]["title"] == "Song"
        session.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mood_orchestrator.py::TestMoodSession -v`
Expected: `AttributeError` on `MoodSession`.

- [ ] **Step 3: Write minimal implementation**

```python
# mood_orchestrator.py
import threading
from typing import Any


class MoodSession:
    """Orchestrate continuous mic -> song -> mood -> WLED with ambient fallback."""

    STATE_IDLE = "idle"
    STATE_LISTENING = "listening"
    STATE_RECOGNIZED = "recognized"
    STATE_AMBIENT = "ambient"

    def __init__(
        self,
        client: lightctl.LightClient,
        recognize_fn: Callable[[bytes], dict[str, str] | None],
        generate_fn: Callable[[dict[str, str]], lightctl.WledPayload],
        cache: SongCache | None = None,
        smoother: TransitionSmoother | None = None,
        ambient_payload: lightctl.WledPayload | None = None,
        sample_duration: float = 5.0,
        recognize_cooldown: float = 20.0,
        ambient_timeout: float = 60.0,
    ) -> None:
        self.client = client
        self.recognize_fn = recognize_fn
        self.generate_fn = generate_fn
        self.cache = cache or SongCache()
        self.smoother = smoother or TransitionSmoother()
        self.ambient_payload = ambient_payload or lightctl.merge_payloads(
            lightctl.on_payload(True),
            lightctl.brightness_payload(60),
            lightctl.color_payload(*lightctl.kelvin_to_rgbw(2700)),
        )
        self.ambient_timeout = ambient_timeout
        self._buffer = AudioSampleBuffer(recognize_fn, cooldown_seconds=recognize_cooldown)
        self._state = self.STATE_IDLE
        self._lock = threading.Lock()
        self._current_song: dict[str, str] | None = None
        self._last_recognition_time: float = 0.0
        self._last_song_key: str = ""
        self._running = False

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._state = self.STATE_LISTENING
            self._current_song = None
            self._last_recognition_time = 0.0

    def stop(self) -> None:
        with self._lock:
            self._running = False
            self._state = self.STATE_IDLE
            self._current_song = None

    def sample(self, audio_bytes: bytes) -> dict[str, Any]:
        with self._lock:
            if not self._running:
                return self.status()

        result = self._buffer.maybe_recognize(audio_bytes)

        if result:
            return self._handle_recognition(result)

        self._check_ambient_timeout()
        return self.status()

    def _handle_recognition(self, song: dict[str, str]) -> dict[str, Any]:
        key = self._song_key(song)
        with self._lock:
            self._last_recognition_time = time.monotonic()
            changed = key != self._last_song_key

        if not changed:
            return self.status()

        cached = self.cache.get(key)
        if cached:
            payload = dict(cached)
        else:
            payload = self.generate_fn(song)
            self.cache.set(key, dict(payload))

        source = "ambient_to_mood" if self._state == self.STATE_AMBIENT else "recognized"
        self._apply_payload(payload, source)

        with self._lock:
            self._state = self.STATE_RECOGNIZED
            self._current_song = song
            self._last_song_key = key

        return self.status()

    def _check_ambient_timeout(self) -> None:
        with self._lock:
            if self._state != self.STATE_RECOGNIZED:
                return
            if time.monotonic() - self._last_recognition_time < self.ambient_timeout:
                return
            self._state = self.STATE_AMBIENT
            self._current_song = None

        self._apply_payload(self.ambient_payload, "ambient")

    def _apply_payload(
        self,
        payload: lightctl.WledPayload,
        source: str,
    ) -> None:
        try:
            current_state = self.client.get_state()
        except Exception:
            current_state = None
        for smoothed in self.smoother.smooth(payload, current_state, source):
            try:
                self.client.post_state(smoothed)
            except Exception:
                # Don't let a transient WLED error crash the session.
                pass

    def _song_key(self, song: dict[str, str]) -> str:
        parts = [
            str(song.get("artist", "")).strip().lower(),
            str(song.get("title", "")).strip().lower(),
            str(song.get("album", "")).strip().lower(),
        ]
        return "||".join(parts)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self._running,
                "state": self._state,
                "song": self._current_song,
                "last_recognition_time": self._last_recognition_time,
            }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_mood_orchestrator.py::TestMoodSession -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/test_mood_orchestrator.py mood_orchestrator.py
git commit -m "feat: add MoodSession state machine"
```

---

## Task 5: MoodGenerator callback in light_gui.py

**Files:**
- Modify: `light_gui.py`

**Interfaces:**
- Consumes: `call_openai_for_plan`, `device_snapshot_text`, `system_knowledge_prompt` (already in `light_gui.py`).
- Produces: `_generate_mood_for_song(song: dict[str, str], client: lightctl.LightClient) -> lightctl.WledPayload`.

- [ ] **Step 1: Add the helper function**

Insert near the other AI helpers in `light_gui.py` (after `match_lights_to_song`):

```python
def generate_mood_for_song(
    client: lightctl.LightClient,
    song: dict[str, str],
) -> lightctl.WledPayload:
    """Ask the AI to create a smooth WLED mood for a recognized song."""
    title = song.get("title", "Unknown")
    artist = song.get("artist", "Unknown")
    genre = song.get("genre", "")
    prompt = (
        f"The song '{title}' by {artist}"
        + (f" (genre: {genre})" if genre else "")
        + " is playing. Design one smooth, non-jarring WLED mood that matches its energy and style. "
        "Choose a safe effect, palette by name, primary and secondary colors, speed, intensity, and brightness. "
        "The transition must feel gentle — avoid sudden brightness jumps or strobe-like effects. "
        "Do not include mode1_start; beat reaction is handled separately."
    )
    snapshot = client.get_device_snapshot()
    plan = call_openai_for_plan(prompt, song, snapshot)
    actions = plan.get("actions", [])
    # Find the first action that actually produces a WLED payload.
    for action in actions:
        payload = payload_for_ai_action(action)
        if payload:
            return payload
    raise ValueError("AI did not return a usable WLED payload for mood generation")
```

- [ ] **Step 2: Verify the helper can be imported**

Run a quick smoke check:

```bash
python -c "import light_gui; print('generate_mood_for_song' in dir(light_gui))"
```

Expected: `True`.

- [ ] **Step 3: Commit**

```bash
git add light_gui.py
git commit -m "feat: add generate_mood_for_song helper"
```

---

## Task 6: Wire MoodSession into GuiState and add endpoints

**Files:**
- Modify: `light_gui.py`

**Interfaces:**
- Consumes: `MoodSession` from `mood_orchestrator`.
- Produces: `POST /api/mood/sample`, `POST /api/mood/control`, mood status in SSE.

- [ ] **Step 1: Import and instantiate MoodSession in GuiState**

At the top of `light_gui.py`:

```python
import mood_orchestrator
```

In `GuiState.__init__`:

```python
self.mood_session = mood_orchestrator.MoodSession(
    client=client,
    recognize_fn=music_recognizer.recognize_audio_bytes_sync,
    generate_fn=lambda song: generate_mood_for_song(client, song),
)
```

- [ ] **Step 2: Add POST /api/mood/sample handler**

In `Handler.do_POST`, add before the existing `if path == "/api/ai"` block:

```python
if path == "/api/mood/sample":
    audio_b64 = data.get("audio_b64") or data.get("audio")
    if not audio_b64:
        self.respond_json({"ok": False, "error": "audio_b64 is required"}, status=400)
        return
    import base64
    try:
        audio_bytes = base64.b64decode(audio_b64)
    except Exception as exc:
        self.respond_json({"ok": False, "error": f"Invalid audio data: {exc}"}, status=400)
        return
    result = state.mood_session.sample(audio_bytes)
    self.respond_json({"ok": True, **result})
    return

if path == "/api/mood/control":
    command = str(data.get("command", "")).strip().lower()
    if command == "start":
        state.mood_session.start()
        message = "Mood session started."
    elif command == "stop":
        state.mood_session.stop()
        message = "Mood session stopped."
    elif command == "status":
        message = "OK"
    else:
        self.respond_json({"ok": False, "error": "command must be start, stop, or status"}, status=400)
        return
    self.respond_json({"ok": True, "message": message, **state.mood_session.status()})
    return
```

- [ ] **Step 3: Include mood status in SSE**

In the `/api/events` handler, update the payload:

```python
payload = json.dumps({
    "state": st,
    "autonomous": auto_status,
    "mood": state.mood_session.status() if hasattr(state, "mood_session") else {},
    "intel": intel,
})
```

- [ ] **Step 4: Add HEAD support for new endpoints**

In `do_HEAD`, add `/api/mood/sample` and `/api/mood/control` to the allowed paths list and respond with JSON headers.

- [ ] **Step 5: Run existing tests**

Run: `pytest tests/ -q`
Expected: All existing tests pass (no new tests yet for this task).

- [ ] **Step 6: Commit**

```bash
git add light_gui.py
git commit -m "feat: wire MoodSession into GUI server endpoints and SSE"
```

---

## Task 7: Browser mic capture and upload loop

**Files:**
- Modify: `light_gui_html.py`

**Interfaces:**
- Consumes: `/api/mood/sample`, `/api/mood/control`, SSE `mood` field.
- Produces: 5-second WAV upload loop; updated Music Mode status readouts.

- [ ] **Step 1: Add WAV encoder helper in JS**

Insert before `switchTab` in the `<script>`:

```javascript
function encodeWav(audioBuffer) {
  const numOfChan = audioBuffer.numberOfChannels;
  const length = audioBuffer.length * numOfChan * 2 + 44;
  const buffer = new ArrayBuffer(length);
  const view = new DataView(buffer);
  const channels = [];
  let sample = 0;
  let offset = 0;
  let pos = 0;

  function setUint16(data) { view.setUint16(pos, data, true); pos += 2; }
  function setUint32(data) { view.setUint32(pos, data, true); pos += 4; }

  setUint32(0x46464952); // 'RIFF'
  setUint32(length - 8);
  setUint32(0x45564157); // 'WAVE'
  setUint32(0x20746d66); // 'fmt '
  setUint32(16);
  setUint16(1);
  setUint16(numOfChan);
  setUint32(audioBuffer.sampleRate);
  setUint32(audioBuffer.sampleRate * 2 * numOfChan);
  setUint16(numOfChan * 2);
  setUint16(16);
  setUint32(0x61746164); // 'data'
  setUint32(length - pos - 4);

  for (let i = 0; i < audioBuffer.numberOfChannels; i++) {
    channels.push(audioBuffer.getChannelData(i));
  }

  while (pos < length) {
    for (let i = 0; i < numOfChan; i++) {
      sample = Math.max(-1, Math.min(1, channels[i][offset]));
      sample = sample < 0 ? sample * 0x8000 : sample * 0x7FFF;
      view.setInt16(pos, sample, true);
      pos += 2;
    }
    offset++;
  }
  return new Blob([view], { type: 'audio/wav' });
}

async function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => resolve(reader.result.split(',')[1]);
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}
```

- [ ] **Step 2: Add recorder loop state and helper**

Add near the other `let` declarations:

```javascript
let moodRecorder = null;
let moodRecorderInterval = null;
```

Add helper:

```javascript
async function startMoodRecorder() {
  if (!micStream) return;
  if (moodRecorderInterval) clearInterval(moodRecorderInterval);

  const audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 44100 });
  const source = audioCtx.createMediaStreamSource(micStream);
  const processor = audioCtx.createScriptProcessor(4096, 1, 1);
  const chunks = [];

  processor.onaudioprocess = (e) => {
    if (!musicModeRunning) return;
    chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
  };
  source.connect(processor);
  processor.connect(audioCtx.destination);

  async function uploadChunk() {
    if (!musicModeRunning || chunks.length === 0) return;
    const totalLength = chunks.reduce((sum, c) => sum + c.length, 0);
    const combined = new Float32Array(totalLength);
    let idx = 0;
    for (const c of chunks) { combined.set(c, idx); idx += c.length; }
    chunks.length = 0;

    const offline = new OfflineAudioContext(1, combined.length, 44100);
    const buf = offline.createBuffer(1, combined.length, 44100);
    buf.getChannelData(0).set(combined);
    const offlineSource = offline.createBufferSource();
    offlineSource.buffer = buf;
    offlineSource.connect(offline.destination);
    offlineSource.start();
    const rendered = await offline.startRendering();
    const wavBlob = encodeWav(rendered);
    const b64 = await blobToBase64(wavBlob);

    try {
      await fetch('/api/mood/sample', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({audio_b64: b64})
      });
    } catch (err) {
      console.warn('Mood sample upload failed:', err);
    }
  }

  await uploadChunk();
  moodRecorderInterval = setInterval(uploadChunk, 5000);
  moodRecorder = { audioCtx, source, processor, stop: () => {
    clearInterval(moodRecorderInterval);
    moodRecorderInterval = null;
    try { processor.disconnect(); source.disconnect(); audioCtx.close(); } catch(e){}
  }};
}

function stopMoodRecorder() {
  if (moodRecorder) {
    moodRecorder.stop();
    moodRecorder = null;
  }
}
```

- [ ] **Step 3: Start/stop the recorder with Music Mode**

In `startMusicMode()`, after `await startAudioReactive()`:

```javascript
await startMoodRecorder();
setText('songSourceState', 'Webcam mic');
```

In `stopMusicMode()`, add:

```javascript
stopMoodRecorder();
```

- [ ] **Step 4: Display mood status from SSE**

In the SSE `onmessage` handler, add after `updateAutonomousStatus`:

```javascript
if (payload.mood) {
  updateMoodStatus(payload.mood);
}
```

Add helper:

```javascript
function updateMoodStatus(mood) {
  if (!mood || !mood.running) return;
  const st = document.getElementById('autoStatus');
  const songSource = document.getElementById('songSourceState');
  const nextMatch = document.getElementById('nextMatchState');
  if (songSource) songSource.textContent = 'Webcam mic';
  if (nextMatch) nextMatch.textContent = 'Continuous';
  if (!st) return;
  if (mood.state === 'ambient') {
    st.textContent = 'No music detected — ambient fallback active.';
  } else if (mood.state === 'recognized' && mood.song) {
    const t = mood.song.title || 'song';
    const a = mood.song.artist || 'unknown artist';
    st.textContent = `Matched: ${t} by ${a}`;
  } else {
    st.textContent = 'Listening for music…';
  }
}
```

- [ ] **Step 5: Run a server smoke test**

Start the GUI in dry-run mode:

```bash
./light-gui --dry-run
```

Open `http://127.0.0.1:8123/`, click **Start Full Music Mode**, allow mic access, and verify the status text changes and no console errors appear. Stop with **Stop**.

- [ ] **Step 6: Commit**

```bash
git add light_gui_html.py
git commit -m "feat: browser mic upload loop and mood status display"
```

---

## Task 8: Full test suite and final verification

**Files:**
- Modify: `tests/test_mood_orchestrator.py`
- Modify: `light_gui.py`, `light_gui_html.py`, `mood_orchestrator.py` (fix any regressions)

- [ ] **Step 1: Add ambient timeout test**

```python
# tests/test_mood_orchestrator.py
import time

class TestMoodSessionAmbient:
    def test_enters_ambient_after_silence(self):
        client = lightctl.LightClient("http://example.com", dry_run=True)
        session = mood_orchestrator.MoodSession(
            client=client,
            recognize_fn=lambda data: None,
            generate_fn=lambda song: {"bri": 222},
            recognize_cooldown=0,
            ambient_timeout=0.05,
        )
        session.start()
        session.sample(b"audio")  # no match
        time.sleep(0.1)
        session.sample(b"audio")  # triggers timeout check
        assert session.status()["state"] == "ambient"
        session.stop()
```

- [ ] **Step 2: Run all tests**

Run: `pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 3: Fix any regressions**

If tests fail, fix the implementation and rerun until green.

- [ ] **Step 4: Final smoke test**

Run the GUI with a real WLED host or `--dry-run`, start Music Mode, and verify:
- Mic permission prompt appears.
- Status text says "Listening for music…".
- After playing music, status updates to matched song (may take 20s+ due to cooldown).
- After stopping music, ambient fallback activates after 60s.

- [ ] **Step 5: Commit**

```bash
git add tests/test_mood_orchestrator.py mood_orchestrator.py light_gui.py light_gui_html.py
git commit -m "test: ambient timeout and full mood orchestrator coverage"
```

---

## Self-Review Checklist

- [ ] **Spec coverage:** Every requirement in `docs/superpowers/specs/2026-07-03-mood-orchestrator-design.md` maps to a task above.
- [ ] **No placeholders:** No TBD/TODO/"implement later"/"add error handling" steps.
- [ ] **Type consistency:** `MoodSession` constructor signature and `sample()` return type match across tasks.
- [ ] **Testability:** Each task ends with a runnable test or smoke command.
- [ ] **No circular imports:** `mood_orchestrator.py` does not import `light_gui.py`; `light_gui.py` injects the generator callback.
