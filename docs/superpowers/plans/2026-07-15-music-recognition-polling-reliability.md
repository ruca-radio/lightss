# Music Recognition Polling Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure browser Music Mode keeps attempting song recognition every 30 seconds when an initial or later HTTP request stalls.

**Architecture:** Keep the existing browser-owned microphone and single-cycle busy guard. Install the interval before awaiting the first cycle, and route recognition-path JSON requests through a small abortable fetch helper so every cycle reaches its `finally` block.

**Tech Stack:** Python-rendered HTML, browser JavaScript, `unittest`/pytest rendered-surface contract tests.

---

## File Structure

- Modify `tests/test_surfaces.py`: lock scheduler ordering and bounded recognition-request behavior.
- Modify `light_gui_html.py`: add abortable JSON fetch and make scheduler startup independent of the initial cycle.

### Task 1: Lock the Scheduler and Timeout Contracts

**Files:**
- Modify: `tests/test_surfaces.py`
- Test: `tests/test_surfaces.py`

- [ ] **Step 1: Write failing rendered-HTML tests**

Add tests that slice `startMusicMode()` and assert the `setInterval` statement appears before `await runMusicRecognitionCycle(true)`. Add a second test that asserts an abortable `fetchJsonWithTimeout` helper exists and that `/api/now-playing`, `/api/recognize`, and `/api/match-lights` calls use it.

```python
def test_music_mode_installs_recognition_timer_before_initial_cycle(self):
    html = light_gui.render_html()
    start = html.index("async function startMusicMode")
    end = html.index("function stopMusicMode")
    body = html[start:end]

    timer_idx = body.index("musicRecognitionTimer = setInterval")
    initial_cycle_idx = body.index("await runMusicRecognitionCycle(true);")
    self.assertLess(timer_idx, initial_cycle_idx)

def test_music_recognition_requests_have_timeouts(self):
    html = light_gui.render_html()

    self.assertIn("async function fetchJsonWithTimeout", html)
    self.assertIn("const controller = new AbortController();", html)
    self.assertIn("fetchJsonWithTimeout('/api/now-playing'", html)
    self.assertIn("fetchJsonWithTimeout('/api/recognize'", html)
    self.assertIn("fetchJsonWithTimeout('/api/match-lights'", html)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest -q tests/test_surfaces.py::SurfaceTests::test_music_mode_installs_recognition_timer_before_initial_cycle tests/test_surfaces.py::SurfaceTests::test_music_recognition_requests_have_timeouts`

Expected: both tests fail because the timer follows the awaited cycle and no recognition fetch helper exists.

### Task 2: Make Recognition Polling Recoverable

**Files:**
- Modify: `light_gui_html.py`
- Test: `tests/test_surfaces.py`

- [ ] **Step 1: Add the minimal abortable JSON helper**

Add near the existing request helpers:

```javascript
async function fetchJsonWithTimeout(url, options = {}, timeoutMs = 15000) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, {...options, signal: controller.signal});
    return await res.json();
  } finally {
    clearTimeout(timeout);
  }
}
```

- [ ] **Step 2: Route recognition-path requests through the helper**

In `refreshNowPlaying()`, replace the fetch and JSON pair with:

```javascript
const data = await fetchJsonWithTimeout('/api/now-playing');
```

In `recognizeSongOnce()`, replace the fetch and JSON pair with:

```javascript
const data = await fetchJsonWithTimeout('/api/recognize', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({audio: b64})
}, 20000);
```

In both song-matching functions, replace their fetch and JSON pairs with:

```javascript
const data = await fetchJsonWithTimeout('/api/match-lights', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({now_playing: song})
});
```

- [ ] **Step 3: Install the interval before the initial await**

Change the end of `startMusicMode()` to:

```javascript
musicRecognitionTimer = setInterval(() => runMusicRecognitionCycle(false), MUSIC_RECOGNITION_INTERVAL_MS);
await runMusicRecognitionCycle(true);
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `pytest -q tests/test_surfaces.py::SurfaceTests::test_music_mode_installs_recognition_timer_before_initial_cycle tests/test_surfaces.py::SurfaceTests::test_music_recognition_requests_have_timeouts tests/test_surfaces.py::SurfaceTests::test_music_mode_uses_browser_shazam_when_metadata_is_missing tests/test_surfaces.py::SurfaceTests::test_music_mode_does_not_start_competing_server_shazam_session`

Expected: `4 passed`.

### Task 3: Regression Verification

**Files:**
- Verify: `light_gui_html.py`
- Verify: `tests/test_surfaces.py`

- [ ] **Step 1: Run the complete surface test module**

Run: `pytest -q tests/test_surfaces.py`

Expected: all surface tests pass.

- [ ] **Step 2: Run the full repository suite**

Run: `pytest -q`

Expected: all repository tests pass.

- [ ] **Step 3: Run syntax and whitespace checks**

Run: `python -m py_compile light_gui.py light_gui_html.py`

Expected: exit code 0.

Run: `git diff --check -- light_gui_html.py tests/test_surfaces.py`

Expected: no output.

- [ ] **Step 4: Commit if Git metadata is writable**

```bash
git add light_gui_html.py tests/test_surfaces.py docs/superpowers/specs/2026-07-15-music-recognition-polling-reliability-design.md docs/superpowers/plans/2026-07-15-music-recognition-polling-reliability.md
git commit -m "fix: keep music recognition polling alive"
```

If `.git` remains read-only, report the verified working-tree changes without committing.
