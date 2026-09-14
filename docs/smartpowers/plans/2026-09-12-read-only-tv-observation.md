# Read-only TV Observation Implementation Plan

**Goal:** Independently observe TV playback context without enabling TV control.
**Architecture:** Extend the existing FireTV bridge with a separately gated observation function; expose a separate API and UI control. Unknown content remains unknown.
**Tech Stack:** Existing Python >=3.14, stdlib ADB subprocesses and HTTP server, existing embedded JavaScript. No new dependencies.
**Spec:** `docs/smartpowers/specs/2026-09-12-read-only-tv-observation.md`

## Constraints
Preserve pre-existing uncommitted work. Keep `firetv.enabled` unchanged. No WLED writes or playback actions in observation. Test with isolated HOME and bytecode/cache writes disabled. No commits of user-owned changes.

### Task 1: Observation contract
Files: `firetv.py`, new `tests/test_tv_observation.py`.
Write and run failing tests for disabled reads, separate action permission, active foreground session parsing, background-session rejection, mixed YouTube ambiguity, null metadata, read failures and bounded ADB calls. Implement `observe(cfg=None) -> dict`, `parse_media_session(text, foreground_app) -> dict | None`, `activity_hint(awake, foreground_app, media_session) -> tuple[str,str]`. Rerun tests plus `tests/test_firetv.py`.

### Task 2: HTTP and independent setting
Files: `light_gui.py`, `tests/test_tv_observation.py`.
Write failing tests for GET/POST `/api/tv-observation`, boolean-only bodies, preserving TV control and other settings, and no light writes. Add `set_tv_observation_enabled(enabled: bool)` and route registration. Rerun focused tests.

### Task 3: User-visible control
Files: `light_gui_html.py`, `README.md`, `tests/test_tv_observation.py`.
Write failing tests for separate labeled observation/control inputs and observation-only endpoint use. Implement explicit refresh and toggle with in-flight guard, textContent rendering, timeout, and truthful unknown/unavailable status. No automatic background polling in this stage. Document config and limitations. Validate rendered JavaScript syntax and a browser interaction with isolated backend.

### Task 4: Verify and review
Run focused FireTV/GUI/settings tests with isolated configuration. Inspect live observe() output with temporary observation-only config, leaving persisted control settings unchanged. Request focused independent review of the task delta against the baseline backup, resolve significant findings, run `git diff --check`. Report stage-one scope and remaining automatic-lighting work explicitly.

## Execution outcome
- Tasks 1–4 complete; no commit or running-GUI restart performed.
- Full isolated suite: 1018 passed (35388 warnings); focused GUI/settings suite: 199 passed.
- Independent backend review approved after unknown-power, truncated-record and simultaneous-permission-write regressions were fixed.
- Playwright on isolated backend: 1440x1000 desktop and 390x844 mobile; independent toggle, failed-save rollback, unavailable-state clearing, and untrusted metadata rendering passed with no console/page errors.
- Current rendered JavaScript passed node --check.
- Live observation-only read: connected, awake, foreground YouTube, active media metadata; content classified unknown. Persistent configuration unchanged; no TV or WLED control commands sent.
- QA evidence: /tmp/lightss-tv-observation-qa/result.json, desktop.png, mobile.png. Pre-edit copies: /tmp/lightss-tv-observation-baseline-jo9b2mgu.
- Known pre-existing boundary: unrelated configuration writers still use the existing full-config save mechanism; this change serializes the two TV permission setters, not every process that can edit configuration.
- Automatic TV brightness scheduling, activity arbitration and music rendering remain the next stage, not enabled by this feature.
