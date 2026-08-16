# Physical Topology and AI Context Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the verified four-column physical installation and current live WLED state the shared source of truth for every Lightss controller and AI path, then safely migrate both live controllers to 40 BRG pixels per strip.

**Architecture:** Extend `fleet.py` with backward-compatible structured installation and segment metadata, and make the fleet snapshot carry both topology and labeled device snapshots. Format that single snapshot in `light_gui.py` for tool chat, structured fallback, vision, song, and autonomous planning. Keep WLED bus color ordering device-side, and perform live migration through an explicit backup-first script with readback and rollback safeguards.

**Tech Stack:** Python 3.11+, stdlib dataclasses/JSON/urllib, WLED 16 JSON API, unittest/pytest, existing browser-rendered HTML contract tests.

---

## File map

- Modify `fleet.py`: canonical installation model, structured segment metadata, validation, exact verified defaults, topology-bearing snapshots.
- Modify `lightctl.py`: change installation zone defaults from 50 to 40 pixels without coupling generic WLED clients to a controller host.
- Modify `columns.py`: consume the fleet's configured wall order and use a 10-pixel chase phase step for 40-pixel columns.
- Modify `light_gui.py`: format topology plus all live segments; route that context through every planner; round-trip installation settings.
- Modify `ai_chat.py`: remove false 50-pixel/L-shaped prose; retain durable tool and safety rules while runtime context supplies geometry.
- Modify `light_gui_html.py`: retain explicit browser Music Mode handoff and add target propagation to AI requests if missing.
- Create `scripts/migrate_wled_topology.py`: explicit backup, preflight, apply, wait, readback, segment repair, and restore workflow.
- Create `tests/test_wled_topology_migration.py`: migration payload, backup, refusal, and verification tests with fake HTTP.
- Modify `tests/test_fleet.py`, `tests/test_columns.py`, `tests/test_new_features.py`, `tests/test_surfaces.py`, `tests/test_ai_chat.py`, and `tests/test_settings_api.py`: regression contracts from the approved spec.
- Modify `README.md`: document the verified installation and migration command.

## Task 1: Canonical structured installation model

**Files:**
- Modify: `fleet.py`
- Test: `tests/test_fleet.py`

- [ ] **Step 1: Write failing tests for the verified defaults and metadata**

Add tests that require `.110` to own the left pair, `.112` to own the right pair, and every channel to expose calibrated pixels plus its GPIO:

```python
def test_builtin_topology_matches_verified_wall():
    installation, controllers = fleet.load_topology({})
    assert installation.wall_order == [
        "far-left", "middle-left", "middle-right", "far-right"
    ]
    assert installation.spacing_inches == 30
    assert installation.pixel_zero == "bottom"
    assert installation.column_length_m == 2.0
    assert installation.pixels_per_meter == 20
    assert installation.visible_leds_per_meter == 720
    assert installation.color_order == "BRG"
    by_host = {controller.host: controller for controller in controllers}
    assert by_host["http://10.27.27.110"].segments == {
        0: fleet.SegmentConfig("far-left", gpio=16, pixels=40),
        1: fleet.SegmentConfig("middle-left", gpio=2, pixels=40),
    }
    assert by_host["http://10.27.27.112"].segments == {
        0: fleet.SegmentConfig("far-right", gpio=2, pixels=40, start=47, stop=87),
        1: fleet.SegmentConfig("middle-right", gpio=16, pixels=40),
    }


def test_legacy_string_segment_schema_remains_supported():
    config = {"controllers": [{
        "name": "solo", "host": "http://1.2.3.4", "segments": {"0": "bar"}
    }]}
    _installation, controllers = fleet.load_topology(config)
    assert controllers[0].segments[0] == fleet.SegmentConfig("bar")


def test_duplicate_wall_channel_is_rejected():
    config = {
        "installation": {"wall_order": ["same", "same"]},
        "controllers": [{
            "name": "solo", "host": "http://1.2.3.4",
            "segments": {"0": {"channel": "same", "pixels": 40}},
        }],
    }
    with pytest.raises(ValueError, match="duplicate wall channel"):
        fleet.load_topology(config)
```

Import `pytest` in `tests/test_fleet.py` for validation cases.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
python -m pytest tests/test_fleet.py -q
```

Expected: failures because `InstallationConfig`, `SegmentConfig`, and `load_topology()` do not exist and the current defaults are reversed.

- [ ] **Step 3: Add the dataclasses and canonical defaults**

Replace string-only segment configuration with:

```python
@dataclass(frozen=True)
class InstallationConfig:
    name: str = "bedroom-wall"
    wall_order: list[str] = field(default_factory=lambda: list(WALL_ORDER))
    spacing_inches: float = 30.0
    orientation: str = "vertical"
    pixel_zero: str = "bottom"
    column_length_m: float = 2.0
    pixels_per_meter: int = 20
    visible_leds_per_meter: int = 720
    color_order: str = "BRG"


@dataclass(frozen=True)
class SegmentConfig:
    channel: str
    gpio: int | None = None
    pixels: int | None = None


@dataclass
class ControllerConfig:
    name: str
    host: str
    segments: dict[int, SegmentConfig] = field(default_factory=dict)
```

Use these exact built-ins:

```python
_BUILTIN_CONTROLLERS = [
    {
        "name": "left", "host": "http://10.27.27.110",
        "segments": {
            "0": {"channel": "far-left", "gpio": 16, "pixels": 34, "start": 0, "stop": 34},
            "1": {"channel": "middle-left", "gpio": 2, "pixels": 48, "start": 34, "stop": 82},
        },
    },
    {
        "name": "right", "host": "http://10.27.27.112",
        "segments": {
            "0": {"channel": "far-right", "gpio": 2, "pixels": 40, "start": 47, "stop": 87},
            "1": {"channel": "middle-right", "gpio": 16, "pixels": 47, "start": 0, "stop": 47},
        },
    },
]
```

Implement `_parse_segment()`, `_parse_installation()`, and `load_topology()` so string segment values become `SegmentConfig(channel=value)`. Reject empty/duplicate channels, duplicate wall-order entries, nonpositive pixels, unsupported orientation, unsupported `pixel_zero`, and wall-order entries absent from all configured channels. Keep `load_controllers()` as a compatibility wrapper returning `load_topology(config)[1]`.

- [ ] **Step 4: Update fleet routing to use `SegmentConfig.channel`**

In `LightFleet.__init__`, store `self.installation`, set `self.WALL_ORDER = list(installation.wall_order)`, and build `_channels` with:

```python
for seg_id, segment in controller.segments.items():
    channel = segment.channel
    # retain existing collision warnings
    self._channels[channel] = (controller.name, seg_id)
```

Add:

```python
def topology_dict(self) -> dict:
    return {
        "installation": dataclasses.asdict(self.installation),
        "controllers": [
            {
                "name": controller.name,
                "host": controller.host,
                "segments": {
                    str(seg_id): dataclasses.asdict(segment)
                    for seg_id, segment in controller.segments.items()
                },
            }
            for controller in self.controllers
        ],
    }
```

Pass the parsed `InstallationConfig` from `from_config()` into the constructor. If callers construct `LightFleet` directly, accept an optional `installation=` and use the canonical installation only when its wall-order channels match; otherwise derive a minimal installation with the provided channel order so tests and custom fleets do not acquire invented aliases.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run:

```bash
python -m pytest tests/test_fleet.py -q
```

Expected: all fleet tests pass, including legacy config parsing.

- [ ] **Step 6: Commit the topology model**

```bash
git add fleet.py tests/test_fleet.py
git commit -m "fix: model verified WLED wall topology"
```

## Task 2: Correct pixel geometry and wall composition

**Files:**
- Modify: `lightctl.py`
- Modify: `columns.py`
- Test: `tests/test_new_features.py`
- Test: `tests/test_columns.py`

- [ ] **Step 1: Write failing geometry tests**

Add:

```python
def test_verified_column_geometry_defaults_to_40_pixels():
    assert lightctl.LEDS_PER_COLUMN == 40
    assert lightctl.COLUMN_LENGTH_M == 2.0
    assert lightctl.zone_bounds("bottom half") == (0, 20)
    assert lightctl.zone_bounds("top half") == (20, 40)


def test_downward_orientation_flips_top_and_bottom():
    assert lightctl.zone_bounds("top half", orientation="down") == (0, 20)
    assert lightctl.zone_bounds("bottom half", orientation="down") == (20, 40)
```

Update the chase assertion in `tests/test_columns.py` to expect offsets `[0, 10, 20, 30]` across the four 40-pixel columns and assert the actual controller grouping follows `.110` left / `.112` right.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
python -m pytest tests/test_new_features.py tests/test_columns.py -q
```

Expected: geometry tests report 50 instead of 40 and chase offsets report the old 12-pixel step.

- [ ] **Step 3: Make the minimal geometry correction**

In `lightctl.py`:

```python
LEDS_PER_COLUMN = 40
COLUMN_LENGTH_M = 2.0
```

Update the comments from “≈4 cm” and 50 LEDs to “20 addressable WS2811 IC pixels/m; 40 per 2 m column; 720 visible COB LEDs/m.” Do not change AI-facing RGB payloads; physical BRG is a bus setting.

In `columns.py`, use the fleet instance's configured wall order and change the default chase phase:

```python
offset_step = int(seg_opts.pop("offset_step", lightctl.LEDS_PER_COLUMN // 4))
```

- [ ] **Step 4: Run focused tests and verify GREEN**

```bash
python -m pytest tests/test_new_features.py tests/test_columns.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit geometry corrections**

```bash
git add lightctl.py columns.py tests/test_new_features.py tests/test_columns.py
git commit -m "fix: use forty-pixel wall geometry"
```

## Task 3: Put topology and every live segment into the shared AI snapshot

**Files:**
- Modify: `fleet.py`
- Modify: `light_gui.py`
- Test: `tests/test_fleet.py`
- Test: `tests/test_surfaces.py`

- [ ] **Step 1: Write failing snapshot and formatter tests**

Add a fleet snapshot test:

```python
def test_fleet_snapshot_labels_live_devices_with_topology():
    fleet_ = make_fleet()
    snapshot = fleet_.get_fleet_snapshot()
    assert snapshot["topology"]["installation"]["spacing_inches"] == 30
    assert snapshot["topology"]["controllers"][0]["segments"]["0"] == {
        "channel": "far-left", "gpio": 16, "pixels": 40
    }
    assert set(snapshot["devices"]) == {"left", "right"}
```

Add formatter coverage in `tests/test_surfaces.py` with two segments per device:

```python
def test_device_snapshot_text_contains_physical_topology_and_all_segments():
    snapshot = {
        "topology": {
            "installation": {
                "wall_order": ["far-left", "middle-left", "middle-right", "far-right"],
                "spacing_inches": 32, "orientation": "vertical",
                "pixel_zero": "bottom", "column_length_m": 2.0,
                "pixels_per_meter": 20, "visible_leds_per_meter": 720,
                "color_order": "BRG",
            },
            "controllers": [{
                "name": "left", "host": "http://10.27.27.110",
                "segments": {
                    "0": {"channel": "far-left", "gpio": 16, "pixels": 34, "start": 0, "stop": 34},
                    "1": {"channel": "middle-left", "gpio": 2, "pixels": 48, "start": 34, "stop": 82},
                },
            }],
        },
        "devices": {
            "left": {"state": {"on": True, "bri": 80, "seg": [
                {"id": 0, "start": 0, "stop": 40, "fx": 9, "rev": False},
                {"id": 1, "start": 40, "stop": 80, "fx": 67, "rev": True},
            ]}, "info": {"name": "wled-1", "ver": "16.0.1"}},
        },
    }
    text = light_gui.device_snapshot_text(snapshot)
    for expected in (
        "four vertical columns", "32 inches", "40 addressable pixels",
        "LED 0 at the bottom", "BRG", "far-left", "middle-left",
        "segment 0", "segment 1", "fx=9", "fx=67",
    ):
        assert expected in text
```

- [ ] **Step 2: Run the focused tests and verify RED**

```bash
python -m pytest tests/test_fleet.py tests/test_surfaces.py -q
```

Expected: `get_fleet_snapshot()` lacks `topology`/`devices`, and the formatter recognizes only the old controller-map shape and first active segment.

- [ ] **Step 3: Change the fleet snapshot envelope**

Implement:

```python
def get_fleet_snapshot(self) -> dict:
    devices: dict[str, dict] = {}
    for name in self.names():
        try:
            devices[name] = self.clients[name].get_device_snapshot()
        except Exception as exc:
            _warn(f"snapshot of controller {name!r} failed: {exc}")
            devices[name] = {"error": str(exc)}
    return {"topology": self.topology_dict(), "devices": devices}
```

- [ ] **Step 4: Refactor `device_snapshot_text()` around the new envelope**

Add a topology header formatter that describes the installation in deterministic text. Then render every segment, not only `state["seg"][0]`:

```python
for segment in state.get("seg", []):
    lines.append(
        "Segment "
        f"{segment.get('id', '?')}: start={segment.get('start', '?')}, "
        f"stop={segment.get('stop', '?')}, on={segment.get('on', '?')}, "
        f"bri={segment.get('bri', '?')}, fx={segment.get('fx', '?')}, "
        f"pal={segment.get('pal', '?')}, colors={segment.get('col', [])}, "
        f"reverse={segment.get('rev', '?')}, mirror={segment.get('mi', '?')}"
    )
```

Retain compatibility with single-client snapshots and the old `{controller: snapshot}` fleet shape so existing callers/tests do not break abruptly. Sanitize network secrets exactly as today.

- [ ] **Step 5: Run focused tests and verify GREEN**

```bash
python -m pytest tests/test_fleet.py tests/test_surfaces.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit snapshot context**

```bash
git add fleet.py light_gui.py tests/test_fleet.py tests/test_surfaces.py
git commit -m "feat: expose wall topology to AI snapshots"
```

## Task 4: Unify all planner paths and preserve control ownership

**Files:**
- Modify: `ai_chat.py`
- Modify: `light_gui.py`
- Modify: `light_gui_html.py`
- Test: `tests/test_ai_chat.py`
- Test: `tests/test_surfaces.py`

- [ ] **Step 1: Write failing prompt and data-flow tests**

Add assertions that the static tool-chat prompt no longer contains false geometry:

```python
def test_static_tool_prompt_has_no_invented_geometry():
    prompt = ai_chat.TOOL_CHAT_SYSTEM_PROMPT
    assert "50 individually addressable" not in prompt
    assert "LEDs 0-24" not in prompt
    assert "L-shaped" not in prompt
    assert "device snapshot" in prompt.lower()
```

Patch `ai_chat.run_chat` in tests for `/api/ai`, `match_lights_to_song()`, and the autonomous planner, then assert each received `context_text` includes `far-left`, `32 inches`, `40 addressable pixels`, and the current per-segment state. For the legacy and vision paths, inspect the user input passed to `build_openai_request()` / `call_openai_for_plan()`.

Retain and strengthen the existing browser ownership contract:

```python
assert send_body.index("stopMusicMode();") < send_body.index("postAction(action, values)")
assert ai_body.index("stopMusicMode();") < ai_body.index("fetchJsonWithTimeout('/api/ai'")
assert "sendBeatUpdate" in html
```

- [ ] **Step 2: Run focused tests and verify RED**

```bash
python -m pytest tests/test_ai_chat.py tests/test_surfaces.py -q
```

Expected: the static prompt still claims 50 pixels and an L-shaped roof; at least one planner path lacks the new topology assertions.

- [ ] **Step 3: Make the static prompt topology-agnostic**

Keep role, tool-use, gravity, safety, show, TV, and semantic-color guidance in `TOOL_CHAT_SYSTEM_PROMPT`, but replace hardcoded layout prose with:

```text
The user message includes a runtime installation topology and current WLED
snapshot. Treat it as authoritative for controller ownership, wall order,
spacing, orientation, pixel counts, zones, and current state. Never invent
missing geometry. AI-facing colors are semantic RGB; WLED applies the physical
bus color order.
```

Update `system_knowledge_prompt()` the same way: stable behavior belongs in the system prompt; physical facts come from `device_snapshot_text()`.

- [ ] **Step 4: Route one shared context builder through every planner**

Add:

```python
def ai_context_text(client: Any, now_playing: dict | None = None) -> str:
    parts: list[str] = []
    if now_playing:
        parts.append(f"Background audio now playing: {now_playing_text(now_playing)}")
    snapshot = _device_snapshot(client)
    parts.append(device_snapshot_text(snapshot))
    return "\n\n".join(parts)
```

Use it for `/api/ai`, `match_lights_to_song()`, `generate_mood_for_song()`, and `AutonomousMode`. Continue passing the same snapshot into the legacy structured planner and vision observation-to-planner flow. Do not add a second recognizer or server-side continuous writer.

- [ ] **Step 5: Preserve browser ownership and target propagation**

Keep `stopMusicMode()` before explicit `send()` and `askAI()` requests. Include the selected target in the AI body:

```javascript
body: JSON.stringify({
  prompt,
  now_playing: song,
  target: currentTarget(),
  async: true
})
```

Do not call `stopMusicMode()` from beat updates; `sendBeatUpdate()` remains the coalesced Music Mode path.

- [ ] **Step 6: Run focused tests and verify GREEN**

```bash
python -m pytest tests/test_ai_chat.py tests/test_surfaces.py -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit unified planner context**

```bash
git add ai_chat.py light_gui.py light_gui_html.py tests/test_ai_chat.py tests/test_surfaces.py
git commit -m "fix: give every AI path live wall context"
```

## Task 5: Round-trip topology through settings

**Files:**
- Modify: `light_gui.py`
- Test: `tests/test_settings_api.py`

- [ ] **Step 1: Write failing settings tests**

Add `installation` to the temporary config and assert GET/POST preservation:

```python
def test_installation_settings_roundtrip_preserves_metadata(self):
    installation = {
        "wall_order": ["far-left", "middle-left", "middle-right", "far-right"],
        "spacing_inches": 32,
        "orientation": "vertical",
        "pixel_zero": "bottom",
        "column_length_m": 2.0,
        "pixels_per_meter": 20,
        "visible_leds_per_meter": 720,
        "color_order": "BRG",
    }
    status, payload = self._post("/api/settings", {"installation": installation})
    assert status == 200
    assert self._read_config()["installation"] == installation
    _, payload = self._get("/api/settings")
    assert payload["settings"]["installation"] == installation
```

Also change controller fixtures to the structured segment schema and assert unknown compatible segment keys survive a settings round trip.

- [ ] **Step 2: Run tests and verify RED**

```bash
python -m pytest tests/test_settings_api.py -q
```

Expected: `installation` is ignored because it is absent from `SETTINGS_MERGE_KEYS` and `current_settings()`.

- [ ] **Step 3: Add validated installation settings support**

Add `"installation"` to `SETTINGS_MERGE_KEYS`, return the effective installation from `current_settings()`, and call `fleet.load_topology(candidate_config)` before saving controller or installation updates. A validation failure must return HTTP 400 and must not alter the config file.

When rendering default controller settings, serialize dataclasses with `dataclasses.asdict()` so structured segment metadata is retained.

- [ ] **Step 4: Run tests and verify GREEN**

```bash
python -m pytest tests/test_settings_api.py -q
```

Expected: all settings tests pass.

- [ ] **Step 5: Commit settings support**

```bash
git add light_gui.py tests/test_settings_api.py
git commit -m "feat: persist physical installation settings"
```

## Task 6: Build a backup-first live WLED migration utility

**Files:**
- Create: `scripts/migrate_wled_topology.py`
- Create: `tests/test_wled_topology_migration.py`

- [ ] **Step 1: Write failing migration unit tests**

Test pure payload generation and fake HTTP behavior:

```python
EXPECTED = {
    "http://10.27.27.110": [
        {"start": 0, "len": 40, "pin": [16], "order": 2, "type": 22},
        {"start": 40, "len": 40, "pin": [2], "order": 2, "type": 22},
    ],
    "http://10.27.27.112": [
        {"start": 0, "len": 40, "pin": [2], "order": 2, "type": 22},
        {"start": 40, "len": 40, "pin": [16], "order": 2, "type": 22},
    ],
}


def test_build_bus_payload_preserves_unrelated_bus_fields():
    cfg = {"hw": {"led": {"ins": [{
        "start": 0, "len": 200, "pin": [16], "order": 1, "type": 22,
        "rev": False, "skip": 0, "ref": False, "rgbwm": 0,
        "freq": 0, "maxpwr": 0, "ledma": 30, "drv": 0,
    }]}}}
    payload = migration.build_config_payload(cfg, [(16, 0), (2, 40)])
    first = payload["hw"]["led"]["ins"][0]
    assert first["len"] == 40
    assert first["order"] == 2
    assert first["ledma"] == 30


def test_apply_refuses_without_complete_backup(tmp_path):
    transport = FakeTransport(fail_path="/json/pal")
    with pytest.raises(RuntimeError, match="backup incomplete"):
        migration.migrate(transport, tmp_path, apply=True)
    assert transport.posts == []


def test_readback_requires_80_total_pixels_and_brg():
    with pytest.raises(RuntimeError, match="readback mismatch"):
        migration.verify_config({"hw": {"led": {"ins": [
            {"start": 0, "len": 40, "pin": [16], "order": 1},
            {"start": 40, "len": 40, "pin": [2], "order": 2},
        ]}}}, expected_pins=[16, 2])
```

- [ ] **Step 2: Run tests and verify RED**

```bash
python -m pytest tests/test_wled_topology_migration.py -q
```

Expected: import failure because the migration module does not exist.

- [ ] **Step 3: Implement explicit backup and preflight**

The script must:

- default to the two verified hosts;
- GET `/json/cfg`, `/json/state`, `/json/info`, `/json/eff`, and `/json/pal`;
- write one JSON file per endpoint per host under a caller-specified backup directory;
- validate both backups before any POST;
- print the planned bus diff in default dry-run mode;
- require `--apply` for writes;
- use WLED 16's authoritative enum `COL_ORDER_BRG = 2` (from `wled00/const.h`);
- preserve all existing bus fields while changing only `start`, `len`, and `order`;
- match buses by verified GPIO, not by array position.

Use:

```python
WLED_BRG_ORDER = 2
HOST_PINS = {
    "http://10.27.27.110": [16, 2],
    "http://10.27.27.112": [2, 16],
}
SEGMENT_PAYLOAD = {
    "seg": [
        {"id": 0, "start": 0, "stop": 40, "on": True, "fx": 0},
        {"id": 1, "start": 40, "stop": 80, "on": True, "fx": 0},
    ],
    "udpn": {"nn": True},
}
```

POST the partial config envelope to `/json/cfg`, wait with bounded retries for `/json/info`, then POST `SEGMENT_PAYLOAD` to `/json/state`.

- [ ] **Step 4: Implement readback and restore mode**

After apply, GET `/json/cfg`, `/json/state`, and `/json/info`. Require two buses with lengths `[40, 40]`, starts `[0, 40]`, `order == 2`, correct pins, `info.leds.count == 80`, and two segments with bounds `(0, 40)` / `(40, 80)`.

Add `--restore BACKUP_DIR`, which POSTs the backed-up `hw.led` config, waits for the device, then restores the backed-up state. Restore must also perform readback and report mismatches.

- [ ] **Step 5: Run migration tests and verify GREEN**

```bash
python -m pytest tests/test_wled_topology_migration.py -q
```

Expected: all migration tests pass with no network access.

- [ ] **Step 6: Commit the migration utility**

```bash
git add scripts/migrate_wled_topology.py tests/test_wled_topology_migration.py
git commit -m "feat: add safe WLED topology migration"
```

## Task 7: Documentation and complete software verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update installation documentation**

Document the exact verified mapping, four 2 m columns, 32-inch spacing, non-uniform calibrated addressable IC pixel counts (34/48/47/40), bottom-up orientation, 720 visible LEDs/m, and physical BRG bus order. State clearly that API colors remain RGB.

Document dry-run and apply commands:

```bash
backup_dir="/tmp/lightss-wled-backups/$(date +%Y%m%d-%H%M%S)"
python scripts/migrate_wled_topology.py --backup-dir "$backup_dir"
python scripts/migrate_wled_topology.py --backup-dir "$backup_dir" --apply
```

Document restore:

```bash
python scripts/migrate_wled_topology.py --restore "$backup_dir"
```

- [ ] **Step 2: Run all software verification gates**

Run fresh:

```bash
python -m pytest tests/ -q
python -m py_compile lightctl.py fleet.py columns.py ai_chat.py light_gui.py scripts/migrate_wled_topology.py
git diff --check
```

Expected: zero test failures, zero compile errors, and no whitespace errors. If the pre-existing dirty tree contains unrelated failing tests, isolate and report them with exact test names; do not claim full success.

- [ ] **Step 3: Review requirements against the approved spec**

Confirm line by line:

- correct `.110` left-pair and `.112` right-pair mapping;
- 40 pixels per 2 m column;
- 32-inch spacing and bottom-up orientation;
- BRG only at the WLED bus layer;
- topology and all live segments in every AI path;
- tool chat, legacy fallback, vision, song, and autonomous coverage;
- explicit-control Music Mode handoff retained;
- no strobe/blink/flash regression;
- settings round-trip retained;
- backup/rollback utility tested.

- [ ] **Step 4: Commit documentation**

```bash
git add README.md
git commit -m "docs: record verified bedroom wall topology"
```

## Task 8: Back up and migrate the two live controllers

**Files:**
- Runtime backup outside repository: `/tmp/lightss-wled-backups/<timestamp>/`

- [ ] **Step 1: Stop continuous writers before migration**

Stop browser Music Mode from the GUI and verify no server-side autonomous/show process is posting. Do not kill the GUI unless necessary; the goal is to stop writers, not remove observability.

- [ ] **Step 2: Run migration dry-run and inspect both diffs**

```bash
backup_dir="/tmp/lightss-wled-backups/$(date +%Y%m%d-%H%M%S)"
python scripts/migrate_wled_topology.py --backup-dir "$backup_dir"
```

Expected: complete backup files for both hosts and a plan changing only bus starts, lengths, and color order. `.110` retains pins `[16, 2]`; `.112` retains `[2, 16]`.

- [ ] **Step 3: Apply migration and verify machine readback**

```bash
python scripts/migrate_wled_topology.py --backup-dir "$backup_dir" --apply
```

Expected for both hosts: WLED returns after reboot; total count is 80; buses are `0..39` and `40..79`; both report `order: 2`; segments are `0..39` and `40..79`.

- [ ] **Step 4: Identify all four segments visually with steady dim colors**

With Music Mode stopped, illuminate one segment at a time using effect 0 and brightness 80, always setting `udpn.nn=true`. Confirm with the user:

1. `.110` segment 0 is far-left and the full 2 m lights;
2. `.110` segment 1 is middle-left and the full 2 m lights;
3. `.112` segment 1 is middle-right and the full 2 m lights;
4. `.112` segment 0 is far-right and the full 2 m lights.

Send semantic red, green, and blue to one segment to verify BRG hardware ordering produces the requested visible colors. Use steady solid colors only.

- [ ] **Step 5: Roll back immediately on any mismatch**

If length, mapping, color, reboot, or readback fails:

```bash
python scripts/migrate_wled_topology.py --restore "$backup_dir"
```

Then verify restored `/json/cfg`, `/json/state`, and `/json/info`; report the exact failed invariant before attempting a different change.

- [ ] **Step 6: Run an end-to-end AI smoke test**

Start/reload the GUI, issue a safe request such as “make the far-left bottom half steady red and the middle-right top half steady blue,” inspect the captured AI context for the correct topology, and verify only the intended physical zones respond. Then start and stop Music Mode once to confirm browser ownership remains functional and an explicit command afterward remains visible.

- [ ] **Step 7: Final verification and handoff**

Re-run:

```bash
python -m pytest tests/ -q
git diff --check
curl --connect-timeout 2 --max-time 5 -fsS http://10.27.27.110/json/cfg | jq '.hw.led.ins | map({start,len,pin,order,type})'
curl --connect-timeout 2 --max-time 5 -fsS http://10.27.27.112/json/cfg | jq '.hw.led.ins | map({start,len,pin,order,type})'
```

Expected: fresh test success, clean diff check, and two 40-pixel/order-2 buses per controller with the verified GPIOs.
