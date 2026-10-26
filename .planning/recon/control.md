# Recon: AI control surface / per-strip addressing (task-3)

Read-only recon. No code, config, or service changes; live GUI untouched. Live topology was read from `~/.config/lightsctl/config.json`. Two AI paths converge on the same WLED post: (a) native tool calling, (b) legacy JSON-plan fallback.

## 1. Tool / action catalog

**Primary path (native tool calling).** The catalog is built in `mcp_light.build_tools()` (`mcp_light.py:107-970`) and wrapped as OpenAI tools by `ai_chat._all_chat_tools()` (`ai_chat.py:429-459`); `ai_chat.chat_tools()` filters it by prompt (`ai_chat.py:461-518`; core set `ai_chat.py:238-273`, regex routing `ai_chat.py:275-370`).

- `strips` — `channels[]` (shared look) or `assignments[{channel,fx,pal,sx,ix}]` (distinct looks) (`mcp_light.py:353-390`); dispatch `mcp_light.py:1851-1877`; `columns.per_strip`/`apply_channels` (`columns.py:156-182`).
- `set_zone` — one channel + `fx/pal/col/sx/ix`. `zone` is required by the schema but explicitly "accepted and ignored" (`mcp_light.py:533-558`, text at `:536`); impl `mcp_light.py:1449-1479`.
- `wall_mode` — span/mirror/chase/versus (`mcp_light.py:329-351`); versus = left pair vs right pair only (`mcp_light.py:1554-1591`, `columns.py:135-153`). Note: span, mirror and chase post the same per-channel payload; mirror/chase do not touch the controller mirror bit or offset (`columns.py:113-132`).
- `set_color` / `set_hex_color` / `set_temperature` / `set_effect` — accept `target` or optional `segment` (`mcp_light.py:104, 148-192, 523-528`).
- `set_brightness` — channel target writes segment `bri`; any other target writes controller master `bri` (`mcp_light.py:1630-1638`).
- Wall-wide with no per-strip scope: `atmosphere`, `dynamic_scene`, `design_look`, `realtime_start`, `start_audio_reactive` (target dropped/ignored at `mcp_light.py:1891, 1908, 1922`; wall-only routing `:1878-1925`).
- `start_show` — steps of `atmosphere` | `wall_mode` | raw `payload`+`target` (`mcp_light.py:627-663`; `shows.py:82-164`).
- `observe_lighting` — per-segment fx/pal/col/bri/pixel stats (`mcp_light.py:109-110`; `lighting_context.py:117-240`).
- Refused by design: `set_segment_bounds`, `delete_segment` (`mcp_light.py:561-595`; impls `:1480-1485`).
- `design_palette` writes custom WLED palettes (`mcp_light.py:490-521`); `music_show`'s action enum is dynamic from Smart Director ownership (`mcp_light.py:780-848`; `ai_chat.py:859-887`).
- Every tool gets `target` (default `all`) injected (`mcp_light.py:523-528`); grammar documented at `mcp_light.py:85-97`, resolved in `fleet.py:403-452`.

**Fallback path (provider without tool calling).** `/api/ai` catches `ToolChatError` and runs `call_openai_for_plan` -> `parse_ai_plan` -> `apply_ai_plan` (`light_gui.py:5420-5439, 1644-1709, 2372-2380`). Vocabulary is `actions.AI_ACTIONS` (`actions.py:8-53`); wall routing `_apply_wall_action` (`light_gui.py:1959-2109`); direct payload conversion `payload_for_ai_action` (`light_gui.py:2113-2267`). Its schema (`light_gui.py:1449-1541`) has no `assignments`, `composition_mode`, or `shader`, so per-strip distinct looks are not expressible there.

## 2. Execution path (tool call -> WLED)

1. `ai_chat.run_chat` validates arguments against the runtime and catalog schemas, enforcing `additionalProperties:false` (`ai_chat.py:1282-1301, 941-946`).
2. `mcp_light.call_tool` dispatches to typed helpers (`mcp_light.py:1593-2065`): `strips` -> `columns.per_strip` (`:1851-1877`), `set_zone` -> `_set_zone` (`:1449`), `wall_mode` -> `_wall_mode` (`:1554`).
3. Effect ids are checked against the seeded live catalog (`mcp_light.py:1052-1105`), per channel for `strips` assignments (`mcp_light.py:1856-1858`).
4. `LightFleet.post_state` posts once per controller, strips controller-owned segment fields `start/stop/grp/spc/of/rev/mi/n/sel/set` (`fleet.py:294-310, 490-524`), and injects the resolved `id` for channel targets (`fleet.py:500-516`).
5. Builders: `on_payload`/`brightness_payload`/`color_payload`/`effect_payload`/`palette_payload`/`cct_payload`/`segment_payload` (`lightctl.py:529-740`); `validate_wled_payload` enforces ranges/types/1-3 color slots (`lightctl.py:729-798`); `LightClient.post_state` re-validates before HTTP (`lightctl.py:2246-2250`).

Concrete shapes reaching WLED:
- one channel: `{"seg":[{"id":0,"fx":28,"pal":2,"sx":160}]}`
- `strips` assignments for all four: left POST `{"seg":[{"id":0,...},{"id":1,...}]}`, right POST `{"seg":[{"id":0,...},{"id":1,...}]}` (`columns.py:164-182`)
- `set_brightness` channel: `{"seg":[{"id":1,"bri":120}]}`; `set_brightness` target=all: `{"bri":120}` (`mcp_light.py:1630-1638`)
- show raw look: payload posted with `transition` injected (`shows.py:204-228`), DDP/run engines stopped first (`shows.py:311-316`).

## 3. Addressing model

- Four channels over two controllers, two segments per controller (`fleet.py:29-52`); live config matches it: left = far-left seg 0 + middle-left seg 1, right = middle-right seg 0 + far-right seg 1.
- Targets: `all`, controller names, channel names, comma/plus combos, groups `outer` (far pair) / `inner`=`center` (middle pair) (`fleet.py:25-29, 403-452`).
- A controller is not a column; segment ids are controller-local and may not match wall order (`fleet.py:29-52, 440-452`; prompt warning `ai_chat.py:63-76`).
- `target` works for color/effect/temperature because id-less segment templates expand onto every selected strip (`fleet.py:500-524`). It does not give per-strip brightness: combo/controller/all targets send global `bri` per controller (`mcp_light.py:1630-1638`).
- Zones are not implemented: `zone` is ignored (`mcp_light.py:536, 1449-1479`). `set_segment_bounds` is refused to protect controller geometry (`mcp_light.py:561-595, 1480-1485`).

## 4. Capability table

| capability | exists? | where | gap |
|---|---|---|---|
| all 4 strips, one shared look | yes | `wall_mode` span / `strips.channels` (`columns.py:113-132, 156-161`) | mirror/chase are aliases, not distinct modes |
| any combination, same look | yes | `strips.channels` (`mcp_light.py:1864-1877`) | none major |
| each strip own fx+palette+speed+intensity in one tool call | yes | `strips.assignments` (`columns.py:164-182`; one POST per controller) | schema forbids `col`, `bri`, `c1-c3`, `cct`, `on` (`mcp_light.py:377-393`) |
| each strip own single color (plus fx+pal) | partial | `set_zone`, one strip per call (`mcp_light.py:1449-1479`) | 1 call per strip; one color slot; no brightness |
| each strip own brightness | partial | `set_brightness(target=channel)` (`mcp_light.py:1630-1638`) | 1 call per strip; no per-strip bri in `strips`; combo target = master bri |
| 4 strips: fx+pal+color+bri atomically in one call | **no** | closest: `strips` + 4x`set_zone` + brightness calls | 5-9 sequential calls, no transaction/rollback |
| raw per-segment escape hatch | yes | `wled_write` (`mcp_light.py:876-902, 1389-1416`) | payload broadcast per target; distinct payload per controller needs 2 calls (inference) |
| per-strip animated/realtime (DDP) | no | `realtime_start` has no target and paints all wall channels (`realtime.py:103-148, 907-925`; target popped `mcp_light.py:1908`) | no partial-strip DDP; subset `set_leds` streaming blanks other strips on that controller (`mcp_light.py:597-626`) |
| zone / top-half addressing | no | `set_zone.zone` ignored (`mcp_light.py:536`) | requirement not implemented |
| observe per-strip state/pixels | yes | `observe_lighting` (`lighting_context.py:117-240`) | pixel colors are rendered buffer, not verified light |
| hardware beat-reactive, per strip | no | native audio is wall-wide (`smart_director.py:327-345`); no `native_audio_reactive` tool | fallback action only (`light_gui.py:2264-2266, 2546-2548`) |
| hardware beat-reactive, wall | partial | `music_show native` while Smart Director owns the show (`smart_director.py:712-733`); host-mic mode via `start_audio_reactive` (`mcp_light.py:294-303`, `lightctl.py:2428-2476`) | no typed tool for the WLED AudioReactive usermod; needs director ownership |
| validation / safety | partial | range checks `lightctl.py:729-798`; live catalog `mcp_light.py:1052-1105`; sacred fields `fleet.py:294-310` | no seizure/flashing ban: `BLOCKED_EFFECTS` empty (`lightctl.py:125`), `FORBIDDEN_EFFECT_TERMS` empty (`lightctl.py:177`), prompt allows strobe (`ai_chat.py:79-81`) |

## 5. Gaps vs "AI controls all 4 / any combination / each strip individually"

1. **Schema gap, highest leverage.** `columns._seg_entry` already accepts per-strip `col`, `bri`, `c1-c3`, `cct` (`columns.py:53-78`), and `per_strip` forwards unknown spec keys (`columns.py:175-178`), but `strips.assignments` rejects them (`mcp_light.py:377-393`). Extending that schema (plus the spec whitelist at `columns.py:175-178`) turns the existing transport into full per-strip effect+palette+colors+brightness with one POST per controller.
2. **No atomic multi-target look.** Four strips with distinct effect+palette+color+brightness needs `strips` + up to 4x`set_zone` (color) + up to 4x`set_brightness`; each hits WLED separately, so a mid-plan failure leaves a mixed wall (`mcp_light.py:1593-2065` has no transaction).
3. **`wall_mode` versus only splits L/R pairs** (`columns.py:135-153`); arbitrary 4-way grouping must use `strips` (fx/pal only). Mirror/chase do nothing different from span (`columns.py:119-132`).
4. **Brightness semantics are inconsistent** (`mcp_light.py:1630-1638`): channel target = segment bri, combo/controller/all = master bri, so `set_brightness(target="far-left,far-right")` changes both whole controllers.
5. **`zone` is a no-op** (`mcp_light.py:536`); "top half magenta" can only be reached via `set_leds` or `wled_write`, both outside the per-strip look surface.
6. **Beat-reactive hardware is wall-wide and awkward**: no typed AudioReactive tool; `music_show native` requires Smart Director running and posts one payload to both controllers (`smart_director.py:327-345, 232-237`). Per-strip audio effects are only stock effect ids; whether each responds to audio is firmware-dependent (uncertain).
7. **Refusal-only surfaces** `set_segment_bounds`/`delete_segment` advertise but always raise (`mcp_light.py:1480-1485`); harmless, but the model may retry.
8. **Discovery/usage:** the prompt says "use per-channel tools for independent columns" (`ai_chat.py:74-76`) but has no worked example for 4 independent looks; the model likely under-uses `strips.assignments`.

Uncertainties: sequential-call composition (strips then set_zone colors, then brightness) relies on WLED merging partial segment updates by id — inferred from `fleet.py:500-524`, not exercised on hardware here. Raw `wled_write` behavior with duplicate segment ids across controllers is likewise inferred. No hardware writes were performed.

## 6. Existing tests

- `tests/test_columns.py:48-181` — wall modes, left_vs_right, set_channel, apply_channels, per_strip.
- `tests/test_zones_shows.py:114-182` set_zone payloads/no-bounds; `:296-514` show validation/runner/registry; `:514-880` MCP zone/show tool behavior and schema-required args; `:1026-1034` dry-run validation.
- `tests/test_ai_chat.py:56-106` tool surface and prompt routing; `:298-489` run_chat dispatch/validation (music_show enum, native args).
- `tests/test_ai_effect_policy.py:169-204` live-catalog enforcement for `wall_mode`/`strips`/`set_zone`.
- `tests/test_fleet.py:176-224` target resolution, `:251-396` fan-out, `:661-679` per-segment expansion.
- `tests/test_light_gui_server.py:20-58` fallback payloads; `tests/test_surfaces.py:187-200, 700-720` action enum/payload; `tests/test_realtime.py:148-156` `strips` routing.
- No test exercises per-strip `col`/`bri` assignments because the schema cannot express them (inference from `mcp_light.py:377-393`).

## 7. Recommended tool/action schema addition

Extend `strips` with a `looks[]` array so one validated call carries full per-target looks. Server rule: resolve each `target` via `fleet.resolve` (`fleet.py:403-452`), build one `seg` entry per resolved (controller, segment) with its `id`, reuse `columns._seg_entry` normalization (`columns.py:53-78`), group one POST per controller (`columns.py:164-182`), preserve `SACRED_SEGMENT_KEYS` (`fleet.py:294-310`), and validate via `lightctl.validate_wled_payload` (`lightctl.py:729-798`).

```json
{
  "name": "strips",
  "inputSchema": {
    "type": "object",
    "properties": {
      "looks": {
        "type": "array",
        "minItems": 1,
        "maxItems": 4,
        "items": {
          "type": "object",
          "properties": {
            "target": {"type": "string", "description": "channel | controller | combo, e.g. far-left or far-left,middle-right"},
            "fx": {"type": "integer", "minimum": 0, "maximum": 255},
            "pal": {"type": "integer", "minimum": 0, "maximum": 65535},
            "colors": {"type": "array", "minItems": 1, "maxItems": 3, "items": {"type": "string", "pattern": "^#?[0-9A-Fa-f]{6}$"}},
            "speed": {"type": "integer", "minimum": 0, "maximum": 255},
            "intensity": {"type": "integer", "minimum": 0, "maximum": 255},
            "brightness": {"type": "integer", "minimum": 0, "maximum": 255},
            "c1": {"type": "integer", "minimum": 0, "maximum": 255},
            "c2": {"type": "integer", "minimum": 0, "maximum": 255},
            "c3": {"type": "integer", "minimum": 0, "maximum": 31},
            "cct": {"type": "integer", "minimum": 0, "maximum": 10091},
            "on": {"type": "boolean"},
            "transition": {"type": "integer", "minimum": 0, "maximum": 25500}
          },
          "required": ["target"],
          "additionalProperties": false
        }
      }
    },
    "required": ["looks"],
    "additionalProperties": false
  }
}
```
