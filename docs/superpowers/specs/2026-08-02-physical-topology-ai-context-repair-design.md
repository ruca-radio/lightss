# Physical Topology and AI Context Repair — Design

Date: 2026-08-02
Status: Approved in conversation; pending written-spec review

## Purpose

Repair the Lightss platform and its two live WLED controllers so every AI and
manual-control path understands and preserves the real four-column wall layout.
This specification supersedes conflicting controller mappings, 50-pixel guesses,
and firmware details in the 2026-08-01 fleet design.

## Verified installation

The wall contains four vertical 2.0 m BTF-LIGHTING WS2811 FCOB RGB strips,
spaced 32 inches apart. LED/pixel 0 is at the bottom and indices rise upward.

The product is the 12 V, 12 mm, 720-visible-LED/m model, ASIN B0C38QYR53. A
5 m reel contains 100 WS2811 ICs and has 50 mm cut intervals. WLED therefore
addresses 20 IC pixels per meter, so each exact 2.0 m wall section contains 40
addressable pixels. The listing specifies B-R-G channel order.

The physical mapping was verified interactively by isolating each live segment:

| Wall position | Controller | WLED segment | GPIO | Logical pixels |
|---|---|---:|---:|---:|
| far-left | `10.27.27.110` | 0 | 16 | 34 |
| middle-left | `10.27.27.110` | 1 | 2 | 48 |
| middle-right | `10.27.27.112` | 1 | 16 | 47 |
| far-right | `10.27.27.112` | 0 | 2 | 40 |

Both controllers reported WLED 16.0.1 during the 2026-08-02 live query.

## Root causes

1. The in-progress fleet defaults assign the left/right channel aliases to the
   opposite controller from the verified wall wiring.
2. The existing design and code assume 50 logical pixels per column, while the
   product has 40 addressable WS2811 IC pixels per 2 m column.
3. The live WLED bus lengths are incorrect (`200 + 275` on `.110`, `250 + 250`
   on `.112`) and cannot represent the physical installation accurately.
4. Physical topology is duplicated in prose prompts and code constants rather
   than represented once as structured data shared by all planners.
5. The current fleet snapshot contains per-controller device data but does not
   label it with wall position, spacing, orientation, physical length, pixel
   density, GPIO, or segment ownership.
6. A continuous mode can overwrite an identification/manual state. Platform AI
   and explicit controls must retain the established ownership handoff that
   stops browser Music Mode before applying an explicit look.

## Chosen approach

Use one configuration-driven physical topology model and derive fleet routing,
AI context, wall composition, and validation from it. Reconfigure the live WLED
devices only after exporting their current configuration, and verify every
output visually afterward.

A prompt-only patch is rejected because it leaves device geometry and routing
wrong. Pure live introspection is rejected because WLED cannot infer physical
left-to-right placement, spacing, or wall orientation.

## Structured topology

The effective Lightss configuration will describe each controller and segment
with enough metadata to render a complete AI context. The canonical values are:

```json
{
  "installation": {
    "name": "bedroom-wall",
    "wall_order": ["far-left", "middle-left", "middle-right", "far-right"],
    "spacing_inches": 32,
    "orientation": "vertical",
    "pixel_zero": "bottom",
    "column_length_m": 2.0,
    "pixels_per_meter": 20,
    "visible_leds_per_meter": 720,
    "color_order": "BRG"
  },
  "controllers": [
    {
      "name": "left",
      "host": "http://10.27.27.110",
      "segments": {
        "0": {"channel": "far-left", "gpio": 16, "pixels": 34, "start": 0, "stop": 34},
        "1": {"channel": "middle-left", "gpio": 2, "pixels": 48, "start": 34, "stop": 82}
      }
    },
    {
      "name": "right",
      "host": "http://10.27.27.112",
      "segments": {
        "0": {"channel": "far-right", "gpio": 2, "pixels": 40, "start": 47, "stop": 87},
        "1": {"channel": "middle-right", "gpio": 16, "pixels": 47, "start": 0, "stop": 47}
      }
    }
  ]
}
```

The loader will remain backward-compatible with the existing short form where
a segment value is only a channel-name string. Missing physical metadata will
degrade explicitly rather than inventing geometry.

## AI context and control flow

`LightFleet.get_fleet_snapshot()` will return both structured installation
topology and labeled live controller snapshots. A deterministic formatter will
turn the same object into concise model context. It will state:

- four vertical columns and their physical left-to-right order;
- two-meter height, 32-inch spacing, and non-uniform calibrated addressable pixel counts (34/48/47/40);
- LED 0 at the bottom, including top/bottom zone and motion-direction meaning;
- exact controller, GPIO, and segment ownership for each channel;
- BRG device color order while keeping AI-facing colors semantic RGB;
- current power, brightness, effect, palette, colors, segment bounds, and
  reverse/mirror state for every controller/segment;
- partial snapshot failures without hiding healthy controllers.

The shared context must reach all planning paths:

1. tool-calling AI chat (`ai_chat.py`);
2. legacy structured-plan fallback (`light_gui.py`);
3. room-vision observation followed by the main planner;
4. song matching and autonomous/music planning;
5. MCP state/context tools where applicable.

The model will operate in semantic RGB. WLED owns physical BRG byte ordering;
the application must not manually swap red, blue, and green values.

Before an explicit browser AI or manual command posts a new look, browser Music
Mode must stop and relinquish its beat writer. Music Mode's own coalesced beat
path remains separate and browser-owned. No server-side competing listener will
be introduced.

## Live WLED migration

Live device changes are a controlled migration, not an implicit side effect of
starting Lightss:

1. Export `/json/cfg`, `/json/state`, `/json/info`, and effect/palette lists for
   both hosts into a timestamped local backup outside version control.
2. Resolve WLED 16.0.1's numeric color-order enum from its authoritative source
   or a read/write/read-back experiment. Do not assume `order: 1` means RGB.
3. Update each controller to two WS2811 outputs of 40 pixels, preserving the
   verified GPIO ownership. Use contiguous WLED global ranges `0..39` and
   `40..79` and matching segments.
4. Set the bus color order to the WLED enum corresponding to BRG.
5. Preserve safety, brightness/current-limit, network, sync, and usermod
   settings unless a reviewed requirement explicitly changes them.
6. Allow the controller to reboot, then read back `/json/cfg`, `/json/state`, and
   `/json/info` from both hosts.
7. Illuminate each segment individually with a steady, dim solid color and get
   user confirmation of position, full-length coverage, direction, and channel
   correctness.
8. Restore a safe steady or prior non-flashing look. The no-strobe, no-blink,
   no-flash rule applies throughout migration and testing.

If a 40-pixel test does not illuminate the complete 2 m section, stop the
migration and restore the saved configuration before changing another variable.

## Code boundaries

- `fleet.py`: parse and validate topology; resolve targets; expose a structured,
  topology-labeled snapshot.
- A small focused topology formatter module (or a focused section in `fleet.py`
  if extraction adds no clarity): serialize topology plus live state for AI.
- `light_gui.py`: consume the shared context in legacy, vision, song, and
  autonomous planner paths; preserve explicit-control ownership handoff.
- `ai_chat.py`: replace duplicated physical prose with the shared runtime
  context while retaining durable safety and tool-use instructions.
- `columns.py`: derive wall ordering and direction from topology rather than a
  conflicting hardcoded mapping.
- `lightctl.py`: use 40 as the installation default only through topology;
  generic payload helpers remain device-agnostic.
- GUI settings APIs: expose and save the structured installation without
  dropping unknown compatible keys.

Unrelated refactoring and new dependencies are out of scope.

## Error handling

- Reject duplicate channel names, duplicate wall positions, unknown wall-order
  entries, nonpositive pixel counts, unsupported orientation, and segment
  metadata inconsistent with controller ownership.
- An unavailable controller produces a labeled error entry while healthy
  controller context and commands continue.
- Never silently fall back to the old reversed mapping.
- Never mutate live WLED bus configuration without a successful backup.
- On failed post-migration readback or visual verification, restore the backup
  and report the exact mismatch.

## Test and verification strategy

Implementation follows test-driven development. Required regression coverage:

1. new and legacy config parsing;
2. exact verified host/segment/channel/GPIO mapping;
3. four 40-pixel vertical columns, 32-inch spacing, and bottom-up zone math;
4. topology-labeled fleet snapshots with partial controller failure;
5. identical physical context in tool chat, legacy planner, vision-to-planner,
   song matching, and autonomous planning;
6. semantic RGB payloads with BRG delegated to WLED bus configuration;
7. explicit AI/manual ownership handoff without breaking browser Music Mode;
8. wall modes following physical order;
9. rendered GUI/config round-trip contracts;
10. existing no-strobe/no-flash and browser-owned recognition contracts.

Verification includes focused tests, the full test suite, static compilation or
lint gates already used by the repository, `git diff --check`, live WLED
readback, and the four-step visual segment identification described above.

## Success criteria

- Both live controllers read back two 40-pixel WS2811 outputs and matching
  segments with the verified BRG enum.
- All four physical columns illuminate fully and map to the correct channel.
- Every AI request receives one consistent structured description of physical
  geometry and current live state.
- AI can intentionally target a column, side, vertical zone, or wall-wide motion
  without relying on reversed or invented geometry.
- Explicit actions are not immediately overwritten by Music Mode.
- Existing safety and browser-owned Music Mode behavior remain intact.
