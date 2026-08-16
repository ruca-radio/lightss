# Opinionated Dynamic Scenes

Goal: turn Lightss from a raw WLED action router into an opinionated spatial lighting director for the calibrated four-column far-wall installation.

Confirmed topology context:
- Four vertical far-wall columns, left-to-right: `far-left`, `middle-left`, `middle-right`, `far-right`.
- Controller `10.27.27.110`: far-left 34 px on GPIO 16, middle-left 48 px on GPIO 2.
- Controller `10.27.27.112`: segment 0 far-right 40 px on GPIO 2; segment 1 middle-right 47 px on GPIO 16.
- About 32 inches between strips. Pixel 0/input/power is at the bottom.
- One addressable pixel/logical LED equals one 5-LED physical Smart IC block.

Claim to establish:
- The AI can ask for an opinionated, dynamic scene using mood/intensity/energy/motion words, and the platform will translate that into safe, spatial, non-uniform-column-aware WLED payloads.
- Existing explicit low-level actions and curated atmospheres continue to work.

Failure modes that matter:
- Lazy/random chaos: unsafe or ugly combinations, strobe-like effects, harsh brightness, incoherent per-column mismatches.
- Topology blindness: right/left order wrong, unequal strip lengths ignored, bottom-origin ignored, per-column targeting broken.
- Prompt/API mismatch: AI is told to use a new action but server cannot execute it, or MCP lacks the tool.
- Breaking existing tests/action contracts.

Plan draft:
1. Add `dynamic_scenes.py` as a pure composition engine.
   - Duck-typed against `fleet.LightFleet`.
   - Provides `compose_dynamic_scene(...)` returning per-controller WLED payloads and `apply_dynamic_scene(...)` posting them.
   - Opinionated defaults: ambient/theatrical, smooth, spatial, non-strobe, medium-low brightness unless party/club requested.
   - Strategies: `center_out`, `left_to_right`, `mirror`, `split_temperature`, `vertical_rise`, `quiet_gradient`, `audio_pulse`.
   - Mood profiles choose effect ids/palettes/colors from known-safe curated families; deterministic seed support for tests and repeatability.
2. Wire action surface.
   - Add AI action `dynamic_scene` and route it server-side like `atmosphere`.
   - Add MCP tool so external agents can request dynamic looks.
   - Update prompt: prefer `dynamic_scene` for creative/vague requests; use explicit actions when user gives exact values.
3. Tests.
   - Unit tests for deterministic composition, safety, wall order, per-column segment ids, unequal lengths via target mapping, and action routing.
   - Existing topology/context tests should still pass.

Verification path:
- Pure unit tests using recording fake fleets can inspect generated payloads without live WLED.
- Focused test command: `pytest tests/test_dynamic_scenes.py tests/test_surfaces.py tests/test_fleet.py tests/test_columns.py`.
- Broader if action registry changes ripple: include `tests/test_ai_chat.py tests/test_mcp_light.py` if present/appropriate.

Specialist sessions:
- `exp-1` found topology/action files.
- `fix-1` updated calibrated topology defaults and tests.
- `ora-1` reviewed the dynamic-scene plan.

Oracle-required implementation constraints accepted:
- `dynamic_scenes.py` must use a real topology contract including `channel`, `controller`, `seg_id`, `pixels`, wall index, pixel-zero, and orientation. Do not rely on `channels()` alone or fixed `lightctl.LEDS_PER_COLUMN`.
- `compose_dynamic_scene(...)` should be pure and return `{controller: payload}`. Payloads must include explicit segment ids. `apply_dynamic_scene(...)` only posts the composed payloads.
- Wire every action surface together: `actions.py`, `light_gui.py`, `mcp_light.py`, `ai_chat.py`, and tests.
- Enforce safety in code with a small vetted catalog. Avoid strobe/blink/flash/lightning/fireworks/sparkle and avoid 2D-only effects. Cap brightness and use smooth transitions.
- v1 strategies: `quiet_gradient`, `split_temperature`, `mirror`/`center_out`, `left_to_right`/`chase`, `vertical_rise`. Defer realtime/audio loops; use audio-reactive only if explicitly requested through existing/native WLED behavior.
- No topology mutation, segment start/stop rewrites, extra segment creation/deletion, per-LED frames, or raw passthrough on `dynamic_scene`.

## V2 requirement from live use

User feedback: v1 is doing unique things, but it must not be constrained to stock WLED programming. The AI should be able to choose brightness and spatial composition freely across the four strips:

- all four strips together in unison;
- each strip independently;
- any pair/group combination;
- center-pair vs outer-pair, left side vs right side, alternating columns;
- per-strip brightness chosen by the director;
- generated top/bottom-aware looks that paint pixels directly using WLED `seg.i`, not only stock `fx`/`pal` effects;
- still use stock effects when they are the right tool, but do not depend on them.

Important topology facts for v2:
- Pixel 0 is at the bottom for all four strips.
- `far-left`: controller left, WLED seg 0, 34 pixels.
- `middle-left`: controller left, WLED seg 1, 48 pixels.
- `middle-right`: controller right, WLED seg 1, 47 pixels.
- `far-right`: controller right, WLED seg 0, 40 pixels.
- Right controller segment ID order is not wall order; the composer must preserve physical wall order while emitting the confirmed live WLED segment ids.

V2 plan:
1. Extend `dynamic_scenes.py` with a small custom renderer that emits WLED `seg.i` arrays per segment.
   - Normalize vertical coordinate `y` from bottom=0 to top=1 per strip, respecting `pixel_zero`.
   - Render generated patterns: vertical gradients, top glow, bottom glow, center bloom, independent column palettes, alternating groups, symmetric wall gradient, random-but-tasteful speckles/shimmers without strobe.
   - Per-strip `bri` should be chosen by the scene profile/strategy, not fixed globally. Keep only sane safety bounds (avoid blinding max unless explicitly intense/party).
2. Add composition modes: `unison`, `independent`, `pairs`, `center_vs_outer`, `left_vs_right`, `alternating`, `random_groups`.
3. Keep generated mode as default for `dynamic_scene`; use WLED stock effects only when `engine="effect"` or strategy strongly benefits from native motion.
4. Update prompts/schema so the AI knows it can ask for generated spatial scenes and group modes, and that top/bottom orientation is known.
5. Verify with pure payload tests and one live four-strip generated frame test.

Oracle V2 review constraints accepted:
- Fix/verify right-controller live segment mapping before renderer work: physical wall order must stay `far-left, middle-left, middle-right, far-right`, but right emits seg `1` for middle-right and seg `0` for far-right.
- Do not use fixed-length helpers such as `lightctl.LEDS_PER_COLUMN`; V2 needs 34/48/47/40 length-aware `seg.i` generation.
- Generated payload contract: top-level conservative `bri`/`transition`/`udpn.nn`; each segment has explicit `id`, optional per-strip `bri`, `fx: 0`, and `i`; no `start`/`stop`/`len`.
- Generated mode is static one-frame composition. A future animation loop must be explicit; no hidden strobe/shimmer loop.
- Per-strip brightness must be opinionated and bounded, with stronger caps unless explicitly party/intense.
- Composition modes are deterministic enums: `unison`, `independent`, `pairs`, `center_vs_outer`, `left_vs_right`, `alternating`, `random_groups`.
- Generated speckles/shimmers must be sparse, dim, seeded, static, and non-strobe.
- Orientation math must use normalized `y=0` bottom and `y=1` top; invert if pixel zero ever differs.
- Schema gets `engine: generated|effect` and `composition_mode` enum. No raw `seg.i`, raw `fx/pal`, or segment bounds in `dynamic_scene`.
- Tests must prove generated frame lengths, right segment IDs, no topology mutation keys, top/bottom orientation, composition grouping, safety caps, and deterministic seeded behavior.

## Realtime/direct-control mode

User direction: add a mode where the AI is not relying on presets/default effects and can make its own realtime visuals through direct communication to strips/LEDs.

Accepted architecture:
- Do not have the LLM emit every LED for every frame. That would be slow, expensive, and unsafe.
- The AI should direct a local renderer: choose a shader/look, colors, energy, motion, grouping, intensity, duration/FPS.
- The local renderer streams frames to WLED over DDP UDP on port 4048.
- DDP packet details from WLED research:
  - header is 10 bytes: `struct.pack("!BBBBLH", flags, seq, data_type, dest_id, offset, length)`
  - flags: `0x40 | 0x01` for DDP v1 + PUSH on final packet
  - data type: `0x0B` RGB8
  - destination id: `1`
  - port: `4048`
  - keep packets below MTU; split if needed, though our largest controller is only 87 px = 261 bytes.
- Realtime mode overrides normal WLED state until streaming stops/timeout. Provide explicit stop.

Realtime v1 boundaries:
- One local daemon/thread at a time; starting a new realtime show stops the old one.
- Conservative FPS default 24; cap 40 unless explicitly changed by validated input.
- Duration required/default bounded; no unbounded infinite streaming from AI by default.
- Topology-aware frame assembly per controller:
  - left controller frame length 82, with far-left indices 0..33 and middle-left 34..81.
  - right controller frame length 87, with middle-right indices 0..46 and far-right 47..86.
  - physical wall order remains far-left, middle-left, middle-right, far-right.
  - y=0 bottom, y=1 top for all strips.
- Shaders should be deterministic, bounded, and non-strobe: red rocks pulse, aurora flow, bass bloom, vertical scan, liquid gradient, center wave.
- Add AI/client action names: `realtime_start` and `realtime_stop` or `direct_mode_start`/`direct_mode_stop`.
- Add MCP tools and GUI/AI prompt guidance.
- Tests before live streaming:
  - DDP packet header builder.
  - controller frame mapping lengths and right segment/bus placement.
  - shader bounds and deterministic seed behavior.
  - manager start/stop without network using fake socket/transport.
  - action routing/client surface tests.
