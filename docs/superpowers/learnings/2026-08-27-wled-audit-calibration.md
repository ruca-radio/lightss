# 2026-08-27 — WLED controller sniff + AI capability + self-calibration

Context: audit/fix pass over the whole lightss WLED stack plus a new
self-calibration mechanism (calibrate.py). Suite: 691 → 731 tests, all green.

## Durable project facts

- WLED `tt` is in **100ms units** (0-65535), per-call only; `transition` is the
  persistent sibling. mood_orchestrator wrote raw milliseconds (1200/4000) into
  `tt` → 120s/400s fades. Failure signature: "transitions are glacial" → check
  units against https://kno.wled.ge/interfaces/json-api/ before trusting a
  field name.
- WLED ignores per-LED `seg.i` frames when `on` rides in the same request from
  an off state — prime `{"on": true, "bri": ...}` first (dynamic_scenes and
  set_leds both do now). `i` accepts both `[r,g,b]` arrays and hex strings;
  docs prefer hex for big frames.
- WLED segment `start`/`stop` are **bus-absolute**, while `seg.i` indices are
  segment-relative. Zones must be computed against the channel's configured
  pixel count and offset by its bus `start`.
- Segment-id-less `seg` posts land on the **main segment**; fleet.post_state
  pins channel targets to their configured segment id, so appending a zone
  segment requires an explicit free id + posting to the controller target.
- Effect policy lives in `mcp_light._fx_allowed` (cache-seeded, never fetched
  on the tool hot path): light_gui seeds it from every AI snapshot via
  `_seed_effect_policy`, the MCP server seeds at startup. Unseeded = legacy
  offline `SAFE_EFFECTS` behavior. `atmospheres.classify_effects` is the
  single source of 🚫 (strobe-substring names + RSVD/"-").
- shows ↔ realtime are mutually exclusive now (each start stops the other);
  music_director stops realtime before applying a look. DDP repaints every
  frame and hides anything posted underneath it.
- WLED-SR audio-sync beat flag is latched per packet: consumers must
  edge-trigger on `frame_counter` or one beat re-fires for the whole 2s stale
  window (~80 phantom beats after stream loss).
- calibrate.py is read-only on devices; `lightctl calibrate` probed the live
  fleet and found real drift: `.112` segment 1 (0-47) spans both buses, and
  live bus color order reads RGB while local installation says BRG. Worth a
  human decision before any `calibrate --write`.

## What worked

- Parallel explore-agent audits of the two huge files (light_gui 3.7k lines,
  tray/audio modules) while reading the engine layer serially — every claimed
  bug was verified against the real code before fixing; ~3 false alarms avoided
  (e.g. `i` frames as RGB lists are legal, `tt` keyword exists and is per-call).
- Cache-seeded policy design avoided hot-path network in tests — zero test
  fakery needed outside the files that intentionally changed behavior.

## What struggled

- One test-arithmetic slip (intersection expectation in
  test_ai_effect_policy) — caught instantly by the suite. Change for next
  time: compute expected sets by hand from the fixture catalogs before
  writing the assertion, not from memory of them.

## Follow-ups (not done, flagged to user)

- light_gui `/api/ai` supersede race: the "last request wins" check runs after
  tool side effects already hit the wall (audit B4). Needs the sequence check
  moved before run_chat or all AI execution through the single-worker executor.
- ~~`.112` bus/segment misalignment + RGB-vs-BRG drift from calibration probe~~
  RESOLVED 2026-08-27: buses fixed in the exported wled_cfg (GPIO 16 → 0-47,
  GPIO 2 → 47-40) and reflashed; `calibrate --write` re-synced local config
  (middle-right gpio 16 restored, color_order RGB unanimous).
- realtime_stop could send `{"live": false}` to release WLED realtime mode
  promptly; skipped — semantics for DDP-entered realtime unclear without
  hardware to test against.
