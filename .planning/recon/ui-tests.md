# Recon: UI/status surfaces + test conventions

Read-only recon of `/home/rucaradio/lightss`, 2026-10-23 (live GUI :8123 untouched except one read-only `GET /`). Venv `.venv/bin/python` (3.14), pytest 7.4.4 + pytest_asyncio, `node` v25.9.0 on PATH (JS tests need it).
CAVEAT: the repo changed under me: `light_gui.py` grew 5686->5847 lines (mtimes 03:02-03:03) and `light_gui_html.py` 281232->290499 bytes (03:04) while a teammate landed the self-config boot feature; untracked `tests/test_boot_ui.py` (316 lines, 03:04) and `.planning/2026-10-23-self-config-ai-lighting/design.md` appeared. Line numbers below were refreshed at 03:07 (~`light_gui.py` 5847 lines, `light_gui_html.py` 6384); they may drift again. `boot_sequence.py` still absent at 03:07.

## 1. How director/AI status text reaches the UI

### Backend payloads
- `music_director_status_payload()` -> `{ok,status,enabled}`, enabled=`status.running`, never raises (light_gui.py:3794-3809; GET route 4726).
- `music_director.director_status()` -> running/track/mood/`last_response`/`last_error`/tracker/tv_session (music_director.py:644-662); `last_response` is the AI marquee line (music_director.py:516).
- Smart Director: `smart_director_status_payload()` -> `{ok,settings,status}` incl. `startup_error` (light_gui.py:4138-4147; GET 4621). `status` fields running/selected_mode/mode/reason/brightness_percent/audio/renderer/tv/intelligence/last_error (smart_director.py:500-514; idle fallback 658-660).
- `/api/tv-trivia` -> `{ok,artist,items[]}` (light_gui.py:3812-3829; tv_trivia.py:16,60-73); track from `director_status().track` (light_gui.py:3817-3821).
- In-flight boot: GET `/api/boot` -> `boot_status_payload()` phase/step/lines/report/enabled (light_gui.py:4229-4247; route 4624; config 4218-4226); POST `/api/boot` -> `configure_boot_sequence()` (4331-4360; route 5086-5101) which currently accepts enabled/dwell_s/brightness/run.

### Main page JS paths
- Model Responses panes `#modelResponses` (aria-live) in both player branches (light_gui_html.py:5555-5560, 5567-5572); marquee CSS 534-555.
- `appendModelResponse(kind,text)` (light_gui_html.py:1511-1545) writes `.marquee-content`, truncates >250 chars, persists last 50 in localStorage (~1531-1536), POSTs long text to `/api/model-response/summary` (endpoint light_gui.py:5338); restore 1547-1555, clear 1498-1508.
- Smart Director render: `renderSmartDirectorStatus()` sets mic/renderer/decision ids, then `smartDecisionSummary()` -> `appendModelResponse('decision'|'recipe'|'error', ...)` deduped (light_gui_html.py:2131-2181). Only callers: band-gain tune (1927) and spectrum accent (4238) via POST `/api/music-show`.
- IMPORTANT: `pollSmartDirector()`/`loadSmartDirector()` (light_gui_html.py:2192, 2210) are defined but never called — the page has only declarations, no `setInterval`, and no `id="smartDirector*"` elements (verified via `render_main_html(True/False)`). The "Smart lighting" card was deliberately removed (tests/test_full_music_ui.py:90-96; tests/test_smart_director_html.py:14-19). So Smart Director status text surfaces only after band-gain/accent, never on boot.
- Legacy Music Director: `pollMusicDirector()` reads status.last_error/last_response -> `appendModelResponse('ai'|'error')` (light_gui_html.py:2308-2320); polling starts only when the toggle is on, every 10 s (2325-2332; boot call 5243).
- `/tv`: `TV_AMBIENT_HTML` (light_gui_html.py:5769) served by `tv_ambient_html()` (light_gui.py:3640) on GET `/tv` (4998). Footer marquee 5841, `setFooter` 5995; `pollMusic` 6099-6117 every 10 s (MUSIC_MS 5855, interval 6377) reads `/api/music-director` track+mood; on new track fetches `/api/tv-trivia` (6045-6058) and rotates now-line+items via `animationiteration` (6004-6038). No boot/status channel exists on `/tv`.

## 2. Settings UI cards/toggles + handlers

- In-flight Self-config card `#bootCard` at the top of main (light_gui_html.py:734-758): `#bootEnabled` "Run at startup" (744) -> `toggleBootEnabled()` POST `/api/boot` {enabled} (2461-2472); `#bootRun` -> `runBootNow()` POST {run:true} (2474-2491); `#bootStatus`/`#bootLines`/`#bootPhase` rendered by `renderBootStatus()` (2389-2427); poll 800 ms running / 2000 ms idle (2430-2453, init 5244); lines are fed to the marquee via `appendModelResponse('boot', line)` (2423-2426). Also `#aiLightingEnabled` -> `toggleAiLighting()` (2493-2536).
- TV control `#firetvEnabled` in target bar (light_gui_html.py:766) -> `toggleFiretv()` POST `/api/firetv` {enabled} (1672-1684); GET/POST light_gui.py:4723, 5265-5274; `set_firetv_enabled` (3658-3670).
- Music Director `#musicDirectorEnabled` (light_gui_html.py:781) -> `toggleMusicDirector()` POST `/api/music-director` {enabled} (2350-2362); GET/POST light_gui.py:4726, 5276-5291 -> `set_music_director` (3832-3847), persists `music_director.enabled` (3750-3760).
- TV observation `.tv-observation` (light_gui_html.py:788-794) `#tvObservationEnabled`+refresh -> `toggleTvObservation()` POST `/api/tv-observation` {enabled} (1765-1780); GET light_gui.py:4608, POST 5111-5117, `set_tv_observation_enabled` (4113-4124); observe fields firetv.py:274-300.
- Smart Director has NO visible card (verified): only dormant JS (light_gui_html.py:1784-~2300; `readSmartDirectorForm` 1858-1871, `applySmartDirectorSettings` 2226). Backend GET 4621, POST 5086-5101 -> `configure_smart_director` (light_gui.py:1082-1122); config helper 4127-4136.
- Controllers card (light_gui_html.py:1297-1308; "Verify connections" at 1302) -> `verifyControllers()` (5082-5118; POST at 5087) -> handler light_gui.py:5332-5333 -> `verify_controllers()` probing `/json/info,/json/state,/json/cfg` + topology compare (3850-3912).
- Settings persistence (non-boot): `SETTINGS_MERGE_KEYS` (light_gui.py:3473-3483), `merge_settings_into_config`/`_merge_settings` (3530-3543), POST `/api/settings` 5259-5263; `current_settings` 3486-3536. New top-level keys are ignored unless added here; boot uses its own `/api/boot` path (4331), so the boot toggle does NOT need `SETTINGS_MERGE_KEYS`.
- Boot ordering (in-flight): `main()` starts daemon `_boot_then_automation` (light_gui.py:5824-5830 -> 4311-4328) which runs `_start_boot_sequence` (4277-4293) then `_start_configured_automation` (4295-4309: Smart Director first, else legacy Music Director); `reload_controllers` restarts configured Smart Director (3313).

## 3. Test conventions

- pytest config `testpaths=["tests"]`, `asyncio_mode="auto"` (pyproject.toml:90-92); run from repo root with the venv python.
- Fakes: duck-typed `FakeFleet.channels()/post_state()` recording posts (tests/test_music_director.py:23-44); `RecordingClient(lightctl.LightClient dry_run=True)` + real `fleet.LightFleet` (tests/test_columns.py:25-48); `SimpleNamespace` state (tests/test_smart_director_api.py:18-26); MagicMock state (tests/test_light_gui_server.py:208-218).
- HTTP endpoint tests: real `ThreadingHTTPServer(("127.0.0.1",0), light_gui.make_handler(state))` in setUp, daemon thread + shutdown cleanup (tests/test_smart_director_api.py:114-155; tests/test_settings_api.py:47-83, tmp config via `patch.object(lightctl,"_CONFIG_PATH",...)`:74, `_get`/`_post` 97-115).
- HTML/JS assertions: `light_gui.render_html(player_enabled=...)` + substring asserts or marker slices (tests/test_smart_director_html.py:6-12; tests/test_gui_html_audit.py:7-8).
- Executing shipped JS: extract slice by markers, inject stub prelude, run `node -e`, parse stdout JSON (`run_node` tests/test_full_music_ui.py:10-25; response-history safety 37-66, 253-277; decision dedupe 98-119). Syntax-only `node --check` per `<script>` (tests/test_gui_script_syntax.py:13-31).
- In-flight boot UI tests use the same pattern plus a fake DOM prelude (`Element`/node map, `appendModelResponse` recorder) (tests/test_boot_ui.py:25-62) and assert card markup ids + JS markers for both player branches (69-103), marquee line dedupe (134), run-now refusal/revert (199), AI-lighting toggle (230).
- Endpoint allowlists: `light_gui.API_GET_PATHS`/`API_POST_PATHS` (light_gui.py:143-145, now include `/api/boot`); GET-only assertion tests/test_light_gui_server.py:202-207.

## 4. Exact commands + known failures (verified this session)

```
cd /home/rucaradio/lightss
.venv/bin/python -m pytest tests/test_full_music_ui.py tests/test_smart_director_html.py tests/test_gui_script_syntax.py -q
.venv/bin/python -m pytest tests/test_music_director.py tests/test_smart_director_api.py tests/test_columns.py tests/test_settings_api.py tests/test_light_gui_server.py tests/test_gui_html_audit.py tests/test_gui_xss.py tests/test_live_show_controls_js.py tests/test_tv_observation.py -q
.venv/bin/python -m pytest tests/test_player_toggle.py tests/test_surfaces.py tests/test_new_features.py tests/test_http_security.py tests/test_realtime.py -q
```

Results: 12 passed; 242 passed + 2 failed; 193 passed (2nd/3rd batches predate the 03:02-03:04 edits; HTML tests unaffected). Post-edit smoke of tests/test_smart_director_api.py + test_settings_api.py + test_light_gui_server.py: 80 passed at 03:04. The 2 failures are environment-only, both tests/test_full_music_ui.py: `test_music_identity_css_contains_long_mobile_metadata` (199-223) and `test_google_chrome_executes_shipped_response_history_safely` (253-277). They shell out to `/home/linuxbrew/.linuxbrew/bin/python3` with `PYTHONPATH=/home/rucaradio/.local/lib/python3.14/site-packages`, where Playwright import fails: `ModuleNotFoundError: No module named 'greenlet._greenlet'`. Not a shipped-JS regression; skip with `-k "not css_contains_long_mobile_metadata and not google_chrome"`. `.pytest_cache/v/cache/lastfailed` is stale (lists removed files like `tests/test_debug_path.py`) — do not trust it.

## 5. Recommended placement (status at 03:07)

### (a) Boot-sequence status surface — already implemented, verify not re-add
- The surface exists exactly where this recon would put it: `#bootCard` above the target bar/tabs (light_gui_html.py:734-758), polling `/api/boot` and rendering `phase`/`step`/`lines` (2389-2427), with boot lines appended to the existing Model Responses marquee (2423; pane 5555-5572) — no new endpoint needed (light_gui.py:4229-4247).
- Remaining gap is the backend module: `boot_sequence.py` is missing, imports are lazy (light_gui.py:4219-4220), so `/api/boot` returns `ok:false` until it lands. `/tv` still has no boot line; if wanted, mirror `phase` by extending `refreshIdleFooter`/`pollMusic` (light_gui_html.py:5985-5998, 6099-6117) or `/api/tv-trivia` items (light_gui.py:3812-3829).
- Boot is also injected into the AI context (light_gui.py:1892-1894 `boot_sequence.context_text`), so the visible UI and AI share the same report.

### (b) Boot / boot-verify toggle — already implemented; keep it in the boot card
- `#bootEnabled` "Run at startup" (light_gui_html.py:744) posts `/api/boot` {enabled} (2461-2472); the handler validates/persists the `boot_sequence` section (light_gui.py:4331-4360) and refuses `run` in dry-run/fleet-less/wall-owned states (4335-4347). Do NOT persist through `/api/settings`/`SETTINGS_MERGE_KEYS` (3473-3483) — redundant.
- If "boot-verify" specifically means controller verification rather than self-config, reuse `verify_controllers()` (light_gui.py:3850-3912) inside `_boot_then_automation` (4311-4328) and publish through `boot_status_payload()`; surface it under the existing boot card rather than the Controllers card (1297-1308) to avoid two boot controls.
- Add coverage in the in-flight style of tests/test_boot_ui.py: fake DOM prelude (49-62), marker assertions for both player branches (69-103), marquee dedupe (134-198).

### Uncertainties
- Scope/naming (`boot_verify` vs "boot-everify"; self-config vs controller verify) is still unsettled: `configure_boot_sequence` accepts only enabled/dwell_s/brightness/run (light_gui.py:4337), and no controller-verify-on-boot code exists.
- `boot_sequence.py` referenced but absent at 03:07; `/api/boot` is `ok:false` until it lands.
- Smart Director's missing browser card appears intentional (tests assert its removal); if the Lead expected it, that is a pre-existing gap, not caused by recon.
- Line numbers are snapshots of an actively edited working tree; prefer function/marker names via grep when using this report later.
