# Boot / self-config recon (read-only)

Scope: `/home/rucaradio/lightss` working tree (git status shows modified `fleet.py`, `light_gui.py`, `lightctl.py`, `mcp_light.py`, …). Live GUI = `light_gui.py` (PID 699475, started 02:51). Nothing was modified/restarted except this file.

## 1. Current startup flow
- Entry: `light-gui` shell wrapper → `.venv/bin/python light_gui.py`; `main()` at light_gui.py:5608.
- `main()` order: argparse 5609-5629 → `logging.basicConfig` 5631 → `lightctl.load_dotenv()` 5632 → single-host client 5637-5638 or `_build_fleet_client` 5640 (def 2978-2991) → `GuiState(...)` 5646 → `fleet.topology_mismatches()` warnings only 5649-5656 → HTTP bind 5657-5660 → Music Director auto-start iff `music_director.enabled` and not dry-run 5661-5668 → `serve_forever` 5675.
- `GuiState.__init__` 3006-3054: ReactiveThread 3020, BrowserFirstAutonomousMode 3021, song observer 3022, ControllerAudioMonitor 3023, ScheduleExecutor 3026, MoodSession 3027, `schedule.start()` 3039, state/action/reload locks 3048-3050, observers started LAST 3053 (skipped in dry-run).
- Fleet construction is offline/lazy: `fleet.from_config` 347-355 only builds clients; `start_observing` 357-364 opens them later. Asserted in tests/test_wled_gui_lifecycle.py:21-27 and GuiState start at 59-67.
- Therefore "controllers ready" is not an event: first readiness proof is a read — `calibrate.collect_probes` (calibrate.py:197, failures become `{"error":...}`) or the GUI probe `verify_controllers` (light_gui.py:3838, POST route 5171-5173). `fleet.topology_mismatches` (fleet.py:664) also reads state+config per controller.
- Hook points for a boot sequence: (a) after the server exists (~5657) start a daemon thread, before director auto-start 5661 if lights may be written; (b) re-run after `GuiState.reload_controllers()` (3208-3310; re-observes 3299-3300), since successful calibration/self-config calls it (light_gui.py:4991, 5018-5024).

## 2. Existing calibration / identify capabilities
- `calibrate.py`: `probe_client` 151 (get_info/get_state/get_config → JSON probe, `validation_errors`), `collect_probes` 197, `build_controllers` 228 (preserves channel names by name then host), `apply_assignments` 271, `write_topology_config` 328 (backup `config.json.bak-%Y%m%d-%H%M%S` then `save`), `calibrate` 360 (probe→report/optional persist), `identify` 436 (WLED `on:"t"` flashes, even count, restores master power; fleet target = channel/controller/group).
- `calibration_service.py` `CalibrationService`: read-only `observe` 214, `scan` 190 (stages token, config SHA-256 digest, hardware facts, proposed controllers), `apply` 299 (single-use token, digest + fresh-fleet + hardware re-validation, writes only `controllers`, preserves `installation`, unique backup, 0600 atomic write 248-283, cross-process flock `.calibration.lock` 284-297), `identify(controller, segment)` 350 (solid orange marker, bri 24, `IDENTIFY_SECONDS=1.0`, restores `on/bri/seg` in `finally`; refuses when a renderer is active 331-348 or state has `live`/frozen seg). Explicit limitations 24-28: controller declarations cannot prove physical order/orientation.
- `vision_calibration.py`: `light_probes` 86 saves prior `{on,bri,seg}` and lights per-segment `i` runs (probe colors 47, `DEFAULT_PROBE_PIXELS` 4, bri 200), returns restore closure; `parse_observation` 178; `assign_channels` 211 (color at physical position → wall_order name); `build_segment_plan` 245; `begin_probe` 301 (two-step: light → caller captures → `conclude` 330 → vision + mapping + persist + restore-in-finally); `self_configure` 407 (one-shot with injected `capture`, `vision`, `load`, `save`).
- **Stub alert:** `apply_segment_plan` 264-278 does NOT post segment bounds; it returns `ok:false` per controller ("Segment bounds stay as programmed on the controller"). `conclude(write_segments=True)` therefore never repairs WLED segments; it only persists `controllers`/`wall_order`/`pixel_zero`/`orientation` via `calibrate.write_topology_config` (vision_calibration.py:380-401). Module docstring claims otherwise (vision_calibration.py:15-19) — treat as doc/code drift.
- GUI routes (POST unless noted): `/api/calibration` dispatch 4967-5000 (`scan`/`apply`/`identify`, `vision` 4986); `/api/calibration/self-configure/light` 5003-5006 → `start_self_configure` 4322; `/api/calibration/self-configure/finish` 5007-5027 → `finish_self_configure` 4350; pending-token dict/TTL 4293-4295 (120 s); GET `/api/lighting-observation` 4457-4460; GET `/api/state` 4662+. There is no `/api/identify`; identify is the `/api/calibration` action plus MCP tool (mcp_light.py:2046).
- `/api/match-lights` (POST 5250-5293) is song→look AI matching, not calibration.
- UI: desktop shell dialog `desktop_ui.py:4-15`, `identifyCalibration` 106, `selfConfigureFromCamera` 150-185 (browser `getUserMedia` frame → light → 1.2 s wait → finish). Web GUI pages have no calibration dialog (only leftover listener refs light_gui_html.py:5062-5063).

## 3. Per-strip control + state-read primitives
- Light exactly one channel: `columns.set_channel(fleet, channel, **seg_opts)` columns.py:185 (payload id-less; fleet injects the segment id); subset `apply_channels` 156; exact per-strip `per_strip` 164. Payloads built by `lightctl.segment_payload` lightctl.py:713, byte clamping `clamp_byte` 271, sacred fields filtered in `fleet.drop_sacred_segment_fields` fleet.py:299 (`SACRED_SEGMENT_KEYS` 294).
- Fan-out: `LightFleet.post_state` fleet.py:490 (id-less segment templates expand over resolved strips 498-521; channel target injects id; adds `udpn.nn`; one POST/controller; dead controller isolated). `resolve` 440 accepts `all|group|controller|channel|comma/plus combo`. `channels()` 403 = `{channel: (controller, seg_id)}` (source of truth for segment ids); `WALL_ORDER`/`STRIP_GROUPS` fleet.py:25,327.
- Read current programming per column: `fleet.get_state()` fleet.py:526 → `{controller: state}` (or `{error}`); WLED state `"seg"` entries keyed by `id`, fields `fx`/`pal`/`sx`/`ix`/`col`/`bri`/`on`/`start`/`stop` (byte-field set columns.py:28-30). Map channel→seg via `channels()`; cache-aware GUI readers: `GuiState.get_state_throttled` 3106, `api_state` 3117, `channel_map` 3137, `invalidate_caches` 3102. Ready-made decoded per-channel view (channel, effect_name, col, pixel summary): `lighting_context.observe_lighting` 117, segment fields 171-193, per-controller result 233-234. Device-level snapshot: `get_fleet_snapshot` fleet.py:537 / `LightClient.get_device_snapshot` lightctl.py:2190 (state+info+effects+catalog), `get_state` 2240, `get_effects` 2108.
- One-channel identify primitives: `calibration_service.identify` 350 (marker+restore, renderer-guarded) or `calibrate.identify` 436 (flash toggles). Neither is channel-addressed; resolve channel→(controller,segment) yourself first.
- Director interplay: `fleet.post_state` calls `smart_director.before_external_write` when loaded (fleet.py:490-508), but `vision_calibration.light_probes` posts DIRECTLY to `clients` (vision_calibration.py:86-160), bypassing that coordination and restore is best-effort.

## 4. Persistence options
- Canonical config root: `lightctl._CONFIG_ROOT` = `$LIGHTSCTL_CONFIG_DIR` or `~/.config/lightsctl` lightctl.py:1143-1145; `_CONFIG_PATH` 1736. `CONFIG_LOCK` 1156. Helpers: `_read_json_file` 1182 (migrates legacy `~/.config/lightss` via `_migrate_legacy_file` 1158-1179), `_atomic_write_json` 1204 (fsync, 0600, `os.replace`), `load_config` 1739, `save_config` 1743, **`mutate_config` 1750** (preferred atomic read-modify-write).
- `config.json` top keys (observed live fs, Oct 23 02:22): `ai, audio_player, audio_source, controllers, firetv, installation, mic_device, music_director, smart_director, system_prompt_override`; `installation` = name/wall_order/pixel_zero/orientation/spacing/column_length_m/pixels_per_meter/visible_leds_per_meter/color_order; live controllers = left(0 far-left,1 middle-left), right(0 middle-right,1 far-right). Section-accessor pattern: `music_director_config` light_gui.py:3730; validated accessor `smart_director_config` 4115.
- Learned-layout writes: `calibrate.write_topology_config` 328-357 (backup then whole-file `save`; does NOT take `CONFIG_LOCK`) vs `CalibrationService.apply` 299 (flock + digest + atomic 0600, but only cooperating writers). Avoid concurrent writers; prefer `lightctl.mutate_config` for a boot write.
- Other patterns: `look_memory.json` look_memory.py:18/21/37-48 (tmp + `os.replace`, `MAX_EVENTS=100` 14, lives in `_SCENE_DIR` = config root 1147); `song_moods.json` mood_orchestrator.py:22/26 written under **legacy** `~/.config/lightss` (not `lightsctl`; live fs shows both dirs — inconsistency, mark uncertain which is intended long-term; config itself is in `lightsctl`).
- Boot-report placement recommendation: separate `lightctl._CONFIG_ROOT / "boot_report.json"` (report is not config; avoids racing settings writers), written with the `_atomic_write_json` 1204 or `look_memory.save` pattern; optional enable/disable + policy under a new `"boot"` config section via `mutate_config` 1750, read with a `boot_config()` accessor shaped like 3730.

## 5. Test conventions
- `pyproject.toml:90-92`: `[tool.pytest.ini_options] testpaths=["tests"]`, `asyncio_mode="auto"`; no conftest; both plain pytest functions and `unittest.TestCase` classes are used.
- Hardware never touched: duck-typed fake WLED clients exposing `get_info/get_state/get_config/post_state` (tests/test_calibration_service.py:20-46, tests/test_vision_calibration.py:20-50); `lightctl.LightClient(..., dry_run=True)` recorders for fleet/columns (tests/test_columns.py:17-25, tests/test_fleet.py:19-25).
- Injection is the norm: `sleep` arg (calibrate.py:436-441), `clock`/`sleep` attrs (calibration_service.py:38-45), `capture`/`vision`/`load`/`save`/`config_path` kwargs (vision_calibration.py:330-345, 407-425), temp configs via `tmp_path`/`tempfile` (tests/test_vision_calibration.py:132+, tests/test_calibration_service.py:50-60).
- GUI/server tests serve a real `ThreadingHTTPServer` with `light_gui.make_handler(MagicMock(...))` and urllib requests (tests/test_light_gui_server.py:534-620); GuiState lifecycle tested in tests/test_wled_gui_lifecycle.py:59-124; no `main()`/boot test exists (`grep` found none).
- Calibration coverage: `calibrate`/`identify`/MCP tools tests/test_calibrate.py:240-299; scan/apply/identify/renderer-guard tests/test_calibration_service.py:70-215; vision mapping/orientation/restore/backup tests/test_vision_calibration.py:132-320; per-strip payloads tests/test_columns.py:49-182.

## 6. Gaps
1. No boot sequence: `main()` only binds HTTP and optionally starts Music Director 5608-5682; no readiness probe, no self-check, no report.
2. Topology drift at boot is only `logger.warning` 5649-5656; the real check (`topology_mismatches` fleet.py:664) is not surfaced anywhere persistent.
3. Self-config cannot repair segments despite the name: `apply_segment_plan` is a no-op stub vision_calibration.py:264-278; only config channels/wall_order/pixel_zero are persisted 380-401.
4. Autonomous vision is impossible server-side: capture+vision are injected callables (vision_calibration.py:330,407); the GUI supplies browser `getUserMedia` only (desktop_ui.py:150-185). A boot self-config needs either a capture adapter or a human/browser step (uncertain what camera API exists outside Chrome; no server-side camera found).
5. Renderer ownership conflicts: `calibration_service.identify` refuses active renderers 331-348, but probe lighting bypasses director coordination 86-160; director auto-start happens after server bind 5661-5668, so ordering/gating is a design decision.
6. Write-race risk: `write_topology_config` 328-357 bypasses `CONFIG_LOCK`/`mutate_config` 1750; calibration's flock 284-297 only protects cooperating writers.
7. No persistence for a boot report and no tests for startup/readiness; `dry_run` exists (lightctl.LightClient, GuiState 3006-3015) but self-config has no dry-run guard.
8. Config-root drift: `lightctl._CONFIG_ROOT=~/.config/lightsctl` 1143 vs `mood_orchestrator._CONFIG_DIR=~/.config/lightss` 22 (legacy); boot report must pick the modern root explicitly.
9. `calibration_service.identify` needs explicit `(controller, segment)` 350, not a channel; boot flow must resolve via `fleet.channels()` 403 and must tolerate offline controllers.

## 7. Recommended module/API surface (signatures only)
New module `boot_self_config.py` (no behavior specified here), duck-typed like `calibrate`/`vision_calibration`, all effects injected for tests:
```python
@dataclass(frozen=True)
class BootReport: ...            # timestamp, config_digest, ready, controllers, channels, mismatches, proposals, written, errors

def read_channel_programs(fleet: Any) -> dict[str, dict[str, Any]]: ...
def probe_readiness(fleet: Any, *, timeout_s: float = 5.0) -> dict[str, Any]: ...
def plan_channel_repairs(fleet: Any, probes: dict[str, dict]) -> dict[str, Any]: ...
def apply_channel_repairs(fleet: Any, plan: dict[str, Any], *, load: Callable[[], dict] = lightctl.load_config,
                          save: Callable[[dict], None] = lightctl.save_config,
                          mutate: Callable[[Callable[[dict], None]], dict] | None = None,
                          config_path: str = lightctl._CONFIG_PATH) -> dict[str, Any]: ...
def run_boot_self_config(fleet: Any, *, dry_run: bool = False, probe_lights: bool = False,
                         capture: Callable[[], str] | None = None,
                         vision: Callable[[str, str], str] | None = None,
                         config_path: str = lightctl._CONFIG_PATH) -> BootReport: ...
def persist_boot_report(report: BootReport, *, path: str | None = None) -> str: ...
def start_boot_self_config(state: Any, *, delay_s: float = 0.0, config_path: str | None = None) -> threading.Thread | None: ...
```
Integration: call `start_boot_self_config(state)` in `main()` after light_gui.py:5657 (before director auto-start 5661), and again after `reload_controllers()` light_gui.py:3208 / 4991 / 5018-5024. Gate on a new `boot` config section read like `music_director_config` 3730, skip when `state.dry_run` (3006-3015); report to `lightctl._CONFIG_ROOT/"boot_report.json"` (1143,1204) and expose read-only via a GET route next to `/api/lighting-observation` 4457.
